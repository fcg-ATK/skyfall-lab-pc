"""Generate locally licensed Niagara assets; run through SetupProject.cmd."""
from pathlib import Path
import hashlib
import json
import sys

import unreal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.dont_write_bytecode = True
SAVED = ROOT / "Saved"
SYSTEMS = (
    "CombatV2/Effects/NS_HighEnergyImpact", "CombatV2/Effects/NS_LocalDust",
    "CombatV2/Effects/NS_RocketExhaust", "Meteor/NS_MeteorExit",
    "Meteor/NS_MeteorImpact", "Meteor/NS_MeteorTail",
)


def main():
    SAVED.mkdir(exist_ok=True)
    report = {"status": "failed", "engine": unreal.SystemLibrary.get_engine_version()}
    map_file = ROOT / "Content/Skyfall/Maps/DemolitionYard.umap"
    before = hashlib.sha256(map_file.read_bytes()).hexdigest()
    try:
        if not report["engine"].startswith("5.8."):
            raise RuntimeError("This setup requires Unreal Engine 5.8.")
        if not hasattr(unreal, "NiagaraToolset_System"):
            raise RuntimeError("NiagaraToolsets is required. Use SetupProject.cmd.")
        for name in ("Fountain", "UpwardMeshBurst", "OmnidirectionalBurst"):
            path = "/Niagara/DefaultAssets/Templates/Emitters/" + name
            if not unreal.load_asset(path):
                raise RuntimeError("Installed UE template is missing: " + path)
        import effects_v2
        import meteor_art
        effects_v2.build_effects(force_rebuild=True)
        meteor_art.build_meteor_art(force_rebuild=True, require_rendered_shaders=True)
        effects_v2.verify_effects(require_rendered_shaders=True)
        for relative in SYSTEMS:
            if not (ROOT / "Content/Skyfall" / (relative + ".uasset")).is_file():
                raise RuntimeError("Missing generated system: " + relative)
        after = hashlib.sha256(map_file.read_bytes()).hexdigest()
        if before != after:
            raise RuntimeError("Setup unexpectedly changed the gameplay map.")
        report.update(status="passed", generated_systems=list(SYSTEMS), map_sha256=after)
        unreal.log("SKYFALL_PUBLIC_SETUP_PASSED")
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        (SAVED / "setup-project.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
