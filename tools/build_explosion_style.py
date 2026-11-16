"""Build only the layered impact style using the Unreal Niagara authoring plugin."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from effects_v2 import rebuild_impact_style

rebuild_impact_style()
