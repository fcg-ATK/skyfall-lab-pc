"""Native actions and input setup for the Chinese Canvas HUD.

Authoring only: all buttons and keyboard shortcuts execute in Blueprint at
runtime. Call build_ui_actions after creating/resetting the HUD Blueprint and
before wiring its click/hover events. This module owns HUD BeginPlay and H/F1/
Tab input events; it does not draw or register hitboxes, save a map, or rebuild
combat actors. patch_controller_ui_input only removes legacy free-click firing
and suppresses the old startup text on the existing controller asset.
"""

from __future__ import annotations

import unreal

from blueprint_runtime import _call, _compile, _event, _exec, _graph, _input, _save, _value, _wire
from combat_v2 import WEAPONS, _branch, _connect, _function, _get, _math, _set, _variables


CAMERA_PATH = "/Game/Skyfall/Blueprints/BP_SkyfallFreeCamera"
ACTION_NAMES = (
    "weapon_rocket", "weapon_meteor", "weapon_ball",
    "primary_minus", "primary_plus", "secondary_minus", "secondary_plus",
    "angle_minus", "angle_plus", "camera_left", "camera_right", "camera_reset",
    "help", "minimal", "fire", "rebuild",
)


def _external_call(editor, actor_class, name, actor):
    # Generated Blueprint functions are subobjects of the generated class.
    path = actor_class.get_path_name() + ":" + name
    node = editor.add_call_function_node(path)
    if not node:
        raise RuntimeError("Cannot resolve UI target function " + path)
    _connect(actor, node, "self")
    return node


def _external_get(editor, actor_class, name, actor):
    node = editor.add_get_member_variable_node(name, actor_class.get_path_name())
    if not node:
        raise RuntimeError("Cannot read UI target property " + name)
    _connect(actor, node, "self")
    return node, name


def _external_set(editor, actor, name, value, integer=False):
    setter = "SetIntPropertyByName" if integer else "SetDoublePropertyByName"
    node = _call(editor, "KismetSystemLibrary." + setter)
    _connect(actor, node, "Object")
    _value(node, "PropertyName", name)
    _connect(value, node, "Value")
    return node


def _action_match(editor, entry, name):
    return _math(editor, "EqualEqual_NameName", A=(entry, "Action"), B=name)


def _find_actor(editor, actor_class, source, source_pin="then"):
    find = _call(editor, "GameplayStatics.GetActorOfClass")
    _value(find, "ActorClass", actor_class.get_path_name())
    _exec(source, find, source_pin)
    valid = _call(editor, "KismetSystemLibrary.IsValid")
    _wire(find, "ReturnValue", valid, "Object")
    gate = _branch(editor, (valid, "ReturnValue"))
    _exec(find, gate)
    return (find, "ReturnValue"), gate


def _adjust_property(editor, source, actor, actor_class, standard, meteor):
    mode = _external_get(editor, actor_class, "WeaponMode", actor)
    selected = _branch(editor, _math(editor, "EqualEqual_IntInt", A=mode, B=1))
    _exec(source, selected)
    for source_pin, settings in (("then", meteor), ("else", standard)):
        name, step, minimum, maximum = settings
        current = _external_get(editor, actor_class, name, actor)
        value = _math(editor, "FClamp", Value=_math(
            editor, "Add_DoubleDouble", A=current, B=step), Min=minimum, Max=maximum)
        assign = _external_set(editor, actor, name, value)
        _exec(selected, assign, source_pin)


def _build_sync_input_function(hud, controller_class):
    """Keep modal help from passing keyboard gameplay input to the controller.

    Only the combat actor is removed from/re-added to the player's input stack.
    The HUD keeps receiving H/F1/Tab and hitbox events, and the orbit actor is
    left intact. Changing HelpOpen in a runtime check should call this public
    function too, just as UIAction does after toggling an overlay.
    """
    editor, entry, _ = _function(hud, "SyncUIInputState")
    player = _call(editor, "GameplayStatics.GetPlayerController")
    _value(player, "PlayerIndex", 0)
    actor, valid = _find_actor(editor, controller_class, entry)
    help_gate = _branch(editor, _get(editor, "HelpOpen"))
    _exec(valid, help_gate)
    for source_pin, function in (("then", "DisableInput"), ("else", "EnableInput")):
        change = _call(editor, "Actor." + function)
        _connect(actor, change, "self")
        _wire(player, "ReturnValue", change, "PlayerController")
        _exec(help_gate, change, source_pin)
    _compile(hud)


def _build_action_function(hud, controller_class):
    editor, entry, _ = _function(hud, "UIAction", inputs=(("Action", "name"),))
    player = _call(editor, "GameplayStatics.GetPlayerController")
    _value(player, "PlayerIndex", 0)
    down = _call(editor, "PlayerController.IsInputKeyDown")
    _wire(player, "ReturnValue", down, "self")
    _value(down, "Key", "RightMouseButton")
    tail = entry
    if down.find_input_pin("execute").is_valid():
        _exec(tail, down)
        tail = down
    navigation_gate = _branch(editor, (down, "ReturnValue"))
    _exec(tail, navigation_gate)

    # Help/minimal toggles remain reachable while the help sheet is open.
    help_action = _branch(editor, _math(editor, "BooleanOR",
        A=_action_match(editor, entry, "help"), B=_action_match(editor, entry, "help_close")))
    _exec(navigation_gate, help_action, "else")
    help_value = _math(editor, "BooleanAND",
        A=_math(editor, "Not_PreBool", A=_action_match(editor, entry, "help_close")),
        B=_math(editor, "Not_PreBool", A=_get(editor, "HelpOpen")))
    toggle_help = _set(editor, "HelpOpen", help_value)
    _exec(help_action, toggle_help)
    show_full_ui = _set(editor, "MinimalUI", "false")
    _exec(toggle_help, show_full_ui)
    sync_help = editor.add_call_function_node("SyncUIInputState")
    if not sync_help:
        raise RuntimeError("Cannot synchronize modal help input")
    _exec(show_full_ui, sync_help)
    minimal_action = _branch(editor, _action_match(editor, entry, "minimal"))
    _exec(help_action, minimal_action, "else")
    toggle_minimal = _set(editor, "MinimalUI", _math(editor, "Not_PreBool", A=_get(editor, "MinimalUI")))
    close_help = _set(editor, "HelpOpen", "false")
    _exec(minimal_action, toggle_minimal)
    _exec(toggle_minimal, close_help)
    sync_minimal = editor.add_call_function_node("SyncUIInputState")
    if not sync_minimal:
        raise RuntimeError("Cannot restore input after closing modal help")
    _exec(close_help, sync_minimal)
    help_gate = _branch(editor, _get(editor, "HelpOpen"))
    _exec(minimal_action, help_gate, "else")

    previous = help_gate
    # Every action branch terminates on its own success path. Unknown names
    # leave state unchanged. The whole chain is blocked while help is open.
    for action in (name for name in ACTION_NAMES if name not in ("help", "minimal")):
        branch = _branch(editor, _action_match(editor, entry, action))
        _exec(previous, branch, "else")
        previous = branch
        if action == "rebuild":
            restore = _call(editor, "GameplayStatics.SetGlobalTimeDilation")
            _value(restore, "TimeDilation", 1)
            _exec(branch, restore)
            level = _call(editor, "GameplayStatics.GetCurrentLevelName")
            _value(level, "bRemovePrefixString", "true")
            _exec(restore, level)
            reload_level = _call(editor, "GameplayStatics.OpenLevel")
            _wire(level, "ReturnValue", reload_level, "LevelName")
            _exec(level, reload_level)
            continue
        if action.startswith("camera_"):
            camera_bp = unreal.load_asset(CAMERA_PATH)
            if not camera_bp:
                raise RuntimeError("Orbit camera must be installed before the Chinese UI")
            camera_class = camera_bp.generated_class()
            actor, valid = _find_actor(editor, camera_class, branch)
            function = "ResetCamera" if action == "camera_reset" else "StepOrbit"
            call = _external_call(editor, camera_class, function, actor)
            if action != "camera_reset":
                _value(call, "Angle", -45 if action == "camera_left" else 45)
            _exec(valid, call)
            continue
        actor, valid = _find_actor(editor, controller_class, branch)
        if action.startswith("weapon_"):
            key = action.removeprefix("weapon_")
            mode, spec = next((i, spec) for i, spec in enumerate(WEAPONS) if spec["key"] == key)
            tail = valid
            for name, value, integer in (
                    ("WeaponMode", mode, True), ("LaunchSpeed", spec["speed"], False),
                    ("GravityZ", spec["gravity"], False), ("ProjectileRadius", spec["radius"], False)):
                assign = _external_set(editor, actor, name, value, integer)
                _exec(tail, assign)
                tail = assign
        elif action == "fire":
            fire = _external_call(editor, controller_class, "FireWeapon", actor)
            _exec(valid, fire)
        else:
            sign = -1 if action.endswith("minus") else 1
            if action.startswith("primary_"):
                standard = ("YawDegrees", sign*3, -75, 75)
                meteor = ("MeteorTargetX", sign*150, -3500, 3500)
            elif action.startswith("secondary_"):
                standard = ("LaunchHeight", sign*150, 150, 3800)
                meteor = ("MeteorTargetY", sign*150, -3500, 3500)
            elif action.startswith("angle_"):
                standard = ("PitchDegrees", sign*3, -40, 60)
                meteor = ("MeteorAngle", sign*3, 30, 90)
            else:
                raise RuntimeError("Unimplemented HUD action " + action)
            _adjust_property(editor, valid, actor, controller_class, standard, meteor)
    _compile(hud)


def _build_input_events(hud):
    editor = _graph(hud)
    begin = _event(hud, "ReceiveBeginPlay")
    player = _call(editor, "GameplayStatics.GetPlayerController")
    _value(player, "PlayerIndex", 0)
    enable = _call(editor, "Actor.EnableInput")
    _wire(player, "ReturnValue", enable, "PlayerController")
    _exec(begin, enable)
    tail = enable
    for name in ("bShowMouseCursor", "bEnableClickEvents", "bEnableMouseOverEvents"):
        assign = _call(editor, "KismetSystemLibrary.SetBoolPropertyByName")
        _wire(player, "ReturnValue", assign, "Object")
        _value(assign, "PropertyName", name)
        _value(assign, "Value", "true")
        _exec(tail, assign)
        tail = assign
    mode = _call(editor, "/Script/UMG.WidgetBlueprintLibrary.SetInputMode_GameAndUIEx")
    _wire(player, "ReturnValue", mode, "PlayerController")
    _value(mode, "InMouseLockMode", "DoNotLock")
    _value(mode, "bHideCursorDuringCapture", "true")
    _value(mode, "bFlushInput", "false")
    _exec(tail, mode)
    available = list(editor.list_available_nodes([]))
    for key, action in (("H", "help"), ("F1", "help"), ("Tab", "minimal")):
        event = _input(editor, available, key)
        # HUD owns only these shortcuts and does not consume unrelated actor input.
        event.set_editor_property("consume_input", False)
        dispatch = editor.add_call_function_node("UIAction")
        if not dispatch:
            raise RuntimeError("Cannot bind the HUD shortcut " + key)
        _value(dispatch, "Action", action)
        _exec(event, dispatch, "Pressed")
    _compile(hud)


def build_ui_actions(hud, controller_class):
    """Add native button actions and HUD-only shortcuts to a freshly reset HUD."""
    _variables(hud, [("HelpOpen", "bool", False, False), ("MinimalUI", "bool", False, False)])
    _build_sync_input_function(hud, controller_class)
    _build_action_function(hud, controller_class)
    _build_input_events(hud)
    return {"actions": list(ACTION_NAMES), "keyboard": {"H": "help", "F1": "help", "Tab": "minimal"}}


def patch_controller_ui_input(controller_blueprint):
    """Make UI clicks safe without rebuilding or replacing the combat controller.

    HUD consuming hitboxes do not suppress PlayerInput's LeftMouseButton binding.
    Remove that one legacy input event; Space and the HUD fire button remain.
    Existing key 1/2/3, aim, reset, projectile and slow-motion graphs are untouched.
    """
    editor = _graph(controller_blueprint)
    removed = []
    suppressed = 0
    for node in list(editor.list_all_nodes()):
        if node.get_class().get_name() == "K2Node_InputKey":
            key = node.get_editor_property("input_key")
            if str(key.get_editor_property("key_name")) == "LeftMouseButton":
                removed.append(node)
        key_pin = node.find_input_pin("Key")
        screen_pin = node.find_input_pin("bPrintToScreen")
        if (key_pin.is_valid() and screen_pin.is_valid()
                and key_pin.get_pin_value() == "SkyfallHelp"):
            _value(node, "bPrintToScreen", "false")
            suppressed += 1
    if removed:
        editor.remove_nodes(removed)
    _save(controller_blueprint)
    return {"left_mouse_events_removed": len(removed), "startup_messages_suppressed": suppressed,
            "space_fire_preserved": True, "combat_rebuilt": False}
