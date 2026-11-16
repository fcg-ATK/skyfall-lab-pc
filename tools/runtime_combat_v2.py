"""Exercise the saved CombatV2 map in a full Unreal Editor and leave it open.

Run with -ExecCmds="py <absolute path to this file>". All waits use a Slate
post-tick callback and real wall time. Gameplay assets and maps are read-only.
Only live PIE controller settings and its native FireWeapon entry are invoked.
"""

import json
import math
import struct
import time
import traceback
from pathlib import Path

import unreal


PROJECT = Path(__file__).resolve().parents[1]
WORKSPACE = PROJECT / "Saved"
REPORT_PATH = PROJECT / "Saved" / "runtime-combat-v2.json"
IMAGE_DIR = WORKSPACE / "QA" / "skyfall-combat-v2"
BLUEPRINTS = "/Game/Skyfall/Blueprints/"
EFFECTS = "/Game/Skyfall/CombatV2/Effects/"
ROUNDS = (
    {"name": "rocket_miss", "mode": 0, "height": 3800, "speed": 10000, "gravity": 0, "radius": 55},
    {"name": "rocket_hit", "mode": 0, "height": 900, "speed": 10000, "gravity": 0, "radius": 55},
    {"name": "shell_hit", "mode": 1, "height": 900, "speed": 12000, "gravity": 0, "radius": 38},
    {"name": "ball_hit", "mode": 2, "height": 900, "speed": 7200, "gravity": -980, "radius": 100},
    {"name": "reset", "mode": None},
)


def _alive(actor):
    return bool(actor and unreal.SystemLibrary.is_valid(actor))


def _xyz(vector):
    return [float(vector.x), float(vector.y), float(vector.z)]


class CombatRuntimeChecks:
    def __init__(self):
        self.levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        self.editor = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        self.started = time.perf_counter()
        self.stage_started = self.started
        self.round_started = None
        self.next_poll = 0.0
        self.last_write = 0.0
        self.handle = None
        self.stage = "warmup"
        self.world = None
        self.classes = {}
        self.round_index = -1
        self.current = None
        self.shot_at = None
        self.first_hit_at = None
        self.last_sample_at = 0.0
        self.captures = {}
        self.outcome = None
        self.report = {
            "status": "running", "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "engine": unreal.SystemLibrary.get_engine_version(),
            "map": self.editor.get_editor_world().get_path_name(),
            "checks": {}, "rounds": [], "screenshots": {}, "failures": [],
            "timing": {"warmup_real_seconds": 20, "round_limit_real_seconds": 40,
                       "overall_limit_real_seconds": 300},
            "camera_policy": "Use the naturally activated PIE view target and current viewport",
        }

    def start(self):
        IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        if self.levels.is_in_play_in_editor():
            self.levels.editor_request_end_play()
            self.stage = "stopping_existing_pie"
        self.write()
        self.handle = unreal.register_slate_post_tick_callback(self.tick)
        unreal.log("SKYFALL_COMBAT_RUNTIME_STARTED warmup=20s")

    def stop_callback(self):
        if self.handle is not None:
            unreal.unregister_slate_post_tick_callback(self.handle)
            self.handle = None

    def write(self):
        now = time.perf_counter()
        self.report.update(stage=self.stage, elapsed_real_seconds=round(now-self.started, 3))
        temporary = REPORT_PATH.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(REPORT_PATH)
        self.last_write = now

    def set_stage(self, name):
        self.stage = name
        self.stage_started = time.perf_counter()
        self.write()
        unreal.log("SKYFALL_COMBAT_RUNTIME_STAGE " + name)

    def check(self, name, passed, **details):
        key = (self.current["name"] + "/" if self.current else "") + name
        self.report["checks"][key] = dict(passed=bool(passed), **details)
        if not passed and key not in self.report["failures"]:
            self.report["failures"].append(key)
            unreal.log_warning("SKYFALL_COMBAT_CHECK_FAILED " + key + " " + str(details))

    def load_classes(self):
        paths = {
            "controller": BLUEPRINTS + "BP_SkyfallControllerV2",
            "piece": BLUEPRINTS + "BP_SkyfallPieceV2",
            "fragment": BLUEPRINTS + "BP_SkyfallFragmentV2",
            "rocket": BLUEPRINTS + "BP_SkyfallRocketV2",
            "shell": BLUEPRINTS + "BP_SkyfallShellV2",
            "ball": BLUEPRINTS + "BP_SkyfallBallV2",
            "powder": EFFECTS + "BP_LocalPowder",
            "impact": EFFECTS + "BP_HighEnergyImpact",
            "exhaust": EFFECTS + "BP_RocketExhaust",
        }
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
                if _alive(actor)]

    def controller(self):
        actors = self.actors("controller")
        if len(actors) != 1:
            raise RuntimeError("Expected one live ControllerV2, got " + str(len(actors)))
        return actors[0]

    def counts(self):
        pieces = self.actors("piece")
        structures = [actor for actor in pieces if actor.actor_has_tag("structure")]
        return {
            "pieces": len(pieces),
            "activated": sum(bool(actor.get_editor_property("HasActivated")) for actor in pieces),
            "fractured": sum(bool(actor.get_editor_property("HasFractured")) for actor in pieces),
            "scheduled": sum(bool(actor.get_editor_property("CollapseScheduled")) for actor in pieces),
            "structure_activated": sum(bool(actor.get_editor_property("HasActivated")) for actor in structures),
            "structure_fractured": sum(bool(actor.get_editor_property("HasFractured")) for actor in structures),
            "structure_scheduled": sum(bool(actor.get_editor_property("CollapseScheduled")) for actor in structures),
            "fragments": len(self.actors("fragment")),
            "projectiles": sum(len(self.actors(key)) for key in ("rocket", "shell", "ball")),
            "powder": len(self.actors("powder")), "impact": len(self.actors("impact")),
            "exhaust": len(self.actors("exhaust")),
            "time_dilation": float(unreal.GameplayStatics.get_global_time_dilation(self.world)),
            "game_seconds": float(unreal.GameplayStatics.get_time_seconds(self.world)),
        }

    def powder_diagnostics(self):
        powders = self.actors("powder")
        if not powders:
            return {"actor_count": 0, "is_active": False}
        actor = powders[-1]
        component = actor.get_editor_property("niagara_component")
        asset = component.get_asset()
        diagnostic = {
            "actor_count": len(powders), "sample_actor": actor.get_path_name(),
            "is_active": bool(component.is_active()),
            "system": asset.get_path_name() if asset else None,
            "actor_age_game_seconds": float(actor.get_game_time_since_creation()),
            "visible": bool(component.is_visible()),
        }
        if not diagnostic["is_active"] or self.current["particle_snapshots"] >= 2:
            return diagnostic
        self.current["particle_snapshots"] += 1
        try:
            diagnostic["strength"] = component.get_variable_float("User.Strength")
            cache = unreal.NiagaraSimCacheFunctionLibrary.create_niagara_sim_cache(self.world)
            result = unreal.NiagaraSimCacheFunctionLibrary.capture_niagara_sim_cache_immediate(
                cache, unreal.NiagaraSimCacheCreateParameters(), component, False)
            success = bool(result[0]) if isinstance(result, tuple) else bool(result)
            if isinstance(result, tuple) and len(result) > 1 and result[1]:
                cache = result[1]
            diagnostic["particle_capture_success"] = success
            if success and cache.get_num_frames() > 0:
                counts = {}
                for emitter in cache.get_emitter_names():
                    positions = cache.read_position_attribute("Position", emitter, True, 0)
                    counts[str(emitter)] = len(positions)
                diagnostic["particle_counts"] = counts
                diagnostic["particle_count"] = sum(counts.values())
                self.current["max_sampled_powder_particles"] = max(
                    self.current["max_sampled_powder_particles"], diagnostic["particle_count"])
        except Exception as error:
            diagnostic["particle_capture_error"] = str(error)
        return diagnostic

    def fragment_diagnostics(self, all_heights=False):
        fragments = self.actors("fragment")
        diagnostic = {"actor_count": len(fragments), "sample_limit": 12, "samples": []}
        for actor in fragments[:12]:
            component = actor.static_mesh_component
            object_type = component.get_collision_object_type()
            diagnostic["samples"].append({
                "actor": actor.get_path_name(),
                "world_z_cm": float(actor.get_actor_location().z),
                "simulating_physics": bool(component.is_simulating_physics()),
                "gravity_enabled": bool(component.is_gravity_enabled()),
                "velocity_cm_per_second": _xyz(component.get_physics_linear_velocity()),
                "collision_profile": str(component.get_collision_profile_name()),
                "collision_object_type": str(object_type),
                "response_to_own_object_type": str(component.get_collision_response_to_channel(object_type)),
            })
        if all_heights and fragments:
            heights = [float(actor.get_actor_location().z) for actor in fragments]
            diagnostic["all_fragment_min_z_cm"] = min(heights)
            diagnostic["all_fragment_max_z_cm"] = max(heights)
            diagnostic["all_fragment_mean_z_cm"] = sum(heights) / len(heights)
        return diagnostic

    def aim(self):
        controller = self.controller()
        # Editable properties may reconstruct SCS components, so acquire fresh
        # component references for every sample after controller updates.
        components = controller.get_components_by_class(unreal.SceneComponent)
        dots = next((item for item in components if item.get_name().startswith("AimGuideDots")), None)
        arrow = next((item for item in components if item.get_name().startswith("AimGuideArrow")), None)
        velocity = controller.call_method("GetLaunchVelocity")
        alignment = None
        if _alive(arrow) and velocity.length() > 0:
            forward = arrow.get_forward_vector()
            alignment = float((forward.x*velocity.x + forward.y*velocity.y + forward.z*velocity.z)
                              / velocity.length())
        player = unreal.GameplayStatics.get_player_controller(self.world, 0)
        target = player.get_view_target() if _alive(player) else None
        target_tags = list(map(str, target.tags)) if _alive(target) else []
        measurements = {}
        for label, component in (("arrow", arrow), ("dots", dots)):
            if not _alive(component):
                measurements[label] = {"error": "Component missing"}
                continue
            try:
                mesh = component.get_editor_property("static_mesh")
                mesh_bounds = mesh.get_bounds()
                origin, extent, radius = unreal.SystemLibrary.get_component_bounds(component)
                rotation = component.get_world_rotation()
                measurements[label] = {
                    "mesh": mesh.get_path_name(),
                    "mesh_bounds_origin": _xyz(mesh_bounds.origin),
                    "mesh_dimensions_raw_units": [2*value for value in _xyz(mesh_bounds.box_extent)],
                    "world_location_cm": _xyz(component.get_world_location()),
                    "world_rotation_degrees": [float(rotation.pitch), float(rotation.yaw), float(rotation.roll)],
                    "world_scale": _xyz(component.get_world_scale()),
                    "world_bounds_origin_cm": _xyz(origin),
                    "world_bounds_dimensions_cm": [2*value for value in _xyz(extent)],
                    "world_bounds_sphere_radius_cm": float(radius),
                }
                if label == "dots" and component.get_instance_count():
                    instance = component.get_instance_transform(0, world_space=True)
                    if isinstance(instance, (tuple, list)):
                        instance = next((value for value in instance if isinstance(value, unreal.Transform)), None)
                    if isinstance(instance, unreal.Transform):
                        instance_scale = _xyz(instance.scale3d)
                        measurements[label]["first_instance_world_transform"] = str(instance)
                        measurements[label]["first_instance_dimensions_cm"] = [
                            abs(scale)*dimension for scale, dimension in
                            zip(instance_scale, measurements[label]["mesh_dimensions_raw_units"])]
                    else:
                        measurements[label]["instance_transform_error"] = str(instance)
                    material = component.get_material(0)
                    if material:
                        base = material.get_base_material()
                        measurements[label]["material"] = material.get_path_name()
                        measurements[label]["material_used_with_instanced_static_meshes"] = bool(
                            base.get_editor_property("used_with_instanced_static_meshes"))
            except Exception as error:
                measurements[label] = {"error": str(error)}
        return {"dot_count": int(dots.get_instance_count()) if _alive(dots) else 0,
                "arrow_alignment_dot": alignment,
                "launch_velocity": _xyz(velocity),
                "natural_view_target": target.get_path_name() if _alive(target) else None,
                "natural_view_target_tags": target_tags,
                "fixed_camera_active": "SFCamera" in target_tags,
                "geometry_measurements": measurements}

    def request_capture(self, name):
        if name in self.captures:
            return
        path = IMAGE_DIR / (name + ".png")
        requested = time.time()
        player = unreal.GameplayStatics.get_player_controller(self.world, 0)
        if not _alive(player):
            raise RuntimeError("PIE player controller is unavailable for screenshot " + name)
        command = 'HighResShot 1920x1080 filename="' + path.as_posix() + '"'
        self.captures[name] = {
            "path": path, "requested": requested, "real_at": time.perf_counter(),
            "previous_mtime_ns": path.stat().st_mtime_ns if path.exists() else None,
        }
        self.report["screenshots"][name] = {
            "path": str(path), "status": "pending", "width": 1920, "height": 1080,
            "seconds_after_first_hit": (round(time.perf_counter()-self.first_hit_at, 3)
                                         if self.first_hit_at else None),
            "view_target": self.aim()["natural_view_target"],
            "capture_source": "PIE player controller HighResShot",
            "console_command": command,
        }
        # Route through the PIE player's viewport. The editor automation capture
        # API selects a level-editor viewport independently of this game world.
        unreal.SystemLibrary.execute_console_command(self.world, command, player)
        self.write()

    def poll_captures(self, now):
        for name, capture in self.captures.items():
            if self.report["screenshots"][name]["status"] != "pending":
                continue
            path = capture["path"]
            fresh = (path.exists() and path.stat().st_mtime >= capture["requested"]-1
                     and path.stat().st_mtime_ns != capture["previous_mtime_ns"])
            if fresh:
                with path.open("rb") as file:
                    header = file.read(24)
                if len(header) == 24 and header[:8] == b"\x89PNG\r\n\x1a\n":
                    size = struct.unpack(">II", header[16:24])
                    self.check("screenshot_" + name, size == (1920,1080), dimensions=list(size))
                    self.report["screenshots"][name].update(status="captured", bytes=path.stat().st_size)
                    self.write()
            if self.report["screenshots"][name]["status"] == "pending" and now-capture["real_at"] > 25:
                self.report["screenshots"][name]["status"] = "timeout"
                self.check("screenshot_" + name, False, error="Capture did not produce a fresh PNG within 25s")

    def begin_round(self):
        self.round_index += 1
        if self.round_index >= len(ROUNDS):
            self.finish()
            return
        self.current = dict(ROUNDS[self.round_index])
        self.current.update(samples=[], hit_actor_paths=[], max_hit_count=0, max_fractured=0,
                            max_fragments=0, max_y=None, minimum_z=None,
                            slow_motion_seen=False, normal_speed_restored=False,
                            max_powder=0, max_impact=0, duplicate_hit_observed=False,
                            max_structure_activated=0, max_structure_fractured=0,
                            max_structure_scheduled=0, max_scheduled=0, max_structural_hit_targets=0,
                            particle_snapshots=0, max_sampled_powder_particles=0)
        self.report["rounds"].append(self.current)
        self.round_started = time.perf_counter()
        self.shot_at = None
        self.first_hit_at = None
        self.world = None
        self.levels.editor_request_begin_play()
        self.set_stage("waiting_for_pie")

    def fire(self):
        controller = self.controller()
        values = {"WeaponMode": self.current["mode"], "LaunchHeight": float(self.current["height"]),
                  "YawDegrees": 0.0, "PitchDegrees": 0.0,
                  "LaunchSpeed": float(self.current["speed"]), "GravityZ": float(self.current["gravity"]),
                  "ProjectileRadius": float(self.current["radius"])}
        for key, value in values.items():
            controller.set_editor_property(key, value)
        before = {item.get_path_name() for key in ("rocket", "shell", "ball") for item in self.actors(key)}
        controller.call_method("FireWeapon")
        new = [item for key in ("rocket", "shell", "ball") for item in self.actors(key)
               if item.get_path_name() not in before]
        self.check("fire_entry", len(new) == 1, projectile_count=len(new), parameters=values)
        if len(new) != 1:
            raise RuntimeError("FireWeapon did not create exactly one projectile")
        self.current["projectile_path"] = new[0].get_path_name()
        self.current["initial_location"] = _xyz(new[0].get_actor_location())
        self.current["initial_velocity"] = _xyz(new[0].get_editor_property("ShotVelocity"))
        self.shot_at = time.perf_counter()
        self.set_stage("observing_shot")

    def sample_shot(self, now):
        counts = self.counts()
        current = self.current
        current["max_fractured"] = max(current["max_fractured"], counts["fractured"])
        current["max_fragments"] = max(current["max_fragments"], counts["fragments"])
        current["max_powder"] = max(current["max_powder"], counts["powder"])
        current["max_impact"] = max(current["max_impact"], counts["impact"])
        for key in ("structure_activated", "structure_fractured", "structure_scheduled", "scheduled"):
            current["max_" + key] = max(current["max_" + key], counts[key])
        if abs(counts["time_dilation"]-.22) < .015:
            current["slow_motion_seen"] = True
            current.setdefault("first_slow_motion_real_seconds", round(now-self.shot_at, 3))
        if current["slow_motion_seen"] and abs(counts["time_dilation"]-1) < .01:
            current["normal_speed_restored"] = True
            current.setdefault("normal_speed_real_seconds", round(now-self.shot_at, 3))
        projectile = next((item for key in ("rocket", "shell", "ball") for item in self.actors(key)
                           if item.get_path_name() == current["projectile_path"]), None)
        if _alive(projectile):
            position = projectile.get_actor_location()
            current["max_y"] = max(current["max_y"] or position.y, float(position.y))
            current["minimum_z"] = min(current["minimum_z"] or position.z, float(position.z))
            hit_actors = list(projectile.get_editor_property("HitActors"))
            paths = [actor.get_path_name() if _alive(actor) else str(actor) for actor in hit_actors]
            current["duplicate_hit_observed"] |= len(paths) != len(set(paths))
            current["max_hit_count"] = max(current["max_hit_count"], len(paths))
            current["max_structural_hit_targets"] = max(
                current["max_structural_hit_targets"],
                sum(_alive(actor) and actor.actor_has_tag("structure") for actor in hit_actors))
            current["hit_actor_paths"] = list(dict.fromkeys(current["hit_actor_paths"] + paths))
            current["last_velocity"] = _xyz(projectile.get_editor_property("ShotVelocity"))
            current["last_location"] = _xyz(position)
        if self.first_hit_at is None and (counts["fractured"] > 0 or current["max_hit_count"] > 0):
            self.first_hit_at = now
            current["first_hit"] = dict(counts, real_seconds_after_shot=round(now-self.shot_at,3))
            self.write()
        if now-self.last_sample_at >= .2:
            current["samples"].append(dict(counts, real_seconds_after_shot=round(now-self.shot_at,3),
                                            hit_count=current["max_hit_count"], max_y=current["max_y"],
                                            powder_diagnostics=self.powder_diagnostics(),
                                            fragment_diagnostics=self.fragment_diagnostics()))
            self.last_sample_at = now
        return counts

    def complete_shot(self, counts):
        current = self.current
        if current["name"] == "rocket_miss":
            self.check("miss", current["max_hit_count"] == 0 and current["max_fractured"] == 0
                       and current["max_powder"] == 0 and current["max_impact"] == 0
                       and abs(counts["time_dilation"]-1) < .01,
                       hit_count=current["max_hit_count"], fractures=current["max_fractured"],
                       powder=current["max_powder"], impact=current["max_impact"],
                       time_dilation=counts["time_dilation"], duration_real_seconds=time.perf_counter()-self.shot_at)
        else:
            self.check("actual_hit", current["max_fractured"] > 0 and current["max_fragments"] > 0,
                       fractures=current["max_fractured"], fragments=current["max_fragments"],
                       hit_count=current["max_hit_count"])
            self.check("unique_hits", not current["duplicate_hit_observed"],
                       hit_count=current["max_hit_count"], hit_actor_paths=current["hit_actor_paths"])
            if current["name"] == "rocket_hit":
                first = current.get("first_hit", {}).get("fractured", 0)
                self.check("progressive_fracture", 0 < first < current["max_fractured"],
                           first_fractures=first, later_fractures=current["max_fractured"])
                self.check("rear_penetration", (current["max_y"] or -math.inf) > 1100,
                           max_projectile_y=current["max_y"])
                self.check("slow_motion", current["slow_motion_seen"] and current["normal_speed_restored"],
                           slow_seen=current["slow_motion_seen"], normal_restored=current["normal_speed_restored"],
                           slow_real_seconds=current.get("first_slow_motion_real_seconds"),
                           normal_real_seconds=current.get("normal_speed_real_seconds"))
                if current["normal_speed_restored"] and self.first_hit_at:
                    duration = current["normal_speed_real_seconds"] - current["first_hit"]["real_seconds_after_shot"]
                    self.check("slow_motion_real_duration", .85 <= duration <= 1.7,
                               observed_duration_real_seconds=duration, expected_real_seconds=1.2)
                self.check("localized_fx", current["max_powder"] > 0 and current["max_impact"] > 0,
                           powder=current["max_powder"], impact=current["max_impact"])
            elif current["name"] == "shell_hit":
                self.check("shell_penetration", current["max_hit_count"] > 1, hit_count=current["max_hit_count"])
                self.check("shell_structural_damage", current["max_structure_activated"] > 0
                           and current["max_structure_fractured"] > 0 and current["max_structural_hit_targets"] > 0,
                           structural_activated=current["max_structure_activated"],
                           structural_fractured=current["max_structure_fractured"],
                           structural_hit_targets=current["max_structural_hit_targets"])
                self.check("shell_cascade", current["max_structure_scheduled"] > 0,
                           structural_scheduled=current["max_structure_scheduled"],
                           total_scheduled=current["max_scheduled"])
                self.check("shell_local_powder", current["max_powder"] > 0,
                           powder=current["max_powder"])
                first = current.get("first_hit", {}).get("fractured", 0)
                self.check("shell_progressive_fracture", 0 < first < current["max_fractured"]
                           and current["max_fractured"] > 2,
                           first_fractures=first, later_fractures=current["max_fractured"])
            elif current["name"] == "ball_hit":
                self.check("ball_gravity", current.get("last_velocity", [0,0,0])[2] < -5
                           and (current["minimum_z"] or 900) < 895,
                           minimum_z=current["minimum_z"], last_velocity=current.get("last_velocity"))
        current["final_counts"] = counts
        current["final_fragment_diagnostics"] = self.fragment_diagnostics(all_heights=True)
        self.levels.editor_request_end_play()
        self.set_stage("ending_round")

    def finish(self, error=None):
        if error:
            self.report["error"] = error
            unreal.log_error("SKYFALL_COMBAT_RUNTIME_ERROR " + error)
        self.outcome = "failed" if error or self.report["failures"] else "passed"
        if self.levels.is_in_play_in_editor():
            self.levels.editor_request_end_play()
        self.set_stage("finishing")

    def tick(self, delta_seconds):
        now = time.perf_counter()
        if now < self.next_poll:
            return
        self.next_poll = now + .05
        try:
            self.advance(now)
            self.poll_captures(now)
            if now-self.last_write > 1:
                self.write()
        except Exception:
            if self.stage == "finishing":
                self.report.update(status="failed", cleanup_error=traceback.format_exc())
                self.write()
                self.stop_callback()
            else:
                self.finish(traceback.format_exc())

    def advance(self, now):
        if now-self.started > 300 and self.stage != "finishing":
            raise TimeoutError("Full CombatV2 regression exceeded 300 real seconds")
        if self.round_started and now-self.round_started > 40 and self.stage not in ("finishing", "ending_round"):
            self.check("round_timeout", False, real_seconds=now-self.round_started, stage=self.stage)
            self.levels.editor_request_end_play()
            self.set_stage("ending_round")
            return
        if self.stage == "stopping_existing_pie":
            if not self.levels.is_in_play_in_editor() and not self.game_world():
                self.set_stage("warmup")
            elif now-self.stage_started > 15:
                raise TimeoutError("Existing PIE did not stop")
        elif self.stage == "warmup":
            if now-self.stage_started >= 20:
                self.load_classes()
                self.begin_round()
        elif self.stage == "waiting_for_pie":
            self.world = self.game_world()
            if self.world and self.levels.is_in_play_in_editor():
                self.controller()
                self.set_stage("idle")
        elif self.stage == "idle":
            if now-self.stage_started < 1.2:
                return
            counts = self.counts()
            self.current["reset_counts"] = counts
            self.check("clean_reset", counts["pieces"] > 0 and counts["activated"] == 0
                       and counts["fractured"] == 0 and counts["fragments"] == 0
                       and counts["projectiles"] == 0 and abs(counts["time_dilation"]-1) < .01,
                       **counts)
            aim = self.aim()
            self.current["initial_aim"] = aim
            self.check("aim", aim["dot_count"] == 26 and (aim["arrow_alignment_dot"] or 0) > .995, **aim)
            geometry = aim["geometry_measurements"]
            arrow_size = geometry.get("arrow", {}).get("world_bounds_dimensions_cm", [])
            dot_size = geometry.get("dots", {}).get("first_instance_dimensions_cm", [])
            self.check("aim_world_size", bool(arrow_size and dot_size)
                       and 0 < max(arrow_size) <= 220 and 0 < max(dot_size) <= 20.5,
                       arrow_dimensions_cm=arrow_size, dot_dimensions_cm=dot_size,
                       arrow_target_max_cm=200, arrow_limit_with_rotation_tolerance_cm=220,
                       dot_limit_cm=20.5, geometry=geometry)
            self.check("natural_camera", aim["fixed_camera_active"], **aim)
            if self.current["name"] == "reset":
                self.current["final_counts"] = counts
                self.levels.editor_request_end_play()
                self.set_stage("ending_round")
            elif self.current["name"] == "rocket_miss":
                self.request_capture("aim")
                self.set_stage("aim_capture")
            else:
                self.fire()
        elif self.stage == "aim_capture":
            if self.report["screenshots"]["aim"]["status"] != "pending":
                self.fire()
        elif self.stage == "observing_shot":
            counts = self.sample_shot(now)
            if self.current["name"] == "rocket_miss":
                if now-self.shot_at >= 1.5:
                    self.complete_shot(counts)
                return
            if not self.first_hit_at:
                if now-self.shot_at > 6:
                    self.check("hit_timeout", False, counts=counts, projectile=self.current.get("last_location"))
                    self.complete_shot(counts)
                return
            since_hit = now-self.first_hit_at
            if self.current["name"] == "rocket_hit":
                if since_hit >= .3:
                    self.request_capture("impact")
                if since_hit >= 2.6:
                    self.request_capture("collapse")
                if since_hit >= 8:
                    self.request_capture("rubble")
                if since_hit >= 15:
                    self.request_capture("settled")
                done = all(self.report["screenshots"].get(name, {}).get("status") in ("captured", "timeout")
                           for name in ("impact", "collapse", "rubble", "settled"))
                if since_hit >= 15 and done:
                    self.complete_shot(counts)
            elif since_hit >= 4:
                self.complete_shot(counts)
        elif self.stage == "ending_round":
            if not self.levels.is_in_play_in_editor() and not self.game_world():
                self.world = None
                self.begin_round()
            elif now-self.stage_started > 15:
                raise TimeoutError("PIE did not finish between rounds")
        elif self.stage == "finishing":
            if self.levels.is_in_play_in_editor():
                if now-self.stage_started > 15:
                    raise TimeoutError("PIE did not finish during cleanup")
                return
            self.report.update(status=self.outcome, editor_open=True)
            self.set_stage("complete")
            self.stop_callback()
            unreal.log("SKYFALL_COMBAT_RUNTIME_" + self.outcome.upper())


previous = getattr(unreal, "_skyfall_combat_v2_runner", None)
if previous is not None:
    previous.stop_callback()
runner = CombatRuntimeChecks()
unreal._skyfall_combat_v2_runner = runner
runner.start()
