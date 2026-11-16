"""Create a centred, metre-scale meteor; run in Blender background mode."""
import hashlib
import json
import math
from pathlib import Path

import bpy
from mathutils import Vector, noise

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "SourceAssets" / "Meteor"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=5, radius=1.0)
    obj = bpy.context.object
    obj.name = "meteor_core"
    obj.data.name = "meteor_core_mesh"
    depressions = [(Vector((.8, -.4, .5)).normalized(), .38, .17),
                   (Vector((-.3, .9, .25)).normalized(), .30, .11),
                   (Vector((.1, .2, -1)).normalized(), .41, .13)]
    for vertex in obj.data.vertices:
        d = vertex.co.normalized()
        broad = noise.noise(d*2.2 + Vector((4.3, 7.1, 2.6)))
        rough = noise.noise(d*8.5 + Vector((17.1, 5.4, 9.2)))
        grain = noise.noise(d*25.0 + Vector((3.1, 19.7, 6.2)))
        radial = 1.0 + broad*.15 + rough*.055 + grain*.018
        for centre, width, depth in depressions:
            distance = (d-centre).length/width
            radial -= depth*math.exp(-distance*distance*2)
        vertex.co = Vector((d.x*1.76, d.y*1.57, d.z*1.64))*radial
    lows = Vector([min(v.co[i] for v in obj.data.vertices) for i in range(3)])
    highs = Vector([max(v.co[i] for v in obj.data.vertices) for i in range(3)])
    centre = (lows+highs)*.5
    factor = 3.4/max(highs-lows)
    for vertex in obj.data.vertices:
        vertex.co = (vertex.co-centre)*factor
    # Sphere UVs retain a continuous surface over nearly the whole silhouette.
    uv = obj.data.uv_layers.active or obj.data.uv_layers.new(name="Surface")
    for polygon in obj.data.polygons:
        values = []
        for index in polygon.loop_indices:
            p = obj.data.vertices[obj.data.loops[index].vertex_index].co.normalized()
            values.append((index, .5+math.atan2(p.y, p.x)/(2*math.pi),
                           .5-math.asin(max(-1, min(1, p.z)))/math.pi))
        wrap = max(value[1] for value in values)-min(value[1] for value in values) > .5
        for index, u, v in values:
            uv.data[index].uv = (u+1 if wrap and u < .5 else u, v)
        polygon.use_smooth = True
    material = bpy.data.materials.new("MeteorCharredCrust")
    material.diffuse_color = (.045, .037, .032, 1)
    obj.data.materials.append(material)
    obj.data.update()
    path = OUT / "meteor_core.fbx"
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    result = bpy.ops.export_scene.fbx(
        filepath=str(path), use_selection=True, object_types={"MESH"},
        global_scale=1.0, apply_unit_scale=True, apply_scale_options="FBX_SCALE_UNITS",
        axis_forward="-Y", axis_up="Z", use_space_transform=True,
        bake_space_transform=False, use_mesh_modifiers=True, mesh_smooth_type="FACE",
        use_tspace=True, use_custom_props=False, add_leaf_bones=False, bake_anim=False,
        path_mode="RELATIVE", embed_textures=False)
    if result != {"FINISHED"}:
        raise RuntimeError("Meteor FBX export failed")
    dimensions = [max(v.co[i] for v in obj.data.vertices)-min(v.co[i] for v in obj.data.vertices)
                  for i in range(3)]
    report = {"path": path.relative_to(ROOT).as_posix(), "mesh_name": obj.name, "dimensions_m": dimensions,
              "vertices": len(obj.data.vertices), "triangles": len(obj.data.polygons),
              "centred_metres": True, "actor_scale_for_unreal": 100,
              "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.import_scene.fbx(filepath=str(path), use_custom_normals=True)
    imported = next(item for item in scene.objects if item.type == "MESH")
    actual = [max(v.co[i] for v in imported.data.vertices)-min(v.co[i] for v in imported.data.vertices)
              for i in range(3)]
    error = max(abs(a-b) for a,b in zip(dimensions, actual))
    if error > .00002 or imported.location.length > .00002:
        raise RuntimeError("Meteor metre-scale FBX roundtrip failed")
    report["fbx_roundtrip_max_dimension_error_m"] = error
    (OUT / "meteor-assets.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("SKYFALL_METEOR_EXPORT_OK " + json.dumps(report))


if __name__ == "__main__":
    main()
