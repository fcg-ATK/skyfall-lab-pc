"""Author the persistent combat HUD and its GameMode using native Blueprints.

The HUD reads the existing ControllerV2 instance every draw. Its state reader is
checked on the real PIE player HUD by runtime_hud_v2.py. This module never
rebuilds the combat controller or saves a level.
"""

from __future__ import annotations

import sys
from pathlib import Path

import unreal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from blueprint_runtime import _call, _compile, _event, _exec, _graph, _save, _value, _wire


ASSET_FOLDER = "/Game/Skyfall/UI"
CONTROLLER_PATH = "/Game/Skyfall/Blueprints/BP_SkyfallControllerV2"
FONT_PATH = "/Engine/EngineFonts/Roboto.Roboto"


def _asset(name, parent_class):
    path = ASSET_FOLDER + "/" + name
    blueprint = unreal.load_asset(path)
    if not blueprint:
        unreal.EditorAssetLibrary.make_directory(ASSET_FOLDER)
        factory = unreal.BlueprintFactory()
        factory.set_editor_property("parent_class", parent_class)
        blueprint = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            name, ASSET_FOLDER, unreal.Blueprint, factory)
    if not blueprint:
        raise RuntimeError("Cannot create HUD asset " + path)
    for graph in list(unreal.BlueprintEditorLibrary.list_graphs(blueprint)):
        if graph.get_name() not in ("EventGraph", "UserConstructionScript"):
            unreal.BlueprintEditorLibrary.remove_function_graph(blueprint, graph.get_name())
        elif graph.get_name() == "EventGraph":
            editor = unreal.BlueprintGraphEditor.get_graph_editor(graph)
            editor.remove_nodes(editor.list_all_nodes())
    _compile(blueprint)
    return blueprint


def _concat(editor, first, second):
    node = _call(editor, "KismetStringLibrary.Concat_StrStr")
    for pin, value in (("A", first), ("B", second)):
        if isinstance(value, tuple):
            _wire(value[0], value[1], node, pin)
        else:
            _value(node, pin, value)
    return node, "ReturnValue"


def _number(editor, source, pin, decimals=0):
    text = _call(editor, "KismetTextLibrary.Conv_DoubleToText")
    _wire(source, pin, text, "Value")
    _value(text, "bUseGrouping", "false")
    _value(text, "MinimumFractionalDigits", decimals)
    _value(text, "MaximumFractionalDigits", decimals)
    string = _call(editor, "KismetTextLibrary.Conv_TextToString")
    _wire(text, "ReturnValue", string, "InText")
    return string, "ReturnValue"


def _select_string(editor, condition, first, second):
    node = _call(editor, "KismetMathLibrary.SelectString")
    for pin, value in (("A", first), ("B", second)):
        if isinstance(value, tuple):
            _wire(value[0], value[1], node, pin)
        else:
            _value(node, pin, value)
    _wire(condition, "ReturnValue", node, "bPickA")
    return node, "ReturnValue"


def _metres(editor, variable, name):
    height = _call(editor, "KismetMathLibrary.Divide_DoubleDouble")
    _wire(variable, name, height, "A")
    _value(height, "B", 100)
    return _number(editor, height, "ReturnValue", 1)


def _read_state(blueprint, controller_class):
    editor = unreal.BlueprintGraphEditor.create_and_edit_function_graph(blueprint, "ReadAimState")
    string_type = unreal.BlueprintEditorLibrary.get_basic_type_by_name("string")
    result = editor.add_graph_output_parameter("WeaponText", string_type)
    editor.add_graph_output_parameter("AimText", string_type)
    editor.add_graph_output_parameter("ControlsText", string_type)
    editor.add_graph_output_parameter("ActionText", string_type)
    editor.set_function_is_public()
    entry = editor.find_graph_entry_pin().get_owning_node()
    find = _call(editor, "GameplayStatics.GetActorOfClass")
    _value(find, "ActorClass", controller_class.get_path_name())
    _exec(entry, find)
    valid = _call(editor, "KismetSystemLibrary.IsValid")
    _wire(find, "ReturnValue", valid, "Object")
    branch = editor.add_branch_node()
    _wire(valid, "ReturnValue", branch, "Condition")
    _exec(find, branch)
    _exec(branch, result)
    fallback = editor.add_return_node()
    _value(fallback, "WeaponText", "火箭")
    _value(fallback, "AimText", "正在准备试验场")
    _value(fallback, "ControlsText", "A/D 调整方向 · 上下方向键调整角度")
    _value(fallback, "ActionText", "空格发射 · R 重建场景")
    _exec(branch, fallback, "else")

    variables = {}
    for name in ("WeaponMode", "YawDegrees", "PitchDegrees", "LaunchHeight",
                 "MeteorTargetX", "MeteorTargetY", "MeteorAngle"):
        node = editor.add_get_member_variable_node(name, controller_class.get_path_name())
        if not node:
            raise RuntimeError("Cannot create controller property getter " + name)
        # GetActorOfClass has DeterminesOutputType="ActorClass", so setting its
        # class pin creates a native typed controller reference here.
        _wire(find, "ReturnValue", node, "self")
        variables[name] = node
    equal_rocket = _call(editor, "KismetMathLibrary.EqualEqual_IntInt")
    _wire(variables["WeaponMode"], "WeaponMode", equal_rocket, "A")
    _value(equal_rocket, "B", 0)
    equal_meteor = _call(editor, "KismetMathLibrary.EqualEqual_IntInt")
    _wire(variables["WeaponMode"], "WeaponMode", equal_meteor, "A")
    _value(equal_meteor, "B", 1)
    meteor_embeds = _call(editor, "KismetMathLibrary.GreaterEqual_DoubleDouble")
    _wire(variables["MeteorAngle"], "MeteorAngle", meteor_embeds, "A")
    _value(meteor_embeds, "B", 78)
    meteor_weapon = _select_string(editor, meteor_embeds,
                                    "陨石 · 嵌入冲击",
                                    "陨石 · 贯穿冲击")
    meteor_or_ball = _select_string(editor, equal_meteor, meteor_weapon, "铁球 · 重力撞击")
    weapon = _select_string(editor, equal_rocket, "火箭 · 高速贯穿", meteor_or_ball)
    _wire(weapon[0], weapon[1], result, "WeaponText")

    state = "方向 "
    for item in (_number(editor, variables["YawDegrees"], "YawDegrees"),
                 "°    仰角 ", _number(editor, variables["PitchDegrees"], "PitchDegrees"),
                 "°    高度 ", _metres(editor, variables["LaunchHeight"], "LaunchHeight"), " 米"):
        state = _concat(editor, state, item)
    meteor_state = "横向落点 "
    for item in (_metres(editor, variables["MeteorTargetX"], "MeteorTargetX"), " 米  纵向 ",
                 _metres(editor, variables["MeteorTargetY"], "MeteorTargetY"), " 米  入射角 ",
                 _number(editor, variables["MeteorAngle"], "MeteorAngle"), "°"):
        meteor_state = _concat(editor, meteor_state, item)
    aim = _select_string(editor, equal_meteor, meteor_state, state)
    _wire(aim[0], aim[1], result, "AimText")
    controls = _select_string(editor, equal_meteor, "A/D 横向落点 · W/S 纵向落点 · 上下键调整角度",
                              "A/D 调整方向 · 上下键调整角度")
    _wire(controls[0], controls[1], result, "ControlsText")
    action = _select_string(editor, equal_meteor, "空格发射 · R 重建 · 小于78°贯穿，达到78°嵌入",
                            "W/S 调整高度 · 空格发射 · R 重建")
    _wire(action[0], action[1], result, "ActionText")
    _compile(blueprint)


def _offset_x(editor, base, amount):
    offset = _call(editor, "KismetMathLibrary.Add_DoubleDouble")
    _wire(base, "ReturnValue", offset, "A")
    _value(offset, "B", amount)
    return offset


def _draw_text(editor, source, text, position, y, color, scale=1.0):
    node = _call(editor, "HUD.DrawText")
    if isinstance(text, tuple):
        _wire(text[0], text[1], node, "Text")
    else:
        _value(node, "Text", text)
    _wire(position, "ReturnValue", node, "ScreenX")
    _value(node, "ScreenY", y)
    _value(node, "TextColor", color)
    _value(node, "Scale", scale)
    _value(node, "bScalePosition", "false")
    if unreal.load_asset(FONT_PATH):
        _value(node, "Font", FONT_PATH)
    _exec(source, node)
    return node


def build_hud(controller_blueprint):
    """Return the HUD Blueprint and GameMode class for level/config assignment."""
    if isinstance(controller_blueprint, str):
        controller_blueprint = unreal.load_asset(controller_blueprint)
    if not controller_blueprint:
        raise RuntimeError("ControllerV2 must exist before building its HUD")
    controller_class = controller_blueprint.generated_class()
    from ui_fonts import build_ui_fonts
    from ui_actions import build_ui_actions
    from ui_canvas import draw_ui
    fonts = build_ui_fonts()
    hud = _asset("BP_SkyfallHUDV2", unreal.HUD)
    build_ui_actions(hud, controller_class)
    _read_state(hud, controller_class)
    draw_ui(hud, controller_class, fonts)
    unreal.get_default_object(hud.generated_class()).set_editor_property("show_hud", True)
    _save(hud)

    mode = _asset("BP_SkyfallGameModeV2", unreal.GameModeBase)
    defaults = unreal.get_default_object(mode.generated_class())
    defaults.set_editor_property("hud_class", hud.generated_class())
    # The authored map owns the free-camera actor and actor-based weapon input.
    defaults.set_editor_property("default_pawn_class", None)
    _save(mode)
    unreal.log("SKYFALL_HUD_V2_READY " + hud.get_path_name())
    return {"hud_blueprint": hud, "game_mode_class": mode.generated_class()}


def main():
    # AHUD is not placeable through EditorActorSubsystem. Check the installed
    # HUD owned by the PIE player instead; this route does not rebuild assets.
    from runtime_hud_v2 import start
    start()


if __name__ == "__main__":
    main()
