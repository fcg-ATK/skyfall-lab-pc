"""Import and bind the authored secondary fracture and weapon meshes."""
import json
import sys
from pathlib import Path

import unreal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from build_scene import import_meshes, assign_materials
from materials import MATERIAL_NAMES


def load_combat_assets(materials):
    source = json.loads((ROOT / "SourceAssets/CombatV2/combat-assets.json").read_text(encoding="utf-8"))
    fractures = import_meshes(source["fracture_fbx"], "/Game/Skyfall/CombatV2/Meshes/Fragments", True)
    weapons = import_meshes(source["weapons_fbx"], "/Game/Skyfall/CombatV2/Meshes/Weapons", True)
    expected = {item["name"] for item in source["fracture_mesh_objects"]}
    if len(fractures) != len(expected):
        raise RuntimeError(f"Expected {len(expected)} fracture meshes, imported {len(fractures)}")
    fracture_meshes = {}
    for mesh in fractures:
        mesh_name = mesh.get_name()
        name = next((key for key in expected if mesh_name == key or mesh_name.endswith("_" + key)), None)
        if name is None:
            raise RuntimeError("Unexpected fracture mesh " + mesh.get_name())
        fracture_meshes[name] = mesh
        assign_materials(mesh, materials)
    weapon_meshes = {}
    for mesh in weapons:
        key = next((key for key in source["weapons"] if mesh.get_name() == key or mesh.get_name().endswith("_" + key)), None)
        if key is None:
            raise RuntimeError("Unexpected weapon mesh " + mesh.get_name())
        weapon_meshes[key] = mesh
        for slot, info in enumerate(mesh.static_materials):
            slot_name = str(info.material_slot_name).lower()
            role = "paint" if "orange" in slot_name or "nose" in slot_name else "stainless"
            mesh.set_material(slot, materials[role])
    definitions = {}
    for name, record in source["fracture_defs"].items():
        definitions[name] = {
            "meshes": [fracture_meshes[part["name"]] for part in record["fragments"]],
            "offsets": [(part["offset_m"][0], -part["offset_m"][1], part["offset_m"][2])
                        for part in record["fragments"]],
        }
    unreal.get_editor_subsystem(unreal.EditorAssetSubsystem).save_directory(
        "/Game/Skyfall/CombatV2/Meshes", only_if_is_dirty=True, recursive=True)
    report = {"fracture_meshes": len(fractures), "weapon_meshes": list(weapon_meshes),
              "mapped_parents": len(definitions),
              "runtime_secondary_pieces": sum(len(value["meshes"]) for value in definitions.values())}
    (ROOT / "Saved/combat-meshes.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    unreal.log("SKYFALL_COMBAT_MESHES_OK " + json.dumps(report))
    return weapon_meshes, definitions


if __name__ == "__main__":
    materials = {role: unreal.load_asset("/Game/Skyfall/Materials/" + name)
                 for role, name in MATERIAL_NAMES.items()}
    load_combat_assets(materials)
