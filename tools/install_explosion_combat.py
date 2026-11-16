"""Bind first-contact explosion strength and impact feedback to weapons."""
import json
import sys
from pathlib import Path
import unreal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from combat_v2 import WEAPONS, _build_projectile
from effects_v2 import build_effects
from materials import MATERIAL_NAMES

materials = {role: unreal.load_asset("/Game/Skyfall/Materials/" + name)
             for role, name in MATERIAL_NAMES.items()}
effects = build_effects(materials)
from meteor_art import build_meteor_art
meteor = build_meteor_art()
effects.update({key: value for key, value in meteor.items() if key.endswith("_class")})
report = {}
for spec in WEAPONS:
    path = ("/Engine/BasicShapes/Sphere" if spec["key"] == "ball" else
            "/Game/Skyfall/CombatV2/Meshes/Weapons/weapons_v2_" + spec["key"])
    mesh = meteor["mesh"] if spec["key"] == "meteor" else unreal.load_asset(path)
    blueprint = _build_projectile(spec, mesh, effects, materials)
    report[spec["key"]] = str(blueprint.get_editor_property("status"))
(ROOT / "Saved/explosion-combat-build.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
unreal.log("SKYFALL_EXPLOSION_COMBAT_READY " + json.dumps(report))
