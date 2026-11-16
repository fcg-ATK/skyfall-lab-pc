"""Update projectile graphs and daylight-visible particle materials in place."""
import json
import sys
from pathlib import Path
import unreal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from combat_v2 import WEAPONS, _build_piece, _build_projectile
from effects_v2 import build_effects, _particle_material
from materials import MATERIAL_NAMES

materials = {key: unreal.load_asset("/Game/Skyfall/Materials/" + name)
             for key, name in MATERIAL_NAMES.items()}
effects = build_effects(materials)
for name, color, mode in (
    ("M_FineDust", (.61, .55, .46), "dust"),
    ("M_ExhaustSmoke", (.33, .35, .37), "dust"),
    ("M_RocketFlame", (18, 5.5, .65), "fire"),
    ("M_ImpactFlash", (24, 15, 5), "fire"),
):
    _particle_material(name, color, mode)
report = {}
fragment = unreal.load_asset("/Game/Skyfall/Blueprints/BP_SkyfallFragmentV2")
piece = _build_piece(fragment, effects)
report["piece"] = str(piece.get_editor_property("status"))
for spec in WEAPONS:
    path = ("/Engine/BasicShapes/Sphere" if spec["key"] == "ball" else
            "/Game/Skyfall/CombatV2/Meshes/Weapons/weapons_v2_" + spec["key"])
    blueprint = _build_projectile(spec, unreal.load_asset(path), effects, materials)
    report[spec["key"]] = str(blueprint.get_editor_property("status"))
unreal.EditorAssetLibrary.save_directory("/Game/Skyfall", only_if_is_dirty=True)
(ROOT / "Saved/combat-v2-repair.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
unreal.log("SKYFALL_COMBAT_REPAIRED " + json.dumps(report))
