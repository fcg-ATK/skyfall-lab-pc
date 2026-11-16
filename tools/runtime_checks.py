"""Check native gameplay in a full Unreal Editor and leave the editor open.

Run with -ExecCmds="py <absolute path to this file>". The Slate callback keeps
the UI responsive while waiting for PIE, physics, and native viewport captures.
"""

import json
import struct
import time
import traceback
from pathlib import Path

import unreal


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PROJECT_ROOT / "Saved"
REPORT_PATH = PROJECT_ROOT / "Saved" / "runtime-checks.json"
IMAGE_DIR = WORKSPACE_ROOT / "QA" / "skyfall-pc"
BLUEPRINT_ROOT = "/Game/Skyfall/Blueprints"


class RuntimeChecks:
    def __init__(self):
        self.levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        self.editor = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        self.started = time.perf_counter()
        self.entered = self.started
        self.next_poll = 0.0
        self.callback_handle = None
        self.world = None
        self.controller = None
        self.piece_class = None
        self.controller_class = None
        self.camera = None
        self.capture_task = None
        self.capture_path = None
        self.capture_requested = None
        self.game_stage_started = None
        self.hit_started = None
        self.outcome = None
        self.stage = "warmup"
        self.report = {
            "status": "running",
            "stage": self.stage,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "engine": unreal.SystemLibrary.get_engine_version(),
            "checks": {},
            "screenshots": {},
            "shots": [],
        }

    def start(self):
        IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._write_report()
        self.callback_handle = unreal.register_slate_post_tick_callback(self.tick)
        unreal.log("SKYFALL_RUNTIME_CHECKS_STARTED warmup=20s")

    def _write_report(self):
        self.report["stage"] = self.stage
        self.report["elapsed_seconds"] = round(time.perf_counter() - self.started, 3)
        temporary = REPORT_PATH.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.report, indent=2), encoding="utf-8")
        temporary.replace(REPORT_PATH)

    def _stage(self, name):
        self.stage = name
        self.entered = time.perf_counter()
        self._write_report()
        unreal.log(f"SKYFALL_RUNTIME_STAGE {name}")

    def _load_class(self, name):
        asset = unreal.load_asset(f"{BLUEPRINT_ROOT}/{name}")
        if not asset:
            raise RuntimeError(f"Missing gameplay asset {name}")
        return asset.generated_class()

    def _find_camera(self, world):
        cameras = unreal.GameplayStatics.get_all_actors_with_tag(world, "SFCamera")
        if not cameras:
            cameras = unreal.GameplayStatics.get_all_actors_of_class(world, unreal.CameraActor)
        if not cameras:
            raise RuntimeError("The level has no camera for the viewport captures")
        return cameras[0]

    def _game_world(self):
        world = self.editor.get_game_world()
        if world:
            return world
        worlds = unreal.EditorLevelLibrary.get_pie_worlds(False)
        return worlds[0] if worlds else None

    def _game_time(self):
        return float(unreal.GameplayStatics.get_time_seconds(self.world))

    def _counts(self):
        pieces = unreal.GameplayStatics.get_all_actors_of_class(self.world, self.piece_class)
        active_floors = {}
        activated = 0
        simulating = 0
        for actor in pieces:
            active = bool(actor.get_editor_property("HasActivated"))
            activated += int(active)
            simulating += int(actor.static_mesh_component.is_simulating_physics())
            if active:
                floor = next((str(tag) for tag in actor.tags if str(tag).startswith("Floor_")), "unknown")
                active_floors[floor] = active_floors.get(floor, 0) + 1
        return {"total": len(pieces), "activated": activated, "simulating": simulating,
                "activated_by_floor": active_floors,
                "game_time": round(self._game_time(), 3)}

    def _capture(self, name, camera):
        self.capture_path = IMAGE_DIR / f"{name}.png"
        self.capture_requested = time.time()
        self.capture_task = unreal.AutomationLibrary.take_high_res_screenshot(
            1920, 1080, str(self.capture_path), camera=camera, delay=0.0,
            force_game_view=True)
        if not self.capture_task or not self.capture_task.is_valid_task():
            raise RuntimeError(f"Native viewport screenshot task was not created for {name}")
        self.report["screenshots"][name] = {"path": str(self.capture_path), "status": "pending"}

    def _capture_done(self, name):
        if time.perf_counter() - self.entered > 60:
            raise TimeoutError(f"Timed out waiting for screenshot {name}")
        if not self.capture_task.is_task_done() or not self.capture_path.exists():
            return False
        if self.capture_path.stat().st_mtime < self.capture_requested - 1:
            return False
        with self.capture_path.open("rb") as image_file:
            header = image_file.read(24)
        if header[:8] != b"\x89PNG\r\n\x1a\n" or len(header) < 24:
            raise RuntimeError(f"Screenshot {name} is not a complete PNG")
        width, height = struct.unpack(">II", header[16:24])
        if (width, height) != (1920, 1080):
            raise RuntimeError(f"Unexpected screenshot dimensions {width}x{height}")
        self.report["screenshots"][name].update(
            {"status": "captured", "width": width, "height": height,
             "bytes": self.capture_path.stat().st_size})
        self.capture_task = None
        return True

    def _fire(self, height, label):
        self.controller.set_editor_property("LaunchHeight", float(height))
        self.controller.set_editor_property("AimX", 0.0)
        before = len(unreal.GameplayStatics.get_all_actors_with_tag(self.world, "SkyfallProjectile"))
        self.controller.call_method("FireBall")
        projectiles = unreal.GameplayStatics.get_all_actors_with_tag(self.world, "SkyfallProjectile")
        if len(projectiles) != before + 1:
            raise AssertionError(f"FireBall did not spawn exactly one projectile for {label}")
        ball = projectiles[-1]
        location = ball.get_actor_location()
        velocity = ball.get_velocity()
        self.report["shots"].append(
            {"label": label, "requested_height": height,
             "location": [location.x, location.y, location.z],
             "velocity": [velocity.x, velocity.y, velocity.z],
             "game_time": round(self._game_time(), 3)})
        self.game_stage_started = self._game_time()

    def _finish(self, success, error=None):
        self.outcome = "passed" if success else "failed"
        if error:
            self.report["error"] = error
            unreal.log_error(f"SKYFALL_RUNTIME_CHECKS_FAILED {error}")
        if self.levels.is_in_play_in_editor():
            self.levels.editor_request_end_play()
        self._stage("ending_pie")

    def stop_callback(self):
        if self.callback_handle is not None:
            unreal.unregister_slate_post_tick_callback(self.callback_handle)
            self.callback_handle = None

    def tick(self, delta_seconds):
        now = time.perf_counter()
        if now < self.next_poll:
            return
        self.next_poll = now + 0.1
        try:
            self._tick_stage(now)
        except Exception:
            error = traceback.format_exc()
            if self.stage == "ending_pie":
                self.report.update(status="failed", cleanup_error=error)
                self._write_report()
                self.stop_callback()
                unreal.log_error(error)
            else:
                self._finish(False, error)

    def _tick_stage(self, now):
        if now - self.started > 240 and self.stage != "ending_pie":
            raise TimeoutError("Runtime checks exceeded four minutes")

        if self.stage == "warmup":
            if now - self.entered < 20:
                return
            if self.levels.is_in_play_in_editor():
                raise RuntimeError("A PIE session was already running when checks started")
            self.piece_class = self._load_class("BP_SkyfallPiece")
            self.controller_class = self._load_class("BP_SkyfallController")
            editor_world = self.editor.get_editor_world()
            self._capture("intact", self._find_camera(editor_world))
            self._stage("capture_intact")

        elif self.stage == "capture_intact":
            if self._capture_done("intact"):
                self.levels.editor_request_begin_play()
                self._stage("waiting_for_pie")

        elif self.stage == "waiting_for_pie":
            self.world = self._game_world()
            if not self.world:
                if now - self.entered > 60:
                    raise TimeoutError("PIE world did not become available")
                return
            controllers = unreal.GameplayStatics.get_all_actors_of_class(self.world, self.controller_class)
            if len(controllers) != 1:
                raise AssertionError(f"Expected one gameplay controller, found {len(controllers)}")
            self.controller = controllers[0]
            self.camera = self._find_camera(self.world)
            player = unreal.GameplayStatics.get_player_controller(self.world, 0)
            if player:
                player.set_view_target_with_blend(self.camera, 0.0)
            initial = self._counts()
            if initial["total"] == 0:
                raise AssertionError("PIE world contains no breakable pieces")
            self.report["checks"]["initial"] = initial
            self.game_stage_started = self._game_time()
            self._stage("idle")

        elif self.stage == "idle":
            counts = self._counts()
            if counts["activated"] != 0:
                raise AssertionError(f"Pieces activated before any shot {counts}")
            if self._game_time() - self.game_stage_started >= 3:
                self.report["checks"]["idle"] = dict(counts, passed=True, duration_seconds=3)
                self._fire(3500, "miss")
                self._stage("miss")

        elif self.stage == "miss":
            counts = self._counts()
            if counts["activated"] != 0:
                raise AssertionError(f"The high miss shot activated pieces {counts}")
            if self._game_time() - self.game_stage_started >= 4:
                self.report["checks"]["miss"] = dict(counts, passed=True, duration_seconds=4)
                self._fire(900, "hit")
                self.hit_started = self._game_time()
                self._stage("awaiting_impact")

        elif self.stage == "awaiting_impact":
            counts = self._counts()
            if counts["activated"] > 0:
                self.report["checks"]["first_impact"] = dict(
                    counts, seconds_after_shot=round(self._game_time() - self.hit_started, 3))
                self._capture("impact", self.camera)
                self._stage("capture_impact")
            elif self._game_time() - self.hit_started >= 5:
                raise AssertionError(f"The aimed shot activated no pieces after five seconds {counts}")

        elif self.stage == "capture_impact":
            if self._capture_done("impact"):
                self._stage("rubble")

        elif self.stage == "rubble":
            if self._game_time() - self.hit_started < 12:
                return
            counts = self._counts()
            if counts["activated"] < 1:
                raise AssertionError(f"No activated pieces remain after the aimed shot {counts}")
            self.report["checks"]["hit"] = dict(counts, passed=True, duration_seconds=12)
            self._capture("rubble", self.camera)
            self._stage("capture_rubble")

        elif self.stage == "capture_rubble":
            if self._capture_done("rubble"):
                self._finish(True)

        elif self.stage == "ending_pie":
            if self.levels.is_in_play_in_editor():
                if now - self.entered > 15:
                    raise TimeoutError("PIE did not finish stopping")
                return
            self.report.update(status=self.outcome, editor_restored=True)
            self._stage("complete")
            self.stop_callback()
            unreal.log(f"SKYFALL_RUNTIME_CHECKS_{self.outcome.upper()}")


previous = getattr(unreal, "_skyfall_runtime_check_runner", None)
if previous is not None:
    previous.stop_callback()
runner = RuntimeChecks()
unreal._skyfall_runtime_check_runner = runner
runner.start()
