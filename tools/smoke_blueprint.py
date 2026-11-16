import json
import sys
import traceback
from pathlib import Path

import unreal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
report = {}
try:
    from blueprint_runtime import build_controller
    result = build_controller([
        {"name": "SmokeTarget", "location": (0, 0, 800),
         "scale": (3, 1, 3), "mass_kg": 1000},
    ], dust_material="/Engine/BasicShapes/BasicShapeMaterial")
    report = {"ok": True, "pieces": len(result["pieces"]),
              "launch_height": unreal.get_default_object(
                  result["controller_blueprint"].generated_class()).get_editor_property("LaunchHeight"),
              "controller_graphs": [graph.get_name() for graph in
                                    unreal.BlueprintEditorLibrary.list_graphs(result["controller_blueprint"])],
              "blueprints": {key: value.get_path_name()
                             for key, value in result.items()
                             if key.endswith("_blueprint") and value}}
    unreal.log("SKYFALL_BLUEPRINT_SMOKE_OK")
except Exception:
    report = {"ok": False, "error": traceback.format_exc()}
    unreal.log_error(report["error"])
finally:
    (ROOT / "Saved" / "blueprint-smoke.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")
