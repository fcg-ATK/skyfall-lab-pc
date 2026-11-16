"""Install the production guide and record actual imported mesh dimensions."""
import json
import sys
from pathlib import Path
import unreal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from aim_v2 import install_aim, bind_placed_guides

report = {}
for name in ("aim_arrow", "rocket", "shell"):
    path = "/Game/Skyfall/CombatV2/Meshes/Weapons/weapons_v2_" + name
    mesh = unreal.load_asset(path)
    if mesh is None:
        raise RuntimeError("Missing mesh " + path)
    bounds = mesh.get_bounds()
    report[name] = {"extent": str(bounds.box_extent), "origin": str(bounds.origin)}
blueprint = unreal.load_asset("/Game/Skyfall/Blueprints/BP_SkyfallControllerV2")
mesh = unreal.load_asset("/Game/Skyfall/CombatV2/Meshes/Weapons/weapons_v2_aim_arrow")
install_aim(blueprint, mesh)
levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
levels.load_level("/Game/Skyfall/Maps/DemolitionYard")
bind_placed_guides(blueprint, mesh)
if not levels.save_current_level():
    raise RuntimeError("Could not save production aiming component overrides")
unreal.EditorLoadingAndSavingUtils.new_blank_map(False)
actor = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).spawn_actor_from_class(
    blueprint.generated_class(), unreal.Vector())
actor.call_method("UpdateAim")
arrow = actor.get_editor_property("AimGuideArrow")
report["arrow"] = {"mesh": arrow.static_mesh.get_path_name(),
                   "transform": str(arrow.get_world_transform()),
                   "bounds": str(arrow.get_local_bounds())}
report["blueprint_status"] = str(blueprint.get_editor_property("status"))
(ROOT / "Saved/aim-v2-production.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
unreal.log("SKYFALL_AIM_REPAIRED " + json.dumps(report))
