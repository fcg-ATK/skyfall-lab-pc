"""Validate the installed native free camera in a real PIE session.

Run the full editor with -ExecCmds="py <absolute path to this file>".
The old fixed-camera build fails the camera-presence assertion cleanly.
No assets or maps are saved. The runner stops PIE and leaves the editor open.
Physical mouse/keyboard input is intentionally left to a separate UI check.
"""
import json
import math
import struct
import time
import traceback
from pathlib import Path

import unreal

PROJECT = Path(__file__).resolve().parents[1]
REPORT_PATH = PROJECT / "Saved" / "runtime-free-camera.json"
IMAGE_DIR = PROJECT / "Saved" / "QA" / "skyfall-free-camera"
CONTROLLER = "/Game/Skyfall/Blueprints/BP_SkyfallControllerV2"
METEOR = "/Game/Skyfall/Blueprints/BP_SkyfallMeteorV2"
AIM_PROPERTIES = ("WeaponMode", "YawDegrees", "PitchDegrees", "LaunchHeight",
                  "MeteorTargetX", "MeteorTargetY", "MeteorAngle", "LaunchSpeed",
                  "GravityZ", "ProjectileRadius")
DEFAULT_LOCATION = (6500.0, -9400.0, 4200.0)


def alive(actor):
    return bool(actor and unreal.SystemLibrary.is_valid(actor))


def xyz(value):
    return [float(value.x), float(value.y), float(value.z)]


def delta(first, second):
    return [a-b for a, b in zip(first, second)]


def distance(first, second):
    return math.sqrt(sum(value*value for value in delta(first, second)))


def scaled(vector, factor):
    return [value*factor for value in vector]


def add(first, second):
    return [a+b for a, b in zip(first, second)]


def angle_distance(first, second):
    return abs((float(first)-float(second)+180) % 360-180)


class FreeCameraChecks:
    def __init__(self):
        self.levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        self.editor = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        self.started = self.stage_started = time.perf_counter()
        self.stage = "warmup"
        self.handle = None
        self.world = self.camera = self.controller = self.player = None
        self.pending_capture = None
        self.outcome = None
        self.report = {
            "status": "running", "stage": self.stage,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "engine": unreal.SystemLibrary.get_engine_version(),
            "map": self.editor.get_editor_world().get_path_name(),
            "suite": "native-free-camera", "checks": {}, "failures": [], "screenshots": {},
            "assets_saved": False, "map_saved": False,
            "limitations": [
                "Native ApplyCameraInput tests verify camera math, not physical keyboard/mouse event routing.",
                "Slow-motion check verifies that the chosen pose is retained; input speed through native Tick requires the separate UI check.",
                "Fresh PNG completion is technical evidence; a human must inspect the actual game viewport and framing.",
            ],
        }

    def write(self):
        self.report.update(stage=self.stage, elapsed_real_seconds=round(time.perf_counter()-self.started, 3))
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = REPORT_PATH.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(REPORT_PATH)

    def check(self, name, passed, **details):
        self.report["checks"][name] = dict(passed=bool(passed), **details)
        if not passed and name not in self.report["failures"]:
            self.report["failures"].append(name)

    def set_stage(self, stage):
        self.stage = stage
        self.stage_started = time.perf_counter()
        self.write()
        unreal.log("SKYFALL_FREE_CAMERA_STAGE " + stage)

    def game_world(self):
        world = self.editor.get_game_world()
        if world:
            return world
        worlds = unreal.EditorLevelLibrary.get_pie_worlds(False)
        return worlds[0] if worlds else None

    def start(self):
        IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        if "-run=" in unreal.SystemLibrary.get_command_line().lower():
            raise RuntimeError("Free camera validation requires full-editor PIE, not a commandlet")
        if self.levels.is_in_play_in_editor() or self.game_world():
            self.levels.editor_request_end_play()
            self.set_stage("stopping_existing_pie")
        self.write()
        self.handle = unreal.register_slate_post_tick_callback(self.tick)
        unreal.log("SKYFALL_FREE_CAMERA_STARTED")

    def pose(self):
        rotation = self.camera.get_actor_rotation()
        return dict(location=xyz(self.camera.get_actor_location()),
                    rotation=[float(rotation.pitch), float(rotation.yaw), float(rotation.roll)])

    def same_pose(self, first, second):
        return distance(first["location"], second["location"]) < .05 and all(
            angle_distance(a, b) < .02 for a, b in zip(first["rotation"], second["rotation"]))

    def apply(self, forward=0.0, right=0.0, up=0.0, look_x=0.0, look_y=0.0,
              zoom=0.0, boost=False, look_active=True, real_delta=.1):
        self.camera.call_method("ApplyCameraInput", (float(forward), float(right), float(up),
            float(look_x), float(look_y), float(zoom), bool(boost), bool(look_active), float(real_delta)))
        return self.pose()

    def reset(self):
        self.camera.call_method("ResetCamera")
        return self.pose()

    def setup(self):
        cameras = unreal.GameplayStatics.get_all_actors_with_tag(self.world, "SFFreeCamera")
        self.check("one_installed_free_camera", len(cameras) == 1, found=len(cameras))
        if len(cameras) != 1:
            self.report["baseline_note"] = "The existing fixed-camera build does not provide a free-camera actor."
            self.finish()
            return False
        self.camera = cameras[0]
        self.player = unreal.GameplayStatics.get_player_controller(self.world, 0)
        controller_asset = unreal.load_asset(CONTROLLER)
        controllers = unreal.GameplayStatics.get_all_actors_of_class(self.world, controller_asset.generated_class())
        self.check("one_aim_controller", len(controllers) == 1)
        if len(controllers) != 1:
            self.finish()
            return False
        self.controller = controllers[0]
        self.snapshot = {key: self.controller.get_editor_property(key) for key in AIM_PROPERTIES}
        self.initial = self.pose()
        self.report.update(camera_actor=self.camera.get_path_name(), initial_pose=self.initial,
                           initial_aim=self.snapshot)
        self.check("player_views_free_camera", alive(self.player) and self.player.get_view_target() == self.camera)
        self.check("camera_keeps_scene_tag", self.camera.actor_has_tag("SFCamera"))
        self.check("default_location", distance(self.initial["location"], DEFAULT_LOCATION) < .1,
                   actual=self.initial["location"])
        expected_rotation = unreal.MathLibrary.find_look_at_rotation(unreal.Vector(*DEFAULT_LOCATION), unreal.Vector(0,0,2600))
        self.check("default_rotation", angle_distance(self.initial["rotation"][0], expected_rotation.pitch) < .02 and
                   angle_distance(self.initial["rotation"][1], expected_rotation.yaw) < .02, actual=self.initial["rotation"])
        component = self.camera.get_component_by_class(unreal.CameraComponent)
        self.fov = float(component.get_editor_property("field_of_view"))
        self.check("default_fov", abs(self.fov-52) < .01, actual=self.fov)
        return True

    def math_checks(self):
        self.reset()
        origin = xyz(self.camera.get_actor_location())
        forward = xyz(self.camera.get_actor_forward_vector())
        right = xyz(self.camera.get_actor_right_vector())
        speed = float(self.camera.get_editor_property("CameraMoveSpeed"))
        wheel = float(self.camera.get_editor_property("CameraWheelStep"))
        self.check("camera_tuning_defaults", abs(speed-1800) < .01 and abs(wheel-650) < .01,
                   move_speed=speed, wheel_step=wheel)
        for name, parameters, direction in (
            ("forward", {"forward": 1}, forward), ("backward", {"forward": -1}, scaled(forward,-1)),
            ("right", {"right": 1}, right), ("left", {"right": -1}, scaled(right,-1)),
            ("up", {"up": 1, "look_active": False}, [0,0,1]),
            ("down", {"up": -1, "look_active": False}, [0,0,-1])):
            self.reset()
            pose = self.apply(**parameters)
            expected = add(origin, scaled(direction, 180))
            self.check("moves_"+name, distance(pose["location"], expected) < .1,
                       actual=pose["location"], expected=expected)
        self.reset()
        pose = self.apply(forward=1, right=1, look_x=50, look_y=50, look_active=False)
        self.check("movement_and_look_require_right_button", self.same_pose(pose, self.initial), actual=pose)
        self.reset()
        pose = self.apply(forward=1, right=1)
        self.check("diagonal_speed_normalized", abs(distance(pose["location"], origin)-180) < .1)
        self.reset()
        pose = self.apply(forward=1, boost=True)
        self.check("shift_boost_three_times", abs(distance(pose["location"], origin)-540) < .1)
        self.reset()
        once = self.apply(forward=1, real_delta=.1)
        self.reset()
        self.apply(forward=1, real_delta=.05)
        twice = self.apply(forward=1, real_delta=.05)
        self.check("movement_frame_rate_independent", self.same_pose(once, twice), one_step=once, two_steps=twice)
        self.reset()
        for sign, name in ((1,"in"), (-1,"out")):
            self.reset()
            pose = self.apply(zoom=sign, look_active=False)
            expected = add(origin, scaled(forward, sign*650))
            self.check("wheel_dollies_"+name, distance(pose["location"],expected)<.1,
                       actual=pose["location"], expected=expected)
        component = self.camera.get_component_by_class(unreal.CameraComponent)
        self.check("dolly_preserves_fov", abs(float(component.get_editor_property("field_of_view"))-self.fov)<.01)
        self.reset()
        pose = self.apply(look_x=100, look_y=50)
        self.check("mouse_look_changes_angles", angle_distance(pose["rotation"][1], self.initial["rotation"][1]+16)<.02 and
                   angle_distance(pose["rotation"][0], self.initial["rotation"][0]+8)<.02, actual=pose)
        pose = self.apply(look_y=10000)
        self.check("pitch_upper_limit", abs(pose["rotation"][0]-85)<.02, actual=pose)
        pose = self.apply(look_y=-20000)
        self.check("pitch_lower_limit", abs(pose["rotation"][0]+85)<.02, actual=pose)
        self.reset()
        self.camera.set_actor_location(unreal.Vector(0,0,121), False, False)
        pose = self.apply(up=-1)
        self.check("camera_stays_above_ground", abs(pose["location"][2]-120)<.1, actual=pose)
        self.camera.set_actor_location(unreal.Vector(0,0,15999), False, False)
        pose = self.apply(up=1)
        self.check("height_upper_bound", abs(pose["location"][2]-16000)<.1, actual=pose)
        self.reset()
        self.camera.set_actor_location(unreal.Vector(18001,-18001,1000), False, False)
        pose = self.apply()
        self.check("horizontal_bounds", abs(pose["location"][0]-18000)<.1 and abs(pose["location"][1]+18000)<.1, actual=pose)
        reset = self.reset()
        self.check("reset_restores_default_pose", self.same_pose(reset,self.initial), actual=reset)
        after = {key:self.controller.get_editor_property(key) for key in AIM_PROPERTIES}
        self.check("camera_input_preserves_aim", after == self.snapshot, before=self.snapshot, after=after)
        self.apply(right=1, up=1, look_x=30)
        self.mode_pose = self.pose()
        for mode in (0,1,2):
            self.controller.set_editor_property("WeaponMode",mode)
            self.controller.call_method("UpdateModeCamera")
            self.check("weapon_"+str(mode)+"_preserves_camera", self.same_pose(self.pose(),self.mode_pose))
        for key,value in self.snapshot.items():
            self.controller.set_editor_property(key,value)
        self.report["math_checks_complete"] = True
        self.write()

    def focus(self):
        library = unreal.load_class(None,"/Script/UMG.WidgetBlueprintLibrary")
        if library:
            unreal.get_default_object(library).call_method("SetFocusToGameViewport")

    def prepare_native_tick_probe(self):
        """Force a visible stale transform that only the actual tick can repair."""
        diagnostics = {}
        try:
            diagnostics["is_actor_tick_enabled"] = bool(self.camera.is_actor_tick_enabled())
            primary_tick = self.camera.get_editor_property("primary_actor_tick")
            for name in ("can_ever_tick", "start_with_tick_enabled"):
                try:
                    diagnostics[name] = bool(primary_tick.get_editor_property(name))
                except Exception as error:
                    diagnostics[name+"_not_measured"] = str(error)
        except Exception:
            diagnostics["reflection_error"] = traceback.format_exc()
        self.report["native_tick_diagnostics"] = diagnostics
        self.check("camera_actor_tick_enabled", diagnostics.get("is_actor_tick_enabled") is True,
                   diagnostics=diagnostics)
        if "can_ever_tick" in diagnostics:
            self.check("camera_actor_can_ever_tick", diagnostics["can_ever_tick"] is True,
                       diagnostics=diagnostics)
        self.camera.set_actor_rotation(unreal.Rotator(0, 0, 0), False)
        self.report["native_tick_perturbed_pose"] = self.pose()
        self.check("native_tick_probe_perturbs_actor_only",
                   not self.same_pose(self.pose(), self.mode_pose),
                   expected_recovery=self.mode_pose, perturbed=self.pose(),
                   stored_yaw=float(self.camera.get_editor_property("CameraYaw")),
                   stored_pitch=float(self.camera.get_editor_property("CameraPitch")))

    def capture(self,name):
        self.focus()
        path = IMAGE_DIR/(name+".png")
        self.pending_capture = dict(name=name,path=path,requested=time.time(),real_at=time.perf_counter(),
            previous_mtime=path.stat().st_mtime_ns if path.exists() else None)
        self.report["screenshots"][name] = dict(path=str(path),status="pending",pose=self.pose(),
            view_target=self.player.get_view_target().get_path_name(),visual_review_required=True)
        unreal.SystemLibrary.execute_console_command(self.world,
            'HighResShot 1920x1080 filename="'+path.as_posix()+'"',self.player)
        self.write()

    def poll_capture(self,now):
        item = self.pending_capture
        if not item:
            return
        path = item["path"]
        if path.exists() and path.stat().st_mtime >= item["requested"]-1 and path.stat().st_mtime_ns != item["previous_mtime"] and path.stat().st_size>36:
            with path.open("rb") as file:
                header=file.read(24)
                file.seek(-12,2)
                tail=file.read(12)
            if header[:8]==b"\x89PNG\r\n\x1a\n" and tail[4:8]==b"IEND":
                dimensions=struct.unpack(">II",header[16:24])
                self.check("capture_"+item["name"],dimensions==(1920,1080),dimensions=list(dimensions))
                self.report["screenshots"][item["name"]].update(status="captured",bytes=path.stat().st_size)
                self.pending_capture=None
                self.write()
                return
        if now-item["real_at"]>12:
            raise TimeoutError("No fresh complete PNG: "+str(path))

    def fire(self):
        self.report["fire_camera_pose"] = self.pose()
        self.shot_pose = self.pose()
        projectile_class = unreal.load_asset(METEOR).generated_class()
        before=unreal.GameplayStatics.get_all_actors_of_class(self.world,projectile_class)
        self.controller.call_method("FireWeapon")
        spawned=[actor for actor in unreal.GameplayStatics.get_all_actors_of_class(self.world,projectile_class) if actor not in before]
        self.check("free_camera_does_not_block_fire",len(spawned)==1,count=len(spawned))
        if len(spawned)!=1:
            self.finish()
            return
        self.projectile=spawned[0]
        expected=xyz(self.controller.call_method("GetLaunchOrigin"))
        actual=xyz(self.projectile.get_actor_location())
        self.check("free_camera_does_not_change_launch_origin",distance(expected,actual)<2,expected=expected,actual=actual)
        self.pieces=unreal.GameplayStatics.get_all_actors_with_tag(self.world,"SkyfallPiece")
        self.hit_seen=False
        self.slow_seen=False
        self.normal_seen=False
        self.pose_drift=0.0
        self.max_fractured=0
        self.set_stage("observing_fire")

    def finish(self,error=None):
        if error:
            self.report["error"]=error
            unreal.log_error("SKYFALL_FREE_CAMERA_ERROR "+error)
        self.outcome="failed" if error or self.report["failures"] else "passed"
        if self.world:
            unreal.GameplayStatics.set_global_time_dilation(self.world,1)
        if self.levels.is_in_play_in_editor() or self.game_world():
            self.levels.editor_request_end_play()
        self.set_stage("finishing")

    def advance(self,now):
        self.poll_capture(now)
        if self.stage=="finishing":
            stopped=not self.levels.is_in_play_in_editor() and not self.game_world()
            if not stopped and now-self.stage_started<10:
                return
            self.check("pie_stopped",stopped)
            self.report.update(status="passed" if self.outcome=="passed" and stopped else "failed",pie_stopped=stopped)
            self.set_stage("complete")
            unreal.unregister_slate_post_tick_callback(self.handle)
            self.handle=None
            unreal.log("SKYFALL_FREE_CAMERA_"+self.report["status"].upper())
            return
        if now-self.started>95:
            self.finish("Free camera suite exceeded 95 seconds")
        elif self.stage=="stopping_existing_pie":
            if not self.levels.is_in_play_in_editor() and not self.game_world():
                self.set_stage("warmup")
        elif self.stage=="warmup" and now-self.stage_started>=12:
            self.levels.editor_request_begin_play()
            self.set_stage("waiting_for_pie")
        elif self.stage=="waiting_for_pie":
            self.world=self.game_world()
            if self.world and self.levels.is_in_play_in_editor():
                self.set_stage("waiting_for_actors")
            elif now-self.stage_started>20:
                self.finish("PIE did not start")
        elif self.stage=="waiting_for_actors" and now-self.stage_started>1:
            if self.setup():
                self.math_checks()
                self.prepare_native_tick_probe()
                self.set_stage("checking_native_tick")
        elif self.stage=="checking_native_tick" and now-self.stage_started>.6:
            self.check("native_tick_recovers_stored_camera_angles",self.same_pose(self.pose(),self.mode_pose),
                       expected=self.mode_pose,actual=self.pose())
            unreal.GameplayStatics.set_global_time_dilation(self.world,.22)
            self.set_stage("checking_dilated_tick")
        elif self.stage=="checking_dilated_tick" and now-self.stage_started>.6:
            self.check("dilated_tick_preserves_free_pose",self.same_pose(self.pose(),self.mode_pose))
            unreal.GameplayStatics.set_global_time_dilation(self.world,1)
            self.reset()
            for _ in range(8):
                self.apply(up=1)
            self.set_stage("settling_raised_view")
        elif self.stage=="settling_raised_view" and now-self.stage_started>.6:
            self.capture("raised_view")
            self.set_stage("capturing_raised_view")
        elif self.stage=="capturing_raised_view" and self.pending_capture is None:
            self.reset()
            for _ in range(5):
                self.apply(zoom=1)
            self.set_stage("settling_close_view")
        elif self.stage=="settling_close_view" and now-self.stage_started>.6:
            self.capture("close_view")
            self.set_stage("capturing_close_view")
        elif self.stage=="capturing_close_view" and self.pending_capture is None:
            self.fire()
        elif self.stage=="observing_fire":
            if alive(self.projectile):
                self.hit_seen=self.hit_seen or bool(self.projectile.get_editor_property("DidHit"))
            fractured=sum(bool(piece.get_editor_property("HasFractured")) for piece in self.pieces if alive(piece))
            self.max_fractured=max(self.max_fractured,fractured)
            dilation=float(unreal.GameplayStatics.get_global_time_dilation(self.world))
            self.slow_seen=self.slow_seen or dilation<.5
            self.normal_seen=self.normal_seen or (self.slow_seen and abs(dilation-1)<.01)
            self.pose_drift=max(self.pose_drift,distance(self.pose()["location"],self.shot_pose["location"]))
            if now-self.stage_started>10:
                self.check("adjusted_view_shot_hits_building",self.hit_seen and self.max_fractured>0,
                    hit_seen=self.hit_seen,max_fractured=self.max_fractured)
                self.check("impact_slow_motion_and_restore",self.slow_seen and self.normal_seen)
                self.check("explosion_does_not_reset_camera",self.pose_drift<.1,maximum_position_drift_cm=self.pose_drift)
                self.check("view_target_stays_free_camera",self.player.get_view_target()==self.camera)
                self.finish()

    def tick(self,delta_seconds):
        try:
            self.advance(time.perf_counter())
        except Exception:
            if self.stage=="finishing":
                self.report.update(status="failed",cleanup_error=traceback.format_exc())
                self.stage="complete"
                self.write()
                unreal.unregister_slate_post_tick_callback(self.handle)
                self.handle=None
            else:
                self.finish(traceback.format_exc())


def start():
    previous=getattr(unreal,"_skyfall_free_camera_checks",None)
    if previous is not None and previous.handle is not None:
        raise RuntimeError("Free-camera checks are already running")
    runner=FreeCameraChecks()
    unreal._skyfall_free_camera_checks=runner
    runner.start()
    return runner


if __name__=="__main__":
    start()
