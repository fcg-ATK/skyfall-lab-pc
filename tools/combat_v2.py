"""Author the second-generation demolition gameplay as native Blueprints.

Python creates assets and initializes level instances. All combat, fracture,
timing and input behavior runs in the generated Blueprints.
"""

import re

import unreal

from blueprint_runtime import (
    _blueprint, _call, _compile, _event, _exec, _graph, _input, _pin,
    _save, _value, _vector, _wire,
)


PIECE_TAG = "SkyfallPiece"
CONTROLLER_TAG = "SkyfallController"
WEAPONS = (
    {"name": "Rocket", "key": "rocket", "speed": 10000.0, "gravity": 0.0,
     "radius": 55.0, "damage": 1700.0, "scale": 100.0},
    {"name": "Meteor", "key": "meteor", "speed": 4200.0, "gravity": -1600.0,
     "radius": 170.0, "damage": 2800.0, "scale": 100.0},
    {"name": "Ball", "key": "ball", "speed": 7200.0, "gravity": -980.0,
     "radius": 100.0, "damage": 1950.0, "scale": 100.0},
)


def _type(name):
    library = unreal.BlueprintEditorLibrary
    if name == "vector":
        return library.get_struct_type(unreal.Vector.static_struct())
    if name == "actor":
        return library.get_object_reference_type(unreal.Actor)
    if name == "mesh":
        return library.get_object_reference_type(unreal.StaticMesh)
    return library.get_basic_type_by_name(name)


def _variables(blueprint, definitions):
    names = set(map(str, unreal.BlueprintEditorLibrary.list_member_variable_names(blueprint, False)))
    for name, type_name, default, editable in definitions:
        array = type_name.endswith("[]")
        pin_type = _type(type_name[:-2] if array else type_name)
        if array:
            pin_type = unreal.BlueprintEditorLibrary.get_array_type(pin_type)
        if name not in names:
            if not unreal.BlueprintEditorLibrary.add_member_variable(blueprint, name, pin_type):
                raise RuntimeError(f"Cannot add combat variable {name}")
        if editable:
            unreal.BlueprintEditorLibrary.set_blueprint_variable_instance_editable(blueprint, name, True)
    _compile(blueprint)
    cdo = unreal.get_default_object(blueprint.generated_class())
    for name, type_name, default, editable in definitions:
        if default is not None:
            cdo.set_editor_property(name, default)


def _get(editor, name):
    return editor.add_get_member_variable_node(name), name


def _connect(value, target, name):
    if isinstance(value, tuple) and len(value) == 2 and hasattr(value[0], "find_output_pin"):
        _wire(value[0], value[1], target, name)
    else:
        _value(target, name, value)


def _math(editor, function, **inputs):
    node = _call(editor, "KismetMathLibrary." + function)
    for name, value in inputs.items():
        _connect(value, node, name)
    return node, "ReturnValue"


def _set(editor, name, value):
    node = editor.add_set_member_variable_node(name)
    _connect(value, node, name)
    return node


def _branch(editor, condition):
    node = editor.add_branch_node()
    _connect(condition, node, "Condition")
    return node


def _function(blueprint, name, inputs=(), output=None, pure=False):
    editor = unreal.BlueprintGraphEditor.create_and_edit_function_graph(blueprint, name)
    for param, type_name in inputs:
        editor.add_graph_input_parameter(param, _type(type_name))
    result = editor.add_graph_output_parameter("Output", _type(output)) if output else None
    entry = editor.find_graph_entry_pin().get_owning_node()
    editor.set_function_is_public()
    if pure:
        # Purity removes execution pins from call sites. The function body still
        # needs its entry connected to its return node to evaluate outputs.
        _exec(entry, result)
        editor.set_is_pure_function(True)
    return editor, entry, result


def _loop(editor, array):
    node = editor.add_macro_node("/Engine/EditorBlueprintResources/StandardMacros.StandardMacros:ForEachLoop")
    if not node:
        raise RuntimeError("Cannot add native ForEachLoop macro")
    _connect(array, node, "Array")
    return node


def _component(editor, actor=None):
    if actor is None:
        return editor.add_get_member_variable_node("StaticMeshComponent", "/Script/Engine.StaticMeshActor"), "StaticMeshComponent"
    node = _call(editor, "Actor.GetComponentByClass")
    _value(node, "ComponentClass", "/Script/Engine.StaticMeshComponent")
    _connect(actor, node, "self")
    return node, "ReturnValue"


def _location(editor, actor=None):
    node = _call(editor, "Actor.K2_GetActorLocation")
    if actor:
        _connect(actor, node, "self")
    return node, "ReturnValue"


def _self(editor):
    candidates = [name for name in editor.list_available_nodes([])
                  if str(name).rsplit("|", 1)[-1].lower() in ("self", "getareferencetoself", "get a reference to self")]
    if len(candidates) != 1:
        raise RuntimeError("Cannot resolve self node: " + str([str(n) for n in editor.list_available_nodes([]) if "self" in str(n).lower()]))
    return editor.create_node_from_name(candidates[0], unreal.Vector2D(), [])


def _spawn(editor, blueprint, transform, source, source_pin="then"):
    node = _call(editor, "GameplayStatics.BeginDeferredActorSpawnFromClass")
    generated = blueprint.generated_class() if isinstance(blueprint, unreal.Blueprint) else blueprint
    _value(node, "ActorClass", generated.get_path_name() if hasattr(generated, "get_path_name") else generated)
    _value(node, "CollisionHandlingOverride", "AlwaysSpawn")
    _connect(transform, node, "SpawnTransform")
    _exec(source, node, source_pin)
    return node


def _finish(editor, spawn, transform, source=None):
    node = _call(editor, "GameplayStatics.FinishSpawningActor")
    _wire(spawn, "ReturnValue", node, "Actor")
    _connect(transform, node, "SpawnTransform")
    _exec(source or spawn, node)
    return node


def _effect(editor, effect_class, location, source, source_pin="then", scale=1.0,
            strength=None):
    if not effect_class:
        return source
    transform = _math(editor, "MakeTransform", Location=location,
                      Scale=f"(X={scale},Y={scale},Z={scale})")
    spawn = _spawn(editor, effect_class, transform, source, source_pin)
    tail = spawn
    if strength is not None:
        intensity = _call(editor, "KismetSystemLibrary.SetDoublePropertyByName")
        _wire(spawn, "ReturnValue", intensity, "Object")
        _value(intensity, "PropertyName", "Strength")
        _value(intensity, "Value", strength)
        _exec(spawn, intensity)
        tail = intensity
    return _finish(editor, spawn, transform, tail)


def _impact_shake():
    blueprint = _blueprint("BP_SkyfallImpactShake", unreal.LegacyCameraShake)
    default = unreal.get_default_object(blueprint.generated_class())
    default.set_editor_property("single_instance", True)
    default.set_editor_property("oscillation_duration", 0.22)
    default.set_editor_property("oscillation_blend_in_time", 0.015)
    default.set_editor_property("oscillation_blend_out_time", 0.17)
    rotation = unreal.ROscillator()
    for axis, amplitude, frequency in (("pitch", 0.19, 42), ("yaw", 0.13, 51), ("roll", 0.08, 37)):
        oscillator = unreal.FOscillator()
        oscillator.set_editor_property("amplitude", amplitude)
        oscillator.set_editor_property("frequency", frequency)
        rotation.set_editor_property(axis, oscillator)
    default.set_editor_property("rot_oscillation", rotation)
    _save(blueprint)
    return blueprint


def _physics(component, mass, simulate=False, fragments=False):
    component.set_mobility(unreal.ComponentMobility.MOVABLE)
    component.set_collision_profile_name("PhysicsActor")
    component.set_collision_enabled(unreal.CollisionEnabled.QUERY_AND_PHYSICS)
    component.set_notify_rigid_body_collision(True)
    component.set_editor_property("generate_overlap_events", False)
    component.set_enable_gravity(True)
    component.set_linear_damping(0.16)
    component.set_angular_damping(0.65)
    component.set_mass_override_in_kg("None", float(mass), True)
    component.set_use_ccd(True)
    component.set_max_depenetration_velocity("None", 800.0)
    if fragments:
        # A dedicated debris channel keeps debris/structure contacts while
        # avoiding the quadratic contact load between secondary fragments.
        component.set_collision_profile_name("DemolitionDebris")
        component.set_use_ccd(False)
    component.set_simulate_physics(simulate)


def _build_fragment():
    blueprint = _blueprint("BP_SkyfallFragmentV2", unreal.StaticMeshActor)
    cdo = unreal.get_default_object(blueprint.generated_class())
    cdo.static_mesh_component.set_static_mesh(unreal.load_asset("/Engine/BasicShapes/Cube"))
    _physics(cdo.static_mesh_component, 55, fragments=True)
    cdo.set_editor_property("tags", [unreal.Name("SkyfallFragment")])
    cdo.set_editor_property("initial_life_span", 65.0)
    _save(blueprint)
    return blueprint


def _build_fracture_function(blueprint, fragment, effects):
    editor, entry, _ = _function(blueprint, "Fracture")
    guard = _branch(editor, _get(editor, "HasFractured"))
    _exec(entry, guard)
    meshes = _get(editor, "FractureMeshes")
    length = _call(editor, "KismetArrayLibrary.Array_Length")
    _connect(meshes, length, "TargetArray")
    has_meshes = _branch(editor, _math(editor, "Greater_IntInt", A=(length, "ReturnValue"), B=0))
    _exec(guard, has_meshes, "else")
    mark = _set(editor, "HasFractured", "true")
    _exec(has_meshes, mark)
    activated = _set(editor, "HasActivated", "true")
    _exec(mark, activated)
    collision = _call(editor, "Actor.SetActorEnableCollision")
    _value(collision, "bNewActorEnableCollision", "false")
    _exec(activated, collision)
    hidden = _call(editor, "Actor.SetActorHiddenInGame")
    _value(hidden, "bNewHidden", "true")
    _exec(collision, hidden)
    loop = _loop(editor, meshes)
    _wire(hidden, "then", loop, "Exec")

    parent_transform = _call(editor, "Actor.GetTransform")
    split = _call(editor, "KismetMathLibrary.BreakTransform")
    _wire(parent_transform, "ReturnValue", split, "InTransform")
    offset = _call(editor, "KismetArrayLibrary.Array_Get")
    _connect(_get(editor, "FractureOffsets"), offset, "TargetArray")
    _wire(loop, "Array Index", offset, "Index")
    world_position = _math(editor, "TransformLocation", T=(parent_transform, "ReturnValue"),
                           Location=(offset, "Item"))
    transform = _math(editor, "MakeTransform", Location=world_position,
                      Rotation=(split, "Rotation"), Scale=(split, "Scale"))
    spawn = _spawn(editor, fragment, transform, loop, "LoopBody")
    component = _component(editor, (spawn, "ReturnValue"))
    mesh = _call(editor, "StaticMeshComponent.SetStaticMesh")
    _connect(component, mesh, "self")
    _wire(loop, "Array Element", mesh, "NewMesh")
    _exec(spawn, mesh)
    finish = _finish(editor, spawn, transform, mesh)
    simulate = _call(editor, "PrimitiveComponent.SetSimulatePhysics")
    _connect(component, simulate, "self")
    _value(simulate, "bSimulate", "true")
    _exec(finish, simulate)
    radial = _math(editor, "Subtract_VectorVector", A=world_position, B=_location(editor))
    radial = _math(editor, "Normal", A=radial)
    outward = _math(editor, "Multiply_VectorFloat", A=radial, B=320)
    directed = _math(editor, "Multiply_VectorFloat", A=_get(editor, "ImpactDirection"), B=1150)
    motion = _math(editor, "Add_VectorVector", A=directed, B=outward)
    motion = _math(editor, "Add_VectorVector", A=motion, B="(X=0,Y=0,Z=260)")
    velocity = _call(editor, "PrimitiveComponent.SetPhysicsLinearVelocity")
    _connect(component, velocity, "self")
    _connect(motion, velocity, "NewVel")
    _exec(simulate, velocity)
    angular = _call(editor, "PrimitiveComponent.SetPhysicsAngularVelocityInDegrees")
    _connect(component, angular, "self")
    _value(angular, "NewAngVel", "(X=80,Y=135,Z=65)")
    _exec(velocity, angular)

    emit = _branch(editor, _get(editor, "EmitDust"))
    _exec(loop, emit, "Completed")
    _effect(editor, effects.get("dust_class"), _location(editor), emit)

    # Missing fracture data retains a physically movable source piece.
    fallback = _call(editor, "PrimitiveComponent.SetSimulatePhysics")
    _connect(_component(editor), fallback, "self")
    _value(fallback, "bSimulate", "true")
    _exec(has_meshes, fallback, "else")


def _build_release_function(blueprint):
    editor, entry, _ = _function(blueprint, "ReleasePiece")
    active = _branch(editor, _get(editor, "HasActivated"))
    _exec(entry, active)
    mark = _set(editor, "HasActivated", "true")
    _exec(active, mark, "else")
    simulate = _call(editor, "PrimitiveComponent.SetSimulatePhysics")
    _connect(_component(editor), simulate, "self")
    _value(simulate, "bSimulate", "true")
    _exec(mark, simulate)
    velocity = _call(editor, "PrimitiveComponent.SetPhysicsLinearVelocity")
    _connect(_component(editor), velocity, "self")
    _value(velocity, "NewVel", "(X=0,Y=90,Z=-140)")
    _exec(simulate, velocity)
    time = _call(editor, "KismetSystemLibrary.GetGameTimeInSeconds")
    released = _set(editor, "ReleaseTime", (time, "ReturnValue"))
    _exec(velocity, released)
    # Released floors shed smaller debris while falling. Direct hits retain
    # their stronger forward impulse from the projectile damage event.
    downward = _set(editor, "ImpactDirection", "(X=0,Y=0.1,Z=-0.2)")
    _exec(released, downward)
    crumble = _call(editor, "KismetSystemLibrary.K2_SetTimer")
    _value(crumble, "FunctionName", "Fracture")
    _value(crumble, "Time", 0.45)
    _value(crumble, "bLooping", "false")
    _exec(downward, crumble)


def _build_piece(fragment, effects):
    blueprint = _blueprint("BP_SkyfallPieceV2", unreal.StaticMeshActor)
    _variables(blueprint, [
        ("HasActivated", "bool", False, False),
        ("HasFractured", "bool", False, False),
        ("CollapseScheduled", "bool", False, False),
        ("EmitDust", "bool", True, True),
        ("ReleaseTime", "real", 0.0, False),
        ("ImpactDirection", "vector", unreal.Vector(0, 1, 0.1), False),
        ("FractureMeshes", "mesh[]", None, True),
        ("FractureOffsets", "vector[]", None, True),
    ])
    _build_fracture_function(blueprint, fragment, effects)
    _build_release_function(blueprint)
    _compile(blueprint)
    editor = _graph(blueprint)
    damage = _event(blueprint, "ReceiveAnyDamage")
    direction = _call(editor, "Actor.GetActorForwardVector")
    _wire(damage, "DamageCauser", direction, "self")
    direction_set = _set(editor, "ImpactDirection", (direction, "ReturnValue"))
    _exec(damage, direction_set)
    direct = _branch(editor, _math(editor, "GreaterEqual_DoubleDouble", A=(damage, "Damage"), B=50))
    _exec(direction_set, direct)
    fracture = editor.add_call_function_node("Fracture")
    _exec(direct, fracture)

    scheduled = _branch(editor, _get(editor, "CollapseScheduled"))
    _exec(direct, scheduled, "else")
    mark = _set(editor, "CollapseScheduled", "true")
    _exec(scheduled, mark, "else")
    distance = _call(editor, "Actor.GetDistanceTo")
    _wire(damage, "DamageCauser", distance, "OtherActor")
    distance_delay = _math(editor, "Multiply_DoubleDouble", A=(distance, "ReturnValue"), B=0.0018)
    delay = _math(editor, "Add_DoubleDouble", A=distance_delay, B=0.65)
    timer = _call(editor, "KismetSystemLibrary.K2_SetTimer")
    _value(timer, "FunctionName", "ReleasePiece")
    _connect(delay, timer, "Time")
    _value(timer, "bLooping", "false")
    _exec(mark, timer)

    hit = _event(blueprint, "ReceiveHit", (0, 800))
    active = _branch(editor, _get(editor, "HasActivated"))
    _exec(hit, active)
    impulse = _math(editor, "VSize", A=(hit, "NormalImpulse"))
    hard = _math(editor, "Greater_DoubleDouble", A=impulse, B=25000)
    now = _call(editor, "KismetSystemLibrary.GetGameTimeInSeconds")
    age = _math(editor, "Subtract_DoubleDouble", A=(now, "ReturnValue"), B=_get(editor, "ReleaseTime"))
    old = _math(editor, "Greater_DoubleDouble", A=age, B=0.25)
    struck_piece = _call(editor, "Actor.ActorHasTag")
    _wire(hit, "Other", struck_piece, "self")
    _value(struck_piece, "Tag", PIECE_TAG)
    environment = _math(editor, "Not_PreBool", A=(struck_piece, "ReturnValue"))
    eligible = _math(editor, "BooleanAND", A=hard, B=old)
    eligible = _math(editor, "BooleanAND", A=eligible, B=environment)
    hard_hit = _branch(editor, eligible)
    _exec(active, hard_hit)
    fracture_hit = editor.add_call_function_node("Fracture")
    _exec(hard_hit, fracture_hit)
    _save(blueprint)
    return blueprint


def _build_cascade(blueprint, spec):
    editor, entry, _ = _function(blueprint, "StartCollapse")
    all_pieces = _call(editor, "GameplayStatics.GetAllActorsWithTag")
    _value(all_pieces, "Tag", PIECE_TAG)
    _exec(entry, all_pieces)
    loop = _loop(editor, (all_pieces, "OutActors"))
    _wire(all_pieces, "then", loop, "Exec")
    location = _location(editor, (loop, "Array Element"))
    delta = _math(editor, "Subtract_VectorVector", A=location, B=_get(editor, "ImpactLocation"))
    split = _call(editor, "KismetMathLibrary.BreakVector")
    _connect(delta, split, "InVec")
    if spec["key"] == "meteor":
        horizontal = _math(editor, "MakeVector", X=(split, "X"), Y=(split, "Y"), Z=0)
        distance = _math(editor, "VSize", A=horizontal)
        near = _math(editor, "Less_DoubleDouble", A=distance, B=1700)
        below = _math(editor, "Less_DoubleDouble", A=(split, "Z"), B=300)
        eligible = _math(editor, "BooleanAND", A=near, B=below)
    else:
        distance = _math(editor, "VSize", A=delta)
        near = _math(editor, "Less_DoubleDouble", A=distance, B=2600)
        above = _math(editor, "Greater_DoubleDouble", A=(split, "Z"), B=-380)
        eligible = _math(editor, "BooleanAND", A=near, B=above)
    branch = _branch(editor, eligible)
    _exec(loop, branch, "LoopBody")
    damage = _call(editor, "GameplayStatics.ApplyDamage")
    _wire(loop, "Array Element", damage, "DamagedActor")
    _value(damage, "BaseDamage", 10)
    # The projectile is the default self for this actor-typed pin.
    self_node = _self(editor)
    _wire(self_node, "self", damage, "DamageCauser")
    _exec(branch, damage)


def _notify_slow_motion(editor, source):
    controllers = _call(editor, "GameplayStatics.GetAllActorsWithTag")
    _value(controllers, "Tag", CONTROLLER_TAG)
    _exec(source, controllers)
    loop = _loop(editor, (controllers, "OutActors"))
    _wire(controllers, "then", loop, "Exec")
    damage = _call(editor, "GameplayStatics.ApplyDamage")
    _wire(loop, "Array Element", damage, "DamagedActor")
    _value(damage, "BaseDamage", 1)
    _exec(loop, damage, "LoopBody")
    return loop


def _physics_body_types(editor, query):
    candidates = [name for name in editor.list_available_nodes([_pin(query, "ObjectTypes")])
                  if str(name).rsplit("|", 1)[-1].replace(" ", "").lower() == "makearray"]
    if len(candidates) != 1:
        raise RuntimeError("Cannot resolve physics object-type array " + str(candidates))
    types = editor.create_node_from_name(candidates[0], unreal.Vector2D(), [_pin(query, "ObjectTypes")])
    _wire(types, "Array", query, "ObjectTypes")
    _value(types, "[0]", "ObjectTypeQuery4")


def _build_contact_pressure(blueprint, spec):
    """Transfer a real impact to nearby structure through a bounded overlap."""
    editor, entry, _ = _function(blueprint, "ApplyContactPressure")
    overlap = _call(editor, "KismetSystemLibrary.SphereOverlapActors")
    _connect(_get(editor, "ImpactLocation"), overlap, "SpherePos")
    _value(overlap, "SphereRadius", {"ball": 200, "meteor": 600}.get(spec["key"], 250))
    _physics_body_types(editor, overlap)
    _exec(entry, overlap)
    loop = _loop(editor, (overlap, "OutActors"))
    _wire(overlap, "then", loop, "Exec")
    structural = _call(editor, "Actor.ActorHasTag")
    _wire(loop, "Array Element", structural, "self")
    _value(structural, "Tag", "structure")
    collidable = _call(editor, "Actor.GetActorEnableCollision")
    _wire(loop, "Array Element", collidable, "self")
    contains = _call(editor, "KismetArrayLibrary.Array_Contains")
    _connect(_get(editor, "HitActors"), contains, "TargetArray")
    _wire(loop, "Array Element", contains, "ItemToFind")
    fresh = _math(editor, "Not_PreBool", A=(contains, "ReturnValue"))
    eligible = _math(editor, "BooleanAND", A=(structural, "ReturnValue"), B=(collidable, "ReturnValue"))
    eligible = _math(editor, "BooleanAND", A=eligible, B=fresh)
    valid = _branch(editor, eligible)
    _exec(loop, valid, "LoopBody")
    remember = _call(editor, "KismetArrayLibrary.Array_AddUnique")
    _connect(_get(editor, "HitActors"), remember, "TargetArray")
    _wire(loop, "Array Element", remember, "NewItem")
    _exec(valid, remember)
    damage = _call(editor, "GameplayStatics.ApplyDamage")
    _wire(loop, "Array Element", damage, "DamagedActor")
    _value(damage, "BaseDamage", spec["damage"])
    self_node = _self(editor)
    _wire(self_node, "self", damage, "DamageCauser")
    _exec(remember, damage)
    started = _branch(editor, _get(editor, "CascadeStarted"))
    _exec(damage, started)
    mark = _set(editor, "CascadeStarted", "true")
    _exec(started, mark, "else")
    cascade = editor.add_call_function_node("StartCollapse")
    _exec(mark, cascade)


def _clear_meteor_tail(editor, source):
    attached = _call(editor, "Actor.GetAttachedActors")
    children = _loop(editor, (attached, "OutActors"))
    _wire(source, "then", children, "Exec")
    destroy = _call(editor, "Actor.K2_DestroyActor")
    _wire(children, "Array Element", destroy, "self")
    _exec(children, destroy, "LoopBody")
    return children


def _build_meteor_functions(blueprint, effects):
    editor, entry, _ = _function(blueprint, "ReactMeteorImpact")
    first_location = _set(editor, "FirstImpactLocation", _get(editor, "ImpactLocation"))
    _exec(entry, first_location)
    direction = _set(editor, "EntryDirection", _math(editor, "Normal", A=_get(editor, "ShotVelocity")))
    _exec(first_location, direction)
    fraction = _math(editor, "SelectFloat", A=0.25, B=0.70, bPickA=_get(editor, "MeteorEmbedMode"))
    retained = _math(editor, "VLerp", A="(X=0,Y=0,Z=0)", B=_get(editor, "ShotVelocity"), Alpha=fraction)
    slow = _set(editor, "ShotVelocity", retained)
    _exec(direction, slow)
    gravity = _set(editor, "GravityZ", 0.0)
    _exec(slow, gravity)

    editor, entry, _ = _function(blueprint, "ReleaseEmbeddedMeteor")
    component = _component(editor)
    profile = _call(editor, "PrimitiveComponent.SetCollisionProfileName")
    _connect(component, profile, "self")
    _value(profile, "InCollisionProfileName", "DemolitionDebris")
    _exec(entry, profile)
    gravity = _call(editor, "PrimitiveComponent.SetEnableGravity")
    _connect(component, gravity, "self")
    _value(gravity, "bGravityEnabled", "true")
    _exec(profile, gravity)
    ccd = _call(editor, "PrimitiveComponent.SetUseCCD")
    _connect(component, ccd, "self")
    _value(ccd, "InUseCCD", "true")
    _exec(gravity, ccd)
    simulate = _call(editor, "PrimitiveComponent.SetSimulatePhysics")
    _connect(component, simulate, "self")
    _value(simulate, "bSimulate", "true")
    _exec(ccd, simulate)
    velocity = _call(editor, "PrimitiveComponent.SetPhysicsLinearVelocity")
    _connect(component, velocity, "self")
    _value(velocity, "NewVel", "(X=0,Y=0,Z=-100)")
    _exec(simulate, velocity)
    lifespan = _call(editor, "Actor.SetLifeSpan")
    _value(lifespan, "InLifespan", 65.0)
    _exec(velocity, lifespan)

    editor, entry, _ = _function(blueprint, "UpdateMeteorAfterFlight")
    touched = _branch(editor, _get(editor, "DidHit"))
    _exec(entry, touched)
    steep = _branch(editor, _get(editor, "MeteorEmbedMode"))
    _exec(touched, steep)
    offset = _math(editor, "Subtract_VectorVector", A=_location(editor), B=_get(editor, "FirstImpactLocation"))
    depth = _math(editor, "Dot_VectorVector", A=offset, B=_get(editor, "EntryDirection"))
    deep = _branch(editor, _math(editor, "GreaterEqual_DoubleDouble", A=depth, B=850.0))
    _exec(steep, deep)
    mark = _set(editor, "MeteorEmbedded", "true")
    _exec(deep, mark)
    penetration = _math(editor, "VLerp", A="(X=0,Y=0,Z=0)", B=_get(editor, "EntryDirection"), Alpha=850)
    lodged_location = _math(editor, "Add_VectorVector", A=_get(editor, "FirstImpactLocation"), B=penetration)
    lodge = _call(editor, "Actor.K2_SetActorLocation")
    _connect(lodged_location, lodge, "NewLocation")
    _value(lodge, "bTeleport", "true")
    _exec(mark, lodge)
    stopped = _set(editor, "ShotVelocity", "(X=0,Y=0,Z=0)")
    _exec(lodge, stopped)
    stop_tick = _call(editor, "Actor.SetActorTickEnabled")
    _value(stop_tick, "bEnabled", "false")
    _exec(stopped, stop_tick)
    tail = _clear_meteor_tail(editor, stop_tick)
    lifetime = _call(editor, "Actor.SetLifeSpan")
    _value(lifetime, "InLifespan", 65.0)
    _exec(tail, lifetime, "Completed")
    release = _call(editor, "KismetSystemLibrary.K2_SetTimer")
    _value(release, "FunctionName", "ReleaseEmbeddedMeteor")
    _value(release, "Time", 1.0)
    _value(release, "bLooping", "false")
    _exec(lifetime, release)

    # Crossing the original building envelope, not an interior cavity, is an exit.
    already_exited = _branch(editor, _get(editor, "MeteorExited"))
    _exec(steep, already_exited, "else")
    envelope = _math(editor, "Add_VectorVector", A=_get(editor, "MeteorBuildingExtent"), B="(X=180,Y=180,Z=180)")
    outside = _math(editor, "Not_PreBool", A=_math(editor, "IsPointInBox", Point=_location(editor),
                    BoxOrigin=_get(editor, "MeteorBuildingCenter"), BoxExtent=envelope))
    current = _call(editor, "KismetMathLibrary.BreakVector")
    _connect(_location(editor), current, "InVec")
    through_base = _math(editor, "LessEqual_DoubleDouble", A=(current, "Z"), B=190)
    outside = _math(editor, "BooleanOR", A=outside, B=through_base)
    exit_branch = _branch(editor, outside)
    _exec(already_exited, exit_branch, "else")
    exited = _set(editor, "MeteorExited", "true")
    _exec(exit_branch, exited)
    outward = _math(editor, "VLerp", A="(X=0,Y=0,Z=0)", B=_get(editor, "EntryDirection"), Alpha=130)
    exit_position = _math(editor, "Add_VectorVector", A=_get(editor, "ImpactLocation"), B=outward)
    split_exit = _call(editor, "KismetMathLibrary.BreakVector")
    _connect(exit_position, split_exit, "InVec")
    exit_position = _math(editor, "MakeVector", X=(split_exit, "X"), Y=(split_exit, "Y"),
                          Z=_math(editor, "FMax", A=(split_exit, "Z"), B=190))
    _effect(editor, effects.get("meteor_exit_class"), exit_position, exited, strength=1.0)

    # Ground cleanup is independent of a building hit; it never damages the building.
    editor, entry, _ = _function(blueprint, "CleanupMeteorAtGround")
    split = _call(editor, "KismetMathLibrary.BreakVector")
    _connect(_location(editor), split, "InVec")
    grounded = _branch(editor, _math(editor, "LessEqual_DoubleDouble", A=(split, "Z"), B=190))
    _exec(entry, grounded)
    stop = _call(editor, "Actor.SetActorTickEnabled")
    _value(stop, "bEnabled", "false")
    _exec(grounded, stop)
    tail = _clear_meteor_tail(editor, stop)
    destroy = _call(editor, "Actor.K2_DestroyActor")
    _exec(tail, destroy, "Completed")
    _compile(blueprint)


def _build_projectile(spec, mesh, effects, materials):
    meteor = spec["key"] == "meteor"
    shake = _impact_shake() if spec["key"] in ("rocket", "meteor") else None
    blueprint = _blueprint("BP_Skyfall" + spec["name"] + "V2", unreal.StaticMeshActor)
    _variables(blueprint, [
        ("ShotVelocity", "vector", unreal.Vector(0, spec["speed"], 0), True),
        ("GravityZ", "real", spec["gravity"], True),
        ("PreviousLocation", "vector", unreal.Vector(), False),
        ("ImpactLocation", "vector", unreal.Vector(), False),
        ("HitActors", "actor[]", None, False),
        ("DidHit", "bool", False, False),
        ("CascadeStarted", "bool", False, False),
    ])
    if meteor:
        _variables(blueprint, [
            ("MeteorEmbedMode", "bool", False, True),
            ("MeteorEmbedded", "bool", False, False),
            ("MeteorExited", "bool", False, False),
            ("FirstImpactLocation", "vector", unreal.Vector(), False),
            ("EntryDirection", "vector", unreal.Vector(), False),
            ("MeteorBuildingCenter", "vector", unreal.Vector(0, 0, 1200), True),
            ("MeteorBuildingExtent", "vector", unreal.Vector(800, 750, 1200), True),
        ])
    cdo = unreal.get_default_object(blueprint.generated_class())
    component = cdo.static_mesh_component
    component.set_mobility(unreal.ComponentMobility.MOVABLE)
    component.set_static_mesh(mesh)
    if spec["key"] == "ball" and materials.get("stainless"):
        component.set_material(0, _asset(materials["stainless"]))
    component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
    if meteor:
        component.set_enable_gravity(True)
        component.set_mass_override_in_kg("None", 25000.0, True)
        component.set_linear_damping(0.12)
        component.set_angular_damping(0.5)
    cdo.set_editor_property("tags", [unreal.Name("SkyfallProjectile")])
    cdo.set_editor_property("initial_life_span", 8.0)
    _build_cascade(blueprint, spec)
    _build_contact_pressure(blueprint, spec)
    if meteor:
        _build_meteor_functions(blueprint, effects)
    _compile(blueprint)
    editor = _graph(blueprint)
    begin = _event(blueprint, "ReceiveBeginPlay")
    previous = _set(editor, "PreviousLocation", _location(editor))
    _exec(begin, previous)

    tick = _event(blueprint, "ReceiveTick", (0, 500))
    gravity = _math(editor, "MakeVector", Z=_get(editor, "GravityZ"))
    acceleration = _math(editor, "Multiply_VectorFloat", A=gravity, B=(tick, "DeltaSeconds"))
    velocity = _math(editor, "Add_VectorVector", A=_get(editor, "ShotVelocity"), B=acceleration)
    update_velocity = _set(editor, "ShotVelocity", velocity)
    _exec(tick, update_velocity)
    half_acceleration = _math(editor, "VLerp", A="(X=0,Y=0,Z=0)", B=acceleration, Alpha=0.5)
    midpoint_velocity = _math(editor, "Subtract_VectorVector", A=_get(editor, "ShotVelocity"), B=half_acceleration)
    step = _math(editor, "Multiply_VectorFloat", A=midpoint_velocity, B=(tick, "DeltaSeconds"))
    move = _call(editor, "Actor.K2_AddActorWorldOffset")
    _connect(step, move, "DeltaLocation")
    _value(move, "bSweep", "false")
    _exec(update_velocity, move)
    rotation = _math(editor, "MakeRotFromX", X=_get(editor, "ShotVelocity"))
    rotate = _call(editor, "Actor.K2_SetActorRotation")
    _connect(rotation, rotate, "NewRotation")
    _exec(move, rotate)

    trace = _call(editor, "KismetSystemLibrary.SphereTraceMultiForObjects")
    _connect(_get(editor, "PreviousLocation"), trace, "Start")
    _connect(_location(editor), trace, "End")
    _value(trace, "Radius", spec["radius"])
    _physics_body_types(editor, trace)
    _value(trace, "bTraceComplex", "false")
    _value(trace, "DrawDebugType", "None")
    _exec(rotate, trace)
    loop = _loop(editor, (trace, "OutHits"))
    _wire(trace, "then", loop, "Exec")
    hit = _call(editor, "GameplayStatics.BreakHitResult")
    _wire(loop, "Array Element", hit, "Hit")
    tag = _call(editor, "Actor.ActorHasTag")
    _value(tag, "Tag", PIECE_TAG)
    _wire(hit, "HitActor", tag, "self")
    contains = _call(editor, "KismetArrayLibrary.Array_Contains")
    _connect(_get(editor, "HitActors"), contains, "TargetArray")
    _wire(hit, "HitActor", contains, "ItemToFind")
    fresh = _math(editor, "Not_PreBool", A=(contains, "ReturnValue"))
    eligible = _math(editor, "BooleanAND", A=(tag, "ReturnValue"), B=fresh)
    if meteor:
        eligible = _math(editor, "BooleanAND", A=eligible,
                         B=_math(editor, "Not_PreBool", A=_get(editor, "MeteorExited")))
    valid = _branch(editor, eligible)
    _exec(loop, valid, "LoopBody")
    add = _call(editor, "KismetArrayLibrary.Array_AddUnique")
    _connect(_get(editor, "HitActors"), add, "TargetArray")
    _wire(hit, "HitActor", add, "NewItem")
    _exec(valid, add)
    impact = _set(editor, "ImpactLocation", (hit, "ImpactPoint"))
    _exec(add, impact)
    damage = _call(editor, "GameplayStatics.ApplyDamage")
    _wire(hit, "HitActor", damage, "DamagedActor")
    _value(damage, "BaseDamage", spec["damage"])
    # A function input defaults to self only when its metadata explicitly says so.
    self_node = _self(editor)
    _wire(self_node, "self", damage, "DamageCauser")
    _exec(impact, damage)
    pressure = editor.add_call_function_node("ApplyContactPressure")
    _exec(damage, pressure)
    first = _branch(editor, _get(editor, "DidHit"))
    _exec(pressure, first)
    marked = _set(editor, "DidHit", "true")
    _exec(first, marked, "else")
    if meteor:
        response = editor.add_call_function_node("ReactMeteorImpact")
        _exec(marked, response)
    else:
        response = marked
    notify = _notify_slow_motion(editor, response)
    if shake:
        surface_offset = _math(editor, "VLerp", A="(X=0,Y=0,Z=0)",
                               B=(hit, "ImpactNormal"), Alpha=85)
        explosion_position = _math(editor, "Add_VectorVector", A=(hit, "ImpactPoint"), B=surface_offset)
        impact_class = effects.get("meteor_impact_class" if meteor else "explosion_class")
        effect = _effect(editor, impact_class, explosion_position, notify, "Completed", strength=1.0)
        camera = _call(editor, "GameplayStatics.PlayWorldCameraShake")
        _value(camera, "Shake", shake.generated_class().get_path_name())
        _wire(hit, "ImpactPoint", camera, "Epicenter")
        _value(camera, "InnerRadius", 12000)
        _value(camera, "OuterRadius", 18000)
        _exec(effect, camera)
        effect = camera
    else:
        effect = _effect(editor, effects.get("dust_class"), (hit, "ImpactPoint"), notify,
                         "Completed", strength=0.65)

    # Every structural hit can reach this guard. Glass never starts the cascade.
    structural = _call(editor, "Actor.ActorHasTag")
    _wire(hit, "HitActor", structural, "self")
    _value(structural, "Tag", "structure")
    unused = _math(editor, "Not_PreBool", A=_get(editor, "CascadeStarted"))
    cascade = _branch(editor, _math(editor, "BooleanAND", A=(structural, "ReturnValue"), B=unused))
    _exec(first, cascade)
    if effect is notify:
        _exec(notify, cascade, "Completed")
    else:
        _exec(effect, cascade)
    cascade_mark = _set(editor, "CascadeStarted", "true")
    _exec(cascade, cascade_mark)
    start = editor.add_call_function_node("StartCollapse")
    _exec(cascade_mark, start)
    next_previous = _set(editor, "PreviousLocation", _location(editor))
    _exec(loop, next_previous, "Completed")
    if meteor:
        post_flight = editor.add_call_function_node("UpdateMeteorAfterFlight")
        _exec(next_previous, post_flight)
        cleanup = editor.add_call_function_node("CleanupMeteorAtGround")
        _exec(post_flight, cleanup)
    _save(blueprint)
    return blueprint


def _meteor_selected(editor):
    return _math(editor, "EqualEqual_IntInt", A=_get(editor, "WeaponMode"), B=1)


def _meteor_velocity(editor):
    pitch = _math(editor, "Multiply_DoubleDouble", A=_get(editor, "MeteorAngle"), B=-1)
    rotation = _math(editor, "MakeRotator", Pitch=pitch, Yaw=-60)
    direction = _math(editor, "GetForwardVector", InRot=rotation)
    return _math(editor, "VLerp", A="(X=0,Y=0,Z=0)", B=direction, Alpha=4200)


def _launch_functions(blueprint):
    editor, entry, result = _function(blueprint, "GetMeteorTarget", output="vector", pure=True)
    target = _math(editor, "MakeVector", X=_get(editor, "MeteorTargetX"),
                   Y=_get(editor, "MeteorTargetY"), Z=2075)
    _connect(target, result, "Output")
    _compile(blueprint)
    editor, entry, result = _function(blueprint, "GetLaunchOrigin", output="vector", pure=True)
    location = _math(editor, "MakeVector", X=0, Y=-3500, Z=_get(editor, "LaunchHeight"))
    target = editor.add_call_function_node("GetMeteorTarget")
    meteor_origin = _math(editor, "Subtract_VectorVector", A=(target, "Output"), B=_meteor_velocity(editor))
    meteor_origin = _math(editor, "Add_VectorVector", A=meteor_origin, B="(X=0,Y=0,Z=800)")
    selected = _math(editor, "SelectVector", A=meteor_origin, B=location, bPickA=_meteor_selected(editor))
    _connect(selected, result, "Output")
    editor, entry, result = _function(blueprint, "GetLaunchVelocity", output="vector", pure=True)
    yaw = _math(editor, "Add_DoubleDouble", A=_get(editor, "YawDegrees"), B=90)
    rotation = _math(editor, "MakeRotator", Pitch=_get(editor, "PitchDegrees"), Yaw=yaw)
    direction = _math(editor, "GetForwardVector", InRot=rotation)
    velocity = _math(editor, "Multiply_VectorFloat", A=direction, B=_get(editor, "LaunchSpeed"))
    selected = _math(editor, "SelectVector", A=_meteor_velocity(editor), B=velocity,
                     bPickA=_meteor_selected(editor))
    _connect(selected, result, "Output")
    _function(blueprint, "UpdateAim")
    _compile(blueprint)


def _fire_weapon(blueprint, projectiles, effects):
    editor, entry, _ = _function(blueprint, "FireWeapon")
    navigating = editor.add_call_function_node("IsCameraNavigating")
    navigation_gate = _branch(editor, (navigating, "Output"))
    _exec(entry, navigation_gate)
    now = _call(editor, "GameplayStatics.GetRealTimeSeconds")
    since = _math(editor, "Subtract_DoubleDouble", A=(now, "ReturnValue"), B=_get(editor, "LastFireTime"))
    cooldown = _math(editor, "SelectFloat", A=2.2, B=0.45, bPickA=_meteor_selected(editor))
    ready = _branch(editor, _math(editor, "Greater_DoubleDouble", A=since, B=cooldown))
    _exec(navigation_gate, ready, "else")
    stamp = _set(editor, "LastFireTime", (now, "ReturnValue"))
    _exec(ready, stamp)
    origin = editor.add_call_function_node("GetLaunchOrigin")
    velocity = editor.add_call_function_node("GetLaunchVelocity")
    rotation = _math(editor, "MakeRotFromX", X=(velocity, "Output"))
    previous_branch = None
    for mode, (spec, projectile) in enumerate(zip(WEAPONS, projectiles)):
        selected = _math(editor, "EqualEqual_IntInt", A=_get(editor, "WeaponMode"), B=mode)
        branch = _branch(editor, selected)
        _exec(previous_branch or stamp, branch, "else" if previous_branch else "then")
        scale = spec["scale"]
        if spec["key"] == "ball":
            mesh = unreal.get_default_object(projectile.generated_class()).static_mesh_component.static_mesh
            scale = 100.0 / max(mesh.get_bounds().box_extent.x, 0.001)
        transform = _math(editor, "MakeTransform", Location=(origin, "Output"), Rotation=rotation,
                          Scale=f"(X={scale},Y={scale},Z={scale})")
        spawn = _spawn(editor, projectile, transform, branch)
        shot_velocity = _call(editor, "KismetSystemLibrary.SetVectorPropertyByName")
        _wire(spawn, "ReturnValue", shot_velocity, "Object")
        _value(shot_velocity, "PropertyName", "ShotVelocity")
        _wire(velocity, "Output", shot_velocity, "Value")
        _exec(spawn, shot_velocity)
        gravity = _call(editor, "KismetSystemLibrary.SetDoublePropertyByName")
        _wire(spawn, "ReturnValue", gravity, "Object")
        _value(gravity, "PropertyName", "GravityZ")
        _connect(_get(editor, "GravityZ"), gravity, "Value")
        _exec(shot_velocity, gravity)
        before_finish = gravity
        if spec["key"] == "meteor":
            for field in ("MeteorBuildingCenter", "MeteorBuildingExtent"):
                copy_bounds = _call(editor, "KismetSystemLibrary.SetVectorPropertyByName")
                _wire(spawn, "ReturnValue", copy_bounds, "Object")
                _value(copy_bounds, "PropertyName", field)
                _connect(_get(editor, field), copy_bounds, "Value")
                _exec(before_finish, copy_bounds)
                before_finish = copy_bounds
            embed = _call(editor, "KismetSystemLibrary.SetBoolPropertyByName")
            _wire(spawn, "ReturnValue", embed, "Object")
            _value(embed, "PropertyName", "MeteorEmbedMode")
            _connect(_math(editor, "GreaterEqual_DoubleDouble", A=_get(editor, "MeteorAngle"), B=78), embed, "Value")
            _exec(before_finish, embed)
            before_finish = embed
        finish = _finish(editor, spawn, transform, before_finish)
        tail_key = "meteor_tail_class" if spec["key"] == "meteor" else "launch_class"
        if spec["key"] in ("rocket", "meteor") and effects.get(tail_key):
            direction = _math(editor, "Normal", A=(velocity, "Output"))
            nozzle_offset = _math(editor, "VLerp", A="(X=0,Y=0,Z=0)", B=direction,
                                 Alpha=-130 if spec["key"] == "meteor" else -110)
            nozzle = _math(editor, "Add_VectorVector", A=(origin, "Output"), B=nozzle_offset)
            exhaust_transform = _math(editor, "MakeTransform", Location=nozzle, Rotation=rotation)
            exhaust = _spawn(editor, effects[tail_key], exhaust_transform, finish)
            exhaust_finish = _finish(editor, exhaust, exhaust_transform)
            attach = _call(editor, "Actor.K2_AttachToActor")
            _wire(exhaust_finish, "ReturnValue", attach, "self")
            _wire(finish, "ReturnValue", attach, "ParentActor")
            for rule in ("LocationRule", "RotationRule", "ScaleRule"):
                _value(attach, rule, "KeepWorld")
            _value(attach, "bWeldSimulatedBodies", "false")
            _exec(exhaust_finish, attach)
        previous_branch = branch
    _compile(blueprint)


def _adjust(editor, event, name, step, minimum, maximum, source_pin="Pressed"):
    added = _math(editor, "Add_DoubleDouble", A=_get(editor, name), B=step)
    clamped = _math(editor, "FClamp", Value=added, Min=minimum, Max=maximum)
    assign = _set(editor, name, clamped)
    _exec(event, assign, source_pin)


def _mode_adjust(editor, event, standard, meteor):
    navigating = editor.add_call_function_node("IsCameraNavigating")
    navigation_gate = _branch(editor, (navigating, "Output"))
    _exec(event, navigation_gate, "Pressed")
    branch = _branch(editor, _meteor_selected(editor))
    _exec(navigation_gate, branch, "else")
    _adjust(editor, branch, *meteor, source_pin="then")
    _adjust(editor, branch, *standard, source_pin="else")


def _build_camera_navigation(blueprint):
    """Read the current key state directly; do not depend on camera tick order."""
    editor, entry, result = _function(blueprint, "IsCameraNavigating", output="bool", pure=True)
    player = _call(editor, "GameplayStatics.GetPlayerController")
    _value(player, "PlayerIndex", 0)
    down = _call(editor, "PlayerController.IsInputKeyDown")
    _wire(player, "ReturnValue", down, "self")
    _value(down, "Key", "RightMouseButton")
    _wire(down, "ReturnValue", result, "Output")
    _compile(blueprint)


def _build_mode_camera(blueprint):
    """Keep legacy view support, while never repositioning a free camera."""
    editor, entry, _ = _function(blueprint, "UpdateModeCamera")
    cameras = _call(editor, "GameplayStatics.GetAllActorsWithTag")
    _value(cameras, "Tag", "SFCamera")
    _exec(entry, cameras)
    loop = _loop(editor, (cameras, "OutActors"))
    _wire(cameras, "then", loop, "Exec")
    free_tag = _call(editor, "Actor.ActorHasTag")
    _wire(loop, "Array Element", free_tag, "self")
    _value(free_tag, "Tag", "SFFreeCamera")
    free_camera = _branch(editor, (free_tag, "ReturnValue"))
    _exec(loop, free_camera, "LoopBody")
    meteor = _meteor_selected(editor)
    location = _math(editor, "SelectVector", A="(X=6500,Y=-9400,Z=4200)",
                     B="(X=4200,Y=-6100,Z=2800)", bPickA=meteor)
    target = _math(editor, "SelectVector", A="(X=0,Y=0,Z=2600)",
                   B="(X=0,Y=0,Z=1050)", bPickA=meteor)
    rotation = _math(editor, "FindLookAtRotation", Start=location, Target=target)
    move = _call(editor, "Actor.K2_SetActorLocationAndRotation")
    _wire(loop, "Array Element", move, "self")
    _connect(location, move, "NewLocation")
    _connect(rotation, move, "NewRotation")
    _value(move, "bTeleport", "true")
    _exec(free_camera, move, "else")
    _compile(blueprint)


def _build_controller(projectiles, effects):
    blueprint = _blueprint("BP_SkyfallControllerV2", unreal.Actor)
    _variables(blueprint, [
        ("WeaponMode", "int", 0, True),
        ("YawDegrees", "real", 0.0, True),
        ("PitchDegrees", "real", 0.0, True),
        ("LaunchHeight", "real", 900.0, True),
        ("MeteorTargetX", "real", 0.0, True),
        ("MeteorTargetY", "real", 0.0, True),
        ("MeteorAngle", "real", 40.0, True),
        ("MeteorBuildingCenter", "vector", unreal.Vector(0, 0, 1200), True),
        ("MeteorBuildingExtent", "vector", unreal.Vector(800, 750, 1200), True),
        ("LaunchSpeed", "real", 10000.0, True),
        ("GravityZ", "real", 0.0, True),
        ("ProjectileRadius", "real", 55.0, True),
        ("SlowMoActive", "bool", False, False),
        ("SlowMoUntil", "real", 0.0, False),
        ("LastFireTime", "real", -10.0, False),
    ])
    unreal.get_default_object(blueprint.generated_class()).set_editor_property("tags", [unreal.Name(CONTROLLER_TAG)])
    _launch_functions(blueprint)
    _build_camera_navigation(blueprint)
    _fire_weapon(blueprint, projectiles, effects)
    _build_mode_camera(blueprint)
    editor = _graph(blueprint)
    begin = _event(blueprint, "ReceiveBeginPlay")
    player = _call(editor, "GameplayStatics.GetPlayerController")
    _value(player, "PlayerIndex", 0)
    enable = _call(editor, "Actor.EnableInput")
    _wire(player, "ReturnValue", enable, "PlayerController")
    _exec(begin, enable)
    reset_time = _call(editor, "GameplayStatics.SetGlobalTimeDilation")
    _value(reset_time, "TimeDilation", 1)
    _exec(enable, reset_time)
    help_text = _call(editor, "KismetSystemLibrary.PrintString")
    _value(help_text, "InString", "1 Rocket | 2 Meteor | 3 Ball | SPACE Fire | Meteor: A/D W/S Target, Arrows Entry Angle | R Rebuild")
    _value(help_text, "Duration", 18)
    _value(help_text, "Key", "SkyfallHelp")
    _value(help_text, "bPrintToLog", "false")
    _value(help_text, "bPrintToScreen", "false")  # The Chinese HUD owns player-facing help.
    _exec(reset_time, help_text)

    tick = _event(blueprint, "ReceiveTick", (0, 500))
    update_camera = editor.add_call_function_node("UpdateModeCamera")
    _exec(tick, update_camera)
    update_aim = editor.add_call_function_node("UpdateAim")
    _exec(update_camera, update_aim)
    slow = _branch(editor, _get(editor, "SlowMoActive"))
    _exec(update_aim, slow)
    real_time = _call(editor, "GameplayStatics.GetRealTimeSeconds")
    expired = _branch(editor, _math(editor, "GreaterEqual_DoubleDouble", A=(real_time, "ReturnValue"),
                                   B=_get(editor, "SlowMoUntil")))
    _exec(slow, expired)
    normal = _call(editor, "GameplayStatics.SetGlobalTimeDilation")
    _value(normal, "TimeDilation", 1)
    _exec(expired, normal)
    clear = _set(editor, "SlowMoActive", "false")
    _exec(normal, clear)

    damage = _event(blueprint, "ReceiveAnyDamage", (0, 1100))
    active = _branch(editor, _get(editor, "SlowMoActive"))
    _exec(damage, active)
    mark = _set(editor, "SlowMoActive", "true")
    _exec(active, mark, "else")
    real_now = _call(editor, "GameplayStatics.GetRealTimeSeconds")
    deadline = _math(editor, "Add_DoubleDouble", A=(real_now, "ReturnValue"), B=1.2)
    set_deadline = _set(editor, "SlowMoUntil", deadline)
    _exec(mark, set_deadline)
    dilate = _call(editor, "GameplayStatics.SetGlobalTimeDilation")
    _value(dilate, "TimeDilation", 0.22)
    _exec(set_deadline, dilate)

    available = list(editor.list_available_nodes([]))
    for key in ("SpaceBar",):  # Left clicks belong to HUD buttons; empty space must not fire.
        event = _input(editor, available, key)
        fire = editor.add_call_function_node("FireWeapon")
        _exec(event, fire, "Pressed")
    for mode, (key, spec) in enumerate(zip(("1", "2", "3"), WEAPONS)):
        event = _input(editor, available, key)
        mode_set = _set(editor, "WeaponMode", mode)
        _exec(event, mode_set, "Pressed")
        speed = _set(editor, "LaunchSpeed", spec["speed"])
        _exec(mode_set, speed)
        gravity = _set(editor, "GravityZ", spec["gravity"])
        _exec(speed, gravity)
        radius = _set(editor, "ProjectileRadius", spec["radius"])
        _exec(gravity, radius)
    for key, step in (("A", -3), ("Left", -3), ("D", 3), ("Right", 3)):
        _mode_adjust(editor, _input(editor, available, key),
                     ("YawDegrees", step, -75, 75), ("MeteorTargetX", step*50, -3500, 3500))
    for key, step in (("W", 150), ("S", -150)):
        _mode_adjust(editor, _input(editor, available, key),
                     ("LaunchHeight", step, 150, 3800), ("MeteorTargetY", step, -3500, 3500))
    for key, step in (("Up", 3), ("Down", -3)):
        _mode_adjust(editor, _input(editor, available, key),
                     ("PitchDegrees", step, -40, 60), ("MeteorAngle", step, 30, 90))
    reset = _input(editor, available, "R")
    restore = _call(editor, "GameplayStatics.SetGlobalTimeDilation")
    _value(restore, "TimeDilation", 1)
    _exec(reset, restore, "Pressed")
    level = _call(editor, "GameplayStatics.GetCurrentLevelName")
    _value(level, "bRemovePrefixString", "true")
    _exec(restore, level)
    reload = _call(editor, "GameplayStatics.OpenLevel")
    _wire(level, "ReturnValue", reload, "LevelName")
    _exec(level, reload)
    _save(blueprint)
    return blueprint


def _asset(value):
    return unreal.load_asset(value) if isinstance(value, str) else value


def configure_meteor_building_bounds(controller, pieces):
    """Capture the intact building envelope once, including its roof equipment."""
    if not pieces:
        raise RuntimeError("Cannot initialize meteor envelope without building pieces")
    minimum = [float("inf")] * 3
    maximum = [float("-inf")] * 3
    for piece in pieces:
        origin, extent = piece.get_actor_bounds(False)
        for axis, (centre, radius) in enumerate(zip(origin.to_tuple(), extent.to_tuple())):
            minimum[axis] = min(minimum[axis], centre-radius)
            maximum[axis] = max(maximum[axis], centre+radius)
    centre = unreal.Vector(*[(low+high)*0.5 for low, high in zip(minimum, maximum)])
    extent = unreal.Vector(*[(high-low)*0.5 for low, high in zip(minimum, maximum)])
    controller.set_editor_property("MeteorBuildingCenter", centre)
    controller.set_editor_property("MeteorBuildingExtent", extent)
    return {"center_cm": centre.to_tuple(), "extent_cm": extent.to_tuple(), "pieces": len(pieces)}


def build_controller(spawn_defs, materials, weapon_meshes, fracture_defs, effects):
    """Create combat assets and place level actors without saving the map.

    Fracture meshes retain raw metre vertices. Offsets are measured in their
    parent mesh's raw local metre coordinates, transformed by its actor scale.
    """
    unreal.InternationalizationLibrary.set_current_language("en", False)
    fragment = _build_fragment()
    piece = _build_piece(fragment, effects)
    projectiles = []
    for spec in WEAPONS:
        mesh = _asset(weapon_meshes.get(spec["key"]))
        if mesh is None and spec["key"] == "ball":
            mesh = unreal.load_asset("/Engine/BasicShapes/Sphere")
        if mesh is None:
            raise RuntimeError("Missing weapon mesh " + spec["key"])
        projectiles.append(_build_projectile(spec, mesh, effects, materials))
    controller = _build_controller(projectiles, effects)
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    pieces = []
    for index, definition in enumerate(spawn_defs):
        actor = actors.spawn_actor_from_class(piece.generated_class(), _vector(definition["location"]))
        actor.set_actor_label(definition.get("name", f"Piece_{index:04d}"))
        actor.set_actor_scale3d(_vector(definition.get("scale", (100, 100, 100))))
        if "rotation" in definition:
            pitch, yaw, roll = definition["rotation"]
            actor.set_actor_rotation(unreal.Rotator(pitch=pitch, yaw=yaw, roll=roll), False)
        component = actor.static_mesh_component
        component.set_static_mesh(_asset(definition.get("mesh", "/Engine/BasicShapes/Cube")))
        material_list = definition.get("materials") or ([definition["material"]] if definition.get("material") else [])
        for slot, material in enumerate(material_list):
            component.set_material(slot, _asset(material))
        _physics(component, definition.get("mass_kg", 1800))
        kind = definition.get("kind", "structure")
        actor.set_editor_property("EmitDust", kind != "glass" and index % 2 == 0)
        actor.set_editor_property("tags", [unreal.Name(PIECE_TAG), unreal.Name(kind),
                                           unreal.Name(f"Floor_{definition.get('floor', 0)}")])
        match = re.search(r"(?:^|_)(piece_\d+|window_\d+)$", definition.get("name", ""))
        source_name = definition.get("source_name") or (match.group(1) if match else definition.get("name"))
        fracture = fracture_defs.get(source_name, {})
        meshes = [_asset(value) for value in fracture.get("meshes", [])]
        offsets = [_vector(value) for value in fracture.get("offsets", [])]
        if len(meshes) != len(offsets):
            raise RuntimeError("Mismatched fracture meshes and offsets for " + str(source_name))
        actor.set_editor_property("FractureMeshes", meshes)
        actor.set_editor_property("FractureOffsets", offsets)
        pieces.append(actor)
    controller_actor = actors.spawn_actor_from_class(controller.generated_class(), unreal.Vector())
    controller_actor.set_actor_label("Skyfall_Gameplay_Controller_V2")
    configure_meteor_building_bounds(controller_actor, pieces)
    return {"controller": controller_actor, "pieces": pieces,
            "controller_blueprint": controller, "piece_blueprint": piece,
            "fragment_blueprint": fragment, "rocket_blueprint": projectiles[0],
            "meteor_blueprint": projectiles[1], "ball_blueprint": projectiles[2]}
