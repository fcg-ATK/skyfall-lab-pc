"""Import the bundled OFL Chinese fonts for the native Canvas HUD.

Run build_ui_fonts() in the Unreal editor. FontFileImportFactory creates both a
FontFace and its runtime UFont, including the native composite-font reference.
No operating-system font lookup is needed when the game is launched.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import unreal


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "SourceAssets/UI/Fonts"
ASSET_FOLDER = "/Game/Skyfall/UI/Fonts"
BASE_FONT_SIZE = 24
FONT_FILES = {
    "regular": ("NotoSansSC-Regular.otf", "NotoSansSC_Regular"),
    "bold": ("NotoSansSC-Bold.otf", "NotoSansSC_Bold"),
}


def build_ui_fonts() -> dict[str, str]:
    """Return {'regular': UFont object path, 'bold': UFont object path}.

    HUD.DrawText uses Slate's 96-DPI rasterizer: Scale is desired pixels / 32
    for this 24-point font, additionally multiplied by the viewport UI scale.
    Existing imported assets are reused, so repeated HUD builds do not create
    duplicate *_Font_1 assets. Only these two UI font pairs are touched.
    """
    for required in ("OFL.txt", "SOURCES.md"):
        if not (SOURCE / required).is_file():
            raise RuntimeError("Missing bundled font license/source file: " + required)
    unreal.EditorAssetLibrary.make_directory(ASSET_FOLDER)
    result = {}
    report = {"base_font_size": BASE_FONT_SIZE, "fonts": {}}
    for weight, (filename, asset_name) in FONT_FILES.items():
        source_file = SOURCE / filename
        if not source_file.is_file():
            raise RuntimeError("Missing Chinese font: " + str(source_file))
        face_path = ASSET_FOLDER + "/" + asset_name
        font_path = face_path + "_Font"
        face = unreal.load_asset(face_path)
        font = unreal.load_asset(font_path)
        if not face or not font:
            factory = unreal.FontFileImportFactory()
            factory.set_editor_property(
                "batch_create_font_asset",
                unreal.BatchCreateFontAsset.CREATE_IF_NO_FONT_EXISTS,
            )
            task = unreal.AssetImportTask()
            task.set_editor_property("filename", str(source_file))
            task.set_editor_property("destination_path", ASSET_FOLDER)
            task.set_editor_property("destination_name", asset_name)
            task.set_editor_property("factory", factory)
            task.set_editor_property("automated", True)
            task.set_editor_property("replace_existing", True)
            task.set_editor_property("save", True)
            unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
            face = unreal.load_asset(face_path)
            font = unreal.load_asset(font_path)
        if not isinstance(face, unreal.FontFace) or not isinstance(font, unreal.Font):
            raise RuntimeError("Chinese FontFace/UFont import failed: " + face_path)
        face.set_editor_property("loading_policy", unreal.FontLoadingPolicy.INLINE)
        font.set_editor_property("font_cache_type", unreal.FontCacheType.RUNTIME)
        font.set_editor_property("legacy_font_name", "Default")
        font.set_editor_property("legacy_font_size", BASE_FONT_SIZE)
        for asset in (face, font):
            if not unreal.EditorAssetLibrary.save_loaded_asset(asset):
                raise RuntimeError("Cannot save Chinese font asset: " + asset.get_path_name())
        result[weight] = font.get_path_name()
        report["fonts"][weight] = {
            "source": str(source_file.relative_to(ROOT)),
            "sha256": hashlib.sha256(source_file.read_bytes()).hexdigest(),
            "font_face": face.get_path_name(),
            "font": font.get_path_name(),
            "loading_policy": str(face.get_editor_property("loading_policy")),
            "font_cache_type": str(font.get_editor_property("font_cache_type")),
            "legacy_font_size": font.get_editor_property("legacy_font_size"),
        }
    report["ok"] = True
    (ROOT / "Saved").mkdir(exist_ok=True)
    (ROOT / "Saved/ui-fonts.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    unreal.log("SKYFALL_CHINESE_UI_FONTS_READY " + json.dumps(result))
    return result


if __name__ == "__main__":
    build_ui_fonts()
