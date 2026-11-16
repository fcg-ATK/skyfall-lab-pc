import json
import sys
import traceback
from pathlib import Path
import unreal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
report = {"ok": False}
try:
    from aim_v2 import install_aim
    blueprint = unreal.load_asset("/Game/Skyfall/Blueprints/BP_SkyfallControllerV2")
    install_aim(blueprint, unreal.load_asset("/Engine/BasicShapes/Cone"))
    unreal.EditorLoadingAndSavingUtils.new_blank_map(False)
    actor = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).spawn_actor_from_class(
        blueprint.generated_class(), unreal.Vector())
    actor.call_method("UpdateAim")
    component = actor.get_editor_property("AimGuideDots")
    report["dot_count"] = component.get_instance_count()
    assert report["dot_count"] == 26
    initial = component.get_instance_transform(10, True)
    assert initial is not None and initial.translation.y > -1000, str(initial)
    actor.set_editor_property("YawDegrees", 27.0)
    actor.set_editor_property("PitchDegrees", 15.0)
    actor.call_method("UpdateAim")
    component = actor.get_editor_property("AimGuideDots")
    changed = component.get_instance_transform(10, True)
    report["initial_dot"] = str(initial)
    report["changed_dot"] = str(changed)
    assert changed is not None and abs(changed.translation.x) > 100, str(changed)
    report["blueprint_status"] = str(blueprint.get_editor_property("status"))
    report["ok"] = True
except Exception:
    report["error"] = traceback.format_exc()
    raise
finally:
    (ROOT / "Saved/aim-v2-smoke.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
