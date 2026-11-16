"""Exercise real compiled controller launch functions without changing the map."""
import json
import math
from pathlib import Path
import unreal

root = Path(__file__).resolve().parents[1]
report = {"passed": False}
try:
    unreal.EditorLoadingAndSavingUtils.new_blank_map(False)
    asset = unreal.load_asset('/Game/Skyfall/Blueprints/BP_SkyfallControllerV2')
    actor = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).spawn_actor_from_class(
        asset.generated_class(), unreal.Vector())
    actor.set_editor_property('WeaponMode', 1)
    velocity = actor.call_method('GetLaunchVelocity')
    assert abs(velocity.z + 4200*math.sin(math.radians(40))) < .01, 'Default meteor must descend at its 40-degree entry angle'
    samples = []
    for target_x, target_y, angle in ((0, 0, 68), (600, -300, 55), (2600, 0, 85)):
        actor.set_editor_property('MeteorTargetX', float(target_x))
        actor.set_editor_property('MeteorTargetY', float(target_y))
        actor.set_editor_property('MeteorAngle', float(angle))
        origin = actor.call_method('GetLaunchOrigin')
        velocity = actor.call_method('GetLaunchVelocity')
        landing = origin + velocity + unreal.Vector(0, 0, -800)
        expected = unreal.Vector(target_x, target_y, 2075)
        assert (landing-expected).length() < .1, (landing, expected)
        assert origin.z > 6000 and velocity.z < -3000
        samples.append({"target": str(expected), "origin": str(origin), "velocity": str(velocity)})
    actor.set_editor_property('WeaponMode', 0)
    actor.set_editor_property('YawDegrees', 0.0)
    actor.set_editor_property('PitchDegrees', 0.0)
    actor.set_editor_property('LaunchHeight', 900.0)
    actor.set_editor_property('LaunchSpeed', 10000.0)
    origin = actor.call_method('GetLaunchOrigin')
    velocity = actor.call_method('GetLaunchVelocity')
    assert (origin-unreal.Vector(0,-3500,900)).length() < .01
    assert (velocity-unreal.Vector(0,10000,0)).length() < .01
    report.update(passed=True, meteor_samples=samples, rocket_unchanged=True)
    unreal.log('SKYFALL_METEOR_LAUNCH_PASS')
except Exception as error:
    report['error'] = str(error)
    raise
finally:
    (root/'Saved/meteor-launch-check.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
