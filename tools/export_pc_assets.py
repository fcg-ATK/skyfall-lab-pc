"""Export the existing building and reusable rubble meshes for Unreal Engine.

Run with Blender in background mode and --python-exit-code 1. The source GLB and
original .blend files are read-only inputs. FBX geometry remains in its original
Blender Z-up coordinates. FBX unit metadata describes one Blender unit as one
metre, so Unreal's Convert Scene Unit option imports the dimensions in centimetres.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
from datetime import datetime, timezone
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector


PROJECT = Path(__file__).resolve().parents[1]
WORK = PROJECT / "SourceAssets"
SOURCE_BUILDING = PROJECT / "SourceAssets/BuildingSource/building-v4.glb"
SOURCE_MANIFEST = PROJECT / "SourceAssets/BuildingSource/building-manifest-v4.json"
OUTPUT = PROJECT / "SourceAssets"
TEXTURES = OUTPUT / "Textures"
BUILDING_FBX = OUTPUT / "building_v4_pc.fbx"
DEBRIS_FBX = OUTPUT / "concrete_debris_pc.fbx"


def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for material in list(bpy.data.materials):
        bpy.data.materials.remove(material)
    for image in list(bpy.data.images):
        if image.type == "IMAGE":
            bpy.data.images.remove(image)
    bpy.context.scene.unit_settings.system = "METRIC"
    bpy.context.scene.unit_settings.scale_length = 1.0


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def world_bounds(obj):
    corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    minimum = [min(point[axis] for point in corners) for axis in range(3)]
    maximum = [max(point[axis] for point in corners) for axis in range(3)]
    return {"min": minimum, "max": maximum}


def mesh_record(obj):
    bounds = world_bounds(obj)
    dimensions = [bounds["max"][axis] - bounds["min"][axis] for axis in range(3)]
    return {
        "name": obj.name,
        "parent": obj.parent.name if obj.parent else None,
        "location_m": list(obj.location),
        "ue_location_cm": [obj.location.x * 100, -obj.location.y * 100, obj.location.z * 100],
        "rotation_degrees": [math.degrees(value) for value in obj.rotation_euler],
        "scale": list(obj.scale),
        "dimensions_m": dimensions,
        "dimensions_ue_cm": [dimension * 100 for dimension in dimensions],
        "world_bounds_m": bounds,
        "materials": [material.name for material in obj.data.materials if material],
        "vertices": len(obj.data.vertices),
        "triangles": sum(len(polygon.vertices) - 2 for polygon in obj.data.polygons),
        "orientation_landmarks_local_m": [
            list(obj.data.vertices[index].co)
            for index in sorted({0, len(obj.data.vertices) // 7, len(obj.data.vertices) // 3,
                                 len(obj.data.vertices) // 2, len(obj.data.vertices) - 1})
        ],
    }


def object_records():
    bpy.context.view_layer.update()
    return [mesh_record(obj) for obj in bpy.context.scene.objects if obj.type == "MESH"]


def material_records():
    records = []
    for material in bpy.data.materials:
        images = []
        if material.use_nodes:
            for node in material.node_tree.nodes:
                if node.type == "TEX_IMAGE" and node.image:
                    images.append(Path(bpy.path.abspath(node.image.filepath)).resolve().relative_to(PROJECT).as_posix())
        records.append({
            "source_name": material.name,
            "fbx_name": material.name,
            "texture_paths": sorted(set(images)),
        })
    return records


def extract_images():
    records = []
    for image in list(bpy.data.images):
        if image.type != "IMAGE":
            continue
        original_name = image.name
        colorspace = image.colorspace_settings.name
        if image.packed_file:
            payload = bytes(image.packed_file.data)
        else:
            source = Path(bpy.path.abspath(image.filepath))
            if not source.is_file():
                raise FileNotFoundError(f"Missing image source for {image.name}: {source}")
            payload = source.read_bytes()
        if payload.startswith(b"\x89PNG\r\n\x1a\n"):
            suffix = ".png"
        elif payload.startswith(b"\xff\xd8\xff"):
            suffix = ".jpg"
        else:
            raise ValueError(f"Unsupported embedded image format: {original_name}")
        stem = re.sub(r"[^\w.\-]+", "_", Path(original_name).stem)
        destination = TEXTURES / (stem + suffix)
        destination.write_bytes(payload)
        # Use fresh file-backed images. Editing an imported GLB packed image's
        # path leaves Blender's packed-file lookup pointing to an empty filename.
        replacement = bpy.data.images.load(str(destination), check_existing=False)
        replacement.colorspace_settings.name = colorspace
        for material in bpy.data.materials:
            if material.node_tree:
                for node in material.node_tree.nodes:
                    if node.type == "TEX_IMAGE" and node.image == image:
                        node.image = replacement
        bpy.data.images.remove(image)
        previous_png = TEXTURES / (stem + ".png")
        if suffix != ".png" and previous_png.is_file() and previous_png.read_bytes() == payload:
            previous_png.unlink()
        records.append({
            "image_name": original_name,
            "path": destination.resolve().relative_to(PROJECT).as_posix(),
            "width": replacement.size[0],
            "height": replacement.size[1],
            "bytes": destination.stat().st_size,
            "sha256": digest(destination),
            "colorspace": colorspace,
        })
    return records


def prepare_building_meshes():
    """Bake each GLB root into a centred, individually importable static mesh."""
    source_nodes = []
    roots = [obj for obj in bpy.context.scene.objects if obj.type == "EMPTY" and not obj.parent]
    for root in roots:
        meshes = [obj for obj in root.children_recursive if obj.type == "MESH"]
        if not meshes:
            continue
        name = root.name
        child_bounds = [world_bounds(obj) for obj in meshes]
        original_bounds = {
            "min": [min(bounds["min"][axis] for bounds in child_bounds) for axis in range(3)],
            "max": [max(bounds["max"][axis] for bounds in child_bounds) for axis in range(3)],
        }
        source_nodes.append({
            "root_name": name,
            "root_location_m": list(root.matrix_world.translation),
            "child_mesh_names": [obj.name for obj in meshes],
        })
        bpy.ops.object.select_all(action="DESELECT")
        for obj in meshes:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = meshes[0]
        if len(meshes) > 1:
            bpy.ops.object.join()
        mesh = bpy.context.object
        source_world = mesh.matrix_world.copy()
        bounds = world_bounds(mesh)
        center = Vector([(bounds["min"][axis] + bounds["max"][axis]) * 0.5 for axis in range(3)])
        mesh.data.transform(source_world)
        mesh.data.transform(Matrix.Translation(-center))
        mesh.parent = None
        mesh.matrix_world = Matrix.Identity(4)
        mesh.location = center
        root.name = "source_root_" + name
        mesh.name = name
        mesh.data.name = name + "_mesh"
        mesh["source_root"] = name
        mesh["pivot_mode"] = "geometry_bounds_center"
        mesh["source_children"] = " | ".join(source_nodes[-1]["child_mesh_names"])
        bpy.context.view_layer.update()
        centered_bounds = world_bounds(mesh)
        error = max(abs(original_bounds[side][axis] - centered_bounds[side][axis])
                    for side in ("min", "max") for axis in range(3))
        if error > 0.0001:
            raise RuntimeError(f"World geometry moved while centring {name}: {error} m")
        source_nodes[-1]["centering_bounds_error_m"] = error
    for obj in list(bpy.context.scene.objects):
        if obj.type == "EMPTY":
            bpy.data.objects.remove(obj, do_unlink=True)
    bpy.context.view_layer.update()
    return source_nodes


def export_fbx(path):
    bpy.ops.object.select_all(action="DESELECT")
    objects = [obj for obj in bpy.context.scene.objects if obj.type in {"MESH", "EMPTY"}]
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    result = bpy.ops.export_scene.fbx(
        filepath=str(path), use_selection=True, object_types={"MESH", "EMPTY"},
        global_scale=1.0, apply_unit_scale=True, apply_scale_options="FBX_SCALE_UNITS",
        axis_forward="-Y", axis_up="Z", use_space_transform=True,
        bake_space_transform=False, use_mesh_modifiers=True,
        mesh_smooth_type="FACE", use_tspace=True, use_custom_props=True,
        add_leaf_bones=False, bake_anim=False, path_mode="RELATIVE",
        embed_textures=False,
    )
    if result != {"FINISHED"} or not path.is_file() or path.stat().st_size < 1024:
        raise RuntimeError(f"FBX export failed: {path}")


def build_collision_meshes(manifest):
    """Use small source-occupied boxes instead of a convex hull over empty rooms."""
    records = []
    for piece in manifest["pieces"]:
        body = bpy.data.objects[piece["name"]]
        boxes = []
        for collider in piece["colliders"]:
            position, offset, half = piece["position"], collider["offset"], collider["halfExtents"]
            center = Vector((position["x"] + offset["x"],
                             -position["z"] - offset["z"],
                             position["y"] + offset["y"]))
            boxes.append((center, Vector((half["x"], half["z"], half["y"]))))
        for index, (center, half) in enumerate(boxes):
            name = f"UCX_{body.name}_{index:02d}"
            bpy.ops.mesh.primitive_cube_add(size=2, location=body.location)
            collider = bpy.context.object
            collider.name = name
            collider.data.name = name + "_mesh"
            for vertex in collider.data.vertices:
                vertex.co = Vector([vertex.co[axis] * half[axis] for axis in range(3)]) + center - body.location
            collider.data.update()
            bpy.context.view_layer.update()
            collider["collision_for"] = body.name
            record = mesh_record(collider)
            record["collision_for"] = body.name
            records.append(record)
    bpy.context.view_layer.update()
    return records


def verify_roundtrip(path, expected_meshes, expected_empties):
    clear_scene()
    result = bpy.ops.import_scene.fbx(filepath=str(path), use_custom_normals=True)
    if result != {"FINISHED"}:
        raise RuntimeError(f"FBX import validation failed: {path}")
    actual = {record["name"]: record for record in object_records()}
    expected = {record["name"]: record for record in expected_meshes}
    if set(actual) != set(expected):
        raise RuntimeError(f"FBX mesh names changed in {path.name}")
    actual_empties = {obj.name for obj in bpy.context.scene.objects if obj.type == "EMPTY"}
    if actual_empties != set(expected_empties):
        raise RuntimeError(f"FBX empty hierarchy names changed in {path.name}")
    max_bounds_error = 0.0
    for name, original in expected.items():
        imported = actual[name]
        if imported["parent"] != original["parent"]:
            raise RuntimeError(f"FBX parent changed for {name}")
        for side in ("min", "max"):
            for old_value, new_value in zip(original["world_bounds_m"][side], imported["world_bounds_m"][side]):
                max_bounds_error = max(max_bounds_error, abs(old_value - new_value))
    if max_bounds_error > 0.002:
        raise RuntimeError(f"FBX metre scale or transform changed by {max_bounds_error} m")
    expected_materials = {name for record in expected_meshes for name in record["materials"]}
    actual_materials = {name for record in actual.values() for name in record["materials"]}
    if expected_materials != actual_materials:
        raise RuntimeError(f"FBX material names changed in {path.name}")
    return {
        "passed": True, "mesh_count": len(actual), "empty_count": len(actual_empties),
        "max_world_bounds_error_m": max_bounds_error,
        "material_names_preserved": True, "parent_names_preserved": True,
    }


def fbx_coordinate_metadata(path):
    from io_scene_fbx import parse_fbx

    data, version = parse_fbx.parse(str(path))
    settings = next(element for element in data.elems if element.id == b"GlobalSettings")
    properties = next(element for element in settings.elems if element.id == b"Properties70")
    keys = {"UpAxis", "UpAxisSign", "FrontAxis", "FrontAxisSign", "CoordAxis", "CoordAxisSign", "UnitScaleFactor"}
    values = {"fbx_version": version}
    for element in properties.elems:
        name = element.props[0].decode("utf-8")
        if name in keys:
            values[name] = element.props[-1]
    return values


def simple_material(name, color, roughness, metallic=0.0):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    shader = next((node for node in material.node_tree.nodes
                   if node.type == "BSDF_PRINCIPLED"), None)
    if shader is None:
        shader = material.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
        output = next((node for node in material.node_tree.nodes
                       if node.type == "OUTPUT_MATERIAL"), None)
        if output is None:
            output = material.node_tree.nodes.new("ShaderNodeOutputMaterial")
        material.node_tree.links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    shader.inputs["Base Color"].default_value = (*color, 1)
    shader.inputs["Roughness"].default_value = roughness
    shader.inputs["Metallic"].default_value = metallic
    material.diffuse_color = (*color, 1)
    return material


def rubble_materials():
    return [
        simple_material("PC_FreshConcrete", (0.43, 0.41, 0.37), 0.96),
        simple_material("PC_ConcreteAggregateLight", (0.55, 0.52, 0.46), 0.97),
        simple_material("PC_ConcreteAggregateDark", (0.28, 0.27, 0.25), 0.98),
        simple_material("PC_RebarOxidizedSteel", (0.22, 0.10, 0.045), 0.78, 0.68),
        simple_material("PC_RebarExposedSteel", (0.26, 0.27, 0.25), 0.57, 0.85),
    ]


def irregular_concrete(name, dimensions, seed, materials):
    rng = random.Random(seed)
    editable = bmesh.new()
    bmesh.ops.create_icosphere(editable, subdivisions=3, radius=1.0)
    for vertex in editable.verts:
        direction = vertex.co.normalized()
        # A superellipsoid gives fragments a chipped block/slab silhouette.
        block_factor = max(abs(component) for component in direction) ** -0.65
        radius = block_factor * rng.uniform(0.86, 1.07)
        vertex.co = Vector([
            direction[axis] * radius * dimensions[axis] * 0.5 for axis in range(3)
        ])
        vertex.co.z += 0.07 * dimensions[2] * math.sin(vertex.co.x * 19.0 + vertex.co.y * 13.0)
    editable.normal_update()
    mesh = bpy.data.meshes.new(name + "_mesh")
    editable.to_mesh(mesh)
    editable.free()
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    for material in materials[:3]:
        mesh.materials.append(material)
    for polygon in mesh.polygons:
        chance = rng.random()
        polygon.material_index = 1 if chance < 0.055 else 2 if chance < 0.11 else 0
        polygon.use_smooth = False
    return obj


def curve_frame(points, index):
    previous = points[max(0, index - 1)]
    following = points[min(len(points) - 1, index + 1)]
    tangent = (following - previous).normalized()
    reference = Vector((0, 0, 1))
    if abs(tangent.dot(reference)) > 0.92:
        reference = Vector((0, 1, 0))
    normal = tangent.cross(reference).normalized()
    bitangent = tangent.cross(normal).normalized()
    return normal, bitangent


def round_bar(name, points, radius, material, rib_material):
    vertices, faces = [], []
    sides = 12
    for index, center in enumerate(points):
        normal, bitangent = curve_frame(points, index)
        for side in range(sides):
            angle = side * math.tau / sides
            vertices.append(center + radius * (normal * math.cos(angle) + bitangent * math.sin(angle)))
    faces.append(tuple(reversed(range(sides))))
    for ring in range(len(points) - 1):
        for side in range(sides):
            following = (side + 1) % sides
            faces.append((ring * sides + side, ring * sides + following,
                          (ring + 1) * sides + following, (ring + 1) * sides + side))
    faces.append(tuple((len(points) - 1) * sides + side for side in range(sides)))
    # Raised ribs have a round cross-section and follow each curved bar's frame.
    rib_start = len(faces)
    for index in range(3, len(points) - 1, 2):
        center = points[index]
        normal, bitangent = curve_frame(points, index)
        tangent = normal.cross(bitangent).normalized()
        start = len(vertices)
        for side in range(sides):
            angle = side * math.tau / sides
            outward = normal * math.cos(angle) + bitangent * math.sin(angle)
            for cross in range(4):
                cross_angle = cross * math.tau / 4
                vertices.append(center + outward * (radius * 1.08 + radius * 0.16 * math.cos(cross_angle))
                                + tangent * radius * 0.16 * math.sin(cross_angle))
        for side in range(sides):
            for cross in range(4):
                faces.append((start + side * 4 + cross,
                              start + ((side + 1) % sides) * 4 + cross,
                              start + ((side + 1) % sides) * 4 + ((cross + 1) % 4),
                              start + side * 4 + ((cross + 1) % 4)))
    mesh = bpy.data.meshes.new(name + "_mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    mesh.materials.append(material)
    mesh.materials.append(rib_material)
    for index, polygon in enumerate(mesh.polygons):
        polygon.use_smooth = True
        polygon.material_index = int(index >= rib_start)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def reinforcement_points(dimensions, index, bar_count, seed):
    rng = random.Random(seed)
    width, depth, height = dimensions
    lateral = (index - (bar_count - 1) * 0.5) * depth * 0.23
    bend = rng.uniform(-0.12, 0.16)
    lift = rng.uniform(0.08, 0.22)
    extension = rng.uniform(0.24, 0.42)
    points = []
    for step in range(25):
        t = step / 24
        exposed = max(0.0, (t - 0.45) / 0.55)
        points.append(Vector((
            -width * 0.26 + t * (width * 0.79 + extension),
            lateral + bend * exposed * exposed,
            height * 0.04 + lift * exposed * exposed - 0.04 * math.sin(math.pi * t),
        )))
    return points


def join_variant(parts, name, category, bar_count):
    bpy.ops.object.select_all(action="DESELECT")
    for obj in parts:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    if len(parts) > 1:
        bpy.ops.object.join()
    obj = bpy.context.object
    obj.name = name
    bpy.context.view_layer.update()
    bounds = world_bounds(obj)
    center = Vector([(bounds["min"][axis] + bounds["max"][axis]) * 0.5 for axis in range(3)])
    obj.data.transform(Matrix.Translation(-center))
    obj["category"] = category
    obj["rebar_count"] = bar_count
    obj["dimensions_unit"] = "metres"
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.quads_convert_to_tris(quad_method="BEAUTY", ngon_method="BEAUTY")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.02)
    bpy.ops.object.mode_set(mode="OBJECT")
    return obj


def build_debris():
    clear_scene()
    materials = rubble_materials()
    configurations = [
        ("PC_Debris_01_StoneSmall", (0.09, 0.07, 0.055), 0),
        ("PC_Debris_02_StoneMedium", (0.19, 0.13, 0.12), 0),
        ("PC_Debris_03_StoneFlat", (0.31, 0.22, 0.095), 0),
        ("PC_Debris_04_RebarSlab", (0.68, 0.47, 0.19), 3),
        ("PC_Debris_05_RebarBlock", (0.47, 0.38, 0.34), 2),
        ("PC_Debris_06_RebarLongSlab", (0.94, 0.39, 0.16), 3),
        ("PC_Debris_07_RebarColumn", (0.37, 0.33, 0.63), 2),
        ("PC_Debris_08_RebarCorner", (0.72, 0.61, 0.28), 4),
    ]
    variants = []
    for index, (name, dimensions, bar_count) in enumerate(configurations):
        concrete = irregular_concrete(name, dimensions, 7200 + index, materials)
        parts = [concrete]
        for bar in range(bar_count):
            points = reinforcement_points(dimensions, bar, bar_count, 200 + index * 13 + bar)
            parts.append(round_bar(name + f"_bar_{bar + 1}", points,
                                   0.008 + (index % 3) * 0.002,
                                   materials[3], materials[4]))
        obj = join_variant(parts, name, "reinforced_concrete" if bar_count else "small_stone", bar_count)
        variants.append({"name": obj.name, "category": obj["category"], "rebar_count": bar_count})
    return variants


def dimension_range(records):
    return {
        "min_xyz_m": [min(record["dimensions_m"][axis] for record in records) for axis in range(3)],
        "max_xyz_m": [max(record["dimensions_m"][axis] for record in records) for axis in range(3)],
    }


def render_debris_preview():
    meshes = sorted((obj for obj in bpy.context.scene.objects if obj.type == "MESH"), key=lambda obj: obj.name)
    for index, obj in enumerate(meshes):
        obj.location = ((index % 4 - 1.5) * 1.35, (index // 4 - 0.5) * 1.4, 0.4)
    bpy.ops.mesh.primitive_plane_add(size=30, location=(0, 0, 0))
    ground = bpy.context.object
    ground.data.materials.append(simple_material("PreviewGround", (0.12, 0.13, 0.14), 0.92))
    for name, location, energy, size in [
        ("PreviewKey", (-3, -4, 6), 1200, 5),
        ("PreviewRim", (3, 3, 4), 1000, 4),
    ]:
        light = bpy.data.lights.new(name, "AREA")
        light.energy, light.shape, light.size = energy, "DISK", size
        obj = bpy.data.objects.new(name, light)
        bpy.context.collection.objects.link(obj)
        obj.location = location
        obj.rotation_euler = (Vector((0, 0, 0.3)) - obj.location).to_track_quat("-Z", "Y").to_euler()
    camera = bpy.data.objects.new("PreviewCamera", bpy.data.cameras.new("PreviewCamera"))
    bpy.context.collection.objects.link(camera)
    camera.location = (3.8, -5.8, 5.2)
    camera.rotation_euler = (Vector((0, 0, 0.25)) - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera.data.type, camera.data.ortho_scale = "ORTHO", 6.5
    scene = bpy.context.scene
    scene.camera = camera
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 32
    scene.render.resolution_x, scene.render.resolution_y = 1400, 900
    scene.render.resolution_percentage = 100
    scene.world.color = (0.18, 0.18, 0.18)
    scene.render.image_settings.file_format = "PNG"
    path = OUTPUT / "debris_preview.png"
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    return path.resolve().relative_to(PROJECT).as_posix()


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    TEXTURES.mkdir(parents=True, exist_ok=True)
    source = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
    clear_scene()
    result = bpy.ops.import_scene.gltf(filepath=str(SOURCE_BUILDING))
    if result != {"FINISHED"}:
        raise RuntimeError("Building GLB import failed")
    expected_roots = {record["name"] for record in source["pieces"] + source["windows"]}
    actual_roots = {obj.name for obj in bpy.context.scene.objects if obj.type == "EMPTY"}
    if not expected_roots.issubset(actual_roots):
        raise RuntimeError("Building fragment/window root names were lost during import")
    building_textures = extract_images()
    source_nodes = prepare_building_meshes()
    building_meshes = object_records()
    building_empties = [obj.name for obj in bpy.context.scene.objects if obj.type == "EMPTY"]
    building_materials = material_records()
    collision_meshes = build_collision_meshes(source)
    export_fbx(BUILDING_FBX)
    building_verification = verify_roundtrip(BUILDING_FBX, building_meshes + collision_meshes, building_empties)
    building_verification["render_mesh_count"] = len(building_meshes)
    building_verification["collision_mesh_count"] = len(collision_meshes)
    print("BUILDING_VERIFIED", json.dumps(building_verification), flush=True)

    variants = build_debris()
    debris_meshes = object_records()
    debris_materials = material_records()
    export_fbx(DEBRIS_FBX)
    debris_verification = verify_roundtrip(DEBRIS_FBX, debris_meshes, [])
    print("DEBRIS_VERIFIED", json.dumps(debris_verification), flush=True)
    debris_preview = render_debris_preview()

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "blender_version": bpy.app.version_string,
        "generator": Path(__file__).resolve().relative_to(PROJECT).as_posix(),
        "units": {
            "blender_unit_m": 1.0, "fbx_centimetres_per_unit": 100.0,
            "fbx_axis_forward": "-Y", "fbx_axis_up": "Z",
            "unreal_import": {"convert_scene": True, "convert_scene_unit": True, "import_uniform_scale": 1.0},
            "unreal_basis": "location_cm = [Blender_x * 100, -Blender_y * 100, Blender_z * 100]; Force Front XAxis must be false",
            "static_mesh_import": {"combine_meshes": False, "transform_vertex_to_absolute": False, "bake_pivot_in_vertex": False},
            "building_fbx_global_settings": fbx_coordinate_metadata(BUILDING_FBX),
        },
        "building": {
            "source_glb": SOURCE_BUILDING.resolve().relative_to(PROJECT).as_posix(),
            "source_sha256": digest(SOURCE_BUILDING),
            "source_manifest": SOURCE_MANIFEST.resolve().relative_to(PROJECT).as_posix(),
            "path": BUILDING_FBX.resolve().relative_to(PROJECT).as_posix(), "bytes": BUILDING_FBX.stat().st_size,
            "sha256": digest(BUILDING_FBX),
            "piece_roots": sorted(record["name"] for record in source["pieces"]),
            "window_roots": sorted(record["name"] for record in source["windows"]),
            "empty_objects": building_empties, "mesh_objects": building_meshes,
            "collision_mesh_objects": collision_meshes,
            "collision_policy": "All source occupied boxes from the v4 manifest are preserved individually as UCX meshes, with no truncation or subdivision",
            "source_hierarchy": source_nodes,
            "pivot_mode": "geometry_bounds_center; original world geometry is unchanged",
            "dimension_range": dimension_range(building_meshes),
            "materials": building_materials, "textures": building_textures,
            "verification": building_verification,
        },
        "debris": {
            "path": DEBRIS_FBX.resolve().relative_to(PROJECT).as_posix(), "bytes": DEBRIS_FBX.stat().st_size,
            "sha256": digest(DEBRIS_FBX), "variants": variants,
            "mesh_objects": debris_meshes, "dimension_range": dimension_range(debris_meshes),
            "materials": debris_materials, "verification": debris_verification,
            "preview_path": debris_preview,
        },
        "material_notes": [
            "Original material names are preserved. FBX carries base textures and material slots; reconstruct glass transmission/IOR and roughness in Unreal materials.",
            "Fresh broken concrete in the GLB has constant base color. The browser-only procedural grain shader must be recreated in Unreal.",
            "Rubble uses flat chipped concrete facets, sparse aggregate colors, and swept circular rebar with raised ribs. Every variant's geometry bounds are centred at (0,0,0) for individual mesh import.",
        ],
    }
    manifest_path = OUTPUT / "assets.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PC_ASSETS_READY", str(manifest_path.resolve()), flush=True)


if __name__ == "__main__":
    main()
