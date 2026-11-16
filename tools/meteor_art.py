"""Independent meteor art. Author with NiagaraToolsets and D3D rendering.

Only assets below /Game/Skyfall/Meteor are authored. Existing CombatV2
effects are loaded read-only. Gameplay owns collision, damage and lifecycle.
"""
import json
from contextlib import contextmanager
from pathlib import Path

import unreal
import effects_v2 as fx
from blueprint_runtime import _compile

ROOT = Path(__file__).resolve().parents[1]
FOLDER = "/Game/Skyfall/Meteor"
VERSION = "2.0.0"
EDIT = unreal.MaterialEditingLibrary
CLASS_NAMES = ("BP_MeteorTail", "BP_MeteorImpact", "BP_MeteorExit")
SYSTEM_NAMES = ("NS_MeteorTail", "NS_MeteorImpact", "NS_MeteorExit")
MATERIAL_NAMES = ("M_MeteorCrust", "M_MeteorChips", "M_MeteorHeat", "M_MeteorPowder",
                  "M_MeteorSmoke", "M_MeteorSpark", "M_MeteorFlash",
                  "M_MeteorPressureRing", "M_MeteorConcrete")


@contextmanager
def _folder():
    old = fx.FOLDER
    fx.FOLDER = FOLDER
    try:
        yield
    finally:
        fx.FOLDER = old


def _link(source, output, target, pin):
    if not EDIT.connect_material_expressions(source, output, target, pin):
        raise RuntimeError("Meteor material connection failed " + target.get_name()+":"+pin)


def _output(source, output, prop):
    if not EDIT.connect_material_property(source, output, prop):
        raise RuntimeError("Meteor material output failed " + str(prop))


def _constant(material, value):
    node = EDIT.create_material_expression(material, unreal.MaterialExpressionConstant)
    node.set_editor_property("r", value)
    return node


def _crust_material(name, glowing=True):
    path = FOLDER+"/"+name
    material = unreal.load_asset(path) if unreal.EditorAssetLibrary.does_asset_exist(path) else None
    if not material:
        material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            name, FOLDER, unreal.Material, unreal.MaterialFactoryNew())
    for node in list(EDIT.get_material_expressions(material)):
        EDIT.delete_material_expression(material, node)
    material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_OPAQUE)
    material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_DEFAULT_LIT)
    material.set_editor_property("used_with_niagara_mesh_particles", True)
    uv = EDIT.create_material_expression(material, unreal.MaterialExpressionTextureCoordinate)
    surface = EDIT.create_material_expression(material, unreal.MaterialExpressionCustom)
    surface.set_editor_property("output_type", unreal.CustomMaterialOutputType.CMOT_FLOAT4)
    surface.set_editor_property("inputs", [fx._struct(unreal.CustomInput, input_name="UV")])
    surface.set_editor_property("code", """
float2 q = UV * float2(17.0, 8.5);
q += float2(sin(q.y*2.3), cos(q.x*1.8)) * .15;
float2 cell = floor(q), local = frac(q);
float first = 100.0, second = 100.0;
[unroll] for (int y=-1; y<=1; ++y) {
 [unroll] for (int x=-1; x<=1; ++x) {
  float2 offset = float2(x,y);
  float2 h = frac(sin(float2(dot(cell+offset,float2(127.1,311.7)),
                               dot(cell+offset,float2(269.5,183.3))))*43758.5453);
  float d = length(offset + .18 + h*.64 - local);
  if (d < first) { second=first; first=d; } else { second=min(second,d); }
 }
}
float seam = 1.0-smoothstep(.014,.071,second-first);
float grain = .5 + .25*sin(UV.x*483.0+sin(UV.y*221.0))*sin(UV.y*571.0)
                   + .18*sin(UV.x*1291.0+UV.y*913.0);
float patch = .5+.5*sin(UV.x*37.0+sin(UV.y*53.0));
float3 crust = lerp(float3(.019,.017,.016),float3(.115,.085,.058),saturate(grain*.75+patch*.25));
crust *= 1.0-seam*.6;
float glow = seam * (.25+.75*patch);
return float4(crust, glow);
""")
    _link(uv, "", surface, "UV")
    rgb = EDIT.create_material_expression(material, unreal.MaterialExpressionComponentMask)
    rgb.set_editor_properties({"r":True,"g":True,"b":True,"a":False})
    mask = EDIT.create_material_expression(material, unreal.MaterialExpressionComponentMask)
    mask.set_editor_properties({"r":False,"g":False,"b":False,"a":True})
    _link(surface,"",rgb,"")
    _link(surface,"",mask,"")
    _output(rgb,"",unreal.MaterialProperty.MP_BASE_COLOR)
    _output(_constant(material,.91),"",unreal.MaterialProperty.MP_ROUGHNESS)
    _output(_constant(material,.14),"",unreal.MaterialProperty.MP_SPECULAR)
    if glowing:
        tint = EDIT.create_material_expression(material, unreal.MaterialExpressionConstant3Vector)
        tint.set_editor_property("constant",unreal.LinearColor(5.2,.58,.025,1))
        multiply = EDIT.create_material_expression(material,unreal.MaterialExpressionMultiply)
        _link(mask,"",multiply,"A")
        _link(tint,"",multiply,"B")
        exposure=EDIT.create_material_expression(material,unreal.MaterialExpressionEyeAdaptationInverse)
        _link(multiply,"",exposure,"LightValueInput")
        _output(exposure,"",unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    normal=EDIT.create_material_expression(material,unreal.MaterialExpressionCustom)
    normal.set_editor_property("output_type",unreal.CustomMaterialOutputType.CMOT_FLOAT3)
    normal.set_editor_property("inputs",[fx._struct(unreal.CustomInput,input_name="UV")])
    normal.set_editor_property("code","return normalize(float3(.19*sin(UV.x*377+sin(UV.y*137)),.19*cos(UV.y*411+sin(UV.x*153)),1));")
    _link(uv,"",normal,"UV")
    _output(normal,"",unreal.MaterialProperty.MP_NORMAL)
    errors=list(EDIT.recompile_material(material))
    if errors:
        raise RuntimeError("Meteor crust shader: "+str(errors))
    unreal.EditorAssetLibrary.save_loaded_asset(material,only_if_is_dirty=False)
    return material


def _mesh(material):
    folder=FOLDER+"/Meshes"
    subsystem=unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)
    meshes=[unreal.load_asset(path) for path in subsystem.list_assets(folder,recursive=False,include_folder=False)]
    meshes=[value for value in meshes if isinstance(value,unreal.StaticMesh)]
    if not meshes:
        from build_scene import import_meshes
        path=ROOT/"SourceAssets/Meteor/meteor_core.fbx"
        if not path.exists():
            raise RuntimeError("Run Blender export_meteor_asset.py first")
        meshes=import_meshes(path,folder,True)
    if len(meshes)!=1:
        raise RuntimeError("Expected one meteor core mesh")
    mesh=meshes[0]
    mesh.set_material(0,material)
    bounds=mesh.get_bounds()
    extent=bounds.box_extent
    diameter=max(extent.x,extent.y,extent.z)*2
    if not 3.3 < diameter < 3.5:
        raise RuntimeError("Meteor core must retain raw metre geometry, got diameter "+str(diameter))
    mesh_editor=unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem) or unreal.StaticMeshEditorSubsystem()
    if mesh_editor.get_convex_collision_count(mesh)<=0:
        # UE 5.8 exposes EScriptCollisionShapeType (not the legacy Scripting
        # spelling). A convex hull allows the lodged core to become debris.
        added=mesh_editor.add_simple_collisions(mesh,unreal.ScriptCollisionShapeType.NDOP26)
        if added<0 or mesh_editor.get_convex_collision_count(mesh)<=0:
            raise RuntimeError("Meteor core requires a convex physics collision hull")
    unreal.EditorAssetLibrary.save_loaded_asset(mesh,only_if_is_dirty=False)
    return mesh


def _local_space(system,name):
    ref=fx._struct(unreal.NiagaraExt_StackItemReference,system=system,emitter_name=name)
    data=json.loads(fx._native("GetEmitterData",ref).get_editor_property("property_values"))
    data["bLocalSpace"]=True
    fx._native("SetEmitterData",ref,fx._struct(unreal.NiagaraExt_EmitterData,property_values=json.dumps(data)))


def _rock_renderer(system,name,mesh,material):
    ref=fx._struct(unreal.NiagaraExt_StackItemReference,system=system,emitter_name=name,renderer_index=0)
    data=json.loads(fx._native("GetRendererData",ref).get_editor_property("property_values"))
    item=data["Meshes"][0]
    item["mesh"]={"refPath":mesh.get_path_name()}
    # Imported raw mesh diameter is 3.4 units. This makes Niagara scale .1 mean 34 cm.
    item["scale"]={"x":100,"y":75,"z":85}
    data["Meshes"]=[item]
    data["bOverrideMaterials"]=True
    data["OverrideMaterials"]=[{"explicitMat":{"refPath":material.get_path_name()}}]
    data["bCastShadows"]=False
    fx._native("SetRendererData",ref,fx._struct(unreal.NiagaraExt_RendererData,property_values=json.dumps(data)))


def _sprite_growth(system,name,x,y=None):
    """Scale the particle itself: world-space bursts ignore actor scale."""
    fx._expression(system,name,"ParticleUpdateScript","ScaleSpriteSize","Scale Factor",
                   "float2("+x+","+(y if y is not None else x)+")")


def _one_particle(system,name):
    # Strength scales size; stacking flashes or rings would wash out the fire.
    fx._expression(system,name,"EmitterUpdateScript","SpawnBurst_Instantaneous","Spawn Count","1")


def _spray_cone(system,name,angle):
    fx._vector(system,name,"ParticleSpawnScript","AddVelocity","Cone Axis",(0,0,1))
    fx._float(system,name,"ParticleSpawnScript","AddVelocity","Cone Angle",angle)


def _systems(mesh,mats):
    tail=fx._system("NS_MeteorTail")
    fx._trail(tail,"ScorchWake",mats["heat"],88,(.22,.42),(140,235),(1600,2600))
    fx._trail(tail,"EmberSheath",mats["spark"],125,(.24,.48),(6,15),(1100,2300))
    fx._trail(tail,"SmokeWake",mats["smoke"],20,(.30,.54),(100,170),(900,1450),smoke=True)
    for emitter in ("ScorchWake","EmberSheath","SmokeWake"):
        fx._float(tail,emitter,"ParticleSpawnScript","ShapeLocation","Sphere Radius",80)
        fx._float(tail,emitter,"EmitterUpdateScript","EmitterState","Loop Duration",3.5)
        _local_space(tail,emitter)

    # A brief flash gives way to orange, turbulent lobes. Their velocities,
    # radius and per-particle size target an 8-12 m silhouette at Strength 1.
    impact=fx._system("NS_MeteorImpact")
    fx._burst(impact,"MeteorFlash",mats["flash"],1,(.055,.085),(1000,1200),(0,0),(0,0,0),1)
    _one_particle(impact,"MeteorFlash")
    _sprite_growth(impact,"MeteorFlash","0.45 + 0.7 * Particles.NormalizedAge")
    fx._burst(impact,"PressureFront",mats["ring"],1,(.52,.52),(1450,1450),(0,0),(0,0,0),1)
    _one_particle(impact,"PressureFront")
    _sprite_growth(impact,"PressureFront","0.15 + 1.2 * Particles.NormalizedAge")
    fx._burst(impact,"CoreHeat",mats["heat"],4,(.50,.72),(470,600),(90,180),(0,0,120),2.1)
    fx._float(impact,"CoreHeat","ParticleSpawnScript","ShapeLocation","Sphere Radius",40)
    _sprite_growth(impact,"CoreHeat","0.4 + Particles.NormalizedAge")
    fx._burst(impact,"FireLobes",mats["heat"],13,(.70,1.05),(390,540),(550,850),(0,0,160),1.25)
    fx._float(impact,"FireLobes","ParticleSpawnScript","ShapeLocation","Sphere Radius",110)
    _sprite_growth(impact,"FireLobes","0.5 + Particles.NormalizedAge",
                   "0.5 + 1.2 * Particles.NormalizedAge")
    # The large pieces read as the meteor crust; lighter rectangular chips
    # read as the building. Explicit upward cones keep the spray above contact.
    fx._burst(impact,"RadialRock",mats["chips"],28,(1.8,2.9),(.09,.24),(1100,1900),(0,0,-980),.48,mesh=True)
    _rock_renderer(impact,"RadialRock",mesh,mats["chips"])
    _spray_cone(impact,"RadialRock",75)
    fx._burst(impact,"MineralChips",mats["chips"],72,(.9,1.9),(.015,.05),(1350,2300),(0,0,-980),.65,mesh=True)
    _rock_renderer(impact,"MineralChips",mesh,mats["chips"])
    _spray_cone(impact,"MineralChips",83)
    fx._burst(impact,"ConcreteSpray",mats["concrete"],46,(1.0,2.2),(.08,.28),(1200,2100),(0,0,-980),.6,mesh=True)
    _spray_cone(impact,"ConcreteSpray",68)
    fx._burst(impact,"HotCinders",mats["spark"],125,(.28,.95),(6,15),(1400,2700),(0,0,-700),.5)
    _sprite_growth(impact,"HotCinders","0.6 * (1.0 - 0.8 * Particles.NormalizedAge)",
                   "2.8 * (1.0 - 0.8 * Particles.NormalizedAge)")
    fx._burst(impact,"ShockPowder",mats["powder"],18,(.8,1.4),(70,125),(500,850),(0,0,35),1.8,delay=.08)
    _sprite_growth(impact,"ShockPowder","0.55 + Particles.NormalizedAge")
    fx._burst(impact,"LateSmoke",mats["smoke"],4,(1.1,1.65),(120,180),(130,230),(0,0,120),1.7,delay=.40)
    _sprite_growth(impact,"LateSmoke","0.75 + 0.9 * Particles.NormalizedAge")

    # A separate, smaller punch-out beat makes oblique traversal readable;
    # it does not replay the full primary fireball at the far wall.
    exit_system=fx._system("NS_MeteorExit")
    fx._burst(exit_system,"ExitFlash",mats["flash"],1,(.045,.065),(440,580),(0,0),(0,0,0),1)
    _one_particle(exit_system,"ExitFlash")
    _sprite_growth(exit_system,"ExitFlash","0.5 + 0.6 * Particles.NormalizedAge")
    fx._burst(exit_system,"ExitPressure",mats["ring"],1,(.34,.34),(650,650),(0,0),(0,0,0),1)
    _one_particle(exit_system,"ExitPressure")
    _sprite_growth(exit_system,"ExitPressure","0.2 + 1.1 * Particles.NormalizedAge")
    fx._burst(exit_system,"ExitFire",mats["heat"],6,(.35,.58),(230,330),(300,540),(0,0,110),1.3)
    fx._float(exit_system,"ExitFire","ParticleSpawnScript","ShapeLocation","Sphere Radius",60)
    _sprite_growth(exit_system,"ExitFire","0.6 + 0.9 * Particles.NormalizedAge")
    fx._burst(exit_system,"ExitRock",mats["chips"],10,(1.0,1.8),(.06,.15),(850,1450),(0,0,-980),.55,mesh=True)
    _rock_renderer(exit_system,"ExitRock",mesh,mats["chips"])
    _spray_cone(exit_system,"ExitRock",78)
    fx._burst(exit_system,"ExitConcrete",mats["concrete"],32,(.8,1.7),(.06,.22),(1000,1800),(0,0,-980),.55,mesh=True)
    _spray_cone(exit_system,"ExitConcrete",82)
    fx._burst(exit_system,"ExitCinders",mats["spark"],52,(.2,.65),(5,11),(1100,2100),(0,0,-700),.55)
    _sprite_growth(exit_system,"ExitCinders","0.5 * (1.0 - 0.8 * Particles.NormalizedAge)",
                   "2.4 * (1.0 - 0.8 * Particles.NormalizedAge)")
    fx._burst(exit_system,"ExitPowder",mats["powder"],10,(.65,1.1),(55,95),(380,650),(0,0,35),1.8,delay=.08)
    _sprite_growth(exit_system,"ExitPowder","0.6 + Particles.NormalizedAge")
    fx._burst(exit_system,"ExitSmoke",mats["smoke"],3,(.8,1.2),(90,135),(110,190),(0,0,100),1.7,delay=.38)
    _sprite_growth(exit_system,"ExitSmoke","0.7 + 0.8 * Particles.NormalizedAge")
    return tail,impact,exit_system


def _cache():
    paths=[FOLDER+"/"+name for name in (*CLASS_NAMES,*SYSTEM_NAMES,*MATERIAL_NAMES)]
    assets=[]
    for path in paths:
        if not unreal.EditorAssetLibrary.does_asset_exist(path):
            return None
        asset=unreal.load_asset(path)
        if unreal.EditorAssetLibrary.get_metadata_tag(asset,"SkyfallMeteorVersion")!=VERSION:
            return None
        assets.append(asset)
    meshes=[unreal.load_asset(path) for path in unreal.get_editor_subsystem(unreal.EditorAssetSubsystem).list_assets(FOLDER+"/Meshes",recursive=False,include_folder=False)]
    meshes=[item for item in meshes if isinstance(item,unreal.StaticMesh)]
    if len(meshes)!=1 or not all(item.generated_class() for item in assets[:len(CLASS_NAMES)]):
        return None
    if (unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem) or unreal.StaticMeshEditorSubsystem()).get_convex_collision_count(meshes[0])<=0:
        return None
    return {"mesh":meshes[0],"meteor_tail_class":assets[0].generated_class(),
            "meteor_impact_class":assets[1].generated_class(),
            "meteor_exit_class":assets[2].generated_class()}


def build_meteor_art(force_rebuild=False,require_rendered_shaders=True):
    """Return core mesh and tail/impact/exit NiagaraActor classes; never edit the map."""
    cached=None if force_rebuild else _cache()
    if cached:
        unreal.log("SKYFALL_METEOR_ART_CACHED "+VERSION)
        return cached
    if not hasattr(unreal,"NiagaraToolset_System"):
        raise RuntimeError("Author meteor art with -EnablePlugins=NiagaraToolsets")
    unreal.InternationalizationLibrary.set_current_language("en",False)
    with _folder():
        crust=_crust_material("M_MeteorCrust")
        chips=_crust_material("M_MeteorChips",False)
        mesh=_mesh(crust)
        mats={"chips":chips,
              "heat":fx._explosion_material("M_MeteorHeat","fire"),
              "powder":fx._particle_material("M_MeteorPowder",(.42,.37,.30)),
              "smoke":fx._explosion_material("M_MeteorSmoke","smoke"),
              "spark":fx._particle_material("M_MeteorSpark",(7,1.9,.14),"fire"),
              "flash":fx._particle_material("M_MeteorFlash",(26,10,1.5),"fire"),
              "ring":fx._explosion_material("M_MeteorPressureRing","ring"),
              "concrete":fx._particle_material("M_MeteorConcrete",(.54,.49,.42),"concrete")}
        tail,impact,exit_system=_systems(mesh,mats)
        tail_bp=fx._wrapper("BP_MeteorTail",tail,4.0)
        impact_bp=fx._wrapper("BP_MeteorImpact",impact,4.0)
        exit_bp=fx._wrapper("BP_MeteorExit",exit_system,2.8)
        report={"version":VERSION,"systems":{},"materials":{},"mesh":mesh.get_path_name(),
                "convex_count":(unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem) or unreal.StaticMeshEditorSubsystem()).get_convex_collision_count(mesh)}
        for system in (tail,impact,exit_system):
            unreal.EditorAssetLibrary.save_loaded_asset(system,only_if_is_dirty=False)
            result=fx._native("GetSystemCompileState",system)
            if result.get_editor_property("error") or not result.get_editor_property("is_complete"):
                raise RuntimeError("Meteor Niagara diagnostic unfinished "+system.get_name())
            state=json.loads(result.get_value_as_json_string())
            if state.get("bHasErrors",state.get("hasErrors")) or state.get("bIsStale",state.get("isStale")):
                raise RuntimeError("Meteor Niagara compile failed "+str(state))
            report["systems"][system.get_name()]=state
        for name in MATERIAL_NAMES:
            material=unreal.load_asset(FOLDER+"/"+name)
            errors=list(EDIT.recompile_material(material))
            stats=EDIT.get_statistics(material)
            pixels=int(stats.get_editor_property("num_pixel_shader_instructions"))
            report["materials"][name]={"errors":errors,"pixel_instructions":pixels}
            if errors or (require_rendered_shaders and pixels<=0):
                raise RuntimeError("Meteor material requires successful D3D shader compile "+name+str(errors))
            unreal.EditorAssetLibrary.save_loaded_asset(material,only_if_is_dirty=False)
        for bp in (tail_bp,impact_bp,exit_bp):
            _compile(bp)
        for name in (*CLASS_NAMES,*SYSTEM_NAMES,*MATERIAL_NAMES):
            asset=unreal.load_asset(FOLDER+"/"+name)
            unreal.EditorAssetLibrary.set_metadata_tag(asset,"SkyfallMeteorVersion",VERSION)
            unreal.EditorAssetLibrary.save_loaded_asset(asset,only_if_is_dirty=False)
        (ROOT/"Saved/meteor-art.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
        unreal.log("SKYFALL_METEOR_ART_READY "+VERSION)
        return {"mesh":mesh,"meteor_tail_class":tail_bp.generated_class(),
                "meteor_impact_class":impact_bp.generated_class(),
                "meteor_exit_class":exit_bp.generated_class()}
