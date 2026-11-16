"""Read-only PIE regression for visible meteor embed/pierce and separate exits.

Run with the full editor's py ExecCmds. The proven original capture/PIE helpers
are loaded as definitions only; its autorunner never executes. No assets/maps
are saved. New fields are optional to read, so old builds fail behavior checks
with useful evidence rather than aborting on an unknown property.
"""
from pathlib import Path

_base_path = Path(__file__).with_name("runtime_meteor.py")
_base_source = _base_path.read_text(encoding="utf-8")
_base_marker = "\nprevious = getattr"
if _base_marker not in _base_source:
    raise RuntimeError("Original runtime helper autorunner boundary was not found")
exec(compile(_base_source.split(_base_marker, 1)[0], str(_base_path), "exec"), globals())

REPORT_PATH = PROJECT / "Saved" / "runtime-meteor-v2.json"
IMAGE_DIR = WORKSPACE / "QA" / "skyfall-meteor-v2"
ROUNDS = (
    {"name": "meteor_miss55", "mode": 1, "angle": 55.0, "target_x": 2600, "target_y": 0, "miss": True},
    {"name": "meteor_vertical90", "mode": 1, "angle": 90.0, "target_x": 0, "target_y": 0,
     "embed": True, "capture": True},
    {"name": "meteor_oblique40", "mode": 1, "angle": 40.0, "target_x": 0, "target_y": 0,
     "embed": False, "capture": False, "scope": "Exact continuous-flight and exit behavior without screenshots"},
    {"name": "meteor_oblique40_capture", "mode": 1, "angle": 40.0, "target_x": 0, "target_y": 0,
     "embed": False, "capture": True, "capture_only": True,
     "scope": "Visual phases, effect counts, building damage, and live Niagara particles"},
    {"name": "rocket_regression", "mode": 0},
)
AFTER_HIT_CAPTURES = ((.25, "entry_peak"), (2.5, "collapse"), (6.0, "lower_floors"), (8.2, "ruins"))


def safeprop(actor, name, default=None):
    try:
        return actor.get_editor_property(name) if alive(actor) else default
    except Exception:
        return default


def safevector(actor, name):
    vector = safeprop(actor, name)
    return xyz(vector) if vector is not None else None


def length(vector):
    return math.sqrt(sum(value*value for value in vector))


def dot(first, second):
    return sum(a*b for a, b in zip(first, second))


class MeteorV2Checks(MeteorChecks):
    def __init__(self):
        super().__init__()
        self.report.update(
            suite="meteor-v2-embed-and-pierce",
            expectations={"angle_range_degrees": [30, 90], "default_angle_degrees": 40,
                "embed_angle_threshold_degrees": 78, "embed_depth_cm": 850,
                "embed_hold_game_seconds": 1.0, "entry_velocity_fraction_embed": .25,
                "entry_velocity_fraction_pierce": .7, "post_hit_gravity": 0,
                "contact_pressure_radius_cm": 600, "cascade_horizontal_radius_cm": 1700,
                "original_building_bounds_source": "Union of live PIE SkyfallPiece actor bounds before firing",
                "exit_bounds_expansion_cm": 180, "exit_ground_height_cm": 190,
                "exit_effect_forward_offset_cm": 130},
            limitations=["Input event clamp values require source/asset inspection; this suite assigns angles directly.",
                "PNG completion and Niagara counts are technical evidence; a human must inspect scale, readability, and framing.",
                "HighResShot can skip intermediate game-state observations; exact oblique exit checks run in a separate fresh world without captures."],
            capture_timing_evidence={"source": "Second meteor-v2 runtime attempt",
                "last_observed_before_exit_game_seconds": 3.30867,
                "next_observation_game_seconds": 3.55153,
                "gap_game_seconds": .24286,
                "observed_exit_effect_count": 1, "observed_entry_to_exit_cm": 1838.86,
                "observation": "Capture gap spanned the side exit and subsequent ground cleanup, leaving no live projectile exit snapshot.",
                "resolution": "Independent no-capture behavior round retains every production threshold; separate capture round verifies visuals and counts."},
        )
        self.report["timing"].update(overall_limit_real_seconds=300,
            round_limit_real_seconds=75, meteor_end_game_seconds_after_hit=8.5,
            phase_capture_game_offsets=[item[0] for item in AFTER_HIT_CAPTURES])

    def load_classes(self):
        super().load_classes()
        asset = unreal.load_asset(METEOR + "BP_MeteorExit")
        self.classes["meteor_exit"] = asset.generated_class() if asset else None
        self.check("separate_exit_effect_class_available", bool(self.classes["meteor_exit"]),
                   asset=METEOR + "BP_MeteorExit")

    def actors(self, key):
        if not self.classes.get(key):
            return []
        return super().actors(key)

    def counts(self):
        result = super().counts()
        result["meteor_exit"] = len(self.actors("meteor_exit"))
        return result

    def begin_round(self):
        super().begin_round()
        if self.stage == "finishing":
            return
        self.current.update(body_samples=[], exit_paths=[], max_meteor_exit=0,
                            event_captures=[], effect_observations=[])

    def effect_diagnostics(self):
        result = super().effect_diagnostics()
        # Every capture retains emitter counts and particle extents. Actor bounds
        # alone do not measure the rendered cloud/flash size.
        for key in ("meteor_impact", "meteor_exit"):
            for actor in self.actors(key):
                component = safeprop(actor, "niagara_component")
                observation = {"kind": key, "actor": actor.get_path_name(),
                    "location_cm": xyz(actor.get_actor_location()),
                    "age_game_seconds": float(actor.get_game_time_since_creation()),
                    "particle_extent_diagnostic": True}
                try:
                    observation.update(active=bool(component.is_active()), visible=bool(component.is_visible()))
                    cache = unreal.NiagaraSimCacheFunctionLibrary.create_niagara_sim_cache(self.world)
                    captured = unreal.NiagaraSimCacheFunctionLibrary.capture_niagara_sim_cache_immediate(
                        cache, unreal.NiagaraSimCacheCreateParameters(), component, False)
                    success = bool(captured[0]) if isinstance(captured, tuple) else bool(captured)
                    if isinstance(captured, tuple) and len(captured) > 1 and captured[1]:
                        cache = captured[1]
                    observation["particle_capture_success"] = success
                    if success and cache.get_num_frames() > 0:
                        emitters = {}
                        for name in cache.get_emitter_names():
                            positions = cache.read_position_attribute("Position", name, True, 0)
                            points = [xyz(item) for item in positions]
                            emitter = {"count": len(points)}
                            if points:
                                low = [min(point[index] for point in points) for index in range(3)]
                                high = [max(point[index] for point in points) for index in range(3)]
                                emitter.update(position_min_cm=low, position_max_cm=high,
                                               position_extent_cm=[b-a for a, b in zip(low, high)])
                            emitters[str(name)] = emitter
                        observation["emitters"] = emitters
                except Exception as error:
                    observation["particle_capture_error"] = str(error)
                result.append(observation)
        return result

    def fire(self):
        controller = self.controller()
        meteor = self.current["mode"] == 1
        original_bounds = []
        for piece in self.pieces:
            if alive(piece):
                centre, extent = piece.get_actor_bounds(False)
                original_bounds.append((xyz(centre), xyz(extent)))
        if not original_bounds:
            raise RuntimeError("Cannot measure the original building envelope without live pieces")
        low = [min(centre[index]-extent[index] for centre, extent in original_bounds) for index in range(3)]
        high = [max(centre[index]+extent[index] for centre, extent in original_bounds) for index in range(3)]
        centre = [(a+b)*.5 for a, b in zip(low, high)]
        extent = [(b-a)*.5 for a, b in zip(low, high)]
        self.current["original_building_bounds"] = {"min_cm": low, "max_cm": high,
            "center_cm": centre, "extent_cm": extent, "piece_count": len(original_bounds)}
        self.current["controller_initial_angle"] = safeprop(controller, "MeteorAngle")
        if self.round_index == 0:
            self.check("controller_default_angle40", self.current["controller_initial_angle"] == 40,
                       actual=self.current["controller_initial_angle"])
        angle = self.current.get("angle", 55.0)
        values = {"WeaponMode": self.current["mode"], "YawDegrees": 0.0, "PitchDegrees": 0.0,
            "LaunchHeight": 900.0, "LaunchSpeed": 4200.0 if meteor else 10000.0,
            "GravityZ": -1600.0 if meteor else 0.0, "ProjectileRadius": 170.0 if meteor else 55.0,
            "MeteorTargetX": float(self.current.get("target_x", 0)),
            "MeteorTargetY": float(self.current.get("target_y", 0)), "MeteorAngle": float(angle)}
        for key, value in values.items():
            controller.set_editor_property(key, value)
        if meteor:
            pitch, yaw = math.radians(-angle), math.radians(-60)
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
        if meteor:
            fields = ("MeteorEmbedMode", "MeteorEmbedded", "MeteorExited", "FirstImpactLocation", "EntryDirection",
                      "MeteorBuildingCenter", "MeteorBuildingExtent")
            self.current["missing_v2_properties"] = [name for name in fields if safeprop(self.projectile, name) is None]
            self.check("projectile_v2_state_available", not self.current["missing_v2_properties"],
                       missing=self.current["missing_v2_properties"])
            mode = safeprop(self.projectile, "MeteorEmbedMode")
            self.check("native_fire_sets_angle_mode", mode is not None and bool(mode) == (angle >= 78),
                       angle=angle, actual_embed_mode=mode, expected_embed_mode=angle >= 78)
            for field, expected in (("MeteorBuildingCenter", centre), ("MeteorBuildingExtent", extent)):
                controller_value = safevector(controller, field)
                projectile_value = safevector(self.projectile, field)
                self.check("native_fire_copies_"+field, controller_value is not None and projectile_value is not None
                    and distance(controller_value, expected) < 2 and distance(projectile_value, expected) < 2,
                    expected_from_original_pieces_cm=expected, controller_cm=controller_value,
                    projectile_cm=projectile_value)
        self.shot_at = time.perf_counter()
        self.shot_game = float(unreal.GameplayStatics.get_time_seconds(self.world))
        self.set_stage("observing_shot")

    def body_observation(self, counts):
        actor = self.projectile
        observation = {"alive": alive(actor), "game_seconds": counts["game_seconds"],
            "game_seconds_after_shot": counts["game_seconds"]-self.shot_game,
            "game_seconds_after_hit": self.hit_age()}
        if not alive(actor):
            return observation
        observation.update(position_cm=xyz(actor.get_actor_location()),
            age_game_seconds=float(actor.get_game_time_since_creation()),
            hidden=safeprop(actor, "hidden"), tick_enabled=bool(actor.is_actor_tick_enabled()),
            did_hit=safeprop(actor, "DidHit"), embed_mode=safeprop(actor, "MeteorEmbedMode"),
            embedded=safeprop(actor, "MeteorEmbedded"), exited=safeprop(actor, "MeteorExited"),
            velocity_cm_s=safevector(actor, "ShotVelocity"), gravity_z=safeprop(actor, "GravityZ"),
            first_impact_cm=safevector(actor, "FirstImpactLocation"),
            latest_contact_cm=safevector(actor, "ImpactLocation"),
            entry_direction=safevector(actor, "EntryDirection"),
            hit_count=len(safeprop(actor, "HitActors", [])))
        meshes = actor.get_components_by_class(unreal.StaticMeshComponent)
        observation["meshes"] = []
        for mesh in meshes:
            observation["meshes"].append({"name": mesh.get_name(), "visible": bool(mesh.is_visible()),
                "simulating_physics": bool(mesh.is_simulating_physics()),
                "position_cm": xyz(mesh.get_world_location()),
                "physics_velocity_cm_s": xyz(mesh.get_physics_linear_velocity())})
        observation["physics"] = any(mesh["simulating_physics"] for mesh in observation["meshes"])
        observation["body_visible"] = observation["hidden"] is False and any(
            mesh["visible"] for mesh in observation["meshes"])
        if observation["first_impact_cm"] is not None:
            travel = [a-b for a, b in zip(observation["position_cm"], observation["first_impact_cm"])]
            observation["distance_from_first_contact_cm"] = length(travel)
            if observation["entry_direction"] is not None:
                observation["depth_along_entry_cm"] = dot(travel, observation["entry_direction"])
        return observation

    def sample(self, now):
        counts = super().sample(now)
        current = self.current
        current["max_meteor_exit"] = max(current["max_meteor_exit"], counts["meteor_exit"])
        exits = self.actors("meteor_exit")
        current["exit_paths"] = list(dict.fromkeys(current["exit_paths"] + [actor.get_path_name() for actor in exits]))
        if current["mode"] != 1:
            return counts
        observation = self.body_observation(counts)
        current["body_samples"].append(observation)
        if observation.get("did_hit"):
            current.setdefault("first_body_hit", dict(observation))
            if observation.get("embedded"):
                current.setdefault("first_embedded", dict(observation))
            if observation.get("physics"):
                current.setdefault("first_physics", dict(observation))
            if observation.get("exited"):
                current.setdefault("first_exit", dict(observation))
        if exits:
            current.setdefault("first_exit_effect", {"game_seconds": counts["game_seconds"],
                "game_seconds_after_hit": self.hit_age(),
                "locations_cm": [xyz(actor.get_actor_location()) for actor in exits],
                "latest_contact_cm": observation.get("latest_contact_cm"),
                "entry_direction": observation.get("entry_direction")})
        if not observation["alive"]:
            current.setdefault("projectile_destroyed_game_seconds", counts["game_seconds"])
        return counts

    def check_embed(self):
        current = self.current
        post = [item for item in current["body_samples"] if item.get("did_hit")]
        moving = [item for item in post if not item.get("embedded") and not item.get("physics")]
        lodged = [item for item in post if item.get("embedded") and not item.get("physics")]
        physical = [item for item in post if item.get("physics")]
        self.check("embed_visible_moving_after_contact", bool(moving) and
                   all(item.get("body_visible") and item.get("tick_enabled") for item in moving),
                   observation_count=len(moving), first=moving[0] if moving else None)
        depths = [item["distance_from_first_contact_cm"] for item in lodged
                  if "distance_from_first_contact_cm" in item]
        self.check("embed_lodges_850cm_past_first_contact", bool(depths) and all(
                   abs(depth-850) <= 60 for depth in depths), depths_cm=sorted(set(round(value, 2) for value in depths)),
                   tolerance_cm=60)
        drift = max((distance(item["position_cm"], lodged[0]["position_cm"]) for item in lodged), default=None)
        self.check("embedded_body_visible_and_still", len(lodged) >= 3 and drift is not None and drift < 5
                   and all(item.get("body_visible") and not item.get("tick_enabled") for item in lodged),
                   samples=len(lodged), max_drift_cm=drift)
        if lodged and physical:
            observed_hold = physical[0]["game_seconds"]-lodged[0]["game_seconds"]
        else:
            observed_hold = None
        self.check("embed_holds_one_game_second_then_physics", observed_hold is not None and
                   .85 <= observed_hold <= 1.2 and physical[0].get("body_visible"),
                   observed_hold_game_seconds=observed_hold, expected=1.0,
                   first_physics=physical[0] if physical else None)
        fall = 0.0
        if physical:
            def mesh_z(item):
                return min((mesh["position_cm"][2] for mesh in item["meshes"] if mesh["simulating_physics"]),
                           default=item["position_cm"][2])
            fall = mesh_z(physical[0])-min(mesh_z(item) for item in physical)
        self.check("embedded_body_falls_as_physical_debris", bool(physical) and fall > 100,
                   observed_drop_cm=fall, physics_samples=len(physical))
        self.check("embedded_has_no_exit_burst", len(current["exit_paths"]) == 0 and
                   not any(item.get("exited") for item in post), paths=current["exit_paths"])
        self.check("embedded_persists_past_pierce_lifetime", alive(self.projectile),
                   current_hit_age=self.hit_age(), projectile_alive=alive(self.projectile))

    def check_pierce(self):
        current = self.current
        post = [item for item in current["body_samples"] if item.get("did_hit")]
        drift = max((distance(item["position_cm"], post[0]["position_cm"]) for item in post), default=0)
        self.check("oblique_pierces_continuously_visible", len(post) >= 3 and drift > 1200
                   and all(item.get("body_visible") and item.get("tick_enabled") and
                           not item.get("embedded") and not item.get("physics") for item in post),
                   observations=len(post), post_contact_displacement_cm=drift)
        directions = [dot(item["velocity_cm_s"], item["entry_direction"])/max(1, length(item["velocity_cm_s"]))
                      for item in post if item.get("entry_direction") is not None and item.get("velocity_cm_s")]
        self.check("oblique_preserves_entry_direction", bool(directions) and min(directions) > .995,
                   minimum_alignment=min(directions) if directions else None)
        exit_state = current.get("first_exit")
        self.check("oblique_has_one_separate_exit_burst", len(current["exit_paths"]) == 1 and
                   exit_state is not None and current["max_meteor_exit"] == 1,
                   paths=current["exit_paths"], first_exit=exit_state)
        if exit_state:
            position = exit_state["position_cm"]
            z = position[2]
            envelope = current["original_building_bounds"]
            expanded_low = [value-180 for value in envelope["min_cm"]]
            expanded_high = [value+180 for value in envelope["max_cm"]]
            outside = any(value < low or value > high for value, low, high in
                          zip(position, expanded_low, expanded_high))
            self.check("exit_crosses_original_envelope_above_ground", outside and z > 190,
                       position_cm=position, above_ground_cm=z,
                       expanded_original_bounds_cm=[expanded_low, expanded_high])
            after_exit = [item for item in post if item["game_seconds"] >= exit_state["game_seconds"]]
            self.check("no_new_damage_after_exit", bool(after_exit) and len(
                {item["hit_count"] for item in after_exit}) == 1,
                observed_hit_counts=sorted({item["hit_count"] for item in after_exit}))
        else:
            self.check("exit_crosses_original_envelope_above_ground", False, reason="Exit state never observed")
            self.check("no_new_damage_after_exit", False, reason="Exit state never observed")
        effect = current.get("first_exit_effect")
        separation = None
        contact_error = None
        expected_exit = None
        if effect and effect.get("locations_cm") and current.get("first_body_hit"):
            first_contact = current["first_body_hit"].get("first_impact_cm")
            if first_contact:
                separation = distance(effect["locations_cm"][0], first_contact)
            if effect.get("latest_contact_cm") is not None and effect.get("entry_direction") is not None:
                expected_exit = [point+direction*130 for point, direction in
                    zip(effect["latest_contact_cm"], effect["entry_direction"])]
                expected_exit[2] = max(190, expected_exit[2])
                contact_error = distance(effect["locations_cm"][0], expected_exit)
        self.check("exit_is_separate_at_last_actual_contact", separation is not None and separation > 400 and
                   contact_error is not None and contact_error < 5,
                   entry_to_exit_distance_cm=separation, exit_to_expected_position_cm=contact_error,
                   expected_exit_position_cm=expected_exit, forward_visual_offset_cm=130,
                   first_exit_effect=effect)
        self.check("piercing_body_cleans_up", not alive(self.projectile),
                   projectile_alive=alive(self.projectile), destroyed_game_seconds=current.get("projectile_destroyed_game_seconds"))

    def complete_round(self, counts):
        current = self.current
        current["final_counts"] = counts
        current["activation_by_floor"] = self.floor_observations()
        samples = current["flight_samples"]
        maximum = max((sample["error_cm"] for sample in samples), default=None)
        if not current.get("capture_only"):
            self.check("pre_hit_ballistic_motion", len(samples) >= 2 and maximum is not None and maximum < 40,
                       sample_count=len(samples), max_error_cm=maximum, tolerance_cm=40)
        body_samples = current["body_samples"]
        current["sampling_diagnostics"] = {
            "body_observation_count": len(body_samples),
            "maximum_game_seconds_between_body_observations": max((second["game_seconds"]-first["game_seconds"]
                for first, second in zip(body_samples, body_samples[1:])), default=None),
            "exact_exit_state_assertions_enabled": current["mode"] == 1 and not current.get("embed")
                and not current.get("miss") and not current.get("capture_only"),
            "screenshots_enabled": bool(current.get("capture"))}
        self.check("hit_deduplication", not current["duplicate_hits"])
        if current.get("miss"):
            self.check("miss_zero_building_damage", all(current["max_"+key] == 0 for key in
                ("hits", "activated", "fractured", "scheduled", "fragments")),
                hits=current["max_hits"], fractured=current["max_fractured"], scheduled=current["max_scheduled"])
            self.check("miss_zero_building_effects", all(current["max_"+key] == 0 for key in
                ("meteor_impact", "meteor_exit", "rocket_impact", "powder")))
            self.check("miss_normal_speed", not current["slow_seen"] and abs(counts["time_dilation"]-1) < .01)
        else:
            self.check("real_contact_causes_fracture", self.first_hit_at is not None and current["max_hits"] > 0
                       and current["max_fractured"] > 0, hits=current["max_hits"], fractured=current["max_fractured"])
            self.check("one_main_entry_blast", len(current["impact_paths"]) == 1, paths=current["impact_paths"])
            self.check("slow_motion_restored", current["slow_seen"] and current["normal_restored"]
                       and abs(counts["time_dilation"]-1) < .01)
            if current["mode"] == 1:
                self.check("meteor_effect_distinct_from_rocket", current["max_meteor_impact"] == 1
                           and current["max_rocket_impact"] == 0)
                if not current.get("capture_only"):
                    contact = current.get("first_body_hit", {}).get("first_impact_cm")
                    self.check("meteor_first_contact_on_upper_building", contact is not None and contact[2] > 1900,
                               first_contact_cm=contact)
                    post = [item for item in current["body_samples"] if item.get("did_hit") and not item.get("physics")]
                    self.check("post_contact_gravity_zero", bool(post) and all(item.get("gravity_z") == 0 for item in post))
                    first = current.get("first_body_hit")
                    speed_ratio = None
                    if first and first.get("velocity_cm_s"):
                        initial = list(current["initial_velocity"])
                        initial[2] += current["gravity"]*first["age_game_seconds"]
                        speed_ratio = length(first["velocity_cm_s"])/max(1, length(initial))
                    expected_ratio = .25 if current.get("embed") else .7
                    self.check("entry_velocity_reduced_once", speed_ratio is not None and abs(speed_ratio-expected_ratio) < .035,
                               measured_fraction=speed_ratio, expected_fraction=expected_ratio)
                    self.check_embed() if current.get("embed") else self.check_pierce()
                    if not current.get("capture"):
                        duration = current.get("slow_duration_undilated")
                        self.check("slow_duration_without_capture", duration is not None and .85 <= duration <= 1.65,
                                   world_undilated_seconds=duration)
                else:
                    self.check("visual_round_one_separate_exit_burst", len(current["exit_paths"]) == 1
                               and current["max_meteor_exit"] == 1, paths=current["exit_paths"])
                    entry = current.get("first_body_hit", {}).get("first_impact_cm")
                    effect = current.get("first_exit_effect", {})
                    locations = effect.get("locations_cm", [])
                    separation = distance(entry, locations[0]) if entry is not None and locations else None
                    self.check("visual_exit_separated_from_entry", separation is not None and separation > 400,
                               entry_to_exit_distance_cm=separation, exit_locations_cm=locations)
                self.check("meteor_tail_cleared", current["max_meteor_tail"] >= 1 and counts["meteor_tail"] == 0)
                floors = sorted((int(key.split("_")[-1]), value) for key, value in
                    current["activation_by_floor"].items() if value["activated_members"] >= 3)
                ordered = len(floors) >= 3 and all(high["median_game_seconds"] <= low["median_game_seconds"]+.35
                    for (_, low), (_, high) in zip(floors, floors[1:]))
                span = floors[0][1]["median_game_seconds"]-floors[-1][1]["median_game_seconds"] if floors else 0
                self.check("floor_collapse_progresses_downwards", ordered and span > .5,
                    active_floor_count=len(floors), bottom_minus_top_game_seconds=span,
                    floor_observations=current["activation_by_floor"])
                if current.get("capture"):
                    expected_event = "embedded_hold" if current.get("embed") else "exit"
                    self.check("all_visual_phase_captures", self.incoming_requested and
                        self.capture_index == len(AFTER_HIT_CAPTURES) and expected_event in current["event_captures"]
                        and self.pending_capture is None, event_captures=current["event_captures"])
                    peak = self.report["screenshots"].get(current["name"]+"_entry_peak", {})
                    peak_age = peak.get("actual_game_age_after_hit")
                    peak_particles = sum(sum(item.get("emitter_particles", {}).values())
                        for item in peak.get("effects", []) if item.get("kind") == "meteor_impact")
                    self.check("entry_peak_capture_has_live_particles", peak.get("status") == "captured" and
                        peak_age is not None and .20 <= peak_age <= .45 and peak_particles > 0,
                        actual_game_age_after_hit=peak_age, live_particles=peak_particles)
                    if not current.get("embed"):
                        exit_image = self.report["screenshots"].get(current["name"]+"_exit", {})
                        exit_particles = sum(sum(emitter["count"] for emitter in item.get("emitters", {}).values())
                            for item in exit_image.get("effects", []) if item.get("kind") == "meteor_exit")
                        self.check("exit_capture_has_live_particles", exit_image.get("status") == "captured" and
                            exit_particles > 0, live_particles=exit_particles)
            else:
                self.check("rocket_effect_preserved", current["max_rocket_impact"] == 1
                           and current["max_meteor_impact"] == 0 and current["max_meteor_exit"] == 0)
                duration = current.get("slow_duration_undilated")
                self.check("rocket_slow_duration_preserved", duration is not None and .85 <= duration <= 1.65,
                           world_undilated_seconds=duration)
        self.levels.editor_request_end_play()
        self.set_stage("ending_round")

    def advance(self, now):
        if now-self.started >= 295 and self.stage != "finishing":
            self.finish("300 second total budget reached; reserving cleanup time")
            return
        if self.stage == "stopping_existing_pie":
            if not self.levels.is_in_play_in_editor() and not self.game_world():
                self.set_stage("warmup")
            return
        if self.stage == "warmup":
            if now-self.stage_started >= 12:
                self.load_classes()
                self.begin_round()
            return
        if self.stage == "waiting_for_pie":
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
            return
        if self.stage == "idle":
            if now-self.stage_started < 1.2:
                return
            counts = self.counts()
            self.current["initial_counts"] = counts
            self.check("fresh_pie_clean_building", counts["pieces"] > 0 and all(counts[key] == 0 for key in
                ("activated", "fractured", "scheduled", "fragments", "powder", "projectiles",
                 "meteor_impact", "meteor_exit", "rocket_impact")) and abs(counts["time_dilation"]-1) < .01,
                 **counts)
            self.fire()
            return
        if self.stage == "ending_round":
            if not self.levels.is_in_play_in_editor() and not self.game_world():
                self.world = None
                self.begin_round()
            elif now-self.stage_started > 12:
                raise TimeoutError("PIE did not stop between rounds")
            return
        if self.stage == "finishing":
            stopped = not self.levels.is_in_play_in_editor() and not self.game_world()
            if not stopped and now-self.started < 300 and now-self.stage_started < 5:
                return
            if not stopped:
                self.outcome = "failed"
                self.report["cleanup_error"] = "EndPIE requested but not confirmed"
            self.report.update(status=self.outcome, pie_stopped=stopped, editor_open=True)
            self.set_stage("complete")
            self.stop_callback()
            unreal.log("SKYFALL_METEOR_V2_" + self.outcome.upper())
            return
        if self.stage != "observing_shot":
            return
        counts = self.sample(now)
        current = self.current
        shot_age = counts["game_seconds"]-self.shot_game
        prefix = current["name"]
        if current.get("capture") and not self.incoming_requested and shot_age >= .55:
            if self.first_hit_at is None and self.pending_capture is None:
                self.request_capture(prefix+"_incoming", .55)
                self.incoming_requested = True
            elif self.first_hit_at is not None:
                self.check("incoming_capture_before_contact", False, shot_age_game_seconds=shot_age)
                self.incoming_requested = True
        if current.get("miss"):
            if shot_age >= 8.5:
                self.complete_round(counts)
            elif now-self.shot_at > 65:
                raise TimeoutError("Miss round did not advance 8.5 game seconds")
            return
        if self.first_hit_at is None:
            if shot_age > 4 or now-self.shot_at > 20:
                self.check("hit_timeout", False, shot_age_game_seconds=shot_age)
                self.complete_round(counts)
            return
        hit_age = self.hit_age()
        if current.get("capture") and self.pending_capture is None:
            event = None
            if current.get("embed") and "first_embedded" in current and "embedded_hold" not in current["event_captures"]:
                if counts["game_seconds"]-current["first_embedded"]["game_seconds"] >= .35:
                    event = "embedded_hold"
            elif not current.get("embed") and current.get("first_exit_effect") and "exit" not in current["event_captures"]:
                event = "exit"
            if event:
                self.request_capture(prefix+"_"+event, hit_age)
                current["event_captures"].append(event)
            elif self.capture_index < len(AFTER_HIT_CAPTURES):
                offset, name = AFTER_HIT_CAPTURES[self.capture_index]
                if hit_age >= offset:
                    self.request_capture(prefix+"_"+name, offset)
                    self.capture_index += 1
        target_age = 8.5 if current["mode"] == 1 else 3.0
        captures_done = not current.get("capture") or self.capture_index == len(AFTER_HIT_CAPTURES)
        if hit_age >= target_age and captures_done and self.pending_capture is None and abs(counts["time_dilation"]-1) < .01:
            self.complete_round(counts)
        elif now-self.shot_at > 75:
            self.check("round_progress_timeout", False, game_seconds_after_hit=hit_age,
                       wall_seconds_after_shot=now-self.shot_at)
            if self.pending_capture:
                self.finish("Round timeout with a screenshot still pending")
            else:
                self.complete_round(counts)


previous = getattr(unreal, "_skyfall_meteor_runner", None)
if previous is not None:
    previous.stop_callback()
runner = MeteorV2Checks()
unreal._skyfall_meteor_runner = runner
runner.start()
