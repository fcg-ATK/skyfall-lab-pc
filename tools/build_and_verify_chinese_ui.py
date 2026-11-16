"""Full-editor entry point: fonts require Slate, then verify the installed HUD."""
from pathlib import Path
import runpy

TOOLS=Path(__file__).resolve().parent
runpy.run_path(str(TOOLS/'install_chinese_ui.py'),run_name='__main__')
runpy.run_path(str(TOOLS/'runtime_chinese_ui.py'),run_name='__main__')
