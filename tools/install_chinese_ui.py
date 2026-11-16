"""Install the native Chinese UI without rebuilding destruction or camera assets."""
import json
import sys
from pathlib import Path
import unreal

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from ui_actions import patch_controller_ui_input
from hud_v2 import build_hud

unreal.InternationalizationLibrary.set_current_language('en',False)
levels=unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
actors=unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
levels.load_level('/Game/Skyfall/Maps/DemolitionYard')
before=sorted(a.get_path_name() for a in actors.get_all_level_actors() if a.actor_has_tag('SkyfallPiece'))
controller=unreal.load_asset('/Game/Skyfall/Blueprints/BP_SkyfallControllerV2')
assert controller, 'Existing combat controller required'
patch=patch_controller_ui_input(controller)
ui=build_hud(controller)
after=sorted(a.get_path_name() for a in actors.get_all_level_actors() if a.actor_has_tag('SkyfallPiece'))
assert before==after, 'Building pieces changed'
report={'ok':True,'hud_status':str(ui['hud_blueprint'].get_editor_property('status')),
        'controller_status':str(controller.get_editor_property('status')),
        'input_patch':patch,'building_pieces_preserved':len(after),
        'combat_rebuilt':False,'camera_rebuilt':False,'map_saved':False}
(ROOT/'Saved/chinese-ui-install.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
unreal.log('SKYFALL_CHINESE_UI_INSTALLED '+json.dumps(report))
