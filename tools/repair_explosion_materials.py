"""Rebuild only explosion materials; use a D3D editor for shader verification."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import unreal
from effects_v2 import BUILD_VERSION, EDIT, ROOT, _explosion_material, _verify_explosion_graph

report = {}
for name, mode in (("M_BlastFire", "fire"), ("M_BlastSmoke", "smoke"), ("M_PressureRing", "ring")):
    material = _explosion_material(name, mode)
    _verify_explosion_graph(material)
    unreal.SystemLibrary.execute_console_command(None, "Editor.AsyncAssetCompilationFinishAll")
    # GetStatistics calls Resource->FinishCompilation in this engine version.
    stats = EDIT.get_statistics(material)
    pixels = stats.get_editor_property("num_pixel_shader_instructions")
    report[name] = {"graph_connected": True, "pixel_instructions": pixels,
                    "rendered_shader_verified": pixels > 0}
    unreal.EditorAssetLibrary.set_metadata_tag(material, "SkyfallEffectsVersion", BUILD_VERSION)
    unreal.EditorAssetLibrary.save_loaded_asset(material, only_if_is_dirty=False)

(ROOT / "Saved" / "explosion-material-repair.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
if not all(value["rendered_shader_verified"] for value in report.values()):
    raise RuntimeError("Explosion graph repaired but D3D shader verification missing; run this script with rendering enabled")
unreal.log("SKYFALL_EXPLOSION_MATERIALS_REPAIRED " + json.dumps(report))
