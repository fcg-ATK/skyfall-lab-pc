"""Import, save, and validate the exported PC meshes inside Unreal Editor."""

import json
import math
import sys
from collections import Counter
from pathlib import Path

import unreal


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from build_scene import import_meshes


ASSETS = unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)
MESHES = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem) or unreal.StaticMeshEditorSubsystem()
REPORT = ROOT / "Saved" / "mesh-import.json"
TOLERANCE_CM = 0.2
INSTANCE_SCALE = 100.0


def coordinates(vector):
    return [float(vector.x), float(vector.y), float(vector.z)]


def source_record(name, records):
    matches = [record for record in records if name == record["name"] or name.endswith("_" + record["name"])]
    if len(matches) != 1:
        raise RuntimeError(f"Cannot identify source mesh for {name}: {len(matches)} candidates")
    return matches[0]


def read_vertices(mesh):
    dynamic = unreal.DynamicMesh()
    result = unreal.GeometryScript_AssetUtils.copy_mesh_from_static_mesh(
        mesh, dynamic, unreal.GeometryScriptCopyMeshFromAssetOptions(), unreal.GeometryScriptMeshReadLOD()
    )
    if result[-1] != unreal.GeometryScriptOutcomePins.SUCCESS:
        raise RuntimeError(f"Cannot read imported geometry for {mesh.get_name()}: {result}")
    _, positions, _ = unreal.GeometryScript_MeshQueries.get_all_vertex_positions(dynamic, True)
    return unreal.GeometryScript_List.convert_vector_list_to_array(positions)


def landmark_error(vertices, landmarks):
    error = 0.0
    for x, y, z in landmarks:
        expected = (x, -y, z)
        closest_squared = min(
            (vertex.x - expected[0]) ** 2 + (vertex.y - expected[1]) ** 2 + (vertex.z - expected[2]) ** 2
            for vertex in vertices
        )
        error = max(error, math.sqrt(closest_squared))
    return error


def validate_mesh(mesh, record, collision_count):
    bounds = mesh.get_bounding_box()
    minimum, maximum = coordinates(bounds.min), coordinates(bounds.max)
    dimensions = [maximum[axis] - minimum[axis] for axis in range(3)]
    center = [(maximum[axis] + minimum[axis]) * 0.5 for axis in range(3)]
    cleanup = record["import_cleanup"]
    expected_bounds = cleanup["bounds_cm"]
    expected_dimensions = [expected_bounds["max"][i] - expected_bounds["min"][i] for i in range(3)]
    expected_center = [(expected_bounds["max"][i] + expected_bounds["min"][i]) / 2 for i in range(3)]
    dimension_error = max(abs(value * INSTANCE_SCALE - expected) for value, expected in zip(dimensions, expected_dimensions))
    center_error = max(abs(value * INSTANCE_SCALE - expected) for value, expected in zip(center, expected_center))
    if dimension_error > TOLERANCE_CM or center_error > TOLERANCE_CM:
        raise RuntimeError(f"Scale/pivot mismatch {mesh.get_name()}: dimensions={dimension_error}cm center={center_error}cm")
    vertices = read_vertices(mesh)
    orientation_error = landmark_error(vertices, cleanup["landmarks_local_m"]) * INSTANCE_SCALE
    if orientation_error > TOLERANCE_CM:
        raise RuntimeError(f"Axis mismatch {mesh.get_name()}: landmark error={orientation_error}cm")
    hull_count = MESHES.get_convex_collision_count(mesh)
    if collision_count is not None and hull_count != collision_count:
        raise RuntimeError(f"Missing UCX collision {mesh.get_name()}: expected {collision_count}, imported {hull_count}")
    return {
        "name": mesh.get_name(), "source_name": record["name"], "asset_path": mesh.get_path_name(),
        "removed_degenerate_triangles": cleanup["removed_degenerate_triangles"],
        "local_mesh_dimensions": dimensions, "instance_scale": INSTANCE_SCALE, "dimension_error_cm": dimension_error,
        "local_bounds_center_cm": center, "pivot_error_cm": center_error,
        "axis_landmark_error_cm": orientation_error, "ue_location_cm": record["ue_location_cm"],
        "convex_collision_count": hull_count, "expected_ucx_count": collision_count,
        "material_slot_names": [str(slot.material_slot_name) for slot in mesh.static_materials],
    }


def import_group(source, key, folder, expected_count, collision_counts):
    group = source[key]
    meshes = import_meshes(group["path"], folder)
    if len(meshes) != expected_count:
        raise RuntimeError(f"Wrong mesh count in {folder}: {len(meshes)}, expected {expected_count}")
    checks = []
    for index, mesh in enumerate(meshes):
        record = source_record(mesh.get_name(), group["mesh_objects"])
        check = validate_mesh(mesh, record, collision_counts.get(record["name"]))
        if not ASSETS.save_loaded_asset(mesh, only_if_is_dirty=False):
            raise RuntimeError(f"Cannot save {mesh.get_path_name()}")
        checks.append(check)
        if index % 50 == 0:
            unreal.log(f"SKYFALL_MESH_PROGRESS {key} {index + 1}/{expected_count}")
    return {"folder": folder, "mesh_count": len(meshes), "meshes": checks}


def main():
    source = json.loads((ROOT / "SourceAssets" / "assets.json").read_text(encoding="utf-8"))
    collision_counts = Counter(record["collision_for"] for record in source["building"]["collision_mesh_objects"])
    report = {"passed": False, "engine": unreal.SystemLibrary.get_engine_version(), "tolerance_cm": TOLERANCE_CM}
    try:
        report["building"] = import_group(source, "building", "/Game/Skyfall/Meshes/Building", 575, collision_counts)
        report["debris"] = import_group(source, "debris", "/Game/Skyfall/Meshes/Debris", 8, {})
        report["axis_mapping"] = "FBX local pivots retain source metre coordinates. Actor scale 100 converts local geometry and collision hulls to centimetres. Actor location = [100x,-100y,100z]."
        report["source_collision_boxes"] = sum(collision_counts.values())
        report["passed"] = True
        unreal.log("SKYFALL_MESH_IMPORT_OK 575 building meshes and 8 debris meshes saved")
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
