"""Build localized CombatV2 Niagara effects with bundled engine templates.

Author with -EnablePlugins=NiagaraToolsets. Generated Actor classes only need
normal runtime Niagara. Spawn dust/explosion at hit points. Attach launch to
the nozzle with local +X forwards. Strength is an editable float, 0.5..2.5.
No maps or existing materials are changed by this builder.
"""

import json
from pathlib import Path
import sys
import unreal

ROOT = Path(__file__).resolve().parents[1]
FOLDER = "/Game/Skyfall/CombatV2/Effects"
TOOLSET = "NiagaraToolsets.NiagaraToolset_System"
TEMPLATE = "/Niagara/DefaultAssets/Templates/Emitters/"
EDIT = unreal.MaterialEditingLibrary
BUILD_VERSION = "2.1.1"
CLASS_NAMES = {"dust_class": "BP_LocalPowder", "explosion_class": "BP_HighEnergyImpact", "launch_class": "BP_RocketExhaust"}
SYSTEM_NAMES = ("NS_LocalDust", "NS_HighEnergyImpact", "NS_RocketExhaust")
MATERIAL_NAMES = ("M_FineDust", "M_ExhaustSmoke", "M_RocketFlame", "M_ImpactFlash",
                  "M_ConcreteChips", "M_GlassSplinters", "M_BlastFire", "M_BlastSmoke",
                  "M_PressureRing", "M_HotSparks")


def _cached_effects():
    assets = {}
    for name in (*CLASS_NAMES.values(), *SYSTEM_NAMES, *MATERIAL_NAMES):
        path = f"{FOLDER}/{name}"
        if not unreal.EditorAssetLibrary.does_asset_exist(path):
            return None
        asset = unreal.load_asset(path)
        if unreal.EditorAssetLibrary.get_metadata_tag(asset, "SkyfallEffectsVersion") != BUILD_VERSION:
            return None
        assets[name] = asset
    classes = {key: assets[name].generated_class() for key, name in CLASS_NAMES.items()}
    if any(value is None for value in classes.values()):
        return None
    unreal.log("SKYFALL_EFFECTS_V2_CACHED " + BUILD_VERSION)
    return classes


def _struct(kind, **properties):
    value = kind()
    value.set_editor_properties(properties)
    return value


def _native(name, *arguments):
    return unreal.get_default_object(unreal.NiagaraToolset_System).call_method(name, arguments)


def _json_call(name, payload):
    result = unreal.ToolsetRegistry.execute_tool(TOOLSET, name, json.dumps(payload))
    error = result.get_editor_property("error")
    if error:
        raise RuntimeError(f"Niagara {name} failed {error}")
    if not result.get_editor_property("is_complete"):
        raise RuntimeError(f"Niagara {name} is still pending")
    encoded = result.get_value_as_json_string()
    value = json.loads(encoded) if encoded else None
    return json.loads(value) if isinstance(value, str) else value


def _ref(system, emitter, script=None, module=None, inputs=None):
    value = {"System": system.get_path_name(), "EmitterName": emitter}
    if script:
        value["ScriptName"] = script
    if module:
        value["ModuleName"] = module
    if inputs:
        value["InputNameStack"] = inputs if isinstance(inputs, list) else [inputs]
    return value


def _input(system, emitter, script, module, name, kind, value):
    result = _json_call("SetStackInputData", {
        "StackInputRef": _ref(system, emitter, script, module, name),
        "InputData": {"struct": kind, "value": value}})
    if not result or result.get("returnValue") is None:
        raise RuntimeError(f"Input could not be applied {emitter}.{module}.{name}")


def _float(system, emitter, script, module, name, value):
    _input(system, emitter, script, module, name, "/Script/Niagara.NiagaraFloat", {"value": value})


def _vector(system, emitter, script, module, name, value):
    _input(system, emitter, script, module, name, "/Script/CoreUObject.Vector3f", dict(zip(("x", "y", "z"), value)))


def _expression(system, emitter, script, module, name, expression):
    # Single rvalues provide continuous age ramps and externally driven scale.
    _input(system, emitter, script, module, name,
           "/Script/NiagaraEditor.NiagaraExt_StackInputData_HlslExpression", {"hlslExpression": expression})


def _system(name):
    system = unreal.load_asset(f"{FOLDER}/{name}")
    if system:
        for emitter in _native("GetSystemSummary", system).get_editor_property("emitters"):
            _native("RemoveEmitter", _struct(unreal.NiagaraExt_StackItemReference,
                    system=system, emitter_name=emitter.get_editor_property("emitter_name")))
    else:
        system = _native("CreateNiagaraSystem", name, FOLDER, None)
    if not system:
        raise RuntimeError(f"Cannot create Niagara system {name}")
    _json_call("AddUserVariables", {"System": system.get_path_name(), "VariablesToAdd": [{
        "Name": "User.Strength", "Type": {"classStructOrEnum": "/Script/Niagara.NiagaraFloat"},
        "DefaultValue": {"struct": "/Script/Niagara.NiagaraFloat", "value": {"value": 1.0}},
        "Description": "Impact intensity, 0.5 to 2.5. Counts and visible sizes scale together."}]})
    return system


def _particle_material(name, color, mode="dust"):
    material = unreal.load_asset(f"{FOLDER}/{name}")
    if not material:
        material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            name, FOLDER, unreal.Material, unreal.MaterialFactoryNew())
    for node in list(EDIT.get_material_expressions(material)):
        EDIT.delete_material_expression(material, node)
    mesh = mode in ("concrete", "glass")
    blend = unreal.BlendMode.BLEND_OPAQUE if mesh else unreal.BlendMode.BLEND_TRANSLUCENT
    if mode == "fire":
        blend = unreal.BlendMode.BLEND_ADDITIVE
    material.set_editor_property("blend_mode", blend)
    material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_DEFAULT_LIT if mesh else unreal.MaterialShadingModel.MSM_UNLIT)
    material.set_editor_property("two_sided", True)
    material.set_editor_property("used_with_niagara_mesh_particles", mesh)
    material.set_editor_property("used_with_niagara_sprites", not mesh)
    tint = EDIT.create_material_expression(material, unreal.MaterialExpressionConstant3Vector)
    tint.set_editor_property("constant", unreal.LinearColor(*color, 1))
    if mesh:
        EDIT.connect_material_property(tint, "", unreal.MaterialProperty.MP_BASE_COLOR)
    else:
        exposure = EDIT.create_material_expression(material, unreal.MaterialExpressionEyeAdaptationInverse)
        if not EDIT.connect_material_expressions(tint, "", exposure, "LightValueInput"):
            raise RuntimeError("Effect exposure compensation connection failed")
        EDIT.connect_material_property(exposure, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    if mesh:
        rough = EDIT.create_material_expression(material, unreal.MaterialExpressionConstant)
        rough.set_editor_property("r", 0.24 if mode == "glass" else 0.88)
        EDIT.connect_material_property(rough, "", unreal.MaterialProperty.MP_ROUGHNESS)
        if mode == "glass":
            metal = EDIT.create_material_expression(material, unreal.MaterialExpressionConstant)
            metal.set_editor_property("r", 0.42)
            EDIT.connect_material_property(metal, "", unreal.MaterialProperty.MP_METALLIC)
    else:
        uv = EDIT.create_material_expression(material, unreal.MaterialExpressionTextureCoordinate)
        particle = EDIT.create_material_expression(material, unreal.MaterialExpressionParticleColor)
        shape = EDIT.create_material_expression(material, unreal.MaterialExpressionCustom)
        shape.set_editor_property("output_type", unreal.CustomMaterialOutputType.CMOT_FLOAT1)
        shape.set_editor_property("inputs", [_struct(unreal.CustomInput, input_name="UV"), _struct(unreal.CustomInput, input_name="Alpha")])
        if mode == "dust":
            code = ("float2 p=(UV-0.5)*2; float r=length(p); "
                    "float n=0.59+0.19*sin(p.x*12+p.y*4)*sin(p.y*10-p.x*3)"
                    "+0.11*sin(p.x*27+p.y*19); "
                    "return saturate(1-r)*saturate(1-r)*saturate(n)*Alpha*0.76;")
        else:
            code = "float r=length((UV-0.5)*2); return pow(saturate(1-r),2.4)*Alpha;"
        shape.set_editor_property("code", code)
        if not EDIT.connect_material_expressions(uv, "", shape, "UV"):
            raise RuntimeError("Effect UV connection failed")
        EDIT.connect_material_expressions(particle, "A", shape, "Alpha")
        EDIT.connect_material_property(shape, "", unreal.MaterialProperty.MP_OPACITY)
    errors = list(EDIT.recompile_material(material))
    if errors:
        raise RuntimeError(f"Effect shader failed {name} {errors}")
    unreal.EditorAssetLibrary.save_loaded_asset(material, only_if_is_dirty=False)
    return material


def _renderer(system, emitter, material, mesh=False, glass=False):
    ref = _struct(unreal.NiagaraExt_StackItemReference, system=system, emitter_name=emitter, renderer_index=0)
    data = json.loads(_native("GetRendererData", ref).get_editor_property("property_values"))
    data["bCastShadows"] = False
    data["bEnableCameraDistanceCulling"] = True
    data["MaxCameraDistance"] = 50000
    if mesh:
        item = data["Meshes"][0]
        item["mesh"] = {"refPath": "/Engine/BasicShapes/Cube.Cube"}
        item["scale"] = {"x": 1.2 if glass else 1, "y": 0.7, "z": 0.07 if glass else 0.35}
        data["Meshes"] = [item]
        data["bOverrideMaterials"] = True
        data["OverrideMaterials"] = [{"explicitMat": {"refPath": material.get_path_name()}}]
    else:
        data["Material"] = {"refPath": material.get_path_name()}
    _native("SetRendererData", ref, _struct(unreal.NiagaraExt_RendererData, property_values=json.dumps(data)))


def _explosion_material(name, mode):
    """Age-driven turbulent fire, smoke and a thin pressure front."""
    material = unreal.load_asset(f"{FOLDER}/{name}")
    if not material:
        material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            name, FOLDER, unreal.Material, unreal.MaterialFactoryNew())
    for expression in list(EDIT.get_material_expressions(material)):
        EDIT.delete_material_expression(material, expression)
    material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
    # Translucent fire preserves orange/dark regions when lobes overlap. An
    # additive stack otherwise clips all of the interior to a flat white blob.
    material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT
                                 if mode in ("fire", "smoke") else unreal.BlendMode.BLEND_ADDITIVE)
    material.set_editor_property("used_with_niagara_sprites", True)
    material.set_editor_property("two_sided", True)
    uv = EDIT.create_material_expression(material, unreal.MaterialExpressionTextureCoordinate)
    age = EDIT.create_material_expression(material, unreal.MaterialExpressionParticleRelativeTime)
    particle = EDIT.create_material_expression(material, unreal.MaterialExpressionParticleColor)
    surface = EDIT.create_material_expression(material, unreal.MaterialExpressionCustom)
    surface.set_editor_property("output_type", unreal.CustomMaterialOutputType.CMOT_FLOAT4)
    surface.set_editor_property("inputs", [
        _struct(unreal.CustomInput, input_name="UV"),
        _struct(unreal.CustomInput, input_name="Age"),
        _struct(unreal.CustomInput, input_name="Alpha"),
    ])
    # Three octaves of interpolated hash noise break up the silhouette as well
    # as its color. Age animates the turbulence without any external textures.
    noise = """
float2 p = (UV - 0.5) * 2.0;
float r = length(p);
float2 q = p * 3.4 + float2(Age * 0.8, -Age * 1.6);
float n = 0.0;
float weight = 0.57;
[unroll] for (int octave = 0; octave < 3; ++octave) {
    float2 cell = floor(q);
    float2 f = frac(q);
    float2 u = f * f * (3.0 - 2.0 * f);
    float4 h = frac(sin(float4(dot(cell, float2(127.1, 311.7)),
        dot(cell + float2(1, 0), float2(127.1, 311.7)),
        dot(cell + float2(0, 1), float2(127.1, 311.7)),
        dot(cell + float2(1, 1), float2(127.1, 311.7)))) * 43758.5453);
    n += lerp(lerp(h.x, h.y, u.x), lerp(h.z, h.w, u.x), u.y) * weight;
    q = q * 2.07 + float2(5.7, 2.9);
    weight *= 0.48;
}
float edge = saturate((0.87 - r + (n - 0.48) * 0.43) * 5.0);
float fade = saturate((1.0 - Age) * 2.8);
"""
    if mode == "fire":
        body = """
float heat = saturate((1.0 - r) * 0.32 + n * 0.93 - Age * 0.27);
float fireEdge = saturate((0.83 - r + (n - 0.48) * 0.68) * 5.5);
float3 ember = float3(0.18, 0.012, 0.002);
float3 flame = float3(3.5, 0.58, 0.025);
float3 hot = float3(7.5, 3.0, 0.27);
float3 color = lerp(ember, flame, smoothstep(0.26, 0.70, heat));
color = lerp(color, hot, smoothstep(0.79, 0.98, heat));
float shadowPockets = lerp(0.35, 1.0, smoothstep(0.24, 0.58, n));
color *= shadowPockets;
float opacity = fireEdge * (0.48 + n * 0.50) * fade * Alpha * 0.95;
return float4(color, opacity);
"""
    elif mode == "smoke":
        body = """
float rise = saturate(Age * 9.0);
float shade = saturate(n * 0.7 + (1.0 - r) * 0.28);
float3 color = lerp(float3(0.028, 0.025, 0.023), float3(0.22, 0.19, 0.16), shade);
float opacity = edge * (0.22 + n * 0.53) * rise * fade * Alpha * 0.62;
return float4(color, opacity);
"""
    else:
        body = """
float ringRadius = 0.77 + (n - 0.48) * 0.065;
float ring = exp(-pow((r - ringRadius) / 0.025, 2.0));
float opacity = ring * (0.5 + n * 0.5) * pow(saturate(1.0 - Age), 1.2) * Alpha * 0.52;
return float4(3.2, 1.35, 0.28, opacity);
"""
    surface.set_editor_property("code", noise + body)
    for expression, output, pin in ((uv, "", "UV"), (age, "", "Age"), (particle, "A", "Alpha")):
        if not EDIT.connect_material_expressions(expression, output, surface, pin):
            raise RuntimeError("Cannot connect explosion shader input " + pin)
    rgb = EDIT.create_material_expression(material, unreal.MaterialExpressionComponentMask)
    rgb.set_editor_properties({"r": True, "g": True, "b": True, "a": False})
    opacity = EDIT.create_material_expression(material, unreal.MaterialExpressionComponentMask)
    opacity.set_editor_properties({"r": False, "g": False, "b": False, "a": True})
    exposure = EDIT.create_material_expression(material, unreal.MaterialExpressionEyeAdaptationInverse)
    # UMaterialGraphNode::GetShortenPinName maps the native Input property to
    # NAME_None. The MaterialEditingLibrary therefore needs an empty pin name.
    for source, target, pin in ((surface, rgb, ""), (surface, opacity, ""),
                                (rgb, exposure, "LightValueInput")):
        if not EDIT.connect_material_expressions(source, "", target, pin):
            raise RuntimeError("Cannot connect explosion shader edge " + target.get_name() + ":" + pin)
    for source, prop in ((exposure, unreal.MaterialProperty.MP_EMISSIVE_COLOR),
                          (opacity, unreal.MaterialProperty.MP_OPACITY)):
        if not EDIT.connect_material_property(source, "", prop):
            raise RuntimeError("Cannot connect explosion material output " + str(prop))
    _verify_explosion_graph(material)
    errors = list(EDIT.recompile_material(material))
    if errors:
        raise RuntimeError("Explosion shader failed " + name + " " + str(errors))
    unreal.EditorAssetLibrary.save_loaded_asset(material, only_if_is_dirty=False)
    return material


def _verify_explosion_graph(material):
    """Catch disconnected inputs even when NullRHI cannot compile shaders."""
    expressions = list(EDIT.get_material_expressions(material))
    expected = ((unreal.MaterialExpressionCustom, 1, 3),
                (unreal.MaterialExpressionComponentMask, 2, 1),
                (unreal.MaterialExpressionEyeAdaptationInverse, 1, 1))
    for kind, count, required_inputs in expected:
        nodes = [node for node in expressions if isinstance(node, kind)]
        if len(nodes) != count:
            raise RuntimeError("Unexpected explosion shader graph " + material.get_name())
        for node in nodes:
            inputs = list(EDIT.get_inputs_for_material_expression(material, node))
            if len(inputs) < required_inputs or any(value is None for value in inputs[:required_inputs]):
                raise RuntimeError("Disconnected explosion shader input " + node.get_name())
    for prop in (unreal.MaterialProperty.MP_EMISSIVE_COLOR, unreal.MaterialProperty.MP_OPACITY):
        if EDIT.get_material_property_input_node(material, prop) is None:
            raise RuntimeError("Disconnected explosion shader output " + str(prop))


def _impact_system(dust_material, chip_material, glass_material, flash_material):
    """Layer the contact flash, pressure front, burning gas and delayed smoke."""
    fire = _explosion_material("M_BlastFire", "fire")
    smoke = _explosion_material("M_BlastSmoke", "smoke")
    ring = _explosion_material("M_PressureRing", "ring")
    sparks = _particle_material("M_HotSparks", (12, 4.2, 0.3), "fire")
    system = _system("NS_HighEnergyImpact")
    _burst(system, "ContactFlash", flash_material, 1, (0.045, 0.075), (300, 390),
           (0, 0), (0, 0, 0), 1)
    _expression(system, "ContactFlash", "EmitterUpdateScript", "SpawnBurst_Instantaneous", "Spawn Count", "1")
    _expression(system, "ContactFlash", "ParticleUpdateScript", "ScaleSpriteSize", "Scale Factor",
                "float2(0.7 + Particles.NormalizedAge, 0.7 + Particles.NormalizedAge)")
    _burst(system, "PressureFront", ring, 1, (0.40, 0.40), (1100, 1100),
           (0, 0), (0, 0, 0), 1)
    _expression(system, "PressureFront", "EmitterUpdateScript", "SpawnBurst_Instantaneous", "Spawn Count", "1")
    _expression(system, "PressureFront", "ParticleUpdateScript", "ScaleSpriteSize", "Scale Factor",
                "float2(0.12 + 1.45 * Particles.NormalizedAge, 0.12 + 1.45 * Particles.NormalizedAge)")
    _burst(system, "FireLobes", fire, 10, (0.60, 0.90), (260, 390),
           (330, 750), (0, 0, 115), 1.3)
    _float(system, "FireLobes", "ParticleSpawnScript", "ShapeLocation", "Sphere Radius", 90)
    _expression(system, "FireLobes", "ParticleUpdateScript", "ScaleSpriteSize", "Scale Factor",
                "float2(0.65 + 0.85 * Particles.NormalizedAge, 0.65 + 1.1 * Particles.NormalizedAge)")
    _burst(system, "HotSparks", sparks, 64, (0.22, 0.75), (4, 10),
           (1050, 2300), (0, 0, -520), 0.45)
    _expression(system, "HotSparks", "ParticleUpdateScript", "ScaleSpriteSize", "Scale Factor",
                "float2(0.5, 2.2) * (1.0 - 0.7 * Particles.NormalizedAge)")
    _burst(system, "ImpactChips", chip_material, 72, (0.85, 1.8), (0.045, 0.15),
           (650, 1500), (0, 0, -980), 0.65, mesh=True)
    _burst(system, "ImpactGlass", glass_material, 35, (0.65, 1.5), (0.025, 0.1),
           (850, 1800), (0, 0, -850), 0.4, mesh=True, glass=True)
    _burst(system, "BlastSmoke", smoke, 9, (1.3, 2.0), (140, 230),
           (110, 300), (10, 0, 85), 1.8, delay=0.20)
    _expression(system, "BlastSmoke", "ParticleUpdateScript", "ScaleSpriteSize", "Scale Factor",
                "float2(0.7 + 1.35 * Particles.NormalizedAge, 0.8 + 1.55 * Particles.NormalizedAge)")
    _burst(system, "ImpactPowder", dust_material, 28, (1.4, 2.5), (55, 110),
           (240, 560), (8, 0, 35), 2.3, delay=0.07)
    return system


def _burst(system, name, material, count, lifetime, size, speed, gravity, drag, mesh=False, glass=False, delay=0):
    _native("AddEmitter", system, unreal.load_asset(TEMPLATE + ("UpwardMeshBurst" if mesh else "OmnidirectionalBurst")), name)
    update, spawn = "EmitterUpdateScript", "ParticleSpawnScript"
    _expression(system, name, update, "SpawnBurst_Instantaneous", "Spawn Count", f"int({count} * User.Strength)")
    _float(system, name, update, "SpawnBurst_Instantaneous", "Spawn Time", delay)
    for prop, value in zip(("Lifetime Min", "Lifetime Max"), lifetime):
        _float(system, name, spawn, "InitializeParticle", prop, value)
    size_names = ("Mesh Uniform Scale Min", "Mesh Uniform Scale Max") if mesh else ("Uniform Sprite Size Min", "Uniform Sprite Size Max")
    for prop, value in zip(size_names, size):
        _expression(system, name, spawn, "InitializeParticle", prop, f"{value} * User.Strength")
    for prop, value in zip(("Minimum", "Maximum"), speed):
        _float(system, name, spawn, "AddVelocity", ["Velocity Speed", prop], value)
    if mesh:
        _float(system, name, spawn, "AddVelocity", "Cone Angle", 115)
    else:
        _float(system, name, spawn, "ShapeLocation", "Sphere Radius", 13)
        _expression(system, name, "ParticleUpdateScript", "ScaleSpriteSize", "Scale Factor",
                    "float2(0.45 + 2.4 * Particles.NormalizedAge, 0.55 + 2.1 * Particles.NormalizedAge)")
    _vector(system, name, "ParticleUpdateScript", "GravityForce", "Gravity", gravity)
    _float(system, name, "ParticleUpdateScript", "Drag", "Drag", drag)
    _renderer(system, name, material, mesh, glass)


def _trail(system, name, material, rate, lifetime, size, speed, smoke=False):
    _native("AddEmitter", system, unreal.load_asset(TEMPLATE + "Fountain"), name)
    _float(system, name, "EmitterUpdateScript", "SpawnRate", "SpawnRate", rate)
    _input(system, name, "EmitterUpdateScript", "EmitterState", "Loop Behavior",
           "/Script/NiagaraEditor.NiagaraExt_StackInputData_Enum", {
               "enum": "/Niagara/Enums/ENiagara_EmitterStateOptions.ENiagara_EmitterStateOptions",
               "enumName": "ENiagara_EmitterStateOptions::NewEnumerator1"})
    _float(system, name, "EmitterUpdateScript", "EmitterState", "Loop Duration", 2.2)
    for prop, value in zip(("Lifetime Min", "Lifetime Max"), lifetime):
        _float(system, name, "ParticleSpawnScript", "InitializeParticle", prop, value)
    for prop, value in zip(("Uniform Sprite Size Min", "Uniform Sprite Size Max"), size):
        _float(system, name, "ParticleSpawnScript", "InitializeParticle", prop, value)
    _vector(system, name, "ParticleSpawnScript", "AddVelocity", "Cone Axis", (-1, 0, 0))
    _float(system, name, "ParticleSpawnScript", "AddVelocity", "Cone Angle", 18 if smoke else 6)
    for prop, value in zip(("Minimum", "Maximum"), speed):
        _float(system, name, "ParticleSpawnScript", "AddVelocity", ["Velocity Speed", prop], value)
    _float(system, name, "ParticleSpawnScript", "ShapeLocation", "Sphere Radius", 4)
    _vector(system, name, "ParticleUpdateScript", "GravityForce", "Gravity", (0, 0, 16 if smoke else 0))
    _float(system, name, "ParticleUpdateScript", "Drag", "Drag", 1.1 if smoke else 0.1)
    _renderer(system, name, material)


def _wrapper(name, system, lifetime):
    import blueprint_runtime as bp
    previous_folder = bp.ASSET_FOLDER
    bp.ASSET_FOLDER = FOLDER
    try:
        blueprint = bp._blueprint(name, unreal.NiagaraActor)
    finally:
        bp.ASSET_FOLDER = previous_folder
    bp._add_variable(blueprint, "Strength", "real")
    unreal.BlueprintEditorLibrary.set_blueprint_variable_instance_editable(blueprint, "Strength", True)
    bp._compile(blueprint)
    cdo = unreal.get_default_object(blueprint.generated_class())
    cdo.set_editor_property("Strength", 1.0)
    cdo.set_editor_property("initial_life_span", lifetime)
    component = cdo.get_editor_property("niagara_component")
    component.set_asset(system)
    component.set_auto_activate(False)
    component.set_editor_property("cast_shadow", False)
    editor = bp._graph(blueprint)
    begin = bp._event(blueprint, "ReceiveBeginPlay")
    niagara = editor.add_get_member_variable_node("NiagaraComponent", "/Script/Niagara.NiagaraActor")
    strength = editor.add_get_member_variable_node("Strength")
    clamp = bp._call(editor, "KismetMathLibrary.FClamp")
    bp._wire(strength, "Strength", clamp, "Value")
    bp._value(clamp, "Min", 0.5)
    bp._value(clamp, "Max", 2.5)
    assign = bp._call(editor, "/Script/Niagara.NiagaraComponent.SetVariableFloat")
    bp._wire(niagara, "NiagaraComponent", assign, "self")
    bp._value(assign, "InVariableName", "User.Strength")
    bp._wire(clamp, "ReturnValue", assign, "InValue")
    bp._exec(begin, assign)
    activate = bp._call(editor, "ActorComponent.Activate")
    bp._wire(niagara, "NiagaraComponent", activate, "self")
    bp._value(activate, "bReset", "true")
    bp._exec(assign, activate)
    bp._save(blueprint)
    return blueprint


def build_effects(materials=None, force_rebuild=False):
    """Return dust_class, explosion_class and launch_class generated Actor classes."""
    unreal.InternationalizationLibrary.set_current_language("en", False)
    if not force_rebuild:
        cached = _cached_effects()
        if cached:
            return cached
    if not hasattr(unreal, "NiagaraToolset_System"):
        raise RuntimeError("Author effects with -EnablePlugins=NiagaraToolsets")
    dust_mat = _particle_material("M_FineDust", (0.61, 0.55, 0.46))
    smoke_mat = _particle_material("M_ExhaustSmoke", (0.33, 0.35, 0.37))
    fire_mat = _particle_material("M_RocketFlame", (18, 5.5, 0.65), "fire")
    flash_mat = _particle_material("M_ImpactFlash", (24, 15, 5), "fire")
    concrete_mat = _particle_material("M_ConcreteChips", (0.49, 0.44, 0.37), "concrete")
    glass_mat = _particle_material("M_GlassSplinters", (0.29, 0.49, 0.54), "glass")
    dust = _system("NS_LocalDust")
    _burst(dust, "PowderJet", dust_mat, 20, (2.2, 3.8), (32, 80), (90, 260), (15, 4, 32), 2.7)
    _burst(dust, "LatePowder", dust_mat, 9, (1.7, 2.8), (22, 48), (45, 120), (10, 0, 25), 2.2, delay=0.13)
    _burst(dust, "ConcreteChips", concrete_mat, 32, (0.7, 1.5), (0.025, 0.075), (180, 540), (0, 0, -850), 0.7, mesh=True)
    _burst(dust, "GlassSplinters", glass_mat, 14, (0.6, 1.25), (0.025, 0.085), (200, 620), (0, 0, -780), 0.45, mesh=True, glass=True)
    explosion = _impact_system(dust_mat, concrete_mat, glass_mat, flash_mat)
    launch = _system("NS_RocketExhaust")
    _trail(launch, "FlameCore", fire_mat, 160, (0.055, 0.12), (13, 22), (800, 1250))
    _trail(launch, "ExhaustWisps", smoke_mat, 35, (0.35, 0.65), (16, 28), (130, 220), smoke=True)
    blueprints = {"dust": _wrapper("BP_LocalPowder", dust, 4.2),
                  "explosion": _wrapper("BP_HighEnergyImpact", explosion, 4.0),
                  "launch": _wrapper("BP_RocketExhaust", launch, 3.0)}
    for system in (dust, explosion, launch):
        if not unreal.EditorAssetLibrary.save_loaded_asset(system, only_if_is_dirty=False):
            raise RuntimeError(f"Cannot save {system.get_path_name()}")
    unreal.SystemLibrary.execute_console_command(None, "Editor.AsyncAssetCompilationFinishAll")
    report = {"classes": {key: value.generated_class().get_path_name() for key, value in blueprints.items()},
              "systems": [system.get_path_name() for system in (dust, explosion, launch)]}
    (ROOT / "Saved" / "effects-v2-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    verify_effects()
    unreal.log("SKYFALL_EFFECTS_V2_READY " + json.dumps(report))
    return {key + "_class": value.generated_class() for key, value in blueprints.items()}


def rebuild_impact_style():
    """Re-author the high-energy effect while reusing dust and exhaust systems."""
    unreal.InternationalizationLibrary.set_current_language("en", False)
    if not hasattr(unreal, "NiagaraToolset_System"):
        raise RuntimeError("Author effects with -EnablePlugins=NiagaraToolsets")
    materials = []
    for name in ("M_FineDust", "M_ConcreteChips", "M_GlassSplinters", "M_ImpactFlash"):
        material = unreal.load_asset(f"{FOLDER}/{name}")
        if material is None:
            raise RuntimeError("Existing combat material missing " + name)
        materials.append(material)
    system = _impact_system(*materials)
    blueprint = _wrapper("BP_HighEnergyImpact", system, 4.0)
    unreal.EditorAssetLibrary.save_loaded_asset(system, only_if_is_dirty=False)
    unreal.SystemLibrary.execute_console_command(None, "Editor.AsyncAssetCompilationFinishAll")
    verify_effects()
    unreal.log("SKYFALL_EXPLOSION_STYLE_READY " + BUILD_VERSION)
    return blueprint.generated_class()


def verify_effects(require_rendered_shaders=False):
    """Check loaded runtime assets, Niagara diagnostics, Blueprint and shader status."""
    import blueprint_runtime as bp
    report = {"engine": unreal.SystemLibrary.get_engine_version(), "systems": {}, "materials": {}, "blueprints": {}}
    for name in SYSTEM_NAMES:
        system = unreal.load_asset(f"{FOLDER}/{name}")
        if not system:
            raise RuntimeError(f"Missing {name}")
        unreal.EditorAssetLibrary.save_loaded_asset(system, only_if_is_dirty=False)
        result = _native("GetSystemCompileState", system)
        if not result.get_editor_property("is_complete") or result.get_editor_property("error"):
            raise RuntimeError(f"Niagara diagnostics pending {name}")
        state = json.loads(result.get_value_as_json_string())
        report["systems"][name] = state
        if state.get("hasErrors", state.get("bHasErrors")) or state.get("isStale", state.get("bIsStale")):
            raise RuntimeError(f"Niagara compile failed {name} {state}")
    for name in MATERIAL_NAMES:
        material = unreal.load_asset(f"{FOLDER}/{name}")
        if name in ("M_BlastFire", "M_BlastSmoke", "M_PressureRing"):
            _verify_explosion_graph(material)
        errors = list(EDIT.recompile_material(material))
        unreal.SystemLibrary.execute_console_command(None, "Editor.AsyncAssetCompilationFinishAll")
        stats = EDIT.get_statistics(material)
        pixel_instructions = stats.get_editor_property("num_pixel_shader_instructions")
        report["materials"][name] = {"errors": errors, "shader_types": EDIT.get_num_shader_types(material),
            "pixel_instructions": pixel_instructions, "rendered_shader_verified": pixel_instructions > 0}
        if errors:
            raise RuntimeError(f"Shader failed {name} {errors}")
        if require_rendered_shaders and pixel_instructions <= 0:
            raise RuntimeError(f"No compiled pixel shader for {name}; run validation with D3D rendering enabled")
        unreal.EditorAssetLibrary.save_loaded_asset(material, only_if_is_dirty=False)
    for name in CLASS_NAMES.values():
        blueprint = unreal.load_asset(f"{FOLDER}/{name}")
        bp._compile(blueprint)
        report["blueprints"][name] = str(blueprint.get_editor_property("status"))
    # Only successfully validated versions enter the fast-load path.
    report["validated_version"] = BUILD_VERSION
    report["material_validation"] = ("rendered_shaders" if all(
        value["rendered_shader_verified"] for value in report["materials"].values()) else "graph_only")
    if report["material_validation"] == "graph_only":
        unreal.log_warning("SKYFALL_EFFECTS_SHADER_VALIDATION_PENDING: graph checks do not verify D3D shaders")
    for name in (*CLASS_NAMES.values(), *SYSTEM_NAMES, *MATERIAL_NAMES):
        asset = unreal.load_asset(f"{FOLDER}/{name}")
        unreal.EditorAssetLibrary.set_metadata_tag(asset, "SkyfallEffectsVersion", BUILD_VERSION)
        unreal.EditorAssetLibrary.save_loaded_asset(asset, only_if_is_dirty=False)
    (ROOT / "Saved" / "effects-v2-validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    unreal.log("SKYFALL_EFFECTS_V2_VALIDATED " + json.dumps(report))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    if "verify" in sys.argv:
        verify_effects()
    else:
        build_effects()
