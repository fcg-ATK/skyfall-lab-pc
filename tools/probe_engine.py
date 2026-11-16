import json
from pathlib import Path
import unreal

ROOT = Path(__file__).resolve().parents[1]
report = {"engine": unreal.SystemLibrary.get_engine_version(), "classes": {}}
for name in ["BlueprintGraphEditor", "BlueprintEditorLibrary", "GeometryCollection",
             "GeometryCollectionFactory", "GeometryCollectionComponent", "FractureToolVoronoiCutter",
             "GeometryCollectionEngineConversion", "StaticMeshEditorSubsystem",
             "EditorActorSubsystem", "MaterialEditingLibrary", "NiagaraSystemFactoryNew"]:
    cls = getattr(unreal, name, None)
    report["classes"][name] = [n for n in dir(cls) if not n.startswith("_")] if cls else None
try:
    import editor_toolset.toolsets.blueprint as blueprint_tools
    report["blueprint_tools"] = str(blueprint_tools.__file__)
except Exception as exc:
    report["blueprint_tools_error"] = str(exc)
(ROOT / "Saved").mkdir(exist_ok=True)
(ROOT / "Saved" / "engine-probe.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
unreal.log("SKYFALL_ENGINE_PROBE_OK " + report["engine"])
