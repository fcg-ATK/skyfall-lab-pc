"""Remove absolute texture filenames from Blender-exported binary FBX sources.

Uses each texture/video object's existing RelativeFilename. Rewrites FBX node
offsets and footer alignment without touching geometry, transforms or materials.
Run with standard Python: python tools/sanitize_source_fbx.py
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import struct

HEADER = b'Kaydara FBX Binary  \x00\x1a\x00'
FOOT_ID = bytes.fromhex('fabcab09d0c8d466b176fb831cf7267e')
FOOT_MAGIC = bytes.fromhex('f85a8c6adef5d97eece90ce3758f290b')

@dataclass
class Node:
    name: bytes
    count: int
    props: bytes
    children: list
    sentinel: bool

def parse(data):
    if data[:23] != HEADER:
        raise ValueError('Not a binary FBX file')
    version = struct.unpack_from('<I', data, 23)[0]
    form = '<QQQB' if version >= 7500 else '<IIIB'
    size = struct.calcsize(form)
    def one(pos):
        end, count, length, name_length = struct.unpack_from(form, data, pos)
        if end == 0:
            if data[pos:pos + size] != bytes(size):
                raise ValueError('Invalid null FBX record')
            return None, pos + size
        name_start = pos + size
        prop_start = name_start + name_length
        cursor = prop_start + length
        if not cursor <= end <= len(data):
            raise ValueError('Invalid FBX node offsets')
        children, sentinel = [], False
        while cursor < end:
            child, cursor = one(cursor)
            if child is None:
                sentinel = True
                break
            children.append(child)
        if cursor != end:
            raise ValueError('FBX node extent mismatch')
        return Node(data[name_start:prop_start], count, data[prop_start:prop_start + length], children, sentinel), end
    roots, cursor = [], 27
    while True:
        node, cursor = one(cursor)
        if node is None:
            break
        roots.append(node)
    tail = data[cursor:]
    if not tail.startswith(FOOT_ID) or not tail.endswith(FOOT_MAGIC):
        raise ValueError('Unsupported FBX footer')
    if tail[-140:-136] != struct.pack('<I', version) or tail[-136:-16] != bytes(120):
        raise ValueError('Unexpected FBX footer version/padding')
    if any(tail[16:-140]):
        raise ValueError('Unsupported nonzero FBX footer padding')
    return version, roots

def string(node):
    if node.count != 1 or node.props[:1] != b'S':
        raise ValueError('Expected single FBX string property')
    length = struct.unpack_from('<I', node.props, 1)[0]
    if length + 5 != len(node.props):
        raise ValueError('FBX string length mismatch')
    return node.props[5:]

def video_path(node):
    return node.name == b'P' and node.props.startswith(b'S\x04\x00\x00\x00Path')

def sanitize(roots):
    changes = []
    def walk(node, inherited_relative=None):
        relative = next((string(c) for c in node.children if c.name == b'RelativeFilename'), inherited_relative)
        if relative:
            if relative.startswith((b'/', b'\\')) or (len(relative) > 1 and relative[1:2] == b':'):
                raise ValueError('Expected a relative texture filename')
            for child in node.children:
                if child.name in (b'FileName', b'Filename', b'AbsoluteFilename'):
                    old = string(child)
                    absolute = old.startswith((b'/', b'\\')) or (len(old) > 1 and old[1:2] == b':')
                    if absolute:
                        child.props = b'S' + struct.pack('<I', len(relative)) + relative
                        changes.append({'node': node.name.decode('ascii'), 'property': child.name.decode('ascii'),
                                        'new': relative.decode('utf-8'), 'removed_absolute_path': True})
            if video_path(node):
                cursor, items = 0, []
                for _ in range(node.count):
                    if node.props[cursor:cursor+1] != b'S':
                        raise ValueError('Unexpected Video Path property type')
                    length = struct.unpack_from('<I', node.props, cursor+1)[0]
                    items.append(node.props[cursor+5:cursor+5+length])
                    cursor += 5+length
                if cursor != len(node.props) or len(items) != 5:
                    raise ValueError('Unexpected Video Path properties')
                old = items[-1]
                if old.startswith((b'/', b'\\')) or (len(old) > 1 and old[1:2] == b':'):
                    items[-1] = relative
                    node.props = b''.join(b'S'+struct.pack('<I',len(item))+item for item in items)
                    changes.append({'node': 'Video/Properties70/P', 'property': 'Path',
                                    'new': relative.decode('utf-8'), 'removed_absolute_path': True})
        for child in node.children:
            walk(child, relative)
    for root in roots:
        walk(root)
    return changes

def encode(version, roots):
    form = '<QQQB' if version >= 7500 else '<IIIB'
    size = struct.calcsize(form)
    output = bytearray(HEADER + struct.pack('<I', version))
    def one(node):
        start = len(output)
        output.extend(bytes(size))
        output.extend(node.name)
        output.extend(node.props)
        for child in node.children:
            one(child)
        if node.sentinel:
            output.extend(bytes(size))
        struct.pack_into(form, output, start, len(output), node.count, len(node.props), len(node.name))
    for root in roots:
        one(root)
    output.extend(bytes(size))
    output.extend(FOOT_ID)
    # Blender's footer places the version on a 16-byte boundary, after at
    # least four zero padding bytes.
    output.extend(bytes(4 + (-(len(output) + 4)) % 16))
    output.extend(struct.pack('<I', version))
    output.extend(bytes(120))
    output.extend(FOOT_MAGIC)
    return bytes(output)

def fingerprint(roots, ignore_paths=False):
    digest = hashlib.sha256()
    def walk(node):
        digest.update(node.name + struct.pack('<Q?', node.count, node.sentinel))
        if not (ignore_paths and (node.name in (b'FileName', b'Filename', b'AbsoluteFilename') or video_path(node))):
            digest.update(node.props)
        digest.update(struct.pack('<I', len(node.children)))
        for child in node.children:
            walk(child)
    for root in roots:
        walk(root)
    return digest.hexdigest()

def sanitize_file(path):
    original = path.read_bytes()
    version, roots = parse(original)
    baseline = fingerprint(roots, ignore_paths=True)
    changes = sanitize(roots)
    if not changes:
        return {'path': path.name, 'changed_properties': 0, 'status': 'already_relative'}
    sanitized = encode(version, roots)
    new_version, reread = parse(sanitized)
    if new_version != version or fingerprint(reread, ignore_paths=True) != baseline:
        raise ValueError('FBX non-path data changed')
    if fingerprint(reread) != fingerprint(roots):
        raise ValueError('FBX serialization changed the intended tree')
    import re
    if re.search(rb'[A-Za-z]:[\\/]+Users[\\/]', sanitized):
        raise ValueError('An absolute user path remains')
    path.write_bytes(sanitized)
    return {'path': path.name, 'changed_properties': len(changes), 'changes': changes,
            'bytes_before': len(original), 'bytes_after': len(sanitized),
            'sha256': hashlib.sha256(sanitized).hexdigest(), 'non_path_data_unchanged': True,
            'status': 'sanitized'}

def main():
    root = Path(__file__).resolve().parents[1]
    paths = [root / 'SourceAssets/building_v4_pc.fbx', root / 'SourceAssets/CombatV2/fracture_v2.fbx']
    report = [sanitize_file(path) for path in paths]
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
