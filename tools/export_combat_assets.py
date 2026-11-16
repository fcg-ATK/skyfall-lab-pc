"""Generate real secondary building fragments and forward-X combat meshes.

Run in Blender background mode. Every fragment mesh is centred at zero and its
placement relative to the original centred parent is recorded in metres.
The original building FBX and assets.json are read-only inputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "SourceAssets"
OUTPUT = SOURCE / "CombatV2"
FRACTURE_FBX = OUTPUT / "fracture_v2.fbx"
WEAPONS_FBX = OUTPUT / "weapons_v2.fbx"
CUT_QA = {"closed_component_cuts": 0, "closed_component_outputs_with_boundary": 0,
          "max_closed_component_volume_error_m3": 0.0, "new_cap_faces": 0,
          "ngon_zero_normal_caps": 0, "zero_area_cap_faces": 0,
          "max_exported_cap_triangle_normal_deviation": 0.0,
          "removed_zero_area_cap_triangles": 0, "cap_normal_diagnostics": []}


def clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    for item in list(bpy.data.materials):
        if item.users == 0:
            bpy.data.materials.remove(item)
    bpy.context.scene.unit_settings.system = "METRIC"
    bpy.context.scene.unit_settings.scale_length = 1.0


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bounds(parts):
    vertices = [v.co for part in parts for v in part.verts]
    return (Vector([min(v[axis] for v in vertices) for axis in range(3)]),
            Vector([max(v[axis] for v in vertices) for axis in range(3)]))


def mesh_bounds(mesh):
    vertices = [v.co for v in mesh.vertices]
    return ([min(v[axis] for v in vertices) for axis in range(3)],
            [max(v[axis] for v in vertices) for axis in range(3)])


def mesh_statistics(mesh):
    low, high = mesh_bounds(mesh)
    mesh.calc_loop_triangles()
    return {
        "name": mesh.name.removesuffix("_mesh"),
        "bounds_local_m": {"min": low, "max": high},
        "dimensions_m": [high[axis] - low[axis] for axis in range(3)],
        "vertices": len(mesh.vertices),
        "triangles": len(mesh.loop_triangles),
        "materials": [material.name for material in mesh.materials],
    }


def connected_parts(obj):
    editable = bmesh.new()
    editable.from_mesh(obj.data)
    # Imported flat-shaded primitives duplicate every face vertex. Weld these
    # seams before identifying solids and capping cut faces.
    bmesh.ops.remove_doubles(editable, verts=list(editable.verts), dist=0.000002)
    bmesh.ops.dissolve_degenerate(editable, edges=list(editable.edges), dist=0.0000001)
    tiny = [face for face in editable.faces if face.calc_area() < 1e-11]
    if tiny:
        bmesh.ops.delete(editable, geom=tiny, context="FACES")
    isolated = [vertex for vertex in editable.verts if not vertex.link_faces]
    if isolated:
        bmesh.ops.delete(editable, geom=isolated, context="VERTS")
    # Keep the source winding. Adjacent construction solids can share edges
    # after welding, and global normal recalculation on such a non-manifold
    # junction can invert an otherwise correctly oriented concrete slab.
    editable.normal_update()
    role_layer = editable.faces.layers.int.new("cv2_new_cap")
    for axis in range(3):
        editable.faces.layers.float.new("cv2_cap_normal_" + str(axis))
    for face in editable.faces:
        face[role_layer] = 0
    editable.verts.ensure_lookup_table()
    unseen = set(editable.verts)
    components = []
    while unseen:
        start = unseen.pop()
        component = {start}
        pending = [start]
        while pending:
            current = pending.pop()
            for edge in current.link_edges:
                other = edge.other_vert(current)
                if other in unseen:
                    unseen.remove(other)
                    component.add(other)
                    pending.append(other)
        duplicate = editable.copy()
        remove = [vertex for vertex in duplicate.verts if editable.verts[vertex.index] not in component]
        bmesh.ops.delete(duplicate, geom=remove, context="VERTS")
        if duplicate.faces:
            components.append(duplicate)
        else:
            duplicate.free()
    editable.free()
    return components


def component_volume(part):
    return abs(part.calc_volume(signed=True))


def surface_area(parts):
    return sum(face.calc_area() for part in parts for face in part.faces)


def clip_component(part, point, normal, keep_negative, cap_index):
    result = part.copy()
    cut = bmesh.ops.bisect_plane(
        result, geom=list(result.verts) + list(result.edges) + list(result.faces),
        dist=0.0000001, plane_co=point, plane_no=normal,
        clear_outer=keep_negative, clear_inner=not keep_negative,
    )
    cut_edges = [edge for edge in cut["geom_cut"]
                 if isinstance(edge, bmesh.types.BMEdge) and edge.is_valid and edge.is_boundary]
    if cut_edges:
        filled = bmesh.ops.holes_fill(result, edges=cut_edges, sides=0)
        role_layer = result.faces.layers.int.get("cv2_new_cap")
        normal_layers = [result.faces.layers.float.get("cv2_cap_normal_" + str(axis)) for axis in range(3)]
        for face in filled["faces"]:
            face.material_index = cap_index
            if role_layer:
                face[role_layer] = 1
            face.normal_update()
            outward = normal if keep_negative else -normal
            for axis, layer in enumerate(normal_layers):
                face[layer] = outward[axis]
            if face.normal.dot(outward) < 0:
                face.normal_flip()
            CUT_QA["new_cap_faces"] += 1
            if face.calc_area() < 1e-10:
                CUT_QA["zero_area_cap_faces"] += 1
            if face.normal.length < 0.5:
                CUT_QA["ngon_zero_normal_caps"] += 1
            # Explicit planar UVs keep the new faces usable by UE materials.
            uv_layer = result.loops.layers.uv.active
            if uv_layer:
                for loop in face.loops:
                    loop[uv_layer].uv = (loop.vert.co.x + loop.vert.co.y, loop.vert.co.z)
    if not result.faces:
        result.free()
        return None
    result.normal_update()
    if surface_area([result]) < 1e-8:
        result.free()
        return None
    return result


def split_parts(parts, axis, coordinate, cap_index, tilt):
    point = Vector((0, 0, 0))
    point[axis] = coordinate
    normal = Vector(tilt)
    normal[axis] = 1.0
    normal.normalize()
    negative, positive = [], []
    for part in parts:
        distances = [(vertex.co - point).dot(normal) for vertex in part.verts]
        if max(distances) < 0.0000001:
            negative.append(part.copy())
        elif min(distances) > -0.0000001:
            positive.append(part.copy())
        else:
            produced = []
            for keep_negative, destination in ((True, negative), (False, positive)):
                clipped = clip_component(part, point, normal, keep_negative, cap_index)
                if clipped:
                    destination.append(clipped)
                    produced.append(clipped)
            before = part.calc_volume(signed=True)
            after = sum(item.calc_volume(signed=True) for item in produced)
            if all(edge.is_manifold for edge in part.edges):
                CUT_QA["closed_component_cuts"] += 1
                CUT_QA["closed_component_outputs_with_boundary"] += sum(
                    any(edge.is_boundary for edge in item.edges) for item in produced)
                CUT_QA["max_closed_component_volume_error_m3"] = max(
                    CUT_QA["max_closed_component_volume_error_m3"], abs(before-after))
            if abs(before-after) > 0.001:
                print("CUT_VOLUME_DIAGNOSTIC", "axis", axis, "coordinate", coordinate,
                      "before", before, "after", after,
                      "input_nonmanifold", sum(not edge.is_manifold for edge in part.edges),
                      "output_boundaries", [sum(edge.is_boundary for edge in item.edges) for item in produced],
                      flush=True)
    return negative, positive


def partition_structure(parts, count, seed, cap_index):
    """Partition real occupied source solids, keeping trim in its spatial cell."""
    rng = random.Random(seed)
    cells = [parts]
    attempts = 0
    while len(cells) < count and attempts < count * 4:
        attempts += 1
        # Extent prioritisation makes every complete source block smaller.
        scored = []
        for index, cell in enumerate(cells):
            low, high = bounds(cell)
            dimensions = high - low
            score = max(dimensions) * max(surface_area(cell), 0.01) ** 0.13
            scored.append((score, index, low, high, dimensions))
        _, index, low, high, dimensions = max(scored)
        axis = max(range(3), key=lambda item: dimensions[item])
        if dimensions[axis] < 0.5:
            break
        center = (low[axis] + high[axis]) * 0.5
        spacing = rng.uniform(0.65, 0.95)
        coordinate = round(center / spacing) * spacing
        coordinate = max(low[axis] + dimensions[axis] * 0.35,
                         min(high[axis] - dimensions[axis] * 0.35, coordinate))
        tilt = [rng.uniform(-0.075, 0.075) for _ in range(3)]
        left, right = split_parts(cells[index], axis, coordinate, cap_index, tilt)
        if not left or not right:
            for part in left + right:
                part.free()
            # A balanced retry handles hollow cells whose midpoint is empty.
            left, right = split_parts(cells[index], axis, center, cap_index, [0, 0, 0])
        if not left or not right:
            for part in left + right:
                part.free()
            break
        for part in cells[index]:
            part.free()
        cells[index:index + 1] = [left, right]
    return cells


def make_fragment(name, parts, materials):
    low, high = bounds(parts)
    center = (low + high) * 0.5
    mesh = bpy.data.meshes.new(name + "_mesh")
    vertices, faces, material_indices = [], [], []
    uvs, face_roles, face_cap_normals = [], [], []
    for part in parts:
        part.verts.ensure_lookup_table()
        part.verts.index_update()
        base = len(vertices)
        vertices.extend(tuple(vertex.co - center) for vertex in part.verts)
        uv_layer = part.loops.layers.uv.active
        role_layer = part.faces.layers.int.get("cv2_new_cap")
        normal_layers = [part.faces.layers.float.get("cv2_cap_normal_" + str(axis)) for axis in range(3)]
        for face in part.faces:
            faces.append(tuple(base + vertex.index for vertex in face.verts))
            material_indices.append(face.material_index)
            face_roles.append(face[role_layer] if role_layer else 0)
            face_cap_normals.append([face[layer] if layer else 0 for layer in normal_layers])
            uvs.extend(tuple(loop[uv_layer].uv) if uv_layer else (0, 0) for loop in face.loops)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    for material in materials:
        mesh.materials.append(material)
    for polygon, material_index in zip(mesh.polygons, material_indices):
        polygon.material_index = material_index
    layer = mesh.uv_layers.new(name="UVMap")
    for loop, uv in zip(layer.data, uvs):
        loop.uv = uv
    triangulated = bmesh.new()
    triangulated.from_mesh(mesh)
    triangulated.faces.ensure_lookup_table()
    role_layer = triangulated.faces.layers.int.new("cv2_new_cap")
    normal_layers = [triangulated.faces.layers.float.new("cv2_cap_normal_" + str(axis)) for axis in range(3)]
    for index, face in enumerate(triangulated.faces):
        face[role_layer] = face_roles[index]
        for axis, normal_layer in enumerate(normal_layers):
            face[normal_layer] = face_cap_normals[index][axis]
    bmesh.ops.triangulate(triangulated, faces=list(triangulated.faces),
                         quad_method="BEAUTY", ngon_method="BEAUTY")
    tiny_caps = []
    for face in triangulated.faces:
        if not face[role_layer]:
            continue
        area = face.calc_area()
        longest_edge = max(edge.calc_length() for edge in face.edges)
        if area < 1e-7 or 2*area/max(longest_edge, 1e-8) < 1e-6:
            tiny_caps.append(face)
            continue
        face.normal_update()
        outward = Vector([face[layer] for layer in normal_layers])
        if face.normal.dot(outward) < 0:
            face.normal_flip()
            face.normal_update()
        deviation = abs(1-face.normal.dot(outward))
        if deviation > 0.01 and len(CUT_QA["cap_normal_diagnostics"]) < 6:
            CUT_QA["cap_normal_diagnostics"].append({"name": name, "area": face.calc_area(),
                "normal": list(face.normal), "outward": list(outward),
                "vertices": [list(vertex.co) for vertex in face.verts]})
        CUT_QA["max_exported_cap_triangle_normal_deviation"] = max(
            CUT_QA["max_exported_cap_triangle_normal_deviation"], deviation)
    CUT_QA["removed_zero_area_cap_triangles"] += len(tiny_caps)
    if tiny_caps:
        bmesh.ops.delete(triangulated, geom=tiny_caps, context="FACES_ONLY")
    triangulated.to_mesh(mesh)
    triangulated.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    # All exported meshes have identity object transforms. UE creates their
    # actor placement exclusively from the parent-local offset below.
    obj["fragment_offset_m"] = list(center)
    for part in parts:
        part.free()
    return obj, center


def build_structures(source_records):
    definitions = {}
    verification = []
    fresh = bpy.data.materials.get("Fresh broken concrete")
    originals = [bpy.data.objects[record["name"]] for record in source_records
                 if record["name"].startswith("piece_")]
    for sequence, original in enumerate(originals):
        parent = original.name
        materials = list(original.data.materials)
        if fresh not in materials:
            materials.append(fresh)
        cap_index = materials.index(fresh)
        parts = connected_parts(original)
        source_low, source_high = bounds(parts)
        # Use oriented volume conservation. The original model contains
        # non-manifold construction seams whose individual signed integrals
        # can cross zero when subdivided, so summing their absolute values is
        # not an additive coverage metric.
        source_volume = sum(part.calc_volume(signed=True) for part in parts)
        component_count = len(parts)
        nonmanifold_components = sum(any(not edge.is_manifold for edge in part.edges) for part in parts)
        source_area = surface_area(parts)
        source_points = [vertex.co.copy() for part in parts for vertex in part.verts]
        longest = max(source_high - source_low)
        target = min(16, max(8, math.ceil(longest * 3.0)))
        cells = partition_structure(parts, target, 17000 + sequence, cap_index)
        cut_volume = sum(part.calc_volume(signed=True) for cell in cells for part in cell)
        retained_surface_area = 0.0
        for cell in cells:
            for part in cell:
                role_layer = part.faces.layers.int.get("cv2_new_cap")
                retained_surface_area += sum(face.calc_area() for face in part.faces
                                             if not role_layer or face[role_layer] == 0)
        surface_error = abs(retained_surface_area-source_area)/max(source_area, 1e-8)
        if surface_error > 0.0001:
            raise RuntimeError(f"Original surface coverage failure {parent}: {surface_error}")
        fragments = []
        reconstructed_low = Vector((math.inf,) * 3)
        reconstructed_high = Vector((-math.inf,) * 3)
        fragment_points = []
        for index, cell in enumerate(cells):
            volume = sum(component_volume(part) for part in cell)
            obj, offset = make_fragment(f"CV2_{parent}_f{index:02d}", cell, materials)
            record = mesh_statistics(obj.data)
            for vertex in obj.data.vertices:
                point = vertex.co + offset
                fragment_points.append(point)
                for axis in range(3):
                    reconstructed_low[axis] = min(reconstructed_low[axis], point[axis])
                    reconstructed_high[axis] = max(reconstructed_high[axis], point[axis])
            fragments.append({
                "name": obj.name, "offset_m": list(offset),
                "dimensions_m": record["dimensions_m"], "kind": "structure",
                "volume_m3": volume, "triangles": record["triangles"],
                "materials": record["materials"],
            })
        bounds_error = max(abs(reconstructed_low[axis] - source_low[axis])
                           for axis in range(3))
        bounds_error = max(bounds_error, max(abs(reconstructed_high[axis] - source_high[axis])
                                             for axis in range(3)))
        # Every original vertex must still be represented exactly by a clipped
        # fragment after restoring its offset. This checks coverage internally,
        # in addition to the union bounds and per-solid volume conservation.
        from mathutils.kdtree import KDTree
        tree = KDTree(len(fragment_points))
        for index, point in enumerate(fragment_points):
            tree.insert(point, index)
        tree.balance()
        vertex_error = max(tree.find(point)[2] for point in source_points)
        volume_error = abs(cut_volume - source_volume) / max(abs(source_volume), 1e-8)
        if max(bounds_error, vertex_error) > 0.00002:
            raise RuntimeError(f"Fragment coverage failure {parent}: {bounds_error}, {vertex_error}")
        if volume_error > 0.015:
            raise RuntimeError(f"Fragment volume failure {parent}: {volume_error}")
        definitions[parent] = {
            "kind": "structure", "fragments": fragments,
            "source_dimensions_m": list(source_high - source_low),
            "source_loose_components": component_count,
            "source_nonmanifold_components": nonmanifold_components,
        }
        verification.append({
            "parent": parent, "fragments": len(fragments),
            "source_volume_m3": source_volume, "fragments_volume_m3": cut_volume,
            "relative_volume_error": volume_error,
            "bounds_error_m": bounds_error, "source_vertex_coverage_error_m": vertex_error,
            "source_surface_area_m2": source_area,
            "retained_original_surface_area_m2": retained_surface_area,
            "relative_original_surface_area_error": surface_error,
            "volume_metric": "sum_of_oriented_source_solid_volumes",
            "source_nonmanifold_components": nonmanifold_components,
        })
        bpy.data.objects.remove(original, do_unlink=True)
        if sequence % 16 == 0:
            print("STRUCTURE_PROGRESS", sequence + 1, "/", len(originals), flush=True)
    return definitions, verification


def build_glass(source_records):
    definitions, templates, verification = {}, {}, []
    for record in source_records:
        parent = record["name"]
        if not parent.startswith("window_"):
            continue
        original = bpy.data.objects[parent]
        low, high = mesh_bounds(original.data)
        dimensions = [high[axis] - low[axis] for axis in range(3)]
        # Source windows are thin rectangular solids. Reuse exact metre-sized
        # template sets when their dimensions, thin axis and material coincide.
        thin = min(range(3), key=lambda axis: dimensions[axis])
        axes = [axis for axis in range(3) if axis != thin]
        materials = list(original.data.materials)
        key = (tuple(round(value, 5) for value in dimensions), thin,
               tuple(material.name for material in materials))
        if key not in templates:
            template_id = len(templates)
            rng = random.Random(26000 + template_id)
            w, h = dimensions[axes[0]], dimensions[axes[1]]
            # Eight radial shards tile the entire glazing rectangle with exact
            # original thickness. The off-centre impact creates unequal shards.
            outline = [(-w/2, -h/2), (rng.uniform(-.15,.15)*w, -h/2),
                       (w/2, -h/2), (w/2, rng.uniform(-.15,.15)*h),
                       (w/2, h/2), (rng.uniform(-.15,.15)*w, h/2),
                       (-w/2, h/2), (-w/2, rng.uniform(-.15,.15)*h)]
            impact = (rng.uniform(-.18,.18)*w, rng.uniform(-.18,.18)*h)
            shards = []
            total_area = 0.0
            for index in range(8):
                polygon = [impact, outline[index], outline[(index + 1) % 8]]
                vertices = []
                for side in (-0.5, 0.5):
                    for point in polygon:
                        vertex = [0.0, 0.0, 0.0]
                        vertex[thin] = dimensions[thin] * side
                        vertex[axes[0]], vertex[axes[1]] = point
                        vertices.append(vertex)
                mesh = bpy.data.meshes.new("glass_work")
                mesh.from_pydata(vertices, [], [(2,1,0), (3,4,5), (0,1,4,3),
                                                 (1,2,5,4), (2,0,3,5)])
                editable = bmesh.new()
                editable.from_mesh(mesh)
                bmesh.ops.recalc_face_normals(editable, faces=list(editable.faces))
                bpy.data.meshes.remove(mesh)
                obj, offset = make_fragment(f"CV2_glass_t{template_id:03d}_s{index:02d}",
                                            [editable], materials)
                shard = {"name": obj.name, "offset_m": list(offset),
                         "dimensions_m": mesh_statistics(obj.data)["dimensions_m"],
                         "kind": "glass", "thickness_m": dimensions[thin],
                         "materials": [material.name for material in materials]}
                shards.append(shard)
                a, b, c = polygon
                total_area += abs((b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])) * .5
            templates[key] = shards
            verification.append({"template": template_id, "shards": 8,
                                 "source_area_m2": w*h, "shard_area_m2": total_area,
                                 "relative_area_error": abs(total_area-w*h)/(w*h),
                                 "source_thickness_preserved": True})
        center = [(low[axis] + high[axis]) * .5 for axis in range(3)]
        shards = []
        for shared in templates[key]:
            shard = dict(shared)
            shard["offset_m"] = [shared["offset_m"][axis] + center[axis] for axis in range(3)]
            shards.append(shard)
        definitions[parent] = {"kind": "glass", "fragments": shards,
                               "source_dimensions_m": dimensions,
                               "shared_template": shards[0]["name"].rsplit("_s", 1)[0]}
        bpy.data.objects.remove(original, do_unlink=True)
    return definitions, verification, len(templates)


def material(name, color, metallic=0.0, roughness=0.5):
    result = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    result.use_nodes = True
    shader = next(node for node in result.node_tree.nodes if node.type == "BSDF_PRINCIPLED")
    shader.inputs["Base Color"].default_value = (*color, 1)
    shader.inputs["Metallic"].default_value = metallic
    shader.inputs["Roughness"].default_value = roughness
    result.diffuse_color = (*color, 1)
    return result


def lathe(name, rings, surface, sides=24):
    vertices = [(x, radius*math.cos(angle*math.tau/sides), radius*math.sin(angle*math.tau/sides))
                for x, radius in rings for angle in range(sides)]
    faces = [tuple(reversed(range(sides)))]
    for ring in range(len(rings)-1):
        for index in range(sides):
            following = (index+1) % sides
            faces.append((ring*sides+index, ring*sides+following,
                          (ring+1)*sides+following, (ring+1)*sides+index))
    faces.append(tuple((len(rings)-1)*sides+index for index in range(sides)))
    mesh = bpy.data.meshes.new(name + "_mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    mesh.materials.append(surface)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    for face in mesh.polygons:
        face.use_smooth = len(face.vertices) == 4
    return obj


def join(parts, name):
    bpy.ops.object.select_all(action="DESELECT")
    for obj in parts:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.join()
    result = bpy.context.object
    result.name = name
    result.data.name = name + "_mesh"
    return result


def build_weapons():
    clear_scene()
    steel = material("CV2_WeaponSteel", (.12,.15,.18), .82, .3)
    orange = material("CV2_OrangeWarhead", (.85,.19,.025), .4, .34)
    dark = material("CV2_NozzleBlack", (.025,.035,.04), .65, .5)
    brass = material("CV2_ShellBrass", (.46,.31,.09), .75, .32)
    yellow = material("CV2_AimArrowYellow", (1.0,.7,.015), .0, .3)
    rocket = [lathe("RocketBody", [(.14,.095), (.24,.105), (1.34,.105), (1.39,.1)], steel),
              lathe("RocketNose", [(1.36,.106), (1.44,.098), (1.57,.072), (1.8,.001)], orange),
              lathe("RocketNozzle", [(0,.065), (.03,.085), (.18,.085)], dark)]
    for index in range(4):
        angle = index*math.pi*.5
        verts = []
        for thickness in (-.007,.007):
            for x, radial in ((.08,.075), (.1,.24), (.45,.105), (.5,.075)):
                verts.append((x, radial*math.cos(angle)-thickness*math.sin(angle),
                              radial*math.sin(angle)+thickness*math.cos(angle)))
        mesh = bpy.data.meshes.new("RocketFin_mesh")
        mesh.from_pydata(verts, [], [(3,2,1,0), (4,5,6,7), (0,1,5,4),
                                     (1,2,6,5), (2,3,7,6), (3,0,4,7)])
        mesh.materials.append(steel)
        obj = bpy.data.objects.new("RocketFin", mesh)
        bpy.context.collection.objects.link(obj)
        rocket.append(obj)
    join(rocket, "rocket")
    join([lathe("ShellBody", [(0,.075), (.025,.095), (.08,.087), (.68,.087), (.72,.082)], brass),
          lathe("ShellHead", [(.7,.084), (.8,.073), (.9,.045), (1.0,.001)], steel)], "shell")
    join([lathe("ArrowShaft", [(0,.023), (1.38,.023)], yellow, 12),
          lathe("ArrowHead", [(1.31,.24), (2.0,.0005)], yellow, 4)], "aim_arrow")
    return {"rocket": {"length_m": 1.8, "origin": "tail", "forward": "+X", "nozzle_m": [0,0,0]},
            "shell": {"length_m": 1.0, "origin": "tail", "forward": "+X"},
            "aim_arrow": {"length_m": 2.0, "origin": "tail", "forward": "+X",
                          "material_role": "emissive_yellow"}}


def export_fbx(path):
    bpy.ops.object.select_all(action="DESELECT")
    objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    result = bpy.ops.export_scene.fbx(
        filepath=str(path), use_selection=True, object_types={"MESH"},
        global_scale=1.0, apply_unit_scale=True, apply_scale_options="FBX_SCALE_UNITS",
        axis_forward="-Y", axis_up="Z", use_space_transform=True,
        bake_space_transform=False, use_mesh_modifiers=True, mesh_smooth_type="FACE",
        use_tspace=True, use_custom_props=False, add_leaf_bones=False,
        bake_anim=False, path_mode="RELATIVE", embed_textures=False,
    )
    if result != {"FINISHED"}:
        raise RuntimeError(f"Failed to export {path}")


def roundtrip(path, expected):
    clear_scene()
    bpy.ops.import_scene.fbx(filepath=str(path), use_custom_normals=True)
    meshes = {obj.name: obj for obj in bpy.context.scene.objects if obj.type == "MESH"}
    if set(meshes) != {record["name"] for record in expected}:
        raise RuntimeError("FBX roundtrip mesh names differ")
    if any(obj.parent for obj in meshes.values()):
        raise RuntimeError("FBX introduced fragment parent transforms")
    max_error, max_transform = 0.0, 0.0
    for record in expected:
        obj = meshes[record["name"]]
        actual = mesh_statistics(obj.data)
        for side in ("min", "max"):
            for first, second in zip(record["bounds_local_m"][side], actual["bounds_local_m"][side]):
                max_error = max(max_error, abs(first-second))
        max_transform = max(max_transform, obj.location.length,
                            max(abs(value-1) for value in obj.scale),
                            max(abs(value) for value in obj.rotation_euler))
        if record["materials"] != actual["materials"]:
            raise RuntimeError(f"FBX changed material slots for {obj.name}: "
                               f"{record['materials']} -> {actual['materials']}")
    if max_error > 0.00002 or max_transform > 0.00002:
        raise RuntimeError(f"FBX centred metre roundtrip error {max_error} / {max_transform}")
    return {"passed": True, "mesh_count": len(meshes),
            "empty_count": len([obj for obj in bpy.context.scene.objects if obj.type == "EMPTY"]),
            "max_local_bounds_error_m": max_error,
            "max_identity_transform_error": max_transform,
            "material_slot_names_preserved": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else [])
    OUTPUT.mkdir(parents=True, exist_ok=True)
    inputs = {name: sha256(SOURCE/name) for name in ("building_v4_pc.fbx", "assets.json")}
    source = json.loads((SOURCE / "assets.json").read_text(encoding="utf-8"))
    clear_scene()
    bpy.ops.import_scene.fbx(filepath=str(SOURCE/"building_v4_pc.fbx"), use_custom_normals=True)
    records = source["building"]["mesh_objects"]
    if args.inspect:
        for record in [item for item in records if item["name"].startswith("piece_")][:12]:
            obj = bpy.data.objects[record["name"]]
            parts = connected_parts(obj)
            print("SOURCE_DIAGNOSTIC", obj.name, "components", len(parts),
                  "closed", sum(all(edge.is_manifold for edge in part.edges) for part in parts),
                  "volume", sum(component_volume(part) for part in parts), flush=True)
            if obj.name == "piece_7":
                for index, part in enumerate(parts):
                    low, high = bounds([part])
                    print("COMPONENT", index, len(part.faces), "boundary", sum(edge.is_boundary for edge in part.edges),
                          "nonmanifold", sum(not edge.is_manifold for edge in part.edges),
                          "vol", component_volume(part), "dims", list(high-low), flush=True)
            for part in parts:
                part.free()
        return
    if args.limit:
        records = [item for item in records if item["name"] in {f"piece_{i}" for i in range(args.limit)}
                   or item["name"].startswith("window_")]
    definitions, structural_qa = build_structures(records)
    glass_definitions, glass_qa, template_count = build_glass(records)
    definitions.update(glass_definitions)
    if args.audit_only:
        manifest_path = OUTPUT / "combat-assets.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        maximum_offset_error = 0.0
        for parent, definition in definitions.items():
            expected = manifest["fracture_defs"][parent]["fragments"]
            actual = definition["fragments"]
            if [item["name"] for item in expected] != [item["name"] for item in actual]:
                raise RuntimeError("Audit fragment names differ for " + parent)
            for old, new in zip(expected, actual):
                maximum_offset_error = max(maximum_offset_error,
                    max(abs(a-b) for a,b in zip(old["offset_m"], new["offset_m"])))
        audit = dict(CUT_QA)
        audit["max_regenerated_fragment_offset_error_m"] = maximum_offset_error
        audit["max_relative_original_surface_area_error"] = max(
            item["relative_original_surface_area_error"] for item in structural_qa)
        audit["parents_with_original_nonmanifold_seams"] = [
            item["parent"] for item in structural_qa if item["source_nonmanifold_components"]]
        manifest["verification"]["surface_and_caps_audit"] = audit
        manifest["verification"]["structure_coverage"] = structural_qa
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print("SURFACE_AND_CAPS_AUDIT", json.dumps(audit), flush=True)
        return
    for obj in list(bpy.data.objects):
        if not obj.name.startswith("CV2_"):
            bpy.data.objects.remove(obj, do_unlink=True)
    fracture_records = [mesh_statistics(obj.data) for obj in bpy.context.scene.objects if obj.type == "MESH"]
    export_fbx(FRACTURE_FBX)
    fracture_qa = roundtrip(FRACTURE_FBX, fracture_records)
    print("FRACTURE_FBX_VERIFIED", json.dumps(fracture_qa), flush=True)
    weapons = build_weapons()
    weapon_records = [mesh_statistics(obj.data) for obj in bpy.context.scene.objects if obj.type == "MESH"]
    export_fbx(WEAPONS_FBX)
    weapon_qa = roundtrip(WEAPONS_FBX, weapon_records)
    for name, digest in inputs.items():
        if sha256(SOURCE/name) != digest:
            raise RuntimeError("Read-only source changed: " + name)
    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "blender_version": bpy.app.version_string, "generator": Path(__file__).resolve().relative_to(ROOT).as_posix(),
        "source_sha256": inputs,
        "units": {"mesh_unit": "metre", "blender_to_ue": "[x,-y,z]",
                  "actor_scale_for_raw_metre_import": [100,100,100],
                  "offset_rule": "ParentTransform.TransformPosition([offset_m.x,-offset_m.y,offset_m.z]); parent ActorScale already converts raw metres to cm",
                  "fragment_pivot": "geometry_bounds_center", "fragment_object_transforms": "identity"},
        "fracture_fbx": FRACTURE_FBX.resolve().relative_to(ROOT).as_posix(), "weapons_fbx": WEAPONS_FBX.resolve().relative_to(ROOT).as_posix(),
        "fracture_defs": definitions, "weapons": weapons,
        "fracture_mesh_objects": fracture_records, "weapon_mesh_objects": weapon_records,
        "counts": {"structure_parents": len(structural_qa), "window_parents": len(glass_definitions),
                   "structure_meshes": sum(item["fragments"] for item in structural_qa),
                   "glass_template_sets": template_count, "glass_template_meshes": template_count*8,
                   "glass_runtime_shards": len(glass_definitions)*8},
        "verification": {"fracture_fbx": fracture_qa, "weapons_fbx": weapon_qa,
                         "structure_coverage": structural_qa, "glass_coverage": glass_qa,
                         "surface_and_caps_audit": CUT_QA,
                         "original_source_hashes_unchanged": True},
    }
    destination = OUTPUT / "combat-assets.json"
    destination.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("COMBAT_ASSETS_READY", str(destination), json.dumps(manifest["counts"]), flush=True)


if __name__ == "__main__":
    main()
