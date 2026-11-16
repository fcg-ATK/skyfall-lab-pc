"""Run with NiagaraToolsets, -AllowCommandletRendering -d3d12."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from meteor_art import build_meteor_art
build_meteor_art(force_rebuild=True)
