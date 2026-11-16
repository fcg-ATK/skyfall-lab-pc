"""Read-only native PIE verification of meteor gameplay and visual phases.

Run in the full editor with -ExecCmds="py <absolute path to this file>".
No assets or maps are saved. Each round receives a fresh PIE world. Screenshots
use game time, since HighResShot can stall wall-clock time. PNG completion is
verified, but the parent must inspect its HUD/camera to confirm the viewport.
"""
import json
import math
import statistics
import struct
import time
import traceback
from pathlib import Path

import unreal

PROJECT = Path(__file__).resolve().parents[1]
WORKSPACE = PROJECT / "Saved"
REPORT_PATH = PROJECT / "Saved" / "runtime-meteor.json"
IMAGE_DIR = WORKSPACE / "QA" / "skyfall-meteor"
BLUEPRINTS = "/Game/Skyfall/Blueprints/"
EFFECTS = "/Game/Skyfall/CombatV2/Effects/"
METEOR = "/Game/Skyfall/Meteor/"
ROUNDS = (
    {"name": "meteor_miss", "mode": 1, "target_x": 2600, "target_y": 0, "miss": True},
    {"name": "meteor_centre", "mode": 1, "target_x": 0, "target_y": 0, "capture": True},
    {"name": "meteor_edge", "mode": 1, "target_x": 600, "target_y": 0},
    {"name": "rocket_regression", "mode": 0},
)
AFTER_HIT_CAPTURES = ((.10, "impact"), (.35, "fragments"), (1.6, "collapse"),
                      (4.0, "lower_floors"), (8.0, "ruins"))


def alive(actor):
    return bool(actor and unreal.SystemLibrary.is_valid(actor))


def xyz(vector):
    return [float(vector.x), float(vector.y), float(vector.z)]


def distance(first, second):
    return math.sqrt(sum((a-b)**2 for a, b in zip(first, second)))


class MeteorChecks:
    def __init__(self):
        self.levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        self.editor = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        self.started = time.perf_counter()
        self.stage_started = self.started
        self.stage = "warmup"
        self.handle = None
        self.world = None
        self.classes = {}
        self.round_index = -1
        self.current = None
        self.projectile = None
        self.pieces = []
        self.floor_members = {}
        self.activation_times = {}
        self.shot_at = None
        self.shot_game = None
        self.first_hit_at = None
        self.first_hit_game = None
        self.pending_capture = None
        self.capture_index = 0
        self.incoming_requested = False
        self.last_sample_game = -1
        self.last_write = 0
        self.next_poll = 0
        self.outcome = None
        self.report = {
            "status": "running", "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "engine": unreal.SystemLibrary.get_engine_version(),
            "map": self.editor.get_editor_world().get_path_name(),
            "checks": {}, "rounds": [], "screenshots": {}, "failures": [],
            "timing": {"warmup_real_seconds": 12, "overall_limit_real_seconds": 180,
                       "round_limit_real_seconds": 45, "centre_end_game_seconds_after_hit": 8.2,
                       "phase_capture_game_offsets": [item[0] for item in AFTER_HIT_CAPTURES]},
            "camera_policy": "Use natural PIE view target; request native UMG viewport focus",
            "capture_viewport_verified": False,
            "capture_note": "Fresh PNG proves capture completion only. Inspect HUD and damage to confirm gameplay viewport.",
            "control_test_scope": "Calls native FireWeapon and launch functions after assigning live PIE state; keyboard switching is a separate manual test.",
        }

    def start(self):
        IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        if self.levels.is_in_play_in_editor():
            self.levels.editor_request_end_play()
            self.stage = "stopping_existing_pie"
        self.write()
        self.handle = unreal.register_slate_post_tick_callback(self.tick)
        unreal.log("SKYFALL_METEOR_STARTED")

    def stop_callback(self):
        if self.handle is not None:
            unreal.unregister_slate_post_tick_callback(self.handle)
            self.handle = None

    def write(self):
        self.report.update(stage=self.stage, elapsed_real_seconds=round(time.perf_counter()-self.started, 3))
        temporary = REPORT_PATH.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(REPORT_PATH)
        self.last_write = time.perf_counter()

    def set_stage(self, stage):
        self.stage = stage
        self.stage_started = time.perf_counter()
        self.write()
        unreal.log("SKYFALL_METEOR_STAGE " + stage)

    def check(self, name, passed, **details):
        key = (self.current["name"] + "/" if self.current else "") + name
        self.report["checks"][key] = dict(passed=bool(passed), **details)
        if not passed and key not in self.report["failures"]:
            self.report["failures"].append(key)
            unreal.log_warning("SKYFALL_METEOR_CHECK_FAILED " + key + " " + str(details))

    def load_classes(self):
        paths = {key: BLUEPRINTS+name for key, name in {
            "controller": "BP_SkyfallControllerV2", "piece": "BP_SkyfallPieceV2",
            "fragment": "BP_SkyfallFragmentV2", "meteor": "BP_SkyfallMeteorV2",
            "rocket": "BP_SkyfallRocketV2", "ball": "BP_SkyfallBallV2"}.items()}
        paths.update(powder=EFFECTS+"BP_LocalPowder", rocket_impact=EFFECTS+"BP_HighEnergyImpact",
                     meteor_impact=METEOR+"BP_MeteorImpact", meteor_tail=METEOR+"BP_MeteorTail")
        for key, path in paths.items():
            asset = unreal.load_asset(path)
            if not asset or not asset.generated_class():
                raise RuntimeError("Missing native runtime class " + path)
            self.classes[key] = asset.generated_class()

    def game_world(self):
        world = self.editor.get_game_world()
        if world:
            return world
        worlds = unreal.EditorLevelLibrary.get_pie_worlds(False)
        return worlds[0] if worlds else None

    def actors(self, key):
        return [actor for actor in unreal.GameplayStatics.get_all_actors_of_class(self.world, self.classes[key])
                if alive(actor)]

    def projectiles(self):
        return [actor for key in ("rocket", "meteor", "ball") for actor in self.actors(key)]

    def controller(self):
        actors = self.actors("controller")
        if len(actors) != 1:
            raise RuntimeError("Expected one native aim controller, got " + str(len(actors)))
        return actors[0]

    def focus(self):
        try:
            library = getattr(unreal, "WidgetBlueprintLibrary", None)
            if library:
                library.set_focus_to_game_viewport()
                return {"requested": True, "route": "Python WidgetBlueprintLibrary"}
            # This UE installation does not expose a Python wrapper for UMG's
            # function library, even though native UMG and its UFunctions exist.
            native_class = unreal.load_class(None, "/Script/UMG.WidgetBlueprintLibrary")
            if not native_class:
                raise RuntimeError("Native UMG WidgetBlueprintLibrary is unavailable")
            unreal.get_default_object(native_class).call_method("SetFocusToGameViewport")
            return {"requested": True, "route": "Native UMG reflected function"}
        except Exception as error:
            return {"requested": False, "error": str(error)}

    def counts(self):
        pieces = [piece for piece in self.pieces if alive(piece)]
        return {
            "pieces": len(pieces),
            "activated": sum(bool(piece.get_editor_property("HasActivated")) for piece in pieces),
            "fractured": sum(bool(piece.get_editor_property("HasFractured")) for piece in pieces),
            "scheduled": sum(bool(piece.get_editor_property("CollapseScheduled")) for piece in pieces),
            "fragments": len(self.actors("fragment")), "powder": len(self.actors("powder")),
            "meteor_impact": len(self.actors("meteor_impact")),
            "rocket_impact": len(self.actors("rocket_impact")),
            "meteor_tail": len(self.actors("meteor_tail")), "projectiles": len(self.projectiles()),
            "time_dilation": float(unreal.GameplayStatics.get_global_time_dilation(self.world)),
            "game_seconds": float(unreal.GameplayStatics.get_time_seconds(self.world)),
            "world_undilated_seconds": float(unreal.GameplayStatics.get_real_time_seconds(self.world)),
        }

    def hit_age(self):
        if self.first_hit_game is None:
            return None
        return float(unreal.GameplayStatics.get_time_seconds(self.world))-self.first_hit_game

    def effect_diagnostics(self):
        result = []
        for key in ("meteor_tail", "meteor_impact", "rocket_impact"):
            for actor in self.actors(key):
                component = actor.get_editor_property("niagara_component")
                observation = {"kind": key, "actor": actor.get_path_name(),
                               "location_cm": xyz(actor.get_actor_location()),
                               "age_game_seconds": float(actor.get_game_time_since_creation()),
                               "active": bool(component.is_active()), "visible": bool(component.is_visible())}
                try:
                    observation["strength"] = component.get_variable_float("User.Strength")
                    cache = unreal.NiagaraSimCacheFunctionLibrary.create_niagara_sim_cache(self.world)
                    captured = unreal.NiagaraSimCacheFunctionLibrary.capture_niagara_sim_cache_immediate(
                        cache, unreal.NiagaraSimCacheCreateParameters(), component, False)
                    success = bool(captured[0]) if isinstance(captured, tuple) else bool(captured)
                    if isinstance(captured, tuple) and len(captured) > 1 and captured[1]:
                        cache = captured[1]
                    observation["particle_capture_success"] = success
                    if success and cache.get_num_frames() > 0:
                        observation["emitter_particles"] = {str(emitter): len(cache.read_position_attribute(
                            "Position", emitter, True, 0)) for emitter in cache.get_emitter_names()}
                except Exception as error:
                    observation["particle_capture_error"] = str(error)
                result.append(observation)
        return result

    def request_capture(self, name, target_game_age):
        if self.pending_capture is not None:
            raise RuntimeError("Previous native screenshot is still in flight")
        path = IMAGE_DIR / (name + ".png")
        player = unreal.GameplayStatics.get_player_controller(self.world, 0)
        if not alive(player):
            raise RuntimeError("PIE player unavailable for capture")
        target = player.get_view_target()
        camera = {"view_target": target.get_path_name() if alive(target) else None,
                  "tags": list(map(str, target.tags)) if alive(target) else [],
                  "viewport_pixels": list(player.get_viewport_size())}
        focus = self.focus()
        diagnostics = self.effect_diagnostics()
        now = time.perf_counter()
        self.pending_capture = dict(name=name, path=path, requested=time.time(), real_at=now,
                                    previous_mtime_ns=path.stat().st_mtime_ns if path.exists() else None)
        self.report["screenshots"][name] = {
            "path": str(path), "status": "pending", "target_game_age": target_game_age,
            "actual_game_age_after_shot": float(unreal.GameplayStatics.get_time_seconds(self.world))-self.shot_game,
            "actual_game_age_after_hit": self.hit_age(), "actual_wall_seconds_after_shot": now-self.shot_at,
            "camera": camera, "viewport_focus": focus, "effects": diagnostics,
            "capture_viewport_verified": False,
            "capture_route": "HighResShot requested through PIE player controller",
        }
        command = 'HighResShot 1920x1080 filename="' + path.as_posix() + '"'
        unreal.SystemLibrary.execute_console_command(self.world, command, player)
        self.write()

    def poll_capture(self, now):
        capture = self.pending_capture
        if capture is None:
            return
        name, path = capture["name"], capture["path"]
        fresh = (path.exists() and path.stat().st_mtime >= capture["requested"]-1
                 and path.stat().st_mtime_ns != capture["previous_mtime_ns"])
        if fresh and path.stat().st_size > 36:
            try:
                with path.open("rb") as file:
                    header = file.read(24)
                    file.seek(-12, 2)
                    tail = file.read(12)
                complete = header[:8] == b"\x89PNG\r\n\x1a\n" and tail[4:8] == b"IEND"
            except OSError:
                complete = False
            if complete:
                size = struct.unpack(">II", header[16:24])
                self.check("capture_"+name, size == (1920, 1080), dimensions=list(size))
                self.report["screenshots"][name].update(status="captured", bytes=path.stat().st_size,
                    dimensions=list(size), completed_game_age_after_hit=self.hit_age())
                self.pending_capture = None
                self.write()
                return
        if now-capture["real_at"] > 10:
            self.report["screenshots"][name]["status"] = "timeout"
            self.check("capture_"+name, False, error="No complete fresh PNG within 10 wall seconds")
            self.pending_capture = None
            raise TimeoutError("Unconfirmed screenshot " + name)

    def begin_round(self):
        self.round_index += 1
        if self.round_index >= len(ROUNDS):
            self.finish()
            return
        self.current = dict(ROUNDS[self.round_index])
        self.current.update(samples=[], flight_samples=[], impact_paths=[], activation_by_floor={},
            max_fractured=0, max_activated=0, max_scheduled=0, max_fragments=0, max_powder=0,
            max_meteor_impact=0, max_rocket_impact=0, max_meteor_tail=0, max_hits=0,
            slow_seen=False, normal_restored=False, duplicate_hits=False, stopped_samples=[])
        self.report["rounds"].append(self.current)
        self.projectile = None
        self.pieces = []
        self.floor_members = {}
        self.activation_times = {}
        self.shot_at = self.shot_game = self.first_hit_at = self.first_hit_game = None
        self.capture_index = 0
        self.incoming_requested = False
        self.last_sample_game = -1
        self.world = None
        self.levels.editor_request_begin_play()
        self.set_stage("waiting_for_pie")

    def aim_checks(self, controller, expected_origin, expected_velocity):
        controller.call_method("UpdateAim")
        origin = xyz(controller.call_method("GetLaunchOrigin"))
        velocity = xyz(controller.call_method("GetLaunchVelocity"))
        self.check("launch_origin_matches_design", distance(origin, expected_origin) < 2,
                   actual_cm=origin, expected_cm=expected_origin)
        self.check("launch_velocity_matches_design", distance(velocity, expected_velocity) < 2,
                   actual=velocity, expected=expected_velocity)
        components = controller.get_components_by_class(unreal.SceneComponent)
        dots = next((item for item in components if item.get_name().startswith("AimGuideDots")), None)
        arrow = next((item for item in components if item.get_name().startswith("AimGuideArrow")), None)
        self.check("guide_dots_present", alive(dots) and dots.get_instance_count() == 26,
                   count=dots.get_instance_count() if alive(dots) else 0)
        if alive(arrow):
            forward = xyz(arrow.get_forward_vector())
            arrow_velocity = list(velocity)
            # aim_v2 places the meteor arrow at t=.86 on the arc and aligns it
            # with that point's instantaneous velocity, not launch direction.
            if self.current["mode"] == 1:
                arrow_velocity[2] += float(controller.get_editor_property("GravityZ"))*.86
            length = math.sqrt(sum(value*value for value in arrow_velocity))
            alignment = sum(a*b for a, b in zip(forward, arrow_velocity))/max(1, length)
            self.check("arrow_points_along_flight", alignment > .99, alignment_dot=alignment,
                       expected_direction_velocity=arrow_velocity)
        else:
            self.check("arrow_points_along_flight", False, error="Arrow component missing")
        if self.current["mode"] == 1 and alive(dots) and dots.get_instance_count():
            transform = dots.get_instance_transform(25, world_space=True)
            if isinstance(transform, (tuple, list)):
                transform = next((value for value in transform if isinstance(value, unreal.Transform)), None)
            if not isinstance(transform, unreal.Transform):
                self.check("last_dot_matches_target_plane", False, error=str(transform))
            else:
                target = [float(self.current["target_x"]), float(self.current["target_y"]), 2075.0]
                actual = xyz(transform.translation)
                self.check("last_dot_matches_target_plane", distance(actual, target) < 4,
                           actual_cm=actual, target_cm=target)

    def fire(self):
        controller = self.controller()
        meteor = self.current["mode"] == 1
        values = {"WeaponMode": self.current["mode"], "YawDegrees": 0.0, "PitchDegrees": 0.0,
                  "LaunchHeight": 900.0, "LaunchSpeed": 4200.0 if meteor else 10000.0,
                  "GravityZ": -1600.0 if meteor else 0.0, "ProjectileRadius": 170.0 if meteor else 55.0,
                  "MeteorTargetX": float(self.current.get("target_x", 0)),
                  "MeteorTargetY": float(self.current.get("target_y", 0)), "MeteorAngle": 68.0}
        for key, value in values.items():
            controller.set_editor_property(key, value)
        if meteor:
            pitch, yaw = math.radians(-68), math.radians(60)
            velocity = [4200*math.cos(pitch)*math.cos(yaw), 4200*math.cos(pitch)*math.sin(yaw),
                        4200*math.sin(pitch)]
            origin = [values["MeteorTargetX"]-velocity[0], values["MeteorTargetY"]-velocity[1],
                      2075-velocity[2]+800]
        else:
            origin, velocity = [0, -3500, 900], [0, 10000, 0]
        self.aim_checks(controller, origin, velocity)
        before = {actor.get_path_name() for actor in self.projectiles()}
        controller.call_method("FireWeapon")
        spawned = [actor for actor in self.projectiles() if actor.get_path_name() not in before]
        self.check("one_projectile_from_native_fire", len(spawned) == 1, parameters=values, count=len(spawned))
        if len(spawned) != 1:
            raise RuntimeError("Native FireWeapon did not spawn one selected projectile")
        self.projectile = spawned[0]
        expected_class = self.classes["meteor" if meteor else "rocket"]
        self.check("selected_weapon_class", self.projectile.get_class() == expected_class,
                   actual=self.projectile.get_class().get_path_name(), expected=expected_class.get_path_name())
        self.current.update(projectile_path=self.projectile.get_path_name(),
            initial_location_cm=xyz(self.projectile.get_actor_location()),
            initial_velocity=xyz(self.projectile.get_editor_property("ShotVelocity")), gravity=values["GravityZ"])
        self.check("spawn_matches_preview", distance(self.current["initial_location_cm"], origin) < 2
                   and distance(self.current["initial_velocity"], velocity) < 2,
                   spawned_origin=self.current["initial_location_cm"], preview_origin=origin,
                   spawned_velocity=self.current["initial_velocity"], preview_velocity=velocity)
        self.shot_at = time.perf_counter()
        self.shot_game = float(unreal.GameplayStatics.get_time_seconds(self.world))
        self.set_stage("observing_shot")

    def sample(self, now):
        counts = self.counts()
        current = self.current
        for key in ("fractured", "activated", "scheduled", "fragments", "powder",
                    "meteor_impact", "rocket_impact", "meteor_tail"):
            current["max_"+key] = max(current["max_"+key], counts[key])
        impact_key = "meteor_impact" if current["mode"] == 1 else "rocket_impact"
        current["impact_paths"] = list(dict.fromkeys(current["impact_paths"] +
                                                     [actor.get_path_name() for actor in self.actors(impact_key)]))
        if abs(counts["time_dilation"]-.22) < .015:
            current["slow_seen"] = True
            current.setdefault("slow_start_undilated", counts["world_undilated_seconds"])
            current.setdefault("slow_start_wall", now)
        if current["slow_seen"] and abs(counts["time_dilation"]-1) < .01:
            current["normal_restored"] = True
            current.setdefault("slow_duration_undilated", counts["world_undilated_seconds"]-current["slow_start_undilated"])
            current.setdefault("slow_duration_wall", now-current["slow_start_wall"])
        did_hit = False
        if alive(self.projectile):
            did_hit = bool(self.projectile.get_editor_property("DidHit"))
            hits = list(self.projectile.get_editor_property("HitActors"))
            hit_paths = [actor.get_path_name() if alive(actor) else str(actor) for actor in hits]
            current["max_hits"] = max(current["max_hits"], len(hits))
            current["duplicate_hits"] |= len(set(hit_paths)) != len(hit_paths)
            if did_hit:
                current.setdefault("impact_location_cm", xyz(self.projectile.get_editor_property("ImpactLocation")))
                if current["mode"] == 1:
                    observation = {"position_cm": xyz(self.projectile.get_actor_location()),
                                   "game_seconds": counts["game_seconds"], "hit_count": len(hits)}
                    try:
                        observation["hidden"] = bool(self.projectile.get_editor_property("hidden"))
                        observation["tick_enabled"] = bool(self.projectile.is_actor_tick_enabled())
                    except Exception as error:
                        observation["measurement_error"] = str(error)
                    current["stopped_samples"].append(observation)
            elif counts["game_seconds"]-self.last_sample_game >= .04:
                age = float(self.projectile.get_game_time_since_creation())
                initial, velocity, gravity = current["initial_location_cm"], current["initial_velocity"], current["gravity"]
                expected = [initial[index]+velocity[index]*age+(.5*gravity*age*age if index == 2 else 0)
                            for index in range(3)]
                actual = xyz(self.projectile.get_actor_location())
                actual_velocity = xyz(self.projectile.get_editor_property("ShotVelocity"))
                expected_velocity = [velocity[index]+(gravity*age if index == 2 else 0)
                                     for index in range(3)]
                previous = current["flight_samples"][-1] if current["flight_samples"] else {
                    "age_game_seconds": 0, "actual_cm": initial, "actual_velocity": velocity}
                elapsed = age-previous["age_game_seconds"]
                interval_expected = [previous["actual_cm"][index]+.5*(
                    previous["actual_velocity"][index]+actual_velocity[index])*elapsed for index in range(3)]
                current["flight_samples"].append({
                    "age_game_seconds": age, "actual_cm": actual, "expected_cm": expected,
                    "error_cm": distance(actual, expected), "actual_velocity": actual_velocity,
                    "expected_velocity": expected_velocity,
                    "velocity_error_cm_per_second": distance(actual_velocity, expected_velocity),
                    "interval_game_seconds": elapsed, "interval_expected_cm": interval_expected,
                    "interval_error_cm": distance(actual, interval_expected)})
        if self.first_hit_at is None and (did_hit or current["max_hits"] or counts[impact_key]):
            self.first_hit_at, self.first_hit_game = now, counts["game_seconds"]
            current["first_hit"] = dict(counts, game_seconds_after_shot=counts["game_seconds"]-self.shot_game)
        for floor, members in self.floor_members.items():
            for actor in members:
                path = actor.get_path_name()
                if path not in self.activation_times and (actor.get_editor_property("HasActivated") or
                                                          actor.get_editor_property("HasFractured")):
                    self.activation_times[path] = counts["game_seconds"]-self.shot_game
        if counts["game_seconds"]-self.last_sample_game >= .08:
            current["samples"].append(dict(counts, game_seconds_after_shot=counts["game_seconds"]-self.shot_game,
                                           wall_seconds_after_shot=now-self.shot_at))
            self.last_sample_game = counts["game_seconds"]
        return counts

    def floor_observations(self):
        result = {}
        for floor, members in self.floor_members.items():
            values = [self.activation_times[actor.get_path_name()] for actor in members
                      if actor.get_path_name() in self.activation_times]
            result[floor] = {"total_members": len(members), "activated_members": len(values)}
            if values:
                result[floor].update(first_game_seconds=min(values), median_game_seconds=statistics.median(values),
                                     last_game_seconds=max(values))
        return result

    def complete_round(self, counts):
        current = self.current
        current["final_counts"] = counts
        current["activation_by_floor"] = self.floor_observations()
        samples = current["flight_samples"]
        maximum = max((sample["error_cm"] for sample in samples), default=None)
        self.check("pre_hit_ballistic_motion", len(samples) >= 2 and maximum is not None and maximum < 40,
                   sample_count=len(samples), max_error_cm=maximum, tolerance_cm=40)
        velocity_error = max((sample["velocity_error_cm_per_second"] for sample in samples), default=None)
        interval_error = max((sample["interval_error_cm"] for sample in samples), default=None)
        current["flight_diagnostics"] = {"max_velocity_error_cm_per_second": velocity_error,
                                         "max_trapezoidal_interval_error_cm": interval_error}
        self.check("hit_deduplication", not current["duplicate_hits"])
        if current.get("miss"):
            self.check("miss_zero_building_damage", all(current["max_"+key] == 0 for key in
                ("hits", "activated", "fractured", "scheduled", "fragments")),
                hits=current["max_hits"], fractured=current["max_fractured"], scheduled=current["max_scheduled"])
            self.check("miss_zero_building_effects", current["max_meteor_impact"] == 0
                       and current["max_rocket_impact"] == 0 and current["max_powder"] == 0)
            self.check("miss_normal_speed", not current["slow_seen"] and abs(counts["time_dilation"]-1) < .01)
        else:
            self.check("real_contact_causes_fracture", self.first_hit_at is not None and current["max_hits"] > 0
                       and current["max_fractured"] > 0, hits=current["max_hits"], fractured=current["max_fractured"])
            self.check("one_main_impact", len(current["impact_paths"]) == 1, paths=current["impact_paths"])
            self.check("slow_motion_restored", current["slow_seen"] and current["normal_restored"]
                       and abs(counts["time_dilation"]-1) < .01)
            if not current.get("capture"):
                duration = current.get("slow_duration_undilated")
                self.check("slow_duration_without_capture", duration is not None and .85 <= duration <= 1.65,
                           world_undilated_seconds=duration, wall_seconds=current.get("slow_duration_wall"))
            if current["mode"] == 1:
                self.check("meteor_effect_distinct_from_rocket", current["max_meteor_impact"] == 1
                           and current["max_rocket_impact"] == 0)
                impact = current.get("impact_location_cm")
                self.check("meteor_contacts_upper_building", impact is not None and impact[2] > 1900,
                           impact_cm=impact)
                stopped = current["stopped_samples"]
                drift = max((distance(sample["position_cm"], stopped[0]["position_cm"])
                             for sample in stopped), default=None)
                counts_after = [sample["hit_count"] for sample in stopped]
                self.check("meteor_body_stops_after_first_hit", len(stopped) >= 1 and drift is not None and drift < 5,
                           sample_count=len(stopped), max_drift_cm=drift)
                self.check("meteor_no_continued_direct_damage", bool(counts_after) and
                           max(counts_after) == min(counts_after), hit_counts=sorted(set(counts_after)))
                self.check("meteor_body_hidden_after_hit", bool(stopped) and
                           all(sample.get("hidden", False) for sample in stopped),
                           observations=stopped[:2])
                self.check("meteor_tail_cleared", current["max_meteor_tail"] >= 1 and counts["meteor_tail"] == 0)
                if current["name"] == "meteor_centre":
                    floors = sorted((int(key.split("_")[-1]), value) for key, value in
                                    current["activation_by_floor"].items() if value["activated_members"] >= 3)
                    ordered = len(floors) >= 3 and all(
                        high["median_game_seconds"] <= low["median_game_seconds"]+.35
                        for (_, low), (_, high) in zip(floors, floors[1:]))
                    span = floors[0][1]["median_game_seconds"]-floors[-1][1]["median_game_seconds"] if floors else 0
                    self.check("floor_collapse_progresses_downwards", ordered and span > .5,
                               active_floor_count=len(floors), bottom_minus_top_game_seconds=span,
                               floor_observations=current["activation_by_floor"])
                    self.check("all_six_phase_captures", self.incoming_requested and
                               self.capture_index == len(AFTER_HIT_CAPTURES) and self.pending_capture is None)
            else:
                self.check("rocket_effect_preserved", current["max_rocket_impact"] == 1
                           and current["max_meteor_impact"] == 0)
        self.levels.editor_request_end_play()
        self.set_stage("ending_round")

    def finish(self, error=None):
        if error:
            self.report["error"] = error
            unreal.log_error("SKYFALL_METEOR_ERROR " + error)
        self.outcome = "failed" if error or self.report["failures"] else "passed"
        if self.pending_capture:
            self.report["screenshots"][self.pending_capture["name"]]["status"] = "aborted"
            self.pending_capture = None
        if self.levels.is_in_play_in_editor():
            self.levels.editor_request_end_play()
        self.set_stage("finishing")

    def tick(self, delta_seconds):
        now = time.perf_counter()
        if now < self.next_poll:
            return
        self.next_poll = now+.025
        try:
            self.poll_capture(now)
            self.advance(now)
            if now-self.last_write >= 1:
                self.write()
        except Exception:
            if self.stage == "finishing":
                self.report.update(status="failed", cleanup_error=traceback.format_exc())
                self.write()
                self.stop_callback()
            else:
                self.finish(traceback.format_exc())

    def advance(self, now):
        if now-self.started >= 175 and self.stage != "finishing":
            self.finish("180 second total budget reached; reserving cleanup time")
            return
        if self.stage == "stopping_existing_pie":
            if not self.levels.is_in_play_in_editor() and not self.game_world():
                self.set_stage("warmup")
        elif self.stage == "warmup":
            if now-self.stage_started >= 12:
                self.load_classes()
                self.begin_round()
        elif self.stage == "waiting_for_pie":
            self.world = self.game_world()
            if self.world and self.levels.is_in_play_in_editor():
                self.controller()
                self.pieces = self.actors("piece")
                for piece in self.pieces:
                    for tag in map(str, piece.tags):
                        if tag.startswith("Floor_"):
                            self.floor_members.setdefault(tag, []).append(piece)
                self.current["viewport_focus"] = self.focus()
                self.set_stage("idle")
            elif now-self.stage_started > 15:
                raise TimeoutError("PIE did not start")
        elif self.stage == "idle":
            if now-self.stage_started < 1.2:
                return
            counts = self.counts()
            self.current["initial_counts"] = counts
            self.check("fresh_pie_clean_building", counts["pieces"] > 0 and all(counts[key] == 0 for key in
                ("activated", "fractured", "scheduled", "fragments", "powder", "projectiles", "meteor_impact", "rocket_impact"))
                and abs(counts["time_dilation"]-1) < .01, **counts)
            self.fire()
        elif self.stage == "observing_shot":
            counts = self.sample(now)
            shot_age = counts["game_seconds"]-self.shot_game
            if self.current.get("capture") and not self.incoming_requested and shot_age >= .55:
                if self.first_hit_at is None and self.pending_capture is None:
                    self.request_capture("meteor_incoming", .55)
                    self.incoming_requested = True
                elif self.first_hit_at is not None:
                    self.check("incoming_capture_before_contact", False, shot_age_game_seconds=shot_age)
                    self.incoming_requested = True
            if self.current.get("miss"):
                if shot_age >= 8.2:
                    self.complete_round(counts)
                elif now-self.shot_at > 40:
                    raise TimeoutError("Miss round failed to advance 8.2 game seconds")
                return
            if self.first_hit_at is None:
                if shot_age > 4 or now-self.shot_at > 15:
                    self.check("hit_timeout", False, shot_age_game_seconds=shot_age)
                    self.complete_round(counts)
                return
            hit_age = counts["game_seconds"]-self.first_hit_game
            if self.current.get("capture") and self.capture_index < len(AFTER_HIT_CAPTURES) and self.pending_capture is None:
                offset, name = AFTER_HIT_CAPTURES[self.capture_index]
                if hit_age >= offset:
                    self.request_capture("meteor_"+name, offset)
                    self.capture_index += 1
            target_age = 8.2 if self.current["mode"] == 1 else 3.0
            captures_done = not self.current.get("capture") or self.capture_index == len(AFTER_HIT_CAPTURES)
            if hit_age >= target_age and captures_done and self.pending_capture is None and abs(counts["time_dilation"]-1) < .01:
                self.complete_round(counts)
            elif now-self.shot_at > 45:
                self.check("round_progress_timeout", False, game_seconds_after_hit=hit_age,
                           wall_seconds_after_shot=now-self.shot_at)
                if self.pending_capture:
                    self.finish("Round timeout with a screenshot still pending")
                else:
                    self.complete_round(counts)
        elif self.stage == "ending_round":
            if not self.levels.is_in_play_in_editor() and not self.game_world():
                self.world = None
                self.begin_round()
            elif now-self.stage_started > 12:
                raise TimeoutError("PIE did not stop between rounds")
        elif self.stage == "finishing":
            stopped = not self.levels.is_in_play_in_editor() and not self.game_world()
            if not stopped and now-self.started < 180 and now-self.stage_started < 5:
                return
            if not stopped:
                self.outcome = "failed"
                self.report["cleanup_error"] = "EndPIE requested but not confirmed"
            self.report.update(status=self.outcome, pie_stopped=stopped, editor_open=True)
            self.set_stage("complete")
            self.stop_callback()
            unreal.log("SKYFALL_METEOR_" + self.outcome.upper())


previous = getattr(unreal, "_skyfall_meteor_runner", None)
if previous is not None:
    previous.stop_callback()
runner = MeteorChecks()
unreal._skyfall_meteor_runner = runner
runner.start()
