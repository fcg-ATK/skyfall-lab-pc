"""Exercise the installed Chinese HUD in real PIE without rebuilding assets.

Run full UnrealEditor on DemolitionYard with -ExecCmds="py <absolute path>".
The previous HUD fails ui_action_available cleanly. Public HUD actions are
tested against the real combat/camera actors; physical clicking remains a
separate desktop UI check. Screenshots require visual review for font/layout.
PIE is stopped after restoring temporary state. The editor remains open.
"""
import json
import struct
import sys
import time
from pathlib import Path

import unreal

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tools"))
from runtime_free_camera import FreeCameraChecks, AIM_PROPERTIES, alive, angle_distance, xyz

REPORT_PATH = PROJECT / "Saved" / "runtime-chinese-ui.json"
IMAGE_DIR = PROJECT / "Saved" / "QA" / "skyfall-chinese-ui"
HUD_PATH = "/Game/Skyfall/UI/BP_SkyfallHUDV2"
CONTROLLER_PATH = "/Game/Skyfall/Blueprints/BP_SkyfallControllerV2"
OUTPUTS = ("WeaponText", "AimText", "ControlsText", "ActionText")
CAMERA_PROPERTIES = ("CameraYaw", "CameraPitch", "CameraOrbitRadius", "CameraOrbitFocus")
WEAPONS = (
    ("weapon_rocket", 0, "火箭", 10000.0, 0.0, 55.0),
    ("weapon_meteor", 1, "陨石", 4200.0, -1600.0, 170.0),
    ("weapon_ball", 2, "铁球", 7200.0, -980.0, 100.0),
)
CAPTURES = (
    ("meteor", "weapon_meteor", False, False, 1600, 900),
    ("rocket", "weapon_rocket", False, False, 1600, 900),
    ("ball", "weapon_ball", False, False, 1600, 900),
    ("help", "weapon_meteor", True, False, 1600, 900),
    ("minimal", "weapon_meteor", False, True, 1600, 900),
    ("meteor_1280", "weapon_meteor", False, False, 1280, 720),
)


def chinese(value):
    return any("\u3400" <= character <= "\u9fff" for character in str(value))


class ChineseUIChecks(FreeCameraChecks):
    def __init__(self):
        super().__init__()
        self.hud = None
        self.snapshot = self.camera_snapshot = self.hud_snapshot = None
        self.restored = False
        self.capture_index = 0
        self.report.update(
            suite="installed-native-chinese-ui",
            limitations=[
                "Public UIAction dispatch is tested; actual hitbox clicking and hover require desktop verification.",
                "ReadAimState verifies Chinese text data; glyph appearance and layout require screenshot review.",
                "Rebuild dispatch is not invoked because level reload would invalidate the test world; verify separately.",
                "Right mouse held input cannot be synthesized by this native action test.",
            ],
            state_samples=[], controller_restored=False, camera_restored=False,
            hud_restored=False, screenshots={},
        )

    def write(self):
        self.report.update(stage=self.stage,
                           elapsed_real_seconds=round(time.perf_counter()-self.started, 3))
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = REPORT_PATH.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(REPORT_PATH)

    def set_stage(self, stage):
        self.stage = stage
        self.stage_started = time.perf_counter()
        self.write()
        unreal.log("SKYFALL_CHINESE_UI_STAGE " + stage)

    def start(self):
        IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        command = unreal.SystemLibrary.get_command_line().lower()
        if "-run=" in command or "-run " in command:
            raise RuntimeError("Chinese UI checks require full-editor PIE, not a commandlet")
        if self.levels.is_in_play_in_editor() or self.game_world():
            self.levels.editor_request_end_play()
            self.set_stage("stopping_existing_pie")
        self.write()
        self.handle = unreal.register_slate_post_tick_callback(self.tick)
        unreal.log("SKYFALL_CHINESE_UI_STARTED")

    def action(self, action):
        return self.hud.call_method("UIAction", (unreal.Name(action),))

    def set_ui_state(self, help_open=False, minimal=False):
        """Use the real public UI path so modal input is synchronized as well."""
        if help_open and minimal:
            raise ValueError("Help and minimal UI are mutually exclusive states")
        if bool(self.hud.get_editor_property("HelpOpen")):
            self.action("help_close")
        if bool(self.hud.get_editor_property("MinimalUI")) != bool(minimal):
            self.action("minimal")
        if help_open:
            self.action("help")

    def state(self):
        values = self.hud.call_method("ReadAimState")
        if not isinstance(values, (tuple, list)) or len(values) != len(OUTPUTS):
            raise RuntimeError("Unexpected ReadAimState outputs: " + repr(values))
        return dict(zip(OUTPUTS, map(str, values)))

    def setup(self):
        self.player = unreal.GameplayStatics.get_player_controller(self.world, 0)
        self.hud = self.player.get_hud() if alive(self.player) else None
        hud_asset = unreal.load_asset(HUD_PATH)
        self.check("actual_player_owns_installed_hud", alive(self.hud) and bool(hud_asset)
                   and self.hud.get_class() == hud_asset.generated_class())
        if not alive(self.hud) or not hud_asset:
            self.finish()
            return False
        graphs = [str(graph.get_name()) for graph in unreal.BlueprintEditorLibrary.list_graphs(hud_asset)]
        available = "UIAction" in graphs
        self.check("ui_action_available", available, function_graphs=graphs)
        if not available:
            self.report["baseline_note"] = "The prior English HUD only displays text and has no Chinese UIAction interface."
            self.report["baseline_state"] = self.state()
            self.finish()
            return False
        controller_asset = unreal.load_asset(CONTROLLER_PATH)
        controllers = unreal.GameplayStatics.get_all_actors_of_class(self.world, controller_asset.generated_class())
        cameras = unreal.GameplayStatics.get_all_actors_with_tag(self.world, "SFFreeCamera")
        self.check("one_combat_controller", len(controllers) == 1, count=len(controllers))
        self.check("one_orbit_camera", len(cameras) == 1, count=len(cameras))
        if len(controllers) != 1 or len(cameras) != 1:
            self.finish()
            return False
        self.controller, self.camera = controllers[0], cameras[0]
        self.snapshot = {key: self.controller.get_editor_property(key) for key in AIM_PROPERTIES}
        self.camera_snapshot = {key: self.camera.get_editor_property(key) for key in CAMERA_PROPERTIES}
        self.hud_snapshot = {key: self.hud.get_editor_property(key) for key in ("HelpOpen", "MinimalUI")}
        self.initial = self.pose()
        self.report.update(player=self.player.get_path_name(), hud=self.hud.get_path_name(),
                           initial_aim=self.snapshot, initial_pose=self.initial,
                           initial_ui=self.hud_snapshot)
        self.check("hud_visible", bool(self.hud.get_editor_property("show_hud")))
        self.check("player_views_orbit_camera", self.player.get_view_target() == self.camera)
        self.check("mouse_cursor_visible", bool(self.player.get_editor_property("show_mouse_cursor")))
        self.check("mouse_click_events_enabled", bool(self.player.get_editor_property("enable_click_events")))
        self.set_ui_state()
        return True

    def controls_checks(self):
        for width, height, expected in ((1280,720,.8),(1600,900,1.0),
                                        (1920,1080,1.2),(1024,768,.64)):
            actual=float(self.hud.call_method('GetUIScale',(width,height)))
            self.check('fractional_ui_scale_'+str(width),abs(actual-expected)<.00001,
                       actual=actual,expected=expected)
        before_projectiles = len(unreal.GameplayStatics.get_all_actors_with_tag(self.world, "SkyfallProjectile"))
        before_fire = float(self.controller.get_editor_property("LastFireTime"))
        self.camera.call_method("StepOrbit", (90.0,))
        orbit_pose = self.pose()
        for action, mode, label, speed, gravity, radius in WEAPONS:
            self.action(action)
            actual = {name: self.controller.get_editor_property(name)
                      for name in ("WeaponMode", "LaunchSpeed", "GravityZ", "ProjectileRadius")}
            self.check(action+"_sets_complete_launch_profile",
                       actual["WeaponMode"] == mode and abs(actual["LaunchSpeed"]-speed) < .01
                       and abs(actual["GravityZ"]-gravity) < .01 and abs(actual["ProjectileRadius"]-radius) < .01,
                       actual=actual)
            state = self.state()
            self.report["state_samples"].append(dict(action=action, state=state))
            self.check(action+"_chinese_label", label in state["WeaponText"], state=state)
            self.check(action+"_all_state_fields_chinese", all(chinese(value) for value in state.values()))
            self.check(action+"_no_legacy_english_labels", not any(
                word in " ".join(state.values()) for word in ("WEAPON", "ROCKET", "METEOR", "BALL", "HEIGHT", "TARGET", "ENTRY")))
            self.controller.call_method("UpdateModeCamera")
            self.check(action+"_preserves_camera", self.same_pose(self.pose(), orbit_pose))

        self.action("weapon_meteor")
        for angle, label in ((30.0, "贯穿"), (77.9, "贯穿"), (78.0, "嵌入"), (90.0, "嵌入")):
            self.controller.set_editor_property("MeteorAngle", angle)
            state = self.state()
            self.check("meteor_mode_"+str(angle), label in state["WeaponText"], state=state)
        self.controller.set_editor_property("MeteorAngle", 40.0)

        settings = (
            ("weapon_rocket", "primary", "YawDegrees", 0.0, 3.0, -75.0, 75.0),
            ("weapon_rocket", "secondary", "LaunchHeight", 900.0, 150.0, 150.0, 3800.0),
            ("weapon_rocket", "angle", "PitchDegrees", 0.0, 3.0, -40.0, 60.0),
            ("weapon_meteor", "primary", "MeteorTargetX", 0.0, 150.0, -3500.0, 3500.0),
            ("weapon_meteor", "secondary", "MeteorTargetY", 0.0, 150.0, -3500.0, 3500.0),
            ("weapon_meteor", "angle", "MeteorAngle", 40.0, 3.0, 30.0, 90.0),
        )
        for weapon, prefix, prop, initial, step, low, high in settings:
            self.action(weapon)
            self.controller.set_editor_property(prop, initial)
            self.action(prefix+"_plus")
            value = float(self.controller.get_editor_property(prop))
            self.check(prop+"_increments", abs(value-initial-step) < .01, actual=value)
            self.action(prefix+"_minus")
            value = float(self.controller.get_editor_property(prop))
            self.check(prop+"_decrements", abs(value-initial) < .01, actual=value)
            for suffix, starting, expected in (("plus", high-step*.1, high), ("minus", low+step*.1, low)):
                self.controller.set_editor_property(prop, starting)
                self.action(prefix+"_"+suffix)
                value = float(self.controller.get_editor_property(prop))
                self.check(prop+"_clamps_"+suffix, abs(value-expected) < .01, actual=value, expected=expected)
            self.controller.set_editor_property(prop, initial)

        self.action("weapon_ball")
        self.controller.set_editor_property("YawDegrees", 0.0)
        self.action("primary_plus")
        self.check("ball_uses_standard_aim", abs(self.controller.get_editor_property("YawDegrees")-3.0) < .01)
        self.action("camera_reset")
        self.check("camera_reset_restores_initial_pose", self.same_pose(self.pose(), self.initial))
        yaw = float(self.camera.get_editor_property("CameraYaw"))
        self.action("camera_right")
        self.check("camera_right_orbits_45", angle_distance(self.camera.get_editor_property("CameraYaw"), yaw+45) < .02)
        self.action("camera_left")
        self.check("camera_left_reverses_orbit", self.same_pose(self.pose(), self.initial))
        for prop, action in (("HelpOpen", "help"), ("MinimalUI", "minimal")):
            self.set_ui_state()
            self.action(action)
            self.check(action+"_opens", bool(self.hud.get_editor_property(prop)))
            self.action(action)
            self.check(action+"_closes", not bool(self.hud.get_editor_property(prop)))
        self.modal_checks()
        state_before = {key: self.controller.get_editor_property(key) for key in AIM_PROPERTIES}
        self.action("unknown_action_for_qa")
        self.check("unknown_action_does_not_change_aim", state_before == {
            key: self.controller.get_editor_property(key) for key in AIM_PROPERTIES})
        after_projectiles = len(unreal.GameplayStatics.get_all_actors_with_tag(self.world, "SkyfallProjectile"))
        self.check("non_fire_ui_actions_never_fire", before_projectiles == after_projectiles
                   and before_fire == float(self.controller.get_editor_property("LastFireTime")),
                   projectile_count_before=before_projectiles, projectile_count_after=after_projectiles)
        self.write()

    def modal_checks(self):
        self.set_ui_state(minimal=True)
        self.action("help")
        self.check("help_from_minimal_is_visible", bool(self.hud.get_editor_property("HelpOpen"))
                   and not bool(self.hud.get_editor_property("MinimalUI")))
        self.action("help_close")
        self.check("help_close_closes_help", not bool(self.hud.get_editor_property("HelpOpen")))
        self.action("help_close")
        self.check("help_close_is_idempotent", not bool(self.hud.get_editor_property("HelpOpen")))
        self.set_ui_state()
        self.action("weapon_meteor")
        self.controller.set_editor_property("MeteorTargetX", 0.0)
        self.controller.set_editor_property("MeteorTargetY", 0.0)
        self.controller.set_editor_property("MeteorAngle", 40.0)
        self.action("camera_right")
        camera_before = self.pose()
        controller_before = {key: self.controller.get_editor_property(key)
                             for key in AIM_PROPERTIES + ("LastFireTime",)}
        projectiles_before = len(unreal.GameplayStatics.get_all_actors_with_tag(self.world, "SkyfallProjectile"))
        self.action("help")
        for blocked_action in (
                "weapon_rocket", "weapon_meteor", "weapon_ball", "primary_plus", "primary_minus",
                "secondary_plus", "secondary_minus", "angle_plus", "angle_minus",
                "camera_left", "camera_right", "camera_reset", "fire"):
            self.action(blocked_action)
            actual = {key: self.controller.get_editor_property(key) for key in controller_before}
            projectile_count = len(unreal.GameplayStatics.get_all_actors_with_tag(self.world, "SkyfallProjectile"))
            self.check("help_blocks_"+blocked_action, actual == controller_before
                       and self.same_pose(self.pose(), camera_before) and projectile_count == projectiles_before,
                       help_open=bool(self.hud.get_editor_property("HelpOpen")))
        self.report["modal_rebuild_note"] = "Rebuild is intentionally not invoked; its modal branch must be source-reviewed and physically verified separately."
        self.action("help_close")
        self.action("weapon_rocket")
        self.check("closing_help_restores_weapon_action", self.controller.get_editor_property("WeaponMode") == 0)
        self.controller.set_editor_property("YawDegrees", 0.0)
        self.action("primary_plus")
        self.check("closing_help_restores_parameter_action", abs(self.controller.get_editor_property("YawDegrees")-3.0) < .01)
        self.action("camera_reset")
        self.check("closing_help_restores_camera_action", self.same_pose(self.pose(), self.initial))

    def prepare_capture(self, index):
        self.capture_index = index
        _, action, help_open, minimal, _, _ = CAPTURES[index]
        self.set_ui_state()
        for key, value in self.snapshot.items():
            self.controller.set_editor_property(key, value)
        self.action(action)
        self.action("camera_reset")
        self.set_ui_state(help_open=help_open, minimal=minimal)
        self.set_stage("settling_capture")

    def capture(self):
        name, _, _, _, width, height = CAPTURES[self.capture_index]
        self.focus()
        path = IMAGE_DIR / (name+".png")
        self.pending_capture = dict(name=name, path=path, real_at=time.perf_counter(), requested=time.time(),
                                    dimensions=(width, height), previous_mtime=path.stat().st_mtime_ns if path.exists() else None)
        self.report["screenshots"][name] = dict(path=str(path), status="pending", pose=self.pose(),
            hud_state=self.state(), help_open=bool(self.hud.get_editor_property("HelpOpen")),
            minimal_ui=bool(self.hud.get_editor_property("MinimalUI")), visual_review_required=True)
        unreal.SystemLibrary.execute_console_command(self.world,
            f'HighResShot {width}x{height} filename="{path.as_posix()}"', self.player)
        self.write()

    def poll_capture(self, now):
        item = self.pending_capture
        if not item:
            return
        path = item["path"]
        if path.exists() and path.stat().st_mtime >= item["requested"]-1 and path.stat().st_mtime_ns != item["previous_mtime"] and path.stat().st_size > 36:
            with path.open("rb") as stream:
                header = stream.read(24)
                stream.seek(-12, 2)
                tail = stream.read(12)
            if header[:8] == b"\x89PNG\r\n\x1a\n" and tail[4:8] == b"IEND":
                dimensions = struct.unpack(">II", header[16:24])
                self.check("capture_"+item["name"], dimensions == item["dimensions"], dimensions=list(dimensions))
                self.report["screenshots"][item["name"]].update(status="captured", bytes=path.stat().st_size)
                self.pending_capture = None
                self.write()
                return
        if now-item["real_at"] > 15:
            raise TimeoutError("No fresh complete screenshot: "+str(path))

    def fire_check(self):
        self.set_ui_state()
        self.action("weapon_meteor")
        self.controller.set_editor_property("MeteorTargetX", 0.0)
        self.controller.set_editor_property("MeteorTargetY", 0.0)
        self.controller.set_editor_property("MeteorAngle", 40.0)
        before = unreal.GameplayStatics.get_all_actors_with_tag(self.world, "SkyfallProjectile")
        previous_fire_time = float(self.controller.get_editor_property("LastFireTime"))
        self.fire_pose = self.pose()
        self.action("fire")
        after = unreal.GameplayStatics.get_all_actors_with_tag(self.world, "SkyfallProjectile")
        spawned = [actor for actor in after if actor not in before]
        self.check("fire_button_spawns_one_projectile", len(spawned) == 1, spawned=len(spawned))
        self.check("fire_button_advances_cooldown", float(self.controller.get_editor_property("LastFireTime")) > previous_fire_time)
        self.check("fire_button_preserves_camera", self.same_pose(self.pose(), self.fire_pose))
        self.report["fire_spawned"] = [actor.get_path_name() for actor in spawned]
        self.set_stage("observing_fire")

    def restore(self):
        if self.restored:
            return
        self.restored = True
        if self.snapshot is not None and alive(self.controller):
            for key, value in self.snapshot.items():
                self.controller.set_editor_property(key, value)
            restored = {key: self.controller.get_editor_property(key) for key in self.snapshot}
            self.report["controller_restored"] = restored == self.snapshot
            self.check("temporary_controller_state_restored", self.report["controller_restored"])
        if self.camera_snapshot is not None and alive(self.camera):
            for key, value in self.camera_snapshot.items():
                self.camera.set_editor_property(key, value)
            self.camera.call_method("UpdateOrbitPose")
            self.report["camera_restored"] = self.same_pose(self.pose(), self.initial)
            self.check("temporary_camera_state_restored", self.report["camera_restored"])
        if self.hud_snapshot is not None and alive(self.hud):
            self.set_ui_state(help_open=self.hud_snapshot["HelpOpen"], minimal=self.hud_snapshot["MinimalUI"])
            self.report["hud_restored"] = self.hud_snapshot == {
                key: self.hud.get_editor_property(key) for key in self.hud_snapshot}
            self.check("temporary_hud_state_restored", self.report["hud_restored"])

    def finish(self, error=None):
        if error:
            self.report["error"] = error
            unreal.log_error("SKYFALL_CHINESE_UI_ERROR "+error)
        try:
            self.restore()
        except Exception as restore_error:
            self.check("restore_failed", False, error=str(restore_error))
        self.outcome = "failed" if error or self.report["failures"] else "passed"
        if self.world:
            unreal.GameplayStatics.set_global_time_dilation(self.world, 1)
        if self.levels.is_in_play_in_editor() or self.game_world():
            self.levels.editor_request_end_play()
        self.set_stage("finishing")

    def advance(self, now):
        self.poll_capture(now)
        if self.stage == "finishing":
            stopped = not self.levels.is_in_play_in_editor() and not self.game_world()
            if not stopped and now-self.stage_started < 10:
                return
            self.check("pie_stopped", stopped)
            self.report.update(status="passed" if self.outcome == "passed" and stopped else "failed", pie_stopped=stopped)
            self.set_stage("complete")
            unreal.unregister_slate_post_tick_callback(self.handle)
            self.handle = None
            unreal.log("SKYFALL_CHINESE_UI_"+self.report["status"].upper())
            return
        if now-self.started > 140:
            self.finish("Chinese UI suite exceeded 140 seconds")
        elif self.stage == "stopping_existing_pie":
            if not self.levels.is_in_play_in_editor() and not self.game_world():
                self.set_stage("warmup")
        elif self.stage == "warmup" and now-self.stage_started >= 12:
            self.levels.editor_request_begin_play()
            self.set_stage("waiting_for_pie")
        elif self.stage == "waiting_for_pie":
            self.world = self.game_world()
            if self.world and self.levels.is_in_play_in_editor():
                self.set_stage("waiting_for_hud")
            elif now-self.stage_started > 20:
                self.finish("PIE did not start within 20 seconds")
        elif self.stage == "waiting_for_hud" and now-self.stage_started >= 1:
            if self.setup():
                self.controls_checks()
                self.prepare_capture(0)
        elif self.stage == "settling_capture" and now-self.stage_started >= .8:
            self.capture()
            self.set_stage("capturing")
        elif self.stage == "capturing" and self.pending_capture is None:
            if self.capture_index+1 < len(CAPTURES):
                self.prepare_capture(self.capture_index+1)
            else:
                self.fire_check()
        elif self.stage == "observing_fire" and now-self.stage_started > 1.5:
            self.check("fire_does_not_replace_camera", self.player.get_view_target() == self.camera)
            self.finish()


def start():
    previous = getattr(unreal, "_skyfall_chinese_ui_checks", None)
    if previous is not None and previous.handle is not None:
        raise RuntimeError("Chinese UI checks are already running")
    runner = ChineseUIChecks()
    unreal._skyfall_chinese_ui_checks = runner
    runner.start()
    return runner


if __name__ == "__main__":
    start()
