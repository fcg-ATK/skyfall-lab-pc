"""Validate 360-degree building orbit in actual PIE, without changing assets.

Run the full editor on DemolitionYard with -ExecCmds="py <this absolute path>".
The prior free-flight camera fails orbit_state_available cleanly. This harness
checks native math/Tick and takes four player-view captures; physical drag and
key routing remain separate UI checks. PIE stops at completion; editor stays.
"""
import json
import math
import sys
import time
from pathlib import Path

import unreal

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tools"))
import runtime_free_camera as base
from runtime_free_camera import alive, xyz, distance, angle_distance, AIM_PROPERTIES

REPORT_PATH = PROJECT / "Saved" / "runtime-orbit-camera.json"
IMAGE_DIR = PROJECT / "Saved" / "QA" / "skyfall-orbit-camera"
DEFAULT_FOCUS = (0.0, 0.0, 1200.0)
DEFAULT_YAW = math.degrees(math.atan2(9400, -6500))
DEFAULT_PITCH = -20.0
DEFAULT_RADIUS = 7000.0
VIEWS = (("front", 0.0), ("side", 90.0), ("rear", 180.0), ("opposite_side", 270.0))


def normalized(vector):
    length = math.sqrt(sum(value * value for value in vector))
    return [value / length for value in vector] if length else [0.0, 0.0, 0.0]


class OrbitCameraChecks(base.FreeCameraChecks):
    def __init__(self):
        super().__init__()
        self.capture_index = 0
        self.report.update(suite="native-orbit-camera", screenshots={},
            limitations=[
                "Native input calls prove orbital geometry, not physical held-button routing.",
                "Four captures require visual review for neighboring scenery occlusion.",
                "Ground clamping can reduce geometric radius at very low viewing angles.",
            ])

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
        unreal.log("SKYFALL_ORBIT_CAMERA_STAGE " + stage)

    def start(self):
        IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        super().start()

    def scalar(self, name):
        return float(self.camera.get_editor_property(name))

    def orbit(self, angle):
        self.camera.call_method("StepOrbit", (float(angle),))
        return self.pose()

    def focus_alignment(self):
        position = xyz(self.camera.get_actor_location())
        focus = xyz(self.camera.get_editor_property("CameraOrbitFocus"))
        desired = normalized([target - current for target, current in zip(focus, position)])
        forward = xyz(self.camera.get_actor_forward_vector())
        return sum(a * b for a, b in zip(desired, forward))

    def setup(self):
        cameras = unreal.GameplayStatics.get_all_actors_with_tag(self.world, "SFFreeCamera")
        self.check("one_installed_camera", len(cameras) == 1, found=len(cameras))
        if len(cameras) != 1:
            self.finish()
            return False
        self.camera = cameras[0]
        try:
            focus = xyz(self.camera.get_editor_property("CameraOrbitFocus"))
            radius = self.scalar("CameraOrbitRadius")
        except Exception as error:
            self.check("orbit_state_available", False, baseline_reason=str(error))
            self.report["baseline_note"] = "The previous free-flight camera rotates in place and lacks orbital state."
            self.finish()
            return False
        self.check("orbit_state_available", True, focus=focus, radius=radius)
        self.player = unreal.GameplayStatics.get_player_controller(self.world, 0)
        controller_asset = unreal.load_asset(base.CONTROLLER)
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
        self.check("player_views_orbit_camera", alive(self.player) and self.player.get_view_target() == self.camera)
        self.check("camera_keeps_scene_tag", self.camera.actor_has_tag("SFCamera"))
        self.check("default_focus_and_radius", distance(focus, DEFAULT_FOCUS) < .01 and abs(radius-DEFAULT_RADIUS) < .01,
                   focus=focus, radius=radius)
        self.check("default_angles", angle_distance(self.scalar("CameraYaw"), DEFAULT_YAW) < .02 and
                   abs(self.scalar("CameraPitch")-DEFAULT_PITCH) < .02)
        self.check("default_actual_radius", abs(distance(self.initial["location"], focus)-DEFAULT_RADIUS) < .2)
        self.check("default_looks_at_building", self.focus_alignment() > .99999)
        self.fov = float(self.camera.get_component_by_class(unreal.CameraComponent).get_editor_property("field_of_view"))
        self.check("default_fov", abs(self.fov-52) < .01, actual=self.fov)
        return True

    def math_checks(self):
        self.reset()
        focus = xyz(self.camera.get_editor_property("CameraOrbitFocus"))
        poses = []
        for quarter in range(1, 5):
            pose = self.apply(look_x=450.0, real_delta=0.0)
            poses.append(pose)
            self.check("quarter_"+str(quarter)+"_angle", angle_distance(self.scalar("CameraYaw"), DEFAULT_YAW+90*quarter) < .02)
            self.check("quarter_"+str(quarter)+"_radius", abs(distance(pose["location"],focus)-DEFAULT_RADIUS) < .2)
            self.check("quarter_"+str(quarter)+"_looks_at_building", self.focus_alignment() > .99999)
        self.report["quadrant_poses"] = poses
        self.check("rotation_moves_camera_around_building", distance(poses[0]["location"], self.initial["location"]) > 8000)
        self.check("rear_position_opposite_front", abs(poses[1]["location"][0]+self.initial["location"][0]) < .2 and
                   abs(poses[1]["location"][1]+self.initial["location"][1]) < .2)
        self.check("full_turn_returns_to_initial_pose", self.same_pose(poses[-1],self.initial))
        for _ in range(8):
            self.apply(look_x=450.0, real_delta=0.0)
        self.check("multiple_turns_have_no_stop_or_drift", self.same_pose(self.pose(),self.initial))
        self.reset()
        self.apply(look_x=-450, real_delta=0)
        self.check("negative_turn_reaches_other_side", angle_distance(self.scalar("CameraYaw"),DEFAULT_YAW-90) < .02 and
                   distance(self.pose()["location"],poses[2]["location"]) < .2)
        self.reset()
        self.orbit(45)
        self.orbit(45)
        self.check("quick_orbit_steps_accumulate", self.same_pose(self.pose(),poses[0]))
        for _ in range(6):
            self.orbit(45)
        self.check("eight_quick_steps_complete_a_turn", self.same_pose(self.pose(),self.initial))
        self.reset()
        self.apply(look_x=200, look_y=100, forward=1, right=1, look_active=False)
        self.check("mouse_and_wasd_require_navigation_mode", self.same_pose(self.pose(),self.initial))
        self.reset()
        self.apply(look_y=50)
        self.check("mouse_up_raises_orbital_view", abs(self.scalar("CameraPitch")+30)<.02 and
                   self.pose()["location"][2]>self.initial["location"][2]+500 and self.focus_alignment()>.99999)
        self.apply(look_y=-50)
        self.check("opposite_vertical_drag_returns_view", self.same_pose(self.pose(),self.initial))
        for sign, suffix in ((1,"forward"),(-1,"backward")):
            self.reset()
            self.apply(forward=sign)
            self.check("wasd_"+suffix+"_changes_radius", abs(self.scalar("CameraOrbitRadius")-(DEFAULT_RADIUS-sign*180)) < .1)
        for sign, suffix in ((1,"right"),(-1,"left")):
            self.reset()
            self.apply(right=sign)
            self.check("wasd_"+suffix+"_orbits", angle_distance(self.scalar("CameraYaw"),DEFAULT_YAW-sign*5.5)<.02 and
                       self.focus_alignment()>.99999)
        for sign, suffix in ((1,"in"),(-1,"out")):
            self.reset()
            self.apply(zoom=sign, look_active=False)
            self.check("wheel_"+suffix+"_changes_radius", abs(self.scalar("CameraOrbitRadius")-(DEFAULT_RADIUS-sign*650)) < .1)
        self.reset()
        for _ in range(20):
            self.apply(zoom=4)
        self.check("minimum_radius_limit", abs(self.scalar("CameraOrbitRadius")-2800)<.1)
        for _ in range(20):
            self.apply(zoom=-4)
        self.check("maximum_radius_limit", abs(self.scalar("CameraOrbitRadius")-18000)<.1)
        for sign, suffix in ((1,"up"),(-1,"down")):
            self.reset()
            self.apply(up=sign,look_active=False)
            altitude_delta=self.pose()["location"][2]-self.initial["location"][2]
            self.check("qe_"+suffix+"_changes_height", altitude_delta*sign>100 and self.focus_alignment()>.99999,
                       altitude_delta=altitude_delta)
        self.reset()
        self.apply(look_y=-100000)
        self.check("pitch_upper_limit", abs(self.scalar("CameraPitch")-10)<.02)
        self.check("ground_limit", self.pose()["location"][2]>=119.99 and self.focus_alignment()>.99999,
                   location=self.pose()["location"])
        self.apply(look_y=100000)
        self.check("pitch_lower_limit", abs(self.scalar("CameraPitch")+80)<.02 and self.focus_alignment()>.99999)
        self.reset()
        self.apply(forward=1,boost=True)
        self.check("shift_triples_radius_motion", abs(self.scalar("CameraOrbitRadius")-(DEFAULT_RADIUS-540))<.1)
        self.reset()
        once=self.apply(right=1,forward=1,up=1,real_delta=.1)
        self.reset()
        self.apply(right=1,forward=1,up=1,real_delta=.05)
        twice=self.apply(right=1,forward=1,up=1,real_delta=.05)
        self.check("frame_rate_independent", self.same_pose(once,twice), one_step=once,two_steps=twice)
        self.check("orbit_preserves_fov", abs(float(self.camera.get_component_by_class(unreal.CameraComponent).get_editor_property("field_of_view"))-self.fov)<.01)
        self.check("orbit_preserves_weapon_aim", {key:self.controller.get_editor_property(key) for key in AIM_PROPERTIES}==self.snapshot)
        self.reset()
        self.camera.set_editor_property("CameraYaw", -135.0)
        self.camera.set_editor_property("CameraPitch", 0.0)
        self.camera.set_editor_property("CameraOrbitRadius", 12000.0)
        self.camera.call_method("UpdateOrbitPose")
        occluded_radius=distance(self.pose()["location"],focus)
        self.check("neighbor_office_pulls_camera_in",occluded_radius<11500 and self.focus_alignment()>.99999,
                   actual_radius=occluded_radius,requested_radius=self.scalar("CameraOrbitRadius"),pose=self.pose())
        self.check("obstacle_avoidance_keeps_requested_zoom",abs(self.scalar("CameraOrbitRadius")-12000)<.1)
        self.reset()
        self.orbit(180)
        self.mode_pose=self.pose()
        for mode in (0,1,2):
            self.controller.set_editor_property("WeaponMode",mode)
            self.controller.call_method("UpdateModeCamera")
            self.check("weapon_"+str(mode)+"_preserves_orbit",self.same_pose(self.pose(),self.mode_pose))
        for key,value in self.snapshot.items():
            self.controller.set_editor_property(key,value)
        self.check("reset_restores_initial_pose",self.same_pose(self.reset(),self.initial))
        self.orbit(180)
        self.mode_pose=self.pose()
        self.report["math_checks_complete"]=True
        self.write()

    def prepare_native_tick_probe(self):
        self.check("native_tick_enabled",self.camera.is_actor_tick_enabled())
        position=self.camera.get_actor_location()
        self.camera.set_actor_location(position+unreal.Vector(500,400,300),False,False)
        self.camera.set_actor_rotation(unreal.Rotator(),False)
        self.check("native_tick_probe_changes_transform",not self.same_pose(self.pose(),self.mode_pose))

    def capture(self,name):
        self.focus()
        path=IMAGE_DIR/(name+".png")
        self.pending_capture=dict(name=name,path=path,requested=time.time(),real_at=time.perf_counter(),
            previous_mtime=path.stat().st_mtime_ns if path.exists() else None)
        self.report["screenshots"][name]=dict(path=str(path),status="pending",pose=self.pose(),
            view_target=self.player.get_view_target().get_path_name(),visual_review_required=True,
            focus_alignment=self.focus_alignment())
        unreal.SystemLibrary.execute_console_command(self.world,
            'HighResShot 1920x1080 filename="'+path.as_posix()+'"',self.player)
        self.write()

    def prepare_view(self,index):
        self.capture_index=index
        self.reset()
        self.orbit(VIEWS[index][1])
        self.set_stage("settling_orbit_view")

    def advance(self,now):
        self.poll_capture(now)
        if self.stage in ("finishing","stopping_existing_pie","warmup","waiting_for_pie","observing_fire"):
            # Reuse the proven PIE lifecycle, actual projectile contact and slow-mo checks.
            super().advance(now)
            if self.stage=="complete":
                unreal.log("SKYFALL_ORBIT_CAMERA_"+self.report["status"].upper())
            return
        if now-self.started>95:
            self.finish("Orbit camera suite exceeded 95 seconds")
        elif self.stage=="waiting_for_actors" and now-self.stage_started>1:
            if self.setup():
                self.math_checks()
                self.prepare_native_tick_probe()
                self.set_stage("checking_orbit_tick")
        elif self.stage=="checking_orbit_tick" and now-self.stage_started>.6:
            self.check("native_tick_restores_orbital_position_and_look",self.same_pose(self.pose(),self.mode_pose),
                       actual=self.pose(),expected=self.mode_pose)
            unreal.GameplayStatics.set_global_time_dilation(self.world,.22)
            self.set_stage("checking_dilated_orbit")
        elif self.stage=="checking_dilated_orbit" and now-self.stage_started>.6:
            self.check("slow_motion_keeps_orbit_pose",self.same_pose(self.pose(),self.mode_pose))
            unreal.GameplayStatics.set_global_time_dilation(self.world,1)
            self.prepare_view(0)
        elif self.stage=="settling_orbit_view" and now-self.stage_started>.6:
            self.capture(VIEWS[self.capture_index][0])
            self.set_stage("capturing_orbit_view")
        elif self.stage=="capturing_orbit_view" and self.pending_capture is None:
            if self.capture_index+1<len(VIEWS):
                self.prepare_view(self.capture_index+1)
            else:
                self.reset()
                self.orbit(180)
                self.fire()


def start():
    previous=getattr(unreal,"_skyfall_orbit_camera_checks",None)
    if previous is not None and previous.handle is not None:
        raise RuntimeError("Orbit camera checks already running")
    runner=OrbitCameraChecks()
    unreal._skyfall_orbit_camera_checks=runner
    runner.start()
    return runner


if __name__=="__main__":
    start()
