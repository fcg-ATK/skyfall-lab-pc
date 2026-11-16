"""Author a visible mesh arrow and instanced ballistic guide in native Blueprint."""
import unreal

from blueprint_runtime import _call, _compile, _exec, _graph, _save, _value, _wire
from combat_v2 import _connect, _get, _math
from editor_toolset.toolsets.actor import ActorTools


def _guide_material():
    path = "/Game/Skyfall/CombatV2/Materials/M_AimGuide"
    material = unreal.load_asset(path)
    if material is None:
        material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            "M_AimGuide", "/Game/Skyfall/CombatV2/Materials", unreal.Material, unreal.MaterialFactoryNew())
    material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
    for expression in list(unreal.MaterialEditingLibrary.get_material_expressions(material)):
        unreal.MaterialEditingLibrary.delete_material_expression(material, expression)
    color = unreal.MaterialEditingLibrary.create_material_expression(
        material, unreal.MaterialExpressionConstant3Vector)
    color.set_editor_property("constant", unreal.LinearColor(1.0, 0.55, 0.02, 1))
    exposure = unreal.MaterialEditingLibrary.create_material_expression(
        material, unreal.MaterialExpressionEyeAdaptationInverse)
    if not unreal.MaterialEditingLibrary.connect_material_expressions(
            color, "", exposure, "LightValueInput"):
        raise RuntimeError("Cannot connect exposure-independent guide color")
    if not unreal.MaterialEditingLibrary.connect_material_property(
            exposure, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR):
        raise RuntimeError("Cannot connect the aiming material")
    material.set_editor_property("used_with_instanced_static_meshes", True)
    errors = list(unreal.MaterialEditingLibrary.recompile_material(material))
    if errors:
        raise RuntimeError(str(errors))
    unreal.EditorAssetLibrary.save_loaded_asset(material)
    return material


def _component(blueprint, name, component_type):
    default = unreal.get_default_object(blueprint.generated_class())
    for component in ActorTools.get_components(default, component_type.static_class()):
        if component.get_name().removesuffix("_GEN_VARIABLE") == name:
            return component
    return ActorTools.add_component(blueprint, component_type.static_class(), name)


def bind_placed_guides(controller_blueprint, arrow_mesh):
    """Refresh serialized level component overrides after Blueprint compilation."""
    material = _guide_material()
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
    for actor in actors:
        if actor.get_class() != controller_blueprint.generated_class():
            continue
        for name, mesh in (("AimGuideArrow", arrow_mesh),
                           ("AimGuideDots", unreal.load_asset("/Engine/BasicShapes/Sphere")),
                           ("AimMeteorTarget", unreal.load_asset("/Engine/BasicShapes/Cube"))):
            component = actor.get_editor_property(name)
            component.set_static_mesh(mesh)
            component.set_mobility(unreal.ComponentMobility.MOVABLE)
            component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
            component.set_editor_property("cast_shadow", False)
            component.set_editor_property("generate_overlap_events", False)
            component.set_hidden_in_game(False)
            for slot in range(component.get_num_materials()):
                component.set_material(slot, material)
        actor.call_method("UpdateAim")


def _trajectory_position(editor, origin, velocity, seconds):
    distance = _math(editor, "VLerp", A="(X=0,Y=0,Z=0)", B=velocity, Alpha=seconds)
    # With two literal pins the editor's promotable multiply can resolve to
    # a FrameRate overload. Constant sample times can be squared at authoring.
    squared = seconds * seconds if isinstance(seconds, (int, float)) else _math(
        editor, "Multiply_DoubleDouble", A=seconds, B=seconds)
    half_gravity = _math(editor, "Multiply_DoubleDouble", A=_get(editor, "GravityZ"), B=0.5)
    drop = _math(editor, "Multiply_DoubleDouble", A=half_gravity, B=squared)
    fall = _math(editor, "MakeVector", X=0, Y=0, Z=drop)
    direct = _math(editor, "Add_VectorVector", A=origin, B=distance)
    return _math(editor, "Add_VectorVector", A=direct, B=fall)


def _meteor_marker(editor, source, meteor_selected, marker_component):
    """The ring marks the selected roof-height plane, not a collision result."""
    branch = editor.add_branch_node()
    _connect(meteor_selected, branch, "Condition")
    _exec(source, branch, "Completed")
    target = editor.add_call_function_node("GetMeteorTarget")
    if not target:
        raise RuntimeError("Controller must provide GetMeteorTarget before installing its guide")
    loop = editor.add_macro_node("/Engine/EditorBlueprintResources/StandardMacros.StandardMacros:ForLoop")
    _value(loop, "FirstIndex", 0)
    _value(loop, "LastIndex", 23)
    _exec(branch, loop)
    index = _math(editor, "Conv_IntToDouble", InInt=(loop, "Index"))
    angle = _math(editor, "Multiply_DoubleDouble", A=index, B=15)
    x = _math(editor, "Multiply_DoubleDouble", A=_math(editor, "DegCos", A=angle), B=205)
    y = _math(editor, "Multiply_DoubleDouble", A=_math(editor, "DegSin", A=angle), B=205)
    offset = _math(editor, "MakeVector", X=x, Y=y, Z=12)
    position = _math(editor, "Add_VectorVector", A=(target, "Output"), B=offset)
    tangent = _math(editor, "Add_DoubleDouble", A=angle, B=90)
    rotation = _math(editor, "MakeRotator", Yaw=tangent)
    transform = _math(editor, "MakeTransform", Location=position, Rotation=rotation,
                      Scale="(X=0.46,Y=0.10,Z=0.06)")
    add = _call(editor, "InstancedStaticMeshComponent.AddInstance")
    _wire(marker_component, "AimMeteorTarget", add, "self")
    _connect(transform, add, "InstanceTransform")
    _value(add, "bWorldSpace", "true")
    _exec(loop, add, "LoopBody")

    centre = _math(editor, "Add_VectorVector", A=(target, "Output"), B="(X=0,Y=0,Z=12)")
    tail, pin = loop, "Completed"
    for yaw in (0, 90):
        rotation = _math(editor, "MakeRotator", Yaw=yaw)
        transform = _math(editor, "MakeTransform", Location=centre, Rotation=rotation,
                          Scale="(X=1.10,Y=0.08,Z=0.06)")
        add = _call(editor, "InstancedStaticMeshComponent.AddInstance")
        _wire(marker_component, "AimMeteorTarget", add, "self")
        _connect(transform, add, "InstanceTransform")
        _value(add, "bWorldSpace", "true")
        _exec(tail, add, pin)
        tail, pin = add, "then"


def install_aim(controller_blueprint, arrow_mesh, materials=None):
    """Populate the controller's UpdateAim without altering its combat graphs."""
    material = _guide_material()
    definitions = (("AimGuideDots", unreal.InstancedStaticMeshComponent),
                   ("AimGuideArrow", unreal.StaticMeshComponent),
                   ("AimMeteorTarget", unreal.InstancedStaticMeshComponent))
    for name, kind in definitions:
        _component(controller_blueprint, name, kind)
    _compile(controller_blueprint)
    # Adding SCS components can reinstate templates; use fresh references.
    dots, arrow, marker = [_component(controller_blueprint, name, kind) for name, kind in definitions]
    dots.set_static_mesh(unreal.load_asset("/Engine/BasicShapes/Sphere"))
    arrow.set_static_mesh(arrow_mesh)
    marker.set_static_mesh(unreal.load_asset("/Engine/BasicShapes/Cube"))
    for component in (dots, arrow, marker):
        component.set_mobility(unreal.ComponentMobility.MOVABLE)
        component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
        for slot in range(component.get_num_materials()):
            component.set_material(slot, material)
        component.set_editor_property("cast_shadow", False)
        component.set_editor_property("generate_overlap_events", False)
        component.set_hidden_in_game(False)
    _compile(controller_blueprint)

    editor = _graph(controller_blueprint, "UpdateAim")
    entry = editor.find_graph_entry_pin().get_owning_node()
    editor.remove_nodes([node for node in editor.list_all_nodes() if node != entry])
    origin = editor.add_call_function_node("GetLaunchOrigin")
    velocity = editor.add_call_function_node("GetLaunchVelocity")
    dot_component = editor.add_get_member_variable_node("AimGuideDots")
    arrow_component = editor.add_get_member_variable_node("AimGuideArrow")
    marker_component = editor.add_get_member_variable_node("AimMeteorTarget")
    meteor_selected = _math(editor, "EqualEqual_IntInt", A=_get(editor, "WeaponMode"), B=1)

    clear = _call(editor, "InstancedStaticMeshComponent.ClearInstances")
    _wire(dot_component, "AimGuideDots", clear, "self")
    _exec(entry, clear)
    clear_marker = _call(editor, "InstancedStaticMeshComponent.ClearInstances")
    _wire(marker_component, "AimMeteorTarget", clear_marker, "self")
    _exec(clear, clear_marker)

    direction = _math(editor, "Normal", A=(velocity, "Output"))
    tip_offset = _math(editor, "VLerp", A="(X=0,Y=0,Z=0)", B=direction, Alpha=140)
    launch_arrow = _math(editor, "Add_VectorVector", A=(origin, "Output"), B=tip_offset)
    meteor_arrow = _trajectory_position(editor, (origin, "Output"), (velocity, "Output"), 0.86)
    arrow_position = _math(editor, "SelectVector", A=meteor_arrow, B=launch_arrow, bPickA=meteor_selected)
    downward = _math(editor, "Multiply_DoubleDouble", A=_get(editor, "GravityZ"), B=0.86)
    local_gravity = _math(editor, "MakeVector", Z=downward)
    meteor_velocity = _math(editor, "Add_VectorVector", A=(velocity, "Output"), B=local_gravity)
    arrow_velocity = _math(editor, "SelectVector", A=meteor_velocity,
                           B=(velocity, "Output"), bPickA=meteor_selected)
    rotation = _math(editor, "MakeRotFromX", X=arrow_velocity)
    arrow_length = arrow_mesh.get_bounds().box_extent.x * 2.0
    if arrow_length <= 0:
        raise RuntimeError("The aiming arrow has no forward extent")
    arrow_scale = 200.0 / arrow_length
    arrow_transform = _math(editor, "MakeTransform", Location=arrow_position,
                            Rotation=rotation,
                            Scale=f"(X={arrow_scale},Y={arrow_scale},Z={arrow_scale})")
    move_arrow = _call(editor, "SceneComponent.K2_SetWorldTransform")
    _wire(arrow_component, "AimGuideArrow", move_arrow, "self")
    _connect(arrow_transform, move_arrow, "NewTransform")
    _exec(clear_marker, move_arrow)

    loop = editor.add_macro_node("/Engine/EditorBlueprintResources/StandardMacros.StandardMacros:ForLoop")
    _value(loop, "FirstIndex", 1)
    _value(loop, "LastIndex", 26)
    _exec(move_arrow, loop)
    index_value = _math(editor, "Conv_IntToDouble", InInt=(loop, "Index"))
    interval = _math(editor, "SelectFloat", A=1.0/26, B=0.026, bPickA=meteor_selected)
    seconds = _math(editor, "Multiply_DoubleDouble", A=index_value, B=interval)
    position = _trajectory_position(editor, (origin, "Output"), (velocity, "Output"), seconds)
    transform = _math(editor, "MakeTransform", Location=position, Scale="(X=0.18,Y=0.18,Z=0.18)")
    add = _call(editor, "InstancedStaticMeshComponent.AddInstance")
    _wire(dot_component, "AimGuideDots", add, "self")
    _connect(transform, add, "InstanceTransform")
    _value(add, "bWorldSpace", "true")
    _exec(loop, add, "LoopBody")
    _meteor_marker(editor, loop, meteor_selected, marker_component)
    _save(controller_blueprint)
    return {"material": material, "dot_count": 26, "arrow_component": "AimGuideArrow",
            "meteor_marker_component": "AimMeteorTarget", "meteor_marker_instances": 26,
            "meteor_marker_plane_cm": 2075, "meteor_path_seconds": 1.0}
