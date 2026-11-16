"""Author a native 360-degree building orbit camera.

The combat controller keeps its weapon inputs. Camera WASD/look are gated by
RMB, while Q/E and the wheel are always available. The controller must ignore
the SFFreeCamera tag in its legacy mode-camera updater.
"""
import math

import unreal

from blueprint_runtime import _blueprint, _call, _compile, _event, _exec, _graph, _input, _pin, _save, _value, _wire
from combat_v2 import _connect, _function, _get, _location, _math, _self, _set, _variables


CAMERA_PATH = "/Game/Skyfall/Blueprints/BP_SkyfallFreeCamera"
DEFAULT_TARGET = (0.0, 0.0, 1200.0)
DEFAULT_YAW = math.degrees(math.atan2(9400.0, -6500.0))
DEFAULT_PITCH = -20.0
DEFAULT_RADIUS = 7000.0
DEFAULT_LOCATION = (
    -math.cos(math.radians(DEFAULT_PITCH))*math.cos(math.radians(DEFAULT_YAW))*DEFAULT_RADIUS,
    -math.cos(math.radians(DEFAULT_PITCH))*math.sin(math.radians(DEFAULT_YAW))*DEFAULT_RADIUS,
    DEFAULT_TARGET[2]-math.sin(math.radians(DEFAULT_PITCH))*DEFAULT_RADIUS)
INPUT_PARAMETERS = (("Forward", "real"), ("Right", "real"), ("Up", "real"),
                    ("LookX", "real"), ("LookY", "real"), ("Zoom", "real"),
                    ("Boost", "bool"), ("LookActive", "bool"), ("RealDelta", "real"))


def _scale(editor, vector, scalar):
    # Avoid promotable vector-times-scalar resolving to vector-times-vector.
    return _math(editor, "VLerp", A="(X=0,Y=0,Z=0)", B=vector, Alpha=scalar)


def _clamp(editor, value, minimum, maximum):
    return _math(editor, "FClamp", Value=value, Min=minimum, Max=maximum)


def _gate(editor, value, enabled):
    return _math(editor, "SelectFloat", A=value, B=0, bPickA=enabled)


def _pose(editor, source, location, rotation):
    move = _call(editor, "Actor.K2_SetActorLocationAndRotation")
    _connect(location, move, "NewLocation")
    _connect(rotation, move, "NewRotation")
    _value(move, "bSweep", "false")
    _value(move, "bTeleport", "true")
    _exec(source, move)
    return move


def _reset_function(blueprint):
    editor, entry, _ = _function(blueprint, "ResetCamera")
    yaw = _set(editor, "CameraYaw", DEFAULT_YAW)
    pitch = _set(editor, "CameraPitch", DEFAULT_PITCH)
    _exec(entry, yaw)
    _exec(yaw, pitch)
    radius = _set(editor, "CameraOrbitRadius", DEFAULT_RADIUS)
    focus = _set(editor, "CameraOrbitFocus", "(X=0,Y=0,Z=1200)")
    _exec(pitch, radius)
    _exec(radius, focus)
    update = editor.add_call_function_node("UpdateOrbitPose")
    _exec(focus, update)


def _orbit_pose_function(blueprint):
    editor, entry, _ = _function(blueprint, "UpdateOrbitPose")
    focus = _get(editor, "CameraOrbitFocus")
    rotation = _math(editor, "MakeRotator", Pitch=_get(editor, "CameraPitch"),
                     Yaw=_get(editor, "CameraYaw"))
    direction = _math(editor, "GetForwardVector", InRot=rotation)
    desired = _math(editor, "Subtract_VectorVector", A=focus,
                    B=_scale(editor, direction, _get(editor, "CameraOrbitRadius")))
    parts = _call(editor, "KismetMathLibrary.BreakVector")
    _connect(desired, parts, "InVec")
    desired = _math(editor, "MakeVector", X=(parts, "X"), Y=(parts, "Y"),
                    Z=_math(editor, "FMax", A=(parts, "Z"), B=120))
    # Keep scenery from hiding the target when orbiting behind neighbouring offices.
    trace = _call(editor, "KismetSystemLibrary.SphereTraceSingleForObjects")
    _connect(focus, trace, "Start")
    _connect(desired, trace, "End")
    _value(trace, "Radius", 50)
    candidates = [name for name in editor.list_available_nodes([_pin(trace, "ObjectTypes")])
                  if str(name).rsplit("|", 1)[-1].replace(" ", "").lower() == "makearray"]
    if len(candidates) != 1:
        raise RuntimeError("Cannot resolve camera collision object array")
    types = editor.create_node_from_name(candidates[0], unreal.Vector2D(), [_pin(trace, "ObjectTypes")])
    _wire(types, "Array", trace, "ObjectTypes")
    _value(types, "[0]", "ObjectTypeQuery1")
    _value(trace, "bTraceComplex", "false")
    _value(trace, "DrawDebugType", "None")
    _value(trace, "bIgnoreSelf", "true")
    _exec(entry, trace)
    hit = _call(editor, "GameplayStatics.BreakHitResult")
    _wire(trace, "OutHit", hit, "Hit")
    outward = _math(editor, "Normal", A=_math(editor, "Subtract_VectorVector", A=desired, B=focus))
    safe = _math(editor, "Subtract_VectorVector", A=(hit, "Location"), B=_scale(editor, outward, 100))
    position = _math(editor, "SelectVector", A=safe, B=desired, bPickA=(trace, "ReturnValue"))
    safe_parts = _call(editor, "KismetMathLibrary.BreakVector")
    _connect(position, safe_parts, "InVec")
    position = _math(editor, "MakeVector", X=(safe_parts, "X"), Y=(safe_parts, "Y"),
                     Z=_math(editor, "FMax", A=(safe_parts, "Z"), B=120))
    facing = _math(editor, "FindLookAtRotation", Start=position, Target=focus)
    _pose(editor, trace, position, facing)


def _step_orbit_function(blueprint):
    editor, entry, _ = _function(blueprint, "StepOrbit", inputs=(("Angle", "real"),))
    yaw = _set(editor, "CameraYaw", _math(editor, "NormalizeAxis", Angle=_math(
        editor, "Add_DoubleDouble", A=_get(editor, "CameraYaw"), B=(entry, "Angle"))))
    _exec(entry, yaw)
    update = editor.add_call_function_node("UpdateOrbitPose")
    _exec(yaw, update)


def _apply_function(blueprint):
    """Deterministic input integration, also callable by native runtime QA."""
    editor, entry, _ = _function(blueprint, "ApplyCameraInput", inputs=INPUT_PARAMETERS)
    inputs = {name: (entry, name) for name, _ in INPUT_PARAMETERS}
    look_x = _gate(editor, inputs["LookX"], inputs["LookActive"])
    look_y = _gate(editor, inputs["LookY"], inputs["LookActive"])
    boost = _math(editor, "SelectFloat", A=3, B=1, bPickA=inputs["Boost"])
    elapsed = _math(editor, "Multiply_DoubleDouble", A=_clamp(editor, inputs["RealDelta"], 0, .1), B=boost)
    forward = _gate(editor, _clamp(editor, inputs["Forward"], -1, 1), inputs["LookActive"])
    right = _gate(editor, _clamp(editor, inputs["Right"], -1, 1), inputs["LookActive"])
    up = _clamp(editor, inputs["Up"], -1, 1)
    sideways = _math(editor, "Multiply_DoubleDouble", A=right, B=_math(editor, "Multiply_DoubleDouble", A=elapsed, B=55))
    vertical = _math(editor, "Multiply_DoubleDouble", A=up, B=_math(editor, "Multiply_DoubleDouble", A=elapsed, B=25))
    yaw_delta = _math(editor, "Subtract_DoubleDouble",
                      A=_math(editor, "Multiply_DoubleDouble", A=look_x, B=_get(editor, "CameraLookSpeed")), B=sideways)
    pitch_delta = _math(editor, "Add_DoubleDouble",
                        A=_math(editor, "Multiply_DoubleDouble", A=look_y, B=_get(editor, "CameraLookSpeed")), B=vertical)
    yaw = _set(editor, "CameraYaw", _math(editor, "NormalizeAxis", Angle=_math(
        editor, "Add_DoubleDouble", A=_get(editor, "CameraYaw"), B=yaw_delta)))
    pitch = _set(editor, "CameraPitch", _clamp(editor, _math(
        editor, "Subtract_DoubleDouble", A=_get(editor, "CameraPitch"), B=pitch_delta), -80, 10))
    _exec(entry, yaw)
    _exec(yaw, pitch)

    distance = _math(editor, "Multiply_DoubleDouble", A=forward,
                     B=_math(editor, "Multiply_DoubleDouble", A=elapsed, B=_get(editor, "CameraMoveSpeed")))
    zoom = _math(editor, "Multiply_DoubleDouble", A=_clamp(editor, inputs["Zoom"], -4, 4),
                 B=_get(editor, "CameraWheelStep"))
    radius = _set(editor, "CameraOrbitRadius", _clamp(editor, _math(
        editor, "Subtract_DoubleDouble", A=_get(editor, "CameraOrbitRadius"),
        B=_math(editor, "Add_DoubleDouble", A=distance, B=zoom)), 2800, 18000))
    _exec(pitch, radius)
    update = editor.add_call_function_node("UpdateOrbitPose")
    _exec(radius, update)


def _read_player_input(editor, player, tail, function, key=None):
    """Support native const functions regardless of their reflected purity."""
    node = _call(editor, "PlayerController." + function)
    _wire(player, "ReturnValue", node, "self")
    if key:
        # FKey uses custom text serialization: a bare name, not struct syntax.
        _value(node, "Key", key)
    if node.find_input_pin("execute").is_valid():
        _exec(tail, node)
        tail = node
    return node, tail


def _events(blueprint):
    editor = _graph(blueprint)
    player = _call(editor, "GameplayStatics.GetPlayerController")
    _value(player, "PlayerIndex", 0)
    begin = _event(blueprint, "ReceiveBeginPlay")
    enable = _call(editor, "Actor.EnableInput")
    _wire(player, "ReturnValue", enable, "PlayerController")
    _exec(begin, enable)
    reset = editor.add_call_function_node("ResetCamera")
    _exec(enable, reset)
    view = _call(editor, "PlayerController.SetViewTargetWithBlend")
    _wire(player, "ReturnValue", view, "self")
    _wire(_self(editor), "self", view, "NewViewTarget")
    _value(view, "BlendTime", 0)
    _exec(reset, view)

    tick = _event(blueprint, "ReceiveTick", (0, 500))
    tail = tick
    keys = {}
    for key in ("W", "S", "D", "A", "E", "Q", "RightMouseButton", "LeftShift", "RightShift"):
        node, tail = _read_player_input(editor, player, tail, "IsInputKeyDown", key)
        keys[key] = (node, "ReturnValue")
    mouse, tail = _read_player_input(editor, player, tail, "GetInputMouseDelta")
    wheel, tail = _read_player_input(editor, player, tail, "GetInputAnalogKeyState", "MouseWheelAxis")

    def axis(positive, negative):
        return _math(editor, "Subtract_DoubleDouble",
                     A=_math(editor, "Conv_BoolToDouble", InBool=keys[positive]),
                     B=_math(editor, "Conv_BoolToDouble", InBool=keys[negative]))

    dilation = _call(editor, "GameplayStatics.GetGlobalTimeDilation")
    real_delta = _math(editor, "Divide_DoubleDouble", A=(tick, "DeltaSeconds"),
                       B=_math(editor, "FMax", A=(dilation, "ReturnValue"), B=.01))
    apply = editor.add_call_function_node("ApplyCameraInput")
    for name, value in {
        "Forward": axis("W", "S"), "Right": axis("D", "A"), "Up": axis("E", "Q"),
        "LookX": (mouse, "DeltaX"), "LookY": (mouse, "DeltaY"),
        "Zoom": (wheel, "ReturnValue"), "LookActive": keys["RightMouseButton"],
        "Boost": _math(editor, "BooleanOR", A=keys["LeftShift"], B=keys["RightShift"]),
        "RealDelta": real_delta,
    }.items():
        _connect(value, apply, name)
    _exec(tail, apply)

    reset_key = _input(editor, list(editor.list_available_nodes([])), "F")
    reset_call = editor.add_call_function_node("ResetCamera")
    _exec(reset_key, reset_call, "Pressed")
    for key, angle in (("Z", -45), ("C", 45)):
        event = _input(editor, list(editor.list_available_nodes([])), key)
        step = editor.add_call_function_node("StepOrbit")
        _value(step, "Angle", angle)
        _exec(event, step, "Pressed")


def build_free_camera():
    blueprint = _blueprint("BP_SkyfallFreeCamera", unreal.CameraActor)
    _variables(blueprint, [
        ("CameraYaw", "real", DEFAULT_YAW, True),
        ("CameraPitch", "real", DEFAULT_PITCH, True),
        ("CameraMoveSpeed", "real", 1800.0, True),
        ("CameraWheelStep", "real", 650.0, True),
        ("CameraLookSpeed", "real", .20, True),
        ("CameraOrbitRadius", "real", DEFAULT_RADIUS, True),
        ("CameraOrbitFocus", "vector", unreal.Vector(*DEFAULT_TARGET), True),
    ])
    _orbit_pose_function(blueprint)
    _reset_function(blueprint)
    _step_orbit_function(blueprint)
    _apply_function(blueprint)
    _compile(blueprint)
    _events(blueprint)
    _compile(blueprint)
    default = unreal.get_default_object(blueprint.generated_class())
    default.set_editor_property("tags", [unreal.Name("SFCamera"), unreal.Name("SFFreeCamera")])
    default.set_editor_property("auto_activate_for_player", unreal.AutoReceiveInput.PLAYER0)
    default.set_editor_property("input_priority", 10)
    default.set_editor_property("block_input", False)
    default.camera_component.set_mobility(unreal.ComponentMobility.MOVABLE)
    default.camera_component.set_field_of_view(52)
    default.root_component.set_mobility(unreal.ComponentMobility.MOVABLE)
    _save(blueprint)
    return blueprint


def install_free_camera_in_level(blueprint=None):
    """Replace only tagged scene cameras. Caller owns level save/backup."""
    blueprint = blueprint or build_free_camera()
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    old_cameras = [actor for actor in actors.get_all_level_actors() if actor.actor_has_tag("SFCamera")]
    camera = actors.spawn_actor_from_class(blueprint.generated_class(), unreal.Vector(*DEFAULT_LOCATION),
                                           unreal.Rotator(DEFAULT_PITCH, DEFAULT_YAW, 0))
    if camera is None:
        raise RuntimeError("Failed to place native free camera")
    camera.set_actor_label("Demolition free camera")
    camera.set_editor_property("tags", [unreal.Name("SFCamera"), unreal.Name("SFFreeCamera")])
    camera.set_editor_property("auto_activate_for_player", unreal.AutoReceiveInput.PLAYER0)
    camera.camera_component.set_field_of_view(52)
    camera.call_method("ResetCamera")
    for actor in old_cameras:
        if not actors.destroy_actor(actor):
            raise RuntimeError("Could not remove previous tagged camera: " + actor.get_name())
    return camera
