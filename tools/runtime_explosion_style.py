"""Observe saved explosion assets in four independent PIE sessions.

Run in the full editor with -ExecCmds="py <absolute path to this file>".
Assets/maps are never modified or saved. Screenshots use the natural PIE player
viewport; captures are serialized and scheduled using effect game age because
HighResShot can stall simulation. Actual game and wall-clock offsets are recorded.
Particle counts and projected geometry are observations, not aesthetic scores.
The callback ends PIE, writes its report, and leaves the editor open.
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
REPORT_PATH = PROJECT / "Saved" / "runtime-explosion-style.json"
IMAGE_DIR = WORKSPACE / "QA" / "skyfall-explosion-style"
BLUEPRINTS = "/Game/Skyfall/Blueprints/"
EFFECTS = "/Game/Skyfall/CombatV2/Effects/"
ROUNDS = (
    {"name": "rocket_miss", "mode": 0, "height": 3800, "speed": 10000, "radius": 55},
    {"name": "rocket_timing", "mode": 0, "height": 900, "speed": 10000, "radius": 55,
     "capture": False},
    {"name": "rocket_hit", "mode": 0, "height": 900, "speed": 10000, "radius": 55},
    {"name": "shell_hit", "mode": 1, "height": 900, "speed": 12000, "radius": 38},
)
# Game ages derived from 0.22 dilation for the first 1.2 healthy real seconds.
# Screenshot rendering may extend actual wall time; that must not advance phases.
CAPTURE_OFFSETS = ((.0264, .12, "flash"), (.099, .45, "fireball"), (.198, .9, "shockwave"),
                   (.664, 1.6, "smoke"), (2.264, 3.2, "aftermath"))


def _alive(actor):
    return bool(actor and unreal.SystemLibrary.is_valid(actor))


def _xyz(vector):
    return [float(vector.x), float(vector.y), float(vector.z)]


class ExplosionStyleChecks:
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
        self.round_started = None
        self.shot_at = None
        self.first_hit_at = None
        self.first_hit_game_seconds = None
        self.pending_capture = None
        self.capture_index = 0
        self.next_poll = 0.0
        self.last_write = 0.0
        self.last_sample_at = 0.0
        self.outcome = None
        self.report = {
            "status": "running", "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "engine": unreal.SystemLibrary.get_engine_version(),
            "map": self.editor.get_editor_world().get_path_name(),
            "checks": {}, "rounds": [], "screenshots": {}, "failures": [],
            "timing": {"warmup_real_seconds": 12, "overall_limit_real_seconds": 180,
                       "capture_effect_game_ages": [item[0] for item in CAPTURE_OFFSETS],
                       "capture_nominal_real_offsets": [item[1] for item in CAPTURE_OFFSETS],
                       "hit_round_minimum_game_elapsed": 3.0,
                       "hit_round_timeout_real_seconds": 45},
            "camera_policy": "Natural PIE view target; no camera replacement",
            "capture_viewport_verified": False,
            "capture_validation_note": "Fresh PNG validates file completion only; inspect HUD/camera to confirm gameplay viewport",
            "measurement_policy": "Particle counts and screen bounds are observational only; no beauty assertions",
        }

    def start(self):
        IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        if self.levels.is_in_play_in_editor():
            self.levels.editor_request_end_play()
            self.stage = "stopping_existing_pie"
        self.write()
        self.handle = unreal.register_slate_post_tick_callback(self.tick)
        unreal.log("SKYFALL_EXPLOSION_STYLE_STARTED warmup=12s")

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
        unreal.log("SKYFALL_EXPLOSION_STYLE_STAGE " + name)

    def check(self, name, passed, **details):
        key = (self.current["name"] + "/" if self.current else "") + name
        self.report["checks"][key] = dict(passed=bool(passed), **details)
        if not passed and key not in self.report["failures"]:
            self.report["failures"].append(key)
            unreal.log_warning("SKYFALL_EXPLOSION_STYLE_CHECK_FAILED " + key)

    def load_classes(self):
        names = {"controller": "BP_SkyfallControllerV2", "piece": "BP_SkyfallPieceV2",
                 "fragment": "BP_SkyfallFragmentV2", "rocket": "BP_SkyfallRocketV2",
                 "shell": "BP_SkyfallShellV2", "ball": "BP_SkyfallBallV2"}
        paths = {key: BLUEPRINTS+name for key, name in names.items()}
        paths.update(impact=EFFECTS+"BP_HighEnergyImpact", powder=EFFECTS+"BP_LocalPowder")
        for key, path in paths.items():
            asset = unreal.load_asset(path)
            if not asset or not asset.generated_class():
                raise RuntimeError("Missing runtime class " + path)
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

    def projectiles(self):
        return [item for key in ("rocket", "shell", "ball") for item in self.actors(key)]

    def counts(self):
        pieces = self.actors("piece")
        return {
            "pieces": len(pieces),
            "activated": sum(bool(actor.get_editor_property("HasActivated")) for actor in pieces),
            "fractured": sum(bool(actor.get_editor_property("HasFractured")) for actor in pieces),
            "scheduled": sum(bool(actor.get_editor_property("CollapseScheduled")) for actor in pieces),
            "fragments": len(self.actors("fragment")), "projectiles": len(self.projectiles()),
            "powder": len(self.actors("powder")), "impact": len(self.actors("impact")),
            "time_dilation": float(unreal.GameplayStatics.get_global_time_dilation(self.world)),
            "game_seconds": float(unreal.GameplayStatics.get_time_seconds(self.world)),
            "world_undilated_seconds": float(unreal.GameplayStatics.get_real_time_seconds(self.world)),
        }

    def effect_game_age(self):
        impacts = self.actors("impact")
        if impacts:
            return max(float(actor.get_game_time_since_creation()) for actor in impacts)
        if self.first_hit_game_seconds is not None:
            return max(0.0, float(unreal.GameplayStatics.get_time_seconds(self.world))-self.first_hit_game_seconds)
        return None

    def camera_observation(self):
        player = unreal.GameplayStatics.get_player_controller(self.world, 0)
        target = player.get_view_target() if _alive(player) else None
        result = {"view_target": target.get_path_name() if _alive(target) else None,
                  "view_target_tags": list(map(str, target.tags)) if _alive(target) else []}
        try:
            camera = unreal.GameplayStatics.get_player_camera_manager(self.world, 0)
            result.update(location_cm=_xyz(camera.get_camera_location()),
                          rotation_degrees=str(camera.get_camera_rotation()),
                          horizontal_fov_degrees=float(camera.get_fov_angle()),
                          viewport_pixels=list(player.get_viewport_size()))
        except Exception as error:
            result["measurement_error"] = str(error)
        return result

    def focus_game_viewport(self):
        # Native UMG API requests Slate focus without replacing the view target.
        # OS-level foreground activation and image inspection remain separate.
        try:
            unreal.WidgetBlueprintLibrary.set_focus_to_game_viewport()
            return {"requested": True, "method": "WidgetBlueprintLibrary.set_focus_to_game_viewport"}
        except Exception as error:
            return {"requested": False, "error": str(error)}

    def projected_bounds(self, lower, upper):
        """Approximate perspective AABB, clearly separate from measured pixels."""
        try:
            player = unreal.GameplayStatics.get_player_controller(self.world, 0)
            camera = unreal.GameplayStatics.get_player_camera_manager(self.world, 0)
            location = _xyz(camera.get_camera_location())
            rotation = camera.get_camera_rotation()
            forward = _xyz(unreal.MathLibrary.get_forward_vector(rotation))
            right = _xyz(unreal.MathLibrary.get_right_vector(rotation))
            up = _xyz(unreal.MathLibrary.get_up_vector(rotation))
            width, height = player.get_viewport_size()
            focal = float(width) / (2*math.tan(math.radians(camera.get_fov_angle())/2))
            points = []
            for x in (lower[0], upper[0]):
                for y in (lower[1], upper[1]):
                    for z in (lower[2], upper[2]):
                        offset = [point-origin for point, origin in zip((x, y, z), location)]
                        depth = sum(a*b for a, b in zip(offset, forward))
                        if depth > 1:
                            points.append([width/2+focal*sum(a*b for a, b in zip(offset, right))/depth,
                                           height/2-focal*sum(a*b for a, b in zip(offset, up))/depth])
            if not points:
                return {"projected_corners": 0}
            lo = [min(point[index] for point in points) for index in (0, 1)]
            hi = [max(point[index] for point in points) for index in (0, 1)]
            return {"method": "Perspective projection of world AABB corners, not image segmentation",
                    "viewport_pixels": [width, height], "min_pixels": lo, "max_pixels": hi,
                    "dimensions_pixels": [hi[i]-lo[i] for i in (0, 1)], "projected_corners": len(points)}
        except Exception as error:
            return {"measurement_error": str(error)}

    def impact_diagnostics(self):
        observations = []
        for actor in self.actors("impact"):
            component = actor.get_editor_property("niagara_component")
            asset = component.get_asset()
            observation = {
                "actor": actor.get_path_name(), "active": bool(component.is_active()),
                "visible": bool(component.is_visible()), "system": asset.get_path_name() if asset else None,
                "actor_age_game_seconds": float(actor.get_game_time_since_creation()),
                "location_cm": _xyz(actor.get_actor_location()),
            }
            try:
                observation["wrapper_strength"] = float(actor.get_editor_property("Strength"))
                expected_strength = 1.0 if self.current["mode"] == 0 else .85
                self.check("impact_wrapper_strength", abs(observation["wrapper_strength"]-expected_strength) < .001,
                           actual=observation["wrapper_strength"], expected=expected_strength)
                observation["strength"] = component.get_variable_float("User.Strength")
                origin, extent, radius = unreal.SystemLibrary.get_component_bounds(component)
                lower = [a-b for a, b in zip(_xyz(origin), _xyz(extent))]
                upper = [a+b for a, b in zip(_xyz(origin), _xyz(extent))]
                observation["component_bounds"] = {
                    "min_cm": lower, "max_cm": upper, "sphere_radius_cm": float(radius),
                    "projected_bounds": self.projected_bounds(lower, upper),
                }
                cache = unreal.NiagaraSimCacheFunctionLibrary.create_niagara_sim_cache(self.world)
                result = unreal.NiagaraSimCacheFunctionLibrary.capture_niagara_sim_cache_immediate(
                    cache, unreal.NiagaraSimCacheCreateParameters(), component, False)
                success = bool(result[0]) if isinstance(result, tuple) else bool(result)
                if isinstance(result, tuple) and len(result) > 1 and result[1]:
                    cache = result[1]
                observation["particle_capture_success"] = success
                observation["emitters"] = {}
                if success and cache.get_num_frames() > 0:
                    for emitter in cache.get_emitter_names():
                        positions = cache.read_position_attribute("Position", emitter, True, 0)
                        emitter_observation = {"particle_count": len(positions)}
                        if positions:
                            xyz = [_xyz(point) for point in positions]
                            lower = [min(point[index] for point in xyz) for index in range(3)]
                            upper = [max(point[index] for point in xyz) for index in range(3)]
                            emitter_observation.update(world_min_cm=lower, world_max_cm=upper,
                                                       projected_bounds=self.projected_bounds(lower, upper))
                        observation["emitters"][str(emitter)] = emitter_observation
                    observation["particle_count"] = sum(item["particle_count"]
                                                        for item in observation["emitters"].values())
                    self.current["max_sampled_impact_particles"] = max(
                        self.current["max_sampled_impact_particles"], observation["particle_count"])
            except Exception as error:
                observation["particle_capture_error"] = str(error)
            observations.append(observation)
        return observations

    def request_capture(self, game_offset, nominal_real_offset, phase):
        if self.pending_capture is not None:
            raise RuntimeError("A screenshot is already in flight")
        prefix = "rocket" if self.current["mode"] == 0 else "shell"
        name = prefix + "_" + phase
        path = IMAGE_DIR / (name + ".png")
        player = unreal.GameplayStatics.get_player_controller(self.world, 0)
        if not _alive(player):
            raise RuntimeError("PIE player controller unavailable for " + name)
        viewport_focus = self.focus_game_viewport()
        camera = self.camera_observation()
        diagnostics_at = time.perf_counter()
        diagnostics = self.impact_diagnostics()
        game_age = self.effect_game_age()
        requested = time.time()
        now = time.perf_counter()
        elapsed = now-self.first_hit_at
        self.pending_capture = {
            "name": name, "path": path, "requested": requested, "real_at": now,
            "previous_mtime_ns": path.stat().st_mtime_ns if path.exists() else None,
        }
        command = 'HighResShot 1920x1080 filename="' + path.as_posix() + '"'
        self.report["screenshots"][name] = {
            "path": str(path), "status": "pending", "target_effect_age_game_seconds": game_offset,
            "actual_effect_age_game_seconds": game_age,
            "target_nominal_real_seconds_after_first_hit": nominal_real_offset,
            "actual_seconds_after_first_hit": round(elapsed, 4),
            "request_lateness_game_seconds": round(max(0, game_age-game_offset), 4),
            "request_lateness_real_seconds": round(max(0, elapsed-nominal_real_offset), 4),
            "camera": camera, "impact_diagnostics": diagnostics,
            "diagnostics_seconds_after_first_hit": round(diagnostics_at-self.first_hit_at, 4),
            "capture_request_route": "HighResShot via PIE player controller; actual captured viewport unverified",
            "capture_viewport_verified": False, "viewport_focus": viewport_focus,
            "console_command": command,
        }
        unreal.SystemLibrary.execute_console_command(self.world, command, player)
        self.write()

    def poll_capture(self, now):
        capture = self.pending_capture
        if capture is None:
            return
        name, path = capture["name"], capture["path"]
        fresh = (path.exists() and path.stat().st_mtime >= capture["requested"]-1
                 and path.stat().st_mtime_ns != capture["previous_mtime_ns"])
        if fresh and path.stat().st_size >= 36:
            try:
                with path.open("rb") as file:
                    header = file.read(24)
                    file.seek(-12, 2)
                    tail = file.read(12)
                complete_png = header[:8] == b"\x89PNG\r\n\x1a\n" and tail[4:8] == b"IEND"
            except OSError:
                complete_png = False
            if complete_png:
                size = struct.unpack(">II", header[16:24])
                self.check("screenshot_"+name, size == (1920, 1080), dimensions=list(size))
                self.report["screenshots"][name].update(
                    status="captured", bytes=path.stat().st_size, dimensions=list(size),
                    completed_real_seconds_after_first_hit=round(now-self.first_hit_at, 4),
                    completed_effect_age_game_seconds=self.effect_game_age())
                self.pending_capture = None
                self.write()
                return
        if now-capture["real_at"] > 8:
            self.report["screenshots"][name]["status"] = "timeout"
            self.check("screenshot_"+name, False, error="No complete fresh PNG within 8 real seconds")
            # Do not issue another HighResShot after an unconfirmed completion.
            self.pending_capture = None
            raise TimeoutError("Screenshot completion was not confirmed: " + name)

    def begin_round(self):
        self.round_index += 1
        if self.round_index >= len(ROUNDS):
            self.finish()
            return
        self.current = dict(ROUNDS[self.round_index])
        self.current.update(samples=[], impact_actor_paths=[], hit_actor_paths=[],
                            max_fractured=0, max_fragments=0, max_activated=0, max_scheduled=0,
                            max_powder=0, max_impact=0, max_hit_count=0,
                            max_sampled_impact_particles=0, slow_motion_seen=False,
                            normal_speed_restored=False, duplicate_hit_observed=False)
        self.report["rounds"].append(self.current)
        self.round_started = time.perf_counter()
        self.shot_at = None
        self.first_hit_at = None
        self.first_hit_game_seconds = None
        self.capture_index = 0
        self.last_sample_at = 0
        self.world = None
        self.levels.editor_request_begin_play()
        self.set_stage("waiting_for_pie")

    def fire(self):
        controller = self.controller()
        values = {"WeaponMode": self.current["mode"], "LaunchHeight": float(self.current["height"]),
                  "YawDegrees": 0.0, "PitchDegrees": 0.0, "LaunchSpeed": float(self.current["speed"]),
                  "GravityZ": 0.0, "ProjectileRadius": float(self.current["radius"])}
        for key, value in values.items():
            controller.set_editor_property(key, value)
        before = {item.get_path_name() for item in self.projectiles()}
        controller.call_method("FireWeapon")
        projectiles = [item for item in self.projectiles() if item.get_path_name() not in before]
        self.check("one_projectile_from_fire_entry", len(projectiles) == 1, parameters=values,
                   projectile_count=len(projectiles))
        if len(projectiles) != 1:
            raise RuntimeError("FireWeapon did not create exactly one projectile")
        self.current["projectile_path"] = projectiles[0].get_path_name()
        self.current["initial_location_cm"] = _xyz(projectiles[0].get_actor_location())
        self.current["initial_velocity"] = _xyz(projectiles[0].get_editor_property("ShotVelocity"))
        self.shot_at = time.perf_counter()
        self.set_stage("observing_shot")

    def sample_shot(self, now):
        counts = self.counts()
        current = self.current
        for key in ("fractured", "fragments", "activated", "scheduled", "powder", "impact"):
            current["max_"+key] = max(current["max_"+key], counts[key])
        current["impact_actor_paths"] = list(dict.fromkeys(
            current["impact_actor_paths"] + [item.get_path_name() for item in self.actors("impact")]))
        if abs(counts["time_dilation"]-.22) < .015:
            current["slow_motion_seen"] = True
            current.setdefault("first_slow_motion_after_shot", round(now-self.shot_at, 4))
            current.setdefault("first_slow_motion_world_undilated_seconds", counts["world_undilated_seconds"])
        if current["slow_motion_seen"] and abs(counts["time_dilation"]-1) < .01:
            current["normal_speed_restored"] = True
            current.setdefault("normal_speed_after_shot", round(now-self.shot_at, 4))
            current.setdefault("normal_speed_world_undilated_seconds", counts["world_undilated_seconds"])
            current["slow_motion_duration_wall_seconds"] = (
                current["normal_speed_after_shot"]-current["first_slow_motion_after_shot"])
            current["slow_motion_duration_world_undilated_seconds"] = (
                current["normal_speed_world_undilated_seconds"]-current["first_slow_motion_world_undilated_seconds"])
        projectile = next((item for item in self.projectiles()
                           if item.get_path_name() == current["projectile_path"]), None)
        did_hit = False
        if _alive(projectile):
            did_hit = bool(projectile.get_editor_property("DidHit"))
            if did_hit:
                current.setdefault("first_observed_projectile_impact_location_cm",
                                   _xyz(projectile.get_editor_property("ImpactLocation")))
            hit_actors = list(projectile.get_editor_property("HitActors"))
            paths = [actor.get_path_name() if _alive(actor) else str(actor) for actor in hit_actors]
            current["duplicate_hit_observed"] |= len(paths) != len(set(paths))
            current["max_hit_count"] = max(current["max_hit_count"], len(paths))
            current["hit_actor_paths"] = list(dict.fromkeys(current["hit_actor_paths"]+paths))
            current["last_projectile_location_cm"] = _xyz(projectile.get_actor_location())
        if self.first_hit_at is None and (did_hit or current["max_hit_count"] or counts["impact"]):
            self.first_hit_at = now
            self.first_hit_game_seconds = counts["game_seconds"]
            current["first_hit"] = dict(counts, seconds_after_shot=round(now-self.shot_at, 4),
                                        detection="First sampled DidHit, HitActors, or spawned impact")
        if now-self.last_sample_at >= .1:
            current["samples"].append(dict(counts, seconds_after_shot=round(now-self.shot_at, 4),
                                           effect_age_game_seconds=self.effect_game_age()))
            self.last_sample_at = now
        return counts

    def complete_round(self, counts):
        current = self.current
        current["final_counts"] = counts
        current["duration_real_seconds"] = round(time.perf_counter()-self.shot_at, 3)
        impacts = len(current["impact_actor_paths"])
        if current["name"] == "rocket_miss":
            self.check("miss_zero_damage", current["max_hit_count"] == 0
                       and current["max_fractured"] == 0 and current["max_fragments"] == 0
                       and current["max_activated"] == 0 and current["max_scheduled"] == 0,
                       max_hit_count=current["max_hit_count"], max_fractured=current["max_fractured"],
                       max_fragments=current["max_fragments"])
            self.check("miss_zero_impact_or_powder", impacts == 0 and current["max_powder"] == 0,
                       unique_impacts=impacts, max_powder=current["max_powder"])
            self.check("miss_normal_speed", not current["slow_motion_seen"]
                       and abs(counts["time_dilation"]-1) < .01, time_dilation=counts["time_dilation"])
        else:
            self.check("actual_hit_and_fracture", bool(self.first_hit_at)
                       and current["max_hit_count"] > 0 and current["max_fractured"] > 0,
                       hit_count=current["max_hit_count"], fractured=current["max_fractured"])
            self.check("one_impact_per_projectile", impacts == 1 and current["max_impact"] == 1,
                       unique_impacts=impacts, max_concurrent_impacts=current["max_impact"],
                       actor_paths=current["impact_actor_paths"])
            self.check("slow_motion_022_then_restored", current["slow_motion_seen"]
                       and current["normal_speed_restored"] and abs(counts["time_dilation"]-1) < .01,
                       slow_seen=current["slow_motion_seen"], restored=current["normal_speed_restored"],
                       final_time_dilation=counts["time_dilation"])
            self.check("hit_actor_deduplication", not current["duplicate_hit_observed"])
            if current.get("capture", True):
                self.check("all_five_captures_completed", self.capture_index == len(CAPTURE_OFFSETS)
                           and self.pending_capture is None)
            else:
                duration = current.get("slow_motion_duration_world_undilated_seconds")
                self.check("slow_motion_duration_without_capture", duration is not None and .9 <= duration <= 1.65
                           and self.capture_index == 0,
                           world_undilated_seconds=duration,
                           wall_seconds=current.get("slow_motion_duration_wall_seconds"),
                           expected_world_undilated_seconds=1.2, tolerance_seconds=.45,
                           screenshot_count=self.capture_index)
        self.levels.editor_request_end_play()
        self.set_stage("ending_round")

    def finish(self, error=None):
        if error:
            self.report["error"] = error
            unreal.log_error("SKYFALL_EXPLOSION_STYLE_ERROR " + error)
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
        self.next_poll = now+.02
        try:
            self.poll_capture(now)
            self.advance(now)
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
        # Reserve five seconds for asynchronous EndPIE within the 180s budget.
        if now-self.started >= 175 and self.stage != "finishing":
            self.finish("180 second budget reached; reserving cleanup time")
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
                self.current["viewport_focus"] = self.focus_game_viewport()
                self.set_stage("idle")
        elif self.stage == "idle":
            if now-self.stage_started < 1.2:
                return
            counts = self.counts()
            self.current["initial_counts"] = counts
            self.check("independent_clean_pie", counts["pieces"] > 0 and counts["activated"] == 0
                       and counts["fractured"] == 0 and counts["fragments"] == 0
                       and counts["projectiles"] == 0 and counts["impact"] == 0
                       and counts["powder"] == 0 and abs(counts["time_dilation"]-1) < .01, **counts)
            self.current["initial_camera"] = self.camera_observation()
            self.fire()
        elif self.stage == "observing_shot":
            counts = self.sample_shot(now)
            if self.current["name"] == "rocket_miss":
                if now-self.shot_at >= 9:
                    self.complete_round(counts)
                return
            if self.first_hit_at is None:
                if now-self.shot_at > 6:
                    self.check("hit_timeout", False, last_location=self.current.get("last_projectile_location_cm"))
                    self.complete_round(counts)
                return
            elapsed = now-self.first_hit_at
            game_elapsed = counts["game_seconds"]-self.first_hit_game_seconds
            effect_age = self.effect_game_age()
            capture = self.current.get("capture", True)
            if not capture and "first_impact_diagnostics" not in self.current:
                self.current["first_impact_diagnostics"] = self.impact_diagnostics()
            if capture and self.capture_index < len(CAPTURE_OFFSETS) and self.pending_capture is None:
                game_offset, real_offset, phase = CAPTURE_OFFSETS[self.capture_index]
                if effect_age >= game_offset:
                    self.request_capture(game_offset, real_offset, phase)
                    self.capture_index += 1
            captures_done = not capture or (self.capture_index == len(CAPTURE_OFFSETS)
                                            and self.pending_capture is None)
            if captures_done and game_elapsed >= 3.0 and abs(counts["time_dilation"]-1) < .01:
                self.complete_round(counts)
            elif elapsed >= 45:
                self.check("simulation_progress_timeout", False, game_elapsed=game_elapsed,
                           wall_elapsed=elapsed, time_dilation=counts["time_dilation"])
                if self.pending_capture is not None:
                    self.finish("Simulation progress timeout while capture was pending")
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
                self.report["cleanup_error"] = "EndPIE requested but not confirmed within cleanup deadline"
            self.report.update(status=self.outcome, pie_stopped=stopped, editor_open=True)
            self.set_stage("complete")
            self.stop_callback()
            unreal.log("SKYFALL_EXPLOSION_STYLE_" + self.outcome.upper())


previous = getattr(unreal, "_skyfall_explosion_style_runner", None)
if previous is not None:
    previous.stop_callback()
runner = ExplosionStyleChecks()
unreal._skyfall_explosion_style_runner = runner
runner.start()
