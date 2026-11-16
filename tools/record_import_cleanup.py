"""Record Unreal's deterministic FBX degenerate-triangle cleanup for QA."""
import hashlib
import json
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "SourceAssets" / "assets.json"
source = json.loads(path.read_text(encoding="utf-8"))
for group in (source["building"], source["debris"]):
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.import_scene.fbx(filepath=str(ROOT / group["path"]))
    for record in group["mesh_objects"]:
        mesh = bpy.data.objects[record["name"]].data
        mesh.calc_loop_triangles()
        retained = set()
        removed = 0
        for triangle in mesh.loop_triangles:
            a, b, c = (mesh.vertices[index].co for index in triangle.vertices)
            if (b - a).cross(c - a).length_squared <= 1e-8:
                removed += 1
            else:
                retained.update(triangle.vertices)
        if not retained:
            raise RuntimeError("No usable triangles for " + record["name"])
        vertices = [mesh.vertices[index].co for index in sorted(retained)]
        converted = [(v.x * 100, -v.y * 100, v.z * 100) for v in vertices]
        record["import_cleanup"] = {
            "removed_degenerate_triangles": removed,
            "bounds_cm": {
                "min": [min(v[axis] for v in converted) for axis in range(3)],
                "max": [max(v[axis] for v in converted) for axis in range(3)],
            },
            "landmarks_local_m": [list(vertices[index]) for index in
                                  sorted({0, len(vertices) // 3, len(vertices) // 2, len(vertices) - 1})],
        }
    group["sha256"] = hashlib.sha256((ROOT / group["path"]).read_bytes()).hexdigest()
    group["bytes"] = (ROOT / group["path"]).stat().st_size
path.write_text(json.dumps(source, ensure_ascii=False, indent=2), encoding="utf-8")
print("SKYFALL_IMPORT_CLEANUP_RECORDED", flush=True)
