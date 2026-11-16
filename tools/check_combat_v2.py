"""Compile combat assets in a throwaway editor map and report their contracts."""

import json
import sys
import traceback
from pathlib import Path

import unreal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
report = {}
try:
    from combat_v2 import build_controller

    unreal.EditorLoadingAndSavingUtils.new_blank_map(False)
    cube = unreal.load_asset("/Engine/BasicShapes/Cube")
    result = build_controller(
        [{"name": "piece_0", "location": (0, 0, 900), "scale": (3, 1, 3), "mesh": cube,
          "kind": "glass", "mass_kg": 22},
         {"name": "piece_1", "location": (0, 600, 900), "scale": (3, 1, 3), "mesh": cube,
          "kind": "structure", "mass_kg": 300},
         {"name": "window_2", "location": (0, 0, 1800), "scale": (3, 0.1, 3), "mesh": cube,
          "kind": "glass", "mass_kg": 22}],
        {}, {"rocket": cube, "shell": cube, "ball": cube},
        {name: {"meshes": [cube, cube], "offsets": [(-0.25, 0, 0), (0.25, 0, 0)]}
         for name in ("piece_0", "piece_1", "window_2")},
        {"dust_class": unreal.StaticMeshActor.static_class(), "explosion_class": unreal.StaticMeshActor.static_class(),
         "launch_class": unreal.StaticMeshActor.static_class()},
    )
    report = {
        "ok": True,
        "pieces": len(result["pieces"]),
        "blueprints": {key: {"path": value.get_path_name(), "status": str(value.get_editor_property("status"))}
                       for key, value in result.items() if key.endswith("_blueprint")},
        "graphs": [graph.get_name() for graph in unreal.BlueprintEditorLibrary.list_graphs(result["controller_blueprint"])],
        "fracture_mesh_count": len(result["pieces"][0].get_editor_property("FractureMeshes")),
    }
    actor = result["controller"]
    origin = actor.call_method("GetLaunchOrigin")
    velocity = actor.call_method("GetLaunchVelocity")
    report["default_origin"] = origin.to_tuple()
    report["default_velocity"] = velocity.to_tuple()
    assert abs(origin.y + 3500) < 0.01 and abs(origin.z - 900) < 0.01
    assert abs(velocity.y - 10000) < 0.01 and abs(velocity.x) < 0.01
    actor.set_editor_property("YawDegrees", 30.0)
    actor.set_editor_property("PitchDegrees", 15.0)
    adjusted = actor.call_method("GetLaunchVelocity")
    report["adjusted_velocity"] = adjusted.to_tuple()
    assert adjusted.x < -4000 and adjusted.z > 2500
    assert abs(adjusted.length() - 10000) < 0.01
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

    def shoot(height):
        projectile = subsystem.spawn_actor_from_class(
            result["rocket_blueprint"].generated_class(), unreal.Vector(0, -3500, height))
        projectile.set_editor_property("ShotVelocity", unreal.Vector(0, 10000, 0))
        projectile.call_method("ReceiveBeginPlay")
        projectile.call_method("ReceiveTick", (0.5,))
        return projectile

    miss = shoot(3500)
    report["miss_hit_count"] = len(miss.get_editor_property("HitActors"))
    assert report["miss_hit_count"] == 0
    assert not any(target.get_editor_property("HasFractured") for target in result["pieces"])
    glass_only = shoot(1800)
    report["glass_only_hits"] = len(glass_only.get_editor_property("HitActors"))
    report["glass_started_cascade"] = glass_only.get_editor_property("CascadeStarted")
    report["structure_scheduled_by_glass"] = result["pieces"][1].get_editor_property("CollapseScheduled")
    assert report["glass_only_hits"] == 1
    assert not report["glass_started_cascade"]
    assert not report["structure_scheduled_by_glass"]
    hit = shoot(900)
    report["penetration_hit_count"] = len(hit.get_editor_property("HitActors"))
    report["fractured_targets"] = sum(bool(target.get_editor_property("HasFractured")) for target in result["pieces"])
    report["fragment_actor_count"] = sum(item.actor_has_tag("SkyfallFragment") for item in subsystem.get_all_level_actors())
    assert report["penetration_hit_count"] == 2
    assert report["fractured_targets"] == 3
    assert report["fragment_actor_count"] == 6
    hit.call_method("ReceiveTick", (0.1,))
    result["pieces"][0].call_method("Fracture")
    report["fragment_count_after_repeat"] = sum(item.actor_has_tag("SkyfallFragment") for item in subsystem.get_all_level_actors())
    assert report["fragment_count_after_repeat"] == 6
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    report["slow_motion_started"] = unreal.GameplayStatics.get_global_time_dilation(world)
    assert abs(report["slow_motion_started"] - 0.22) < 0.01
    actor.set_editor_property("SlowMoUntil", -1.0)
    actor.call_method("ReceiveTick", (0.016,))
    report["slow_motion_restored"] = unreal.GameplayStatics.get_global_time_dilation(world)
    assert abs(report["slow_motion_restored"] - 1.0) < 0.01
    ball = subsystem.spawn_actor_from_class(result["ball_blueprint"].generated_class(), unreal.Vector(0, -3500, 3500))
    ball.call_method("ReceiveBeginPlay")
    ball.call_method("ReceiveTick", (0.1,))
    report["ball_position_after_100ms"] = ball.get_actor_location().to_tuple()
    assert abs(ball.get_actor_location().z - 3495.1) < 0.01
    assert abs(ball.get_editor_property("ShotVelocity").z + 98) < 0.01
    unreal.log("SKYFALL_COMBAT_V2_SMOKE_OK")
except Exception:
    report.update({"ok": False, "error": traceback.format_exc()})
    unreal.log_error(report["error"])
finally:
    (ROOT / "Saved" / "combat-v2-smoke.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
