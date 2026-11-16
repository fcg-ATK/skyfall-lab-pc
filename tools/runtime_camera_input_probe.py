"""Observe real player input and camera motion for a manual PIE session.

Full editor: -ExecCmds="py <absolute path to this file>". Starts PIE after a
12-second warmup, samples for 120 seconds, then leaves PIE running. Never
changes camera transforms, simulates input, builds assets, or saves a map.
The report is diagnostic evidence rather than an automatic pass/fail verdict.
"""
import json
import math
import time
import traceback
from pathlib import Path

import unreal

PROJECT = Path(__file__).resolve().parents[1]
REPORT_PATH = PROJECT / "Saved" / "camera-input-probe.json"
KEY_NAMES = ("E", "Q", "W", "A", "S", "D", "RightMouseButton", "LeftMouseButton", "LeftShift", "RightShift", "F",
             "MouseScrollUp", "MouseScrollDown")


def alive(actor):
    return bool(actor and unreal.SystemLibrary.is_valid(actor))


def xyz(value):
    return [float(value.x), float(value.y), float(value.z)]


def distance(first, second):
    return math.sqrt(sum((a-b)**2 for a,b in zip(first,second)))


class CameraInputProbe:
    def __init__(self):
        self.levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        self.editor = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        self.started = self.stage_started = time.perf_counter()
        self.stage = "waiting_warmup"
        self.handle = None
        self.world = self.player = self.camera = None
        self.last_write = 0
        self.last_sample = None
        self.keys = {}
        self.down_since = {}
        self.report = {
            "status":"waiting", "stage":self.stage,
            "started_at":time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "engine":unreal.SystemLibrary.get_engine_version(),
            "map":self.editor.get_editor_world().get_path_name(),
            "warmup_seconds":12, "observation_seconds":120,
            "note":"Manual input observation only. PIE remains running when this probe completes.",
            "assets_saved":False, "map_saved":False, "samples":[], "events":[], "errors":{},
            "key_summary":{name:{"samples_down":0,"press_edges":0,"max_hold_seconds":0.0} for name in KEY_NAMES},
            "wheel_peak_abs":0.0, "mouse_delta_peak_abs":[0.0,0.0],
            "camera_motion_frames":0,"maximum_camera_displacement_cm":0.0,
            "dilation_min":1.0,"dilation_max":1.0,
        }

    def write(self,force=False):
        now=time.perf_counter()
        if not force and now-self.last_write<.25:
            return
        self.report.update(stage=self.stage,elapsed_real_seconds=round(now-self.started,3))
        REPORT_PATH.parent.mkdir(parents=True,exist_ok=True)
        temporary=REPORT_PATH.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.report,ensure_ascii=False,indent=2),encoding="utf-8")
        temporary.replace(REPORT_PATH)
        self.last_write=now

    def error(self,key,error):
        if key not in self.report["errors"]:
            self.report["errors"][key]=str(error)

    def set_stage(self,stage):
        self.stage=stage
        self.stage_started=time.perf_counter()
        self.write(True)
        unreal.log("SKYFALL_CAMERA_INPUT_PROBE_STAGE "+stage)

    def game_world(self):
        world=self.editor.get_game_world()
        if world:
            return world
        worlds=unreal.EditorLevelLibrary.get_pie_worlds(False)
        return worlds[0] if worlds else None

    def call_player(self,python_name,native_name,*args):
        method=getattr(self.player,python_name,None)
        return method(*args) if method else self.player.call_method(native_name,args)

    def start(self):
        if "-run=" in unreal.SystemLibrary.get_command_line().lower():
            raise RuntimeError("Input probe needs full-editor PIE, not a commandlet")
        if self.levels.is_in_play_in_editor() or self.game_world():
            self.levels.editor_request_end_play()
            self.set_stage("waiting_old_pie_end")
        self.write(True)
        self.handle=unreal.register_slate_post_tick_callback(self.tick)

    def setup(self):
        self.player=unreal.GameplayStatics.get_player_controller(self.world,0)
        cameras=unreal.GameplayStatics.get_all_actors_with_tag(self.world,"SFFreeCamera")
        if not alive(self.player) or len(cameras)!=1:
            return False
        self.camera=cameras[0]
        self.keys={name:unreal.Key(name) for name in KEY_NAMES+("MouseWheelAxis",)}
        self.report.update(player_controller=self.player.get_path_name(),
                           player_controller_class=self.player.get_class().get_path_name(),
                           camera_actor=self.camera.get_path_name(),
                           initial_camera_location=xyz(self.camera.get_actor_location()),
                           viewport_size=list(self.player.get_viewport_size()),
                           camera_tick_enabled=bool(self.camera.is_actor_tick_enabled()))
        for owner_name,owner,property_name in (("player",self.player,"player_input"),
                ("camera",self.camera,"input_component"),("player",self.player,"input_component")):
            try:
                value=owner.get_editor_property(property_name)
                self.report[owner_name+"_"+property_name+"_class"]=value.get_class().get_path_name() if value else None
            except Exception as error:
                self.error(owner_name+"_"+property_name,error)
        try:
            library=unreal.load_class(None,"/Script/UMG.WidgetBlueprintLibrary")
            unreal.get_default_object(library).call_method("SetFocusToGameViewport")
            self.report["viewport_focus_requested"]=True
        except Exception as error:
            self.error("viewport_focus",error)
        self.report["status"]="observing"
        self.set_stage("observing")
        return True

    def sample(self,now):
        age=now-self.stage_started
        down={}
        for name in KEY_NAMES:
            try:
                down[name]=bool(self.call_player("is_input_key_down","IsInputKeyDown",self.keys[name]))
            except Exception as error:
                self.error("is_input_key_down_"+name,error)
                down[name]=False
            stats=self.report["key_summary"][name]
            if down[name]:
                stats["samples_down"]+=1
                if name not in self.down_since:
                    self.down_since[name]=now
                    stats["press_edges"]+=1
                    self.report["events"].append({"t":round(age,4),"key":name,"down":True})
                stats["max_hold_seconds"]=round(max(stats["max_hold_seconds"],now-self.down_since[name]),4)
            elif name in self.down_since:
                stats["max_hold_seconds"]=round(max(stats["max_hold_seconds"],now-self.down_since.pop(name)),4)
                self.report["events"].append({"t":round(age,4),"key":name,"down":False})
        wheel=0.0
        mouse=[0.0,0.0]
        try:
            wheel=float(self.call_player("get_input_analog_key_state","GetInputAnalogKeyState",self.keys["MouseWheelAxis"]))
        except Exception as error:
            self.error("wheel_analog",error)
        try:
            mouse=list(map(float,self.call_player("get_input_mouse_delta","GetInputMouseDelta")))
        except Exception as error:
            self.error("mouse_delta",error)
        self.report["wheel_peak_abs"]=max(self.report["wheel_peak_abs"],abs(wheel))
        self.report["mouse_delta_peak_abs"]=[max(a,abs(b)) for a,b in zip(self.report["mouse_delta_peak_abs"],mouse)]
        location=xyz(self.camera.get_actor_location())
        rotation=self.camera.get_actor_rotation()
        dilation=float(unreal.GameplayStatics.get_global_time_dilation(self.world))
        sample=dict(t=round(age,4),keys_down=[name for name,value in down.items() if value],
                    wheel=wheel,mouse_delta=mouse,location=location,
                    rotation=[float(rotation.pitch),float(rotation.yaw),float(rotation.roll)],
                    dilation=dilation,tick_enabled=bool(self.camera.is_actor_tick_enabled()))
        self.report["dilation_min"]=min(self.report["dilation_min"],dilation)
        self.report["dilation_max"]=max(self.report["dilation_max"],dilation)
        self.report["maximum_camera_displacement_cm"]=max(self.report["maximum_camera_displacement_cm"],
            distance(location,self.report["initial_camera_location"]))
        moved=self.last_sample and (distance(location,self.last_sample["location"])>.01 or
            any(abs(a-b)>.001 for a,b in zip(sample["rotation"],self.last_sample["rotation"])))
        if moved:
            self.report["camera_motion_frames"]+=1
        if moved or any(down.values()) or abs(wheel)>0 or any(abs(value)>0 for value in mouse) or \
                not self.report["samples"] or age-self.report["samples"][-1]["t"]>=.25:
            self.report["samples"].append(sample)
            # Keep diagnostic sessions bounded, retaining initial and newest data.
            if len(self.report["samples"])>12000:
                self.report["samples"]=self.report["samples"][:1000]+self.report["samples"][-10000:]
        self.report["latest"]=sample
        self.last_sample=sample
        self.write()

    def finish(self,status="complete",error=None):
        self.report.update(status=status,pie_left_running=bool(self.levels.is_in_play_in_editor()))
        if error:
            self.report["error"]=error
        self.set_stage("complete")
        if self.handle is not None:
            unreal.unregister_slate_post_tick_callback(self.handle)
            self.handle=None
        unreal.log("SKYFALL_CAMERA_INPUT_PROBE_"+status.upper())

    def tick(self,delta_seconds):
        try:
            now=time.perf_counter()
            if self.stage=="waiting_old_pie_end":
                if not self.levels.is_in_play_in_editor() and not self.game_world():
                    self.set_stage("waiting_warmup")
            elif self.stage=="waiting_warmup" and now-self.stage_started>=12:
                self.levels.editor_request_begin_play()
                self.set_stage("waiting_pie")
            elif self.stage=="waiting_pie":
                self.world=self.game_world()
                if self.world and self.levels.is_in_play_in_editor():
                    self.set_stage("waiting_actors")
                elif now-self.stage_started>20:
                    self.finish("failed","PIE did not start")
            elif self.stage=="waiting_actors":
                if now-self.stage_started>.8:
                    if not self.setup() and now-self.stage_started>15:
                        self.finish("failed","Player or free-camera actor missing")
            elif self.stage=="observing":
                if not self.levels.is_in_play_in_editor() or not alive(self.camera) or not alive(self.player):
                    self.finish("stopped","PIE ended or observed actor became invalid")
                else:
                    self.sample(now)
                    if now-self.stage_started>=120:
                        self.finish()
            self.write()
        except Exception:
            self.finish("failed",traceback.format_exc())


def start():
    previous=getattr(unreal,"_skyfall_camera_input_probe",None)
    if previous is not None and previous.handle is not None:
        raise RuntimeError("Input probe is already observing")
    runner=CameraInputProbe()
    unreal._skyfall_camera_input_probe=runner
    runner.start()
    return runner


if __name__=="__main__":
    start()
