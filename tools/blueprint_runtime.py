"""Build native Blueprint gameplay. Python is used only while authoring assets."""

import unreal


ASSET_FOLDER = "/Game/Skyfall/Blueprints"
PROJECTILE_TAG = "SkyfallProjectile"
PIECE_TAG = "SkyfallPiece"


def _vector(value):
    if isinstance(value, unreal.Vector):
        return value
    return unreal.Vector(*value)


def _pin(node, name, output=False):
    pin = node.find_output_pin(name) if output else node.find_input_pin(name)
    if not pin.is_valid():
        available = [str(item.get_pin_name()) for item in node.list_all_pins()]
        raise RuntimeError(f"Missing pin {name} on {node.get_node_title()}. Pins {available}")
    return pin


def _wire(source, source_name, target, target_name):
    if not _pin(source, source_name, True).try_create_connection(_pin(target, target_name)):
        raise RuntimeError(f"Could not connect {source.get_node_title()}.{source_name} to "
                           f"{target.get_node_title()}.{target_name}")


def _value(node, name, value):
    if not _pin(node, name).set_pin_value(str(value)):
        raise RuntimeError(f"Invalid default {name}={value} on {node.get_node_title()}")


def _call(editor, function):
    path = function if function.startswith("/") else f"/Script/Engine.{function}"
    node = editor.add_call_function_node(path)
    if not node:
        raise RuntimeError(f"Cannot create native function node {path}")
    return node


def _exec(source, target, source_name="then"):
    _wire(source, source_name, target, "execute")


def _compile(blueprint):
    unreal.BlueprintEditorLibrary.compile_blueprint(blueprint)
    status = blueprint.get_editor_property("status")
    if status != unreal.BlueprintStatus.BS_UP_TO_DATE:
        errors = []
        for graph in unreal.BlueprintEditorLibrary.list_graphs(blueprint):
            editor = unreal.BlueprintGraphEditor.get_graph_editor(graph)
            errors.extend(str(node.get_editor_property("error_msg"))
                          for node in editor.list_nodes_with_errors())
        raise RuntimeError(f"Blueprint compilation failed {blueprint.get_path_name()} {errors}")


def _blueprint(name, parent_class):
    path = f"{ASSET_FOLDER}/{name}"
    blueprint = unreal.load_asset(path) if unreal.EditorAssetLibrary.does_asset_exist(path) else None
    if not blueprint:
        factory = unreal.BlueprintFactory()
        factory.set_editor_property("parent_class", parent_class)
        blueprint = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            name, ASSET_FOLDER, unreal.Blueprint, factory)
    if not blueprint:
        raise RuntimeError(f"Cannot create Blueprint {path}")
    for graph in list(unreal.BlueprintEditorLibrary.list_graphs(blueprint)):
        if graph.get_name() not in ("EventGraph", "UserConstructionScript"):
            unreal.BlueprintEditorLibrary.remove_function_graph(blueprint, graph.get_name())
    for graph in unreal.BlueprintEditorLibrary.list_graphs(blueprint):
        if graph.get_name() != "EventGraph":
            continue
        editor = unreal.BlueprintGraphEditor.get_graph_editor(graph)
        editor.remove_nodes(editor.list_all_nodes())
    _compile(blueprint)
    return blueprint


def _graph(blueprint, name="EventGraph"):
    editor = unreal.BlueprintGraphEditor.get_graph_editor_by_name(blueprint, name)
    if not editor:
        editor = unreal.BlueprintGraphEditor.create_and_edit_function_graph(blueprint, name)
    return editor


def _event(blueprint, name, position=(0, 0)):
    node = unreal.BlueprintEditorLibrary.add_event_override(
        blueprint, name, unreal.IntPoint(*position))
    if not node:
        raise RuntimeError(f"Cannot add event {name}")
    return node


def _add_variable(blueprint, name, type_name):
    names = [str(item) for item in
             unreal.BlueprintEditorLibrary.list_member_variable_names(blueprint, False)]
    if name not in names:
        pin_type = unreal.BlueprintEditorLibrary.get_basic_type_by_name(type_name)
        if not unreal.BlueprintEditorLibrary.add_member_variable(blueprint, name, pin_type):
            raise RuntimeError(f"Cannot add variable {name}")
    _compile(blueprint)


def _input(editor, available_nodes, key):
    aliases = {"SpaceBar": "SpaceBar", "LeftMouseButton": "LeftMouseButton"}
    expected = aliases.get(key, key).lower()
    choices = [item for item in available_nodes
               if item.startswith("Input|") and item.rsplit("|", 1)[-1].lower() == expected]
    if len(choices) != 1:
        raise RuntimeError(f"Cannot uniquely resolve key {key}. Candidates {choices}")
    node = editor.create_node_from_name(choices[0], unreal.Vector2D(0, 0), [])
    if not node:
        raise RuntimeError(f"Cannot create input event {choices[0]}")
    return node


def _save(blueprint):
    _compile(blueprint)
    # The toolset formatter produces readable graph layouts without changing logic.
    from editor_toolset.toolsets.blueprint import BlueprintTools
    for graph in unreal.BlueprintEditorLibrary.list_graphs(blueprint):
        editor = unreal.BlueprintGraphEditor.get_graph_editor(graph)
        BlueprintTools.arrange_nodes(list(editor.list_all_nodes()))
    unreal.EditorAssetLibrary.save_loaded_asset(blueprint, only_if_is_dirty=False)


def _build_dust(material):
    blueprint = _blueprint("BP_SkyfallDust", unreal.StaticMeshActor)
    cdo = unreal.get_default_object(blueprint.generated_class())
    component = cdo.static_mesh_component
    component.set_mobility(unreal.ComponentMobility.MOVABLE)
    component.set_static_mesh(unreal.load_asset("/Engine/BasicShapes/Sphere"))
    component.set_material(0, unreal.load_asset(material) if isinstance(material, str) else material)
    component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
    component.set_editor_property("cast_shadow", False)
    cdo.set_editor_property("initial_life_span", 5.2)

    editor = _graph(blueprint)
    begin = _event(blueprint, "ReceiveBeginPlay")
    mesh = editor.add_get_member_variable_node("StaticMeshComponent", "/Script/Engine.StaticMeshActor")
    dynamic = _call(editor, "PrimitiveComponent.CreateDynamicMaterialInstance")
    _value(dynamic, "ElementIndex", 0)
    _wire(mesh, "StaticMeshComponent", dynamic, "self")
    _exec(begin, dynamic)
    time = _call(editor, "KismetSystemLibrary.GetGameTimeInSeconds")
    birth = _call(editor, "MaterialInstanceDynamic.SetScalarParameterValue")
    _value(birth, "ParameterName", "BirthTime")
    _wire(time, "ReturnValue", birth, "Value")
    _wire(dynamic, "ReturnValue", birth, "self")
    _exec(dynamic, birth)
    lifetime = _call(editor, "MaterialInstanceDynamic.SetScalarParameterValue")
    _value(lifetime, "ParameterName", "Lifetime")
    _value(lifetime, "Value", 5)
    _wire(dynamic, "ReturnValue", lifetime, "self")
    _exec(birth, lifetime)

    tick = _event(blueprint, "ReceiveTick", (0, 600))
    expansion_rate = _call(editor, "KismetMathLibrary.MakeVector")
    for axis in ("X", "Y", "Z"):
        _value(expansion_rate, axis, "0.9")
    expansion = _call(editor, "KismetMathLibrary.Multiply_VectorFloat")
    _wire(expansion_rate, "ReturnValue", expansion, "A")
    _wire(tick, "DeltaSeconds", expansion, "B")
    scale = _call(editor, "Actor.GetActorScale3D")
    larger = _call(editor, "KismetMathLibrary.Add_VectorVector")
    _wire(scale, "ReturnValue", larger, "A")
    _wire(expansion, "ReturnValue", larger, "B")
    set_scale = _call(editor, "Actor.SetActorScale3D")
    _wire(larger, "ReturnValue", set_scale, "NewScale3D")
    _exec(tick, set_scale)
    drift_rate = _call(editor, "KismetMathLibrary.MakeVector")
    _value(drift_rate, "X", 25)
    _value(drift_rate, "Y", 10)
    _value(drift_rate, "Z", 90)
    drift = _call(editor, "KismetMathLibrary.Multiply_VectorFloat")
    _wire(drift_rate, "ReturnValue", drift, "A")
    _wire(tick, "DeltaSeconds", drift, "B")
    move = _call(editor, "Actor.K2_AddActorWorldOffset")
    _wire(drift, "ReturnValue", move, "DeltaLocation")
    _exec(set_scale, move)
    _save(blueprint)
    return blueprint


def _build_piece(dust=None):
    blueprint = _blueprint("BP_SkyfallPiece", unreal.StaticMeshActor)
    _add_variable(blueprint, "HasActivated", "bool")
    if dust:
        _add_variable(blueprint, "EmitDust", "bool")
        unreal.BlueprintEditorLibrary.set_blueprint_variable_instance_editable(blueprint, "EmitDust", True)
        _compile(blueprint)
    editor = _graph(blueprint)
    hit = _event(blueprint, "ReceiveHit")
    activated = editor.add_get_member_variable_node("HasActivated")
    first_hit = editor.add_branch_node()
    _exec(hit, first_hit)
    _wire(activated, "HasActivated", first_hit, "Condition")

    projectile_tag = _call(editor, "Actor.ActorHasTag")
    piece_tag = _call(editor, "Actor.ActorHasTag")
    _value(projectile_tag, "Tag", PROJECTILE_TAG)
    _value(piece_tag, "Tag", PIECE_TAG)
    _wire(hit, "Other", projectile_tag, "self")
    _wire(hit, "Other", piece_tag, "self")
    eligible = _call(editor, "KismetMathLibrary.BooleanOR")
    _wire(projectile_tag, "ReturnValue", eligible, "A")
    other_velocity = _call(editor, "Actor.GetVelocity")
    _wire(hit, "Other", other_velocity, "self")
    speed = _call(editor, "KismetMathLibrary.VSize")
    _wire(other_velocity, "ReturnValue", speed, "A")
    moving = _call(editor, "KismetMathLibrary.Greater_DoubleDouble")
    _wire(speed, "ReturnValue", moving, "A")
    _value(moving, "B", "220")
    moving_piece = _call(editor, "KismetMathLibrary.BooleanAND")
    _wire(piece_tag, "ReturnValue", moving_piece, "A")
    _wire(moving, "ReturnValue", moving_piece, "B")
    _wire(moving_piece, "ReturnValue", eligible, "B")
    eligible_hit = editor.add_branch_node()
    _exec(first_hit, eligible_hit, "else")
    _wire(eligible, "ReturnValue", eligible_hit, "Condition")

    mark_active = editor.add_set_member_variable_node("HasActivated")
    _value(mark_active, "HasActivated", "true")
    _exec(eligible_hit, mark_active)
    simulate = _call(editor, "PrimitiveComponent.SetSimulatePhysics")
    _value(simulate, "bSimulate", "true")
    _wire(hit, "MyComp", simulate, "self")
    _exec(mark_active, simulate)

    # Transfer part of the striking object's velocity to the released fragment.
    # A downward component lets unsupported fragments load the floor below them.
    velocity = _call(editor, "Actor.GetVelocity")
    _wire(hit, "Other", velocity, "self")
    transfer = _call(editor, "KismetMathLibrary.Multiply_VectorFloat")
    _wire(velocity, "ReturnValue", transfer, "A")
    _value(transfer, "B", "0.16")
    falling = _call(editor, "KismetMathLibrary.Add_VectorVector")
    _wire(transfer, "ReturnValue", falling, "A")
    _value(falling, "B", "(X=0,Y=0,Z=-120)")
    set_velocity = _call(editor, "PrimitiveComponent.SetPhysicsLinearVelocity")
    _wire(hit, "MyComp", set_velocity, "self")
    _wire(falling, "ReturnValue", set_velocity, "NewVel")
    _exec(simulate, set_velocity)

    if dust:
        enabled = editor.add_get_member_variable_node("EmitDust")
        emit = editor.add_branch_node()
        _wire(enabled, "EmitDust", emit, "Condition")
        _exec(set_velocity, emit)
        transform = _call(editor, "KismetMathLibrary.MakeTransform")
        _wire(hit, "HitLocation", transform, "Location")
        spawn = _call(editor, "GameplayStatics.BeginDeferredActorSpawnFromClass")
        _value(spawn, "ActorClass", dust.generated_class().get_path_name())
        _value(spawn, "CollisionHandlingOverride", "AlwaysSpawn")
        _wire(transform, "ReturnValue", spawn, "SpawnTransform")
        _exec(emit, spawn)
        finish = _call(editor, "GameplayStatics.FinishSpawningActor")
        _wire(spawn, "ReturnValue", finish, "Actor")
        _wire(transform, "ReturnValue", finish, "SpawnTransform")
        _exec(spawn, finish)

    _save(blueprint)
    return blueprint


def _configure_physics(component, mass_kg, simulate=False):
    component.set_mobility(unreal.ComponentMobility.MOVABLE)
    component.set_collision_profile_name("PhysicsActor")
    component.set_collision_enabled(unreal.CollisionEnabled.QUERY_AND_PHYSICS)
    component.set_notify_rigid_body_collision(True)
    component.set_editor_property("generate_overlap_events", False)
    component.set_enable_gravity(True)
    component.set_linear_damping(0.35)
    component.set_angular_damping(1.0)
    component.set_max_depenetration_velocity("None", 90.0)
    component.set_mass_override_in_kg("None", float(mass_kg), True)
    component.set_use_ccd(True)
    component.set_simulate_physics(simulate)


def _build_ball(material=None):
    blueprint = _blueprint("BP_SkyfallBall", unreal.StaticMeshActor)
    cdo = unreal.get_default_object(blueprint.generated_class())
    component = cdo.static_mesh_component
    component.set_static_mesh(unreal.load_asset("/Engine/BasicShapes/Sphere"))
    if material:
        component.set_material(0, unreal.load_asset(material) if isinstance(material, str) else material)
    _configure_physics(component, 900, simulate=True)
    cdo.set_editor_property("tags", [unreal.Name(PROJECTILE_TAG)])
    cdo.set_editor_property("initial_life_span", 18.0)
    _save(blueprint)
    return blueprint


def _fire_function(blueprint, ball, launch_location):
    editor = unreal.BlueprintGraphEditor.create_and_edit_function_graph(blueprint, "FireBall")
    entry = editor.find_graph_entry_pin().get_owning_node()
    transform = _call(editor, "KismetMathLibrary.MakeTransform")
    location = _call(editor, "KismetMathLibrary.MakeVector")
    _value(location, "X", launch_location[0])
    _value(location, "Y", launch_location[1])
    height = editor.add_get_member_variable_node("LaunchHeight")
    _wire(height, "LaunchHeight", location, "Z")
    _wire(location, "ReturnValue", transform, "Location")
    _value(transform, "Scale", "(X=2,Y=2,Z=2)")
    spawn = _call(editor, "GameplayStatics.BeginDeferredActorSpawnFromClass")
    _value(spawn, "ActorClass", ball.generated_class().get_path_name())
    _value(spawn, "CollisionHandlingOverride", "AlwaysSpawn")
    _wire(transform, "ReturnValue", spawn, "SpawnTransform")
    _exec(entry, spawn)
    finish = _call(editor, "GameplayStatics.FinishSpawningActor")
    _wire(spawn, "ReturnValue", finish, "Actor")
    _wire(transform, "ReturnValue", finish, "SpawnTransform")
    _exec(spawn, finish)

    component = _call(editor, "Actor.GetComponentByClass")
    _value(component, "ComponentClass", "/Script/Engine.StaticMeshComponent")
    _wire(finish, "ReturnValue", component, "self")
    direction = _call(editor, "KismetMathLibrary.MakeVector")
    aim = editor.add_get_member_variable_node("AimX")
    _wire(aim, "AimX", direction, "X")
    _value(direction, "Y", "6100")
    _value(direction, "Z", "400")
    velocity = _call(editor, "PrimitiveComponent.SetPhysicsLinearVelocity")
    _wire(component, "ReturnValue", velocity, "self")
    _wire(direction, "ReturnValue", velocity, "NewVel")
    _exec(finish, velocity)


def _adjust_control(editor, source, name, amount, minimum, maximum):
    current = editor.add_get_member_variable_node(name)
    add = _call(editor, "KismetMathLibrary.Add_DoubleDouble")
    _wire(current, name, add, "A")
    _value(add, "B", amount)
    clamp = _call(editor, "KismetMathLibrary.FClamp")
    _wire(add, "ReturnValue", clamp, "Value")
    _value(clamp, "Min", minimum)
    _value(clamp, "Max", maximum)
    assign = editor.add_set_member_variable_node(name)
    _wire(clamp, "ReturnValue", assign, name)
    _exec(source, assign, "Pressed")
    updated = editor.add_get_member_variable_node(name)
    value_string = _call(editor, "KismetStringLibrary.Conv_DoubleToString")
    _wire(updated, name, value_string, "InDouble")
    message = _call(editor, "KismetStringLibrary.Concat_StrStr")
    _value(message, "A", f"{name} = ")
    _wire(value_string, "ReturnValue", message, "B")
    feedback = _call(editor, "KismetSystemLibrary.PrintString")
    _wire(message, "ReturnValue", feedback, "InString")
    _value(feedback, "Duration", 3)
    _value(feedback, "Key", "SkyfallState")
    _value(feedback, "bPrintToLog", "false")
    _exec(assign, feedback)


def _build_input_controller(ball, launch_location):
    blueprint = _blueprint("BP_SkyfallController", unreal.Actor)
    _add_variable(blueprint, "AimX", "real")
    _add_variable(blueprint, "LaunchHeight", "real")
    for name in ("AimX", "LaunchHeight"):
        unreal.BlueprintEditorLibrary.set_blueprint_variable_instance_editable(blueprint, name, True)
    _compile(blueprint)
    unreal.get_default_object(blueprint.generated_class()).set_editor_property(
        "LaunchHeight", float(launch_location[2]))
    _fire_function(blueprint, ball, launch_location)
    _compile(blueprint)
    editor = _graph(blueprint)
    begin = _event(blueprint, "ReceiveBeginPlay")
    player = _call(editor, "GameplayStatics.GetPlayerController")
    _value(player, "PlayerIndex", "0")
    enable = _call(editor, "Actor.EnableInput")
    _wire(player, "ReturnValue", enable, "PlayerController")
    _exec(begin, enable)
    instructions = _call(editor, "KismetSystemLibrary.PrintString")
    _value(instructions, "InString", "Space/Mouse fire | A/D aim | W/S height | R rebuild")
    _value(instructions, "Duration", 60)
    _value(instructions, "Key", "SkyfallHelp")
    _value(instructions, "bPrintToLog", "false")
    _exec(enable, instructions)

    # Resolve input names from the engine action database for the current version.
    available = list(editor.list_available_nodes([]))
    for key in ("LeftMouseButton", "SpaceBar"):
        event = _input(editor, available, key)
        fire = editor.add_call_function_node("FireBall")
        if not fire:
            raise RuntimeError("Cannot create call to FireBall")
        _exec(event, fire, "Pressed")
    reset = _input(editor, available, "R")
    level_name = _call(editor, "GameplayStatics.GetCurrentLevelName")
    _value(level_name, "bRemovePrefixString", "true")
    open_level = _call(editor, "GameplayStatics.OpenLevel")
    _wire(level_name, "ReturnValue", open_level, "LevelName")
    _exec(reset, level_name, "Pressed")
    _exec(level_name, open_level)

    for key, amount in (("A", -250), ("Left", -250), ("D", 250), ("Right", 250)):
        _adjust_control(editor, _input(editor, available, key), "AimX", amount, -2800, 2800)
    for key, amount in (("W", 250), ("S", -250)):
        _adjust_control(editor, _input(editor, available, key), "LaunchHeight", amount, 150, 3500)
    _save(blueprint)
    return blueprint


def build_controller(spawn_defs, ball_material=None, launch_location=(0, -5000, 900),
                     dust_material=None):
    """Create gameplay assets and place breakable pieces into the current level.

    Each item takes name, location, scale, mesh, material/materials, mass_kg,
    floor, and kind. Mesh and materials accept loaded assets or asset paths.
    All length and velocity values use Unreal centimetres.
    The caller supplies the camera, lighting, scenery, and saves the level.
    """
    unreal.InternationalizationLibrary.set_current_language("en", False)
    dust_blueprint = _build_dust(dust_material) if dust_material else None
    piece_blueprint = _build_piece(dust_blueprint)
    ball_blueprint = _build_ball(ball_material)
    controller_blueprint = _build_input_controller(ball_blueprint, launch_location)
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    pieces = []
    for index, definition in enumerate(spawn_defs):
        actor = actors.spawn_actor_from_class(
            piece_blueprint.generated_class(), _vector(definition["location"]))
        actor.set_actor_label(definition.get("name", f"Piece_{index:04d}"))
        actor.set_actor_scale3d(_vector(definition.get("scale", (1, 1, 1))))
        if "rotation" in definition:
            rotation = definition["rotation"]
            actor.set_actor_rotation(unreal.Rotator(*rotation), False)
        mesh = definition.get("mesh", "/Engine/BasicShapes/Cube")
        component = actor.static_mesh_component
        component.set_static_mesh(unreal.load_asset(mesh) if isinstance(mesh, str) else mesh)
        materials = definition.get("materials")
        if not materials and definition.get("material"):
            materials = [definition["material"]]
        for slot, material in enumerate(materials or []):
            component.set_material(slot, unreal.load_asset(material) if isinstance(material, str) else material)
        _configure_physics(component, definition.get("mass_kg", 1100), simulate=False)
        if dust_blueprint:
            actor.set_editor_property("EmitDust", index % 3 == 0 and definition.get("kind") != "glass")
        actor.set_editor_property("tags", [unreal.Name(PIECE_TAG),
                                           unreal.Name(f"Floor_{definition.get('floor', 0)}"),
                                           unreal.Name(definition.get("kind", "structure"))])
        pieces.append(actor)
    controller = actors.spawn_actor_from_class(controller_blueprint.generated_class(), unreal.Vector())
    controller.set_actor_label("Skyfall_Gameplay_Controller")
    unreal.log(f"SKYFALL_GAMEPLAY_READY pieces={len(pieces)}")
    return {"controller": controller, "pieces": pieces,
            "controller_blueprint": controller_blueprint,
            "piece_blueprint": piece_blueprint, "ball_blueprint": ball_blueprint,
            "dust_blueprint": dust_blueprint}
