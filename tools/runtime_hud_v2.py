"""Check the installed HUD through the real PIE player controller.

Run in the full editor with -ExecCmds="py <absolute path to this file>".
No Blueprint is rebuilt and no asset/map is saved. A fresh PIE session is
started, its live aim properties are snapshotted/restored, then PIE is stopped.
The editor remains open; Saved/hud-v2-smoke.json records completion and results.
"""
import json
import time
import traceback
from pathlib import Path

import unreal

PROJECT = Path(__file__).resolve().parents[1]
REPORT_PATH = PROJECT / "Saved" / "hud-v2-smoke.json"
CONTROLLER_PATH = "/Game/Skyfall/Blueprints/BP_SkyfallControllerV2"
HUD_PATH = "/Game/Skyfall/UI/BP_SkyfallHUDV2"
PROPERTIES = ("WeaponMode", "YawDegrees", "PitchDegrees", "LaunchHeight",
              "MeteorTargetX", "MeteorTargetY", "MeteorAngle")
OUTPUTS = ("WeaponText", "AimText", "ControlsText", "ActionText")


def alive(actor):
    return bool(actor and unreal.SystemLibrary.is_valid(actor))


class HUDV2Smoke:
    def __init__(self):
        self.levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        self.editor = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        self.started = self.stage_started = time.perf_counter()
        self.stage = "warmup"
        self.handle = None
        self.world = None
        self.outcome = None
        self.report = {
            "status": "running", "ok": False, "stage": self.stage,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "engine": unreal.SystemLibrary.get_engine_version(),
            "map": self.editor.get_editor_world().get_path_name(),
            "suite": "installed-native-pie-hud-v2", "checks": {}, "failures": [],
            "samples": [], "meteor_mode_samples": [], "assets_rebuilt": False,
            "map_saved": False, "controller_restored": False,
            "timing": {"warmup_real_seconds": 12, "overall_limit_real_seconds": 60},
            "scope": "ReadAimState on the actual player HUD; temporary live aim-property assignments, no firing.",
            "limitations": ["State checks do not measure rendered text size or keyboard input.",
                "Missing-controller fallback is not exercised because the live aim controller is preserved."],
        }

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
        unreal.log("SKYFALL_HUD_V2_SMOKE_STAGE " + stage)

    def check(self, name, passed, **details):
        self.report["checks"][name] = dict(passed=bool(passed), **details)
        if not passed and name not in self.report["failures"]:
            self.report["failures"].append(name)

    def game_world(self):
        # Same PIE-world lookup used by the meteor runtime harness.
        world = self.editor.get_game_world()
        if world:
            return world
        worlds = unreal.EditorLevelLibrary.get_pie_worlds(False)
        return worlds[0] if worlds else None

    def start(self):
        command_line = unreal.SystemLibrary.get_command_line().lower()
        if "-run=" in command_line or "-run " in command_line:
            self.report.update(status="failed", error="HUD smoke requires full-editor PIE; use -ExecCmds, not a commandlet.")
            self.stage = "complete"
            self.write()
            raise RuntimeError(self.report["error"])
        if self.levels.is_in_play_in_editor() or self.game_world():
            self.levels.editor_request_end_play()
            self.set_stage("stopping_existing_pie")
        self.write()
        self.handle = unreal.register_slate_post_tick_callback(self.tick)
        unreal.log("SKYFALL_HUD_V2_SMOKE_STARTED")

    def stop_callback(self):
        if self.handle is not None:
            unreal.unregister_slate_post_tick_callback(self.handle)
            self.handle = None

    @staticmethod
    def read_state(hud):
        state = hud.call_method("ReadAimState")
        if not isinstance(state, (tuple, list)) or len(state) != len(OUTPUTS):
            raise RuntimeError("Unexpected ReadAimState output: " + repr(state))
        return dict(zip(OUTPUTS, map(str, state)))

    def run_checks(self, player, hud, controller):
        expected_hud = unreal.load_asset(HUD_PATH)
        self.report.update(player_controller=player.get_path_name(), hud_actor=hud.get_path_name(),
                           hud_class=hud.get_class().get_path_name(),
                           aim_controller=controller.get_path_name())
        self.check("player_owns_installed_hud", bool(expected_hud) and
                   hud.get_class() == expected_hud.generated_class())
        self.check("hud_enabled", bool(hud.get_editor_property("show_hud")))
        snapshot = {name: controller.get_editor_property(name) for name in PROPERTIES}
        initial_state = self.read_state(hud)
        self.report.update(controller_before=snapshot, state_before=initial_state)
        try:
            for mode in range(3):
                values = {"WeaponMode": mode, "YawDegrees": -18.0+18.0*mode,
                          "PitchDegrees": 12.0-6.0*mode, "LaunchHeight": 150.0+750.0*mode,
                          "MeteorTargetX": 450.0, "MeteorTargetY": -300.0, "MeteorAngle": 71.0}
                for name, value in values.items():
                    controller.set_editor_property(name, value)
                state = self.read_state(hud)
                self.report["samples"].append({"parameters": values, "state": state})
                label = ("WEAPON  1 / ROCKET", "WEAPON  2 / METEOR / PIERCE", "WEAPON  3 / BALL")[mode]
                self.check("weapon_"+str(mode), state["WeaponText"] == label, state=state)
                if mode == 1:
                    self.check("meteor_aim", all(text in state["AimText"] for text in
                        ("TARGET X 4.5 m", "Y -3.0 m", "ENTRY 71 deg")), state=state)
                    self.check("meteor_controls", all(text in state["ControlsText"] for text in
                        ("A/D: target X", "W/S: target Y", "UP/DOWN: angle")) and
                        all(text in state["ActionText"] for text in
                        ("SPACE: drop", "R: reset", ">=78 lodges; <78 pierces")), state=state)
                else:
                    self.check("aim_"+str(mode), "HEIGHT" in state["AimText"] and
                               "TARGET X" not in state["AimText"], state=state)
                    self.check("controls_"+str(mode), "A/D: direction" in state["ControlsText"] and
                               "W/S: height" in state["ActionText"] and
                               "SPACE: fire" in state["ActionText"], state=state)
            controller.set_editor_property("WeaponMode", 1)
            for angle, expected in ((30.0, "PIERCE"), (55.0, "PIERCE"), (77.9, "PIERCE"),
                                    (78.0, "EMBED"), (90.0, "EMBED")):
                controller.set_editor_property("MeteorAngle", angle)
                state = self.read_state(hud)
                self.report["meteor_mode_samples"].append({"angle": angle, "expected_mode": expected,
                                                           "state": state})
                self.check("meteor_angle_"+str(angle),
                           state["WeaponText"] == "WEAPON  2 / METEOR / " + expected, state=state)
        finally:
            errors = []
            for name, value in snapshot.items():
                try:
                    controller.set_editor_property(name, value)
                except Exception as error:
                    errors.append(name+": "+str(error))
            restored = {name: controller.get_editor_property(name) for name in PROPERTIES}
            self.report["controller_after"] = restored
            self.report["controller_restored"] = not errors and restored == snapshot
            self.check("controller_properties_restored", self.report["controller_restored"], errors=errors)
            restored_state = self.read_state(hud)
            self.report["state_after"] = restored_state
            self.check("initial_hud_state_restored", restored_state == initial_state, state=restored_state)

    def finish(self, error=None):
        if error:
            self.report["error"] = error
            unreal.log_error("SKYFALL_HUD_V2_SMOKE_ERROR " + error)
        self.outcome = "failed" if error or self.report["failures"] else "passed"
        if self.levels.is_in_play_in_editor() or self.game_world():
            self.levels.editor_request_end_play()
        self.set_stage("finishing")

    def advance(self, now):
        if self.stage == "finishing":
            stopped = not self.levels.is_in_play_in_editor() and not self.game_world()
            if not stopped and now-self.stage_started < 10:
                return
            self.check("pie_stopped", stopped)
            if not stopped:
                self.outcome = "failed"
            self.report.update(status=self.outcome, ok=self.outcome == "passed",
                               pie_stopped=stopped, editor_open=True)
            self.set_stage("complete")
            self.stop_callback()
            unreal.log("SKYFALL_HUD_V2_SMOKE_" + self.outcome.upper())
            return
        if now-self.started > 50:
            self.finish("HUD smoke exceeded 50 seconds; reserving 10 seconds for PIE cleanup")
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
        elif self.stage == "waiting_for_hud":
            player = unreal.GameplayStatics.get_player_controller(self.world, 0)
            hud = player.get_hud() if alive(player) else None
            controller_asset = unreal.load_asset(CONTROLLER_PATH)
            controllers = unreal.GameplayStatics.get_all_actors_of_class(
                self.world, controller_asset.generated_class()) if controller_asset else []
            if alive(hud) and len(controllers) == 1 and now-self.stage_started >= .5:
                self.run_checks(player, hud, controllers[0])
                self.finish()
            elif now-self.stage_started > 15:
                self.finish("Real player HUD and one native aim controller were not ready within 15 seconds")

    def tick(self, delta_seconds):
        try:
            self.advance(time.perf_counter())
        except Exception:
            if self.stage == "finishing":
                self.report.update(status="failed", ok=False, cleanup_error=traceback.format_exc())
                self.stage = "complete"
                self.write()
                self.stop_callback()
                unreal.log_error("SKYFALL_HUD_V2_SMOKE_FAILED")
            else:
                self.finish(traceback.format_exc())


def start():
    previous = getattr(unreal, "_skyfall_hud_v2_runner", None)
    if previous is not None and previous.handle is not None:
        raise RuntimeError("A HUD V2 smoke is already running")
    runner = HUDV2Smoke()
    unreal._skyfall_hud_v2_runner = runner
    runner.start()
    return runner


if __name__ == "__main__":
    start()
