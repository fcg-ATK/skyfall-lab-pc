"""Build the PC demolition yard using native Unreal assets and Blueprints."""
import json
import math
import random
import sys
from pathlib import Path

import unreal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
ASSET_ROOT = "/Game/Skyfall"
MAP_PATH = ASSET_ROOT + "/Maps/DemolitionYard"
ACTORS = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
ASSETS = unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)
MESHES = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)


def spawn(cls, name, position, rotation=(0, 0, 0)):
    actor = ACTORS.spawn_actor_from_class(cls, unreal.Vector(*position),
                                         unreal.Rotator(pitch=rotation[0], yaw=rotation[1], roll=rotation[2]))
    actor.set_actor_label(name)
    return actor


def block(name, position, dimensions, material, rotation=(0, 0, 0), collision=True):
    actor = spawn(unreal.StaticMeshActor, name, position, rotation)
    component = actor.static_mesh_component
    component.set_static_mesh(unreal.load_asset("/Engine/BasicShapes/Cube"))
    component.set_material(0, material)
    actor.set_actor_scale3d(unreal.Vector(*(v / 100 for v in dimensions)))
    component.set_collision_profile_name("BlockAll" if collision else "NoCollision")
    return actor


def import_meshes(path, folder, preserve_thin_faces=False):
    existing = ASSETS.list_assets(folder, recursive=False, include_folder=False)
    if existing:
        return [obj for obj in map(unreal.load_asset, existing) if isinstance(obj, unreal.StaticMesh)]
    task = unreal.AssetImportTask()
    task.filename = str(ROOT / path)
    task.destination_path = folder
    task.automated = True
    task.replace_existing = True
    task.save = True
    task.factory = unreal.FbxFactory()
    options = unreal.FbxImportUI()
    options.import_mesh = True
    options.import_materials = False
    options.import_textures = False
    options.import_as_skeletal = False
    options.mesh_type_to_import = unreal.FBXImportType.FBXIT_STATIC_MESH
    options.automated_import_should_detect_type = False
    data = options.static_mesh_import_data
    data.combine_meshes = False
    data.convert_scene = True
    data.convert_scene_unit = True
    data.transform_vertex_to_absolute = False
    data.auto_generate_collision = True
    data.generate_lightmap_u_vs = True
    data.remove_degenerates = not preserve_thin_faces
    data.normal_import_method = unreal.FBXNormalImportMethod.FBXNIM_IMPORT_NORMALS_AND_TANGENTS
    task.options = options
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    meshes = [unreal.load_asset(p) for p in task.imported_object_paths]
    return [m for m in meshes if isinstance(m, unreal.StaticMesh)]


def material_for_slot(slot_name, materials):
    name = str(slot_name).lower()
    if any(word in name for word in ("glass", "glazing", "window")):
        if "tone_1" in name or "tone_3" in name:
            return materials["glasstint"]
        return materials["glass"]
    if "rebar" in name or "corten" in name:
        return materials["metal"]
    if "stainless" in name:
        return materials["stainless"]
    if "seal" in name or "epdm" in name:
        return materials["rubber"]
    if any(word in name for word in ("alumin", "frame", "metal", "steel")):
        return materials["aluminum"]
    if "bronze" in name:
        return materials["bronze"]
    if "plaster" in name:
        return materials["plaster"]
    if "linen" in name or "blind" in name:
        return materials["linen"]
    if "luminaire" in name:
        return materials["luminaire"]
    if "upholstery" in name or "fabric" in name:
        return materials["fabric"]
    if "planting" in name:
        return materials["foliage"]
    if any(word in name for word in ("fracture", "cut", "broken", "aggregate", "interior_concrete", "freshconcrete")):
        return materials["fracture"]
    if any(word in name for word in ("wood", "desk", "door")):
        return materials["wood"]
    if "oak" in name:
        return materials["wood"]
    if any(word in name for word in ("asphalt", "road")):
        return materials["asphalt"]
    return materials["concrete"]


def assign_materials(mesh, materials):
    for index, slot in enumerate(mesh.static_materials):
        material = material_for_slot(slot.material_slot_name, materials)
        if slot.material_interface != material:
            mesh.set_material(index, material)


def build_yard(materials):
    block("Asphalt ground", (0, 0, -65), (80000, 80000, 100), materials["asphalt"])
    block("Demolition apron", (0, 0, -6), (4200, 3400, 60), materials["concrete"])
    for x in range(-1900, 2000, 400):
        block("Apron expansion joint", (x, 0, 25), (3, 3400, 1.5), materials["metal"], collision=False)
    for y in range(-1600, 1800, 400):
        block("Apron cross joint", (0, y, 26), (4200, 3, 1.5), materials["metal"], collision=False)
    for side in (-1, 1):
        for y in range(-4500, 5000, 850):
            block("Access road dash", (side * 3200, y, -13), (18, 330, 1), materials["paint"], collision=False)
        block("Concrete curb", (side * 4200, 0, 5), (35, 19000, 35), materials["concrete"])
    for index, x in enumerate(range(-2400, 2800, 450)):
        # Low barriers give the building an intelligible real-world scale.
        block("Jersey barrier", (x, 2650, 50), (360, 70, 120), materials["concrete"])
        for mark in range(3):
            block("Barrier warning stripe", (x - 95 + mark * 95, 2613, 65),
                  (24, 2, 70), materials["paint"], rotation=(0, 0, -22), collision=False)
    # A modest surrounding streetscape provides reflected detail in the glazing.
    surroundings = [(-8000, 6000, 2600, 4200, 3200), (6500, 6500, 3500, 3800, 2600),
                    (-7200, 12000, 4400, 5500, 3500), (1400, 13500, 2300, 4800, 3400),
                    (8500, 13500, 5100, 4200, 3400)]
    for i, (x, y, height, width, depth) in enumerate(surroundings):
        block(f"Neighbour office {i}", (x, y, height / 2), (width, depth, height), materials["plaster"])
        for z in range(260, height - 100, 340):
            block("Neighbour glazing ribbon", (x, y - depth / 2 - 2, z),
                  (width * 0.88, 5, 190), materials["aluminum"], collision=False)
            block("Neighbour side glazing", (x + width / 2 + 3, y, z),
                  (5, depth * 0.88, 190), materials["aluminum"], collision=False)
            block("Neighbour floor ledge", (x, y - depth / 2 - 8, z + 110),
                  (width + 12, 22, 20), materials["concrete"], collision=False)
        for dx in range(-int(width / 2) + 200, int(width / 2), 260):
            block("Neighbour vertical mullion", (x + dx, y - depth / 2 - 6, height / 2),
                  (10, 9, height), materials["metal"], collision=False)
    for side in (-1, 1):
        for y in (1500, 5500, 9500):
            post = spawn(unreal.StaticMeshActor, "Street light pole", (side * 4300, y, 400))
            post.static_mesh_component.set_static_mesh(unreal.load_asset("/Engine/BasicShapes/Cylinder"))
            post.static_mesh_component.set_material(0, materials["metal"])
            post.set_actor_scale3d(unreal.Vector(0.12, 0.12, 8))
            block("Street light arm", (side * 4200, y, 795), (220, 18, 18), materials["metal"])
            block("Street light housing", (side * 4060, y, 785), (100, 45, 22), materials["metal"])


def build_lighting():
    sun = spawn(unreal.DirectionalLight, "Late afternoon sun", (0, 0, 8000), (-32, 135, 0))
    light = sun.light_component
    light.set_mobility(unreal.ComponentMobility.MOVABLE)
    light.set_intensity(45000)
    light.set_temperature(5350)
    light.set_editor_property("use_temperature", True)
    light.set_editor_property("light_source_angle", 2.5)
    light.set_editor_property("atmosphere_sun_light", True)
    spawn(unreal.SkyAtmosphere, "Atmospheric sky", (0, 0, 0))
    sky = spawn(unreal.SkyLight, "Sky bounce", (0, 0, 5000))
    sky.light_component.set_mobility(unreal.ComponentMobility.MOVABLE)
    cube_path = ASSET_ROOT + "/Textures/T_Daylight_Environment"
    cube = unreal.load_asset(cube_path)
    if cube is None:
        task = unreal.AssetImportTask()
        task.filename = str(ROOT / "SourceAssets/Textures/daylight_environment.hdr")
        task.destination_path = ASSET_ROOT + "/Textures"
        task.destination_name = "T_Daylight_Environment"
        task.automated = True
        task.save = True
        task.factory = unreal.TextureFactory()
        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
        cube = unreal.load_asset(cube_path)
    if not isinstance(cube, unreal.TextureCube):
        raise RuntimeError("Daylight environment did not import as a cubemap")
    sky.light_component.set_editor_property("real_time_capture", False)
    sky.light_component.set_editor_property("source_type", unreal.SkyLightSourceType.SLS_SPECIFIED_CUBEMAP)
    sky.light_component.set_cubemap(cube)
    sky.light_component.set_intensity(1100.0)
    fog = spawn(unreal.ExponentialHeightFog, "Distant atmospheric depth", (0, 0, -1000))
    fog.component.set_editor_property("fog_density", 0.008)
    fog.component.set_editor_property("fog_height_falloff", 0.25)
    fog.component.set_editor_property("start_distance", 12000)
    post = spawn(unreal.PostProcessVolume, "Filmic exposure", (0, 0, 0))
    post.set_editor_property("unbound", True)
    settings = post.settings
    for prop, value in {
        "override_auto_exposure_min_brightness": True,
        "override_auto_exposure_max_brightness": True,
        "auto_exposure_min_brightness": 13.1,
        "auto_exposure_max_brightness": 13.1,
        "override_auto_exposure_method": True,
        "auto_exposure_method": unreal.AutoExposureMethod.AEM_HISTOGRAM,
        "override_bloom_intensity": True,
        "bloom_intensity": 0.12,
        "override_vignette_intensity": True,
        "vignette_intensity": 0.12,
        "override_motion_blur_amount": True,
        "motion_blur_amount": 0.0,
    }.items():
        settings.set_editor_property(prop, value)
    post.settings = settings
    return sun


def build_camera():
    from free_camera import build_free_camera, install_free_camera_in_level
    camera = install_free_camera_in_level(build_free_camera())
    unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).set_level_viewport_camera_info(
        camera.get_actor_location(), camera.get_actor_rotation())
    return camera


def build_debris(source, materials):
    meshes = import_meshes(source["debris"]["path"], ASSET_ROOT + "/Meshes/Debris")
    for mesh in meshes:
        assign_materials(mesh, materials)
    rng = random.Random(2718)
    for index in range(18):
        mesh = meshes[index % len(meshes)]
        actor = spawn(unreal.StaticMeshActor, "Reinforced demolition rubble",
                      (2450 + rng.uniform(-190, 190), 1400 + rng.uniform(-330, 330),
                       35 + (index // 8) * 32),
                      (rng.uniform(-15, 15), rng.uniform(-180, 180), rng.uniform(-15, 15)))
        actor.static_mesh_component.set_static_mesh(mesh)
        actor.set_actor_scale3d(unreal.Vector(100, 100, 100))
        actor.static_mesh_component.set_collision_profile_name("NoCollision")
    return meshes


def main():
    from materials import build_materials, MATERIAL_NAMES
    from combat_v2 import build_controller
    from import_combat_assets import load_combat_assets
    from effects_v2 import build_effects
    from aim_v2 import install_aim, bind_placed_guides
    from hud_v2 import build_hud

    report_path = ROOT / "SourceAssets" / "assets.json"
    source = json.loads(report_path.read_text(encoding="utf-8"))
    materials = {key: unreal.load_asset(ASSET_ROOT + "/Materials/" + name)
                 for key, name in MATERIAL_NAMES.items()}
    if any(material is None for material in materials.values()):
        materials = build_materials()
    levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if ASSETS.does_asset_exist(MAP_PATH):
        if not levels.load_level(MAP_PATH):
            raise RuntimeError("Cannot load demolition map for rebuilding")
        old_actors = [actor for actor in ACTORS.get_all_level_actors()
                      if not isinstance(actor, (unreal.WorldSettings, unreal.LevelScriptActor))]
        ACTORS.destroy_actors(old_actors)
    elif not levels.new_level(MAP_PATH):
        raise RuntimeError("Cannot create demolition map")
    build_yard(materials)
    build_lighting()
    camera = build_camera()
    build_debris(source, materials)
    # The exporter records local mesh pivots and the original building transforms.
    building_path = source["building"]["path"]
    meshes = import_meshes(building_path, ASSET_ROOT + "/Meshes/Building")
    definitions = []
    metadata = source["building"]["mesh_objects"]
    records = {r["name"]: r for r in metadata}
    for mesh in meshes:
        name = mesh.get_name()
        if "static_environment" in name:
            continue
        assign_materials(mesh, materials)
        record = next((r for key, r in records.items() if name == key or name.endswith("_" + key)), None)
        if record is None:
            raise RuntimeError("No transform metadata for " + name)
        position = record.get("ue_location_cm", record.get("location_cm"))
        if position is None:
            x, y, z = record["location_m"]
            position = (x * 100, -y * 100, z * 100)
        floor = min(5, max(0, int((position[2] - 20) / 330)))
        kind = "glass" if "window" in name.lower() else "structure"
        definitions.append({"name": name, "mesh": mesh, "location": position,
                            "scale": (100, 100, 100), "floor": floor, "kind": kind,
                            "mass_kg": 22 if kind == "glass" else 1800})
    weapon_meshes, fracture_defs = load_combat_assets(materials)
    effects = build_effects(materials)
    from meteor_art import build_meteor_art
    meteor_art = build_meteor_art()
    weapon_meshes["meteor"] = meteor_art["mesh"]
    effects.update({key: value for key, value in meteor_art.items() if key.endswith("_class")})
    runtime = build_controller(definitions, materials, weapon_meshes, fracture_defs, effects)
    guide = install_aim(runtime["controller_blueprint"], weapon_meshes["aim_arrow"], materials)
    bind_placed_guides(runtime["controller_blueprint"], weapon_meshes["aim_arrow"])
    hud = build_hud(runtime["controller_blueprint"])
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    world.get_world_settings().set_editor_property("default_game_mode", hud["game_mode_class"])
    if not unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level():
        raise RuntimeError("Failed to save demolition map")
    ASSETS.save_directory(ASSET_ROOT, only_if_is_dirty=True, recursive=True)
    summary = {"engine": unreal.SystemLibrary.get_engine_version(),
               "map": MAP_PATH, "building_meshes": len(meshes),
               "runtime_pieces": len(runtime["pieces"]),
               "secondary_pieces": sum(len(parts["meshes"]) for parts in fracture_defs.values()),
               "weapons": ["rocket", "meteor", "ball"], "aim_dots": guide["dot_count"],
               "camera": camera.get_actor_location().to_tuple()}
    (ROOT / "Saved" / "scene-build.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    unreal.log("SKYFALL_SCENE_BUILD_OK " + json.dumps(summary))


if __name__ == "__main__":
    main()
