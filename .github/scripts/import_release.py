#!/usr/bin/env python3
"""One-time import of the reviewed public source ZIP. Python standard library only.

Run only from the fixed repository's workflow_dispatch on main. This script never
executes archive contents, changes Git settings, or reads a GitHub access token.
"""
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zipfile import BadZipFile, ZipFile, ZIP_DEFLATED, ZIP_STORED

REPOSITORY = "fcg-ATK/skyfall-lab-pc"
ARCHIVE_URL = (
    "https://github.com/fcg-ATK/skyfall-lab-pc/releases/download/"
    "pc-source-v0.1.0/skyfall-lab-pc-source-v0.1.0.zip"
)
MARKER = ".skyfall-source-imported.json"
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_FILE_BYTES = 100 * 1024 * 1024 - 1
MAX_ENTRIES = 15000
MAX_COMPRESSION_RATIO = 1000
MAX_PATH_LENGTH = 220
CHUNK = 1024 * 1024
PROTECTED = {".git", ".github", "import-helpers", "import_release.py", "test_import_release.py", MARKER}
REQUIRED = {"SkyfallPC.uproject", "Config/DefaultEngine.ini", "Content/Skyfall/Maps/DemolitionYard.umap"}


class ImportRejected(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise ImportRejected(message)


def validate_digest(value):
    require(bool(re.fullmatch(r"[a-fA-F0-9]{64}", value or "")), "Set a reviewed, complete SHA256 before running.")
    require(value != "0" * 64, "Placeholder SHA256 is not allowed.")
    return value.lower()


def allowed_download_url(url):
    parsed = urlsplit(url)
    return (parsed.scheme == "https" and parsed.username is None and parsed.password is None
            and parsed.port in (None, 443)
            and parsed.hostname in {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"})


class RestrictedRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        require(allowed_download_url(newurl), "Refusing redirect outside GitHub release storage.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_archive(target):
    request = Request(ARCHIVE_URL, headers={"User-Agent": "skyfall-lab-pc-source-import/1"})
    opener = build_opener(RestrictedRedirects())
    with opener.open(request, timeout=60) as response, target.open("xb") as output:
        require(allowed_download_url(response.geturl()), "Unexpected download destination.")
        advertised = response.headers.get("Content-Length")
        if advertised is not None:
            require(advertised.isdigit() and int(advertised) <= MAX_ARCHIVE_BYTES, "Archive download is too large.")
        size = 0
        while data := response.read(CHUNK):
            size += len(data)
            require(size <= MAX_ARCHIVE_BYTES, "Archive download exceeds size limit.")
            output.write(data)


def canonical_parts(info):
    name = info.orig_filename
    require(name and len(name) <= MAX_PATH_LENGTH, "Empty or excessively long archive path.")
    require(not any(ord(c) < 32 or ord(c) == 127 for c in name), "Control character in archive path.")
    require("\\" not in name and ":" not in name and not name.startswith("/"), "Absolute or Windows-style archive path.")
    raw = name[:-1] if name.endswith("/") else name
    parts = raw.split("/")
    require(all(part and part not in {".", ".."} for part in parts), "Unsafe path component.")
    for part in parts:
        key = unicodedata.normalize("NFC", part).casefold()
        require(key not in PROTECTED, "Archive contains a protected bootstrap or Git path.")
        require(part == part.rstrip(" ."), "Trailing dot or space in archive path.")
        require(not any(c in part for c in '<>"|?*'), "Invalid Windows path character.")
        require(not re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part), "Reserved Windows device name.")
    return parts


def check_target(root, parts, is_directory):
    target = root
    for index, part in enumerate(parts):
        target = target / part
        require(not target.is_symlink(), "Destination contains a symlink.")
        if target.exists():
            if index < len(parts) - 1 or is_directory:
                require(target.is_dir(), "Destination parent is not a directory.")
            else:
                require(target.is_file(), "Destination file is not a regular file.")
        require(target.resolve().is_relative_to(root), "Destination escapes checkout.")


def validate_members(bundle, root):
    members = bundle.infolist()
    require(0 < len(members) <= MAX_ENTRIES, "Archive member count exceeds limit.")
    seen = {}
    spellings = {}
    total = 0
    validated = []
    files = set()
    for info in members:
        parts = canonical_parts(info)
        for end in range(1, len(parts) + 1):
            prefix = "/".join(parts[:end])
            prefix_key = unicodedata.normalize("NFC", prefix).casefold()
            require(prefix_key not in spellings or spellings[prefix_key] == prefix, "Case or Unicode alias in archive path components.")
            spellings[prefix_key] = prefix
        name = "/".join(parts)
        key = unicodedata.normalize("NFC", name).casefold()
        require(key not in seen, "Duplicate or case-colliding archive path.")
        kind = stat.S_IFMT(info.external_attr >> 16)
        require(kind in {0, stat.S_IFREG, stat.S_IFDIR}, "Symlink or special-file archive member.")
        require(not (kind == stat.S_IFDIR and not info.is_dir()), "Directory type mismatch.")
        require(not (kind == stat.S_IFREG and info.is_dir()), "File type mismatch.")
        require(not info.flag_bits & 1, "Encrypted archive members are not accepted.")
        require(info.compress_type in {ZIP_STORED, ZIP_DEFLATED}, "Unsupported ZIP compression.")
        require(0 <= info.file_size <= MAX_FILE_BYTES, "Archive member exceeds file-size limit.")
        require(info.file_size <= max(info.compress_size, 1) * MAX_COMPRESSION_RATIO, "Excessive compression ratio.")
        if info.is_dir():
            require(info.file_size == 0, "Directory contains data.")
        total += info.file_size
        require(total <= MAX_TOTAL_BYTES, "Archive exceeds uncompressed-size limit.")
        seen[key] = info.is_dir()
        check_target(root, parts, info.is_dir())
        validated.append((info, parts))
        if not info.is_dir():
            files.add(name)
    for key in seen:
        pieces = key.split("/")
        for count in range(1, len(pieces)):
            parent = "/".join(pieces[:count])
            require(parent not in seen or seen[parent], "File is also used as a parent directory.")
    require(REQUIRED <= files, "ZIP must contain the project, config and map at its root.")
    return validated


def import_archive(archive, destination, expected_sha256):
    expected = validate_digest(expected_sha256)
    require(destination.is_dir() and not destination.is_symlink(), "Checkout must be an existing real directory.")
    root = destination.resolve()
    require(not (root / MARKER).exists() and not (root / MARKER).is_symlink(), "Source was already imported; refusing to repeat.")
    require(archive.is_file() and archive.stat().st_size <= MAX_ARCHIVE_BYTES, "Archive exceeds download-size limit.")
    hasher = hashlib.sha256()
    with archive.open("rb") as stream:
        while data := stream.read(CHUNK):
            hasher.update(data)
    require(hasher.hexdigest() == expected, "Archive SHA256 does not match the reviewed value.")
    with ZipFile(archive) as bundle, tempfile.TemporaryDirectory(prefix="skyfall-verified-") as staging:
        validated = validate_members(bundle, root)
        stage = Path(staging)
        total = 0
        for info, parts in validated:
            target = stage.joinpath(*parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with bundle.open(info) as source, target.open("xb") as output:
                while data := source.read(CHUNK):
                    written += len(data)
                    total += len(data)
                    require(written <= info.file_size and written <= MAX_FILE_BYTES and total <= MAX_TOTAL_BYTES, "Decompressed data exceeds checked bounds.")
                    output.write(data)
            require(written == info.file_size, "Archive member size mismatch.")
        # No checkout writes happen until every member has passed decompression and CRC checks.
        count = 0
        for info, parts in validated:
            check_target(root, parts, info.is_dir())
            target = root.joinpath(*parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(stage.joinpath(*parts), target)
            target.chmod(0o644)  # Never preserve executable/setuid modes from the ZIP.
            count += 1
    marker = {"repository": REPOSITORY, "archive_url": ARCHIVE_URL, "sha256": expected, "files": count}
    (root / MARKER).write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
    return count


def main():
    require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY, "This importer only runs in the fixed repository.")
    require(os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch", "Only manual dispatch is supported.")
    require(os.environ.get("GITHUB_REF") == "refs/heads/main", "Dispatch must use main.")
    expected = validate_digest(os.environ.get("ARCHIVE_SHA256", ""))
    workspace = os.environ.get("GITHUB_WORKSPACE", "")
    require(bool(workspace), "Missing checkout workspace.")
    root = Path(workspace)
    require(not (root / MARKER).exists(), "Source was already imported; refusing to download again.")
    with tempfile.TemporaryDirectory(prefix="skyfall-download-") as download:
        archive = Path(download) / "source.zip"
        download_archive(archive)
        count = import_archive(archive, root, expected)
    print(f"Verified and imported {count} files from the fixed release; no archive code executed.")


if __name__ == "__main__":
    try:
        main()
    except (ImportRejected, BadZipFile, OSError, ValueError) as error:
        raise SystemExit(f"Import refused: {error}") from error

