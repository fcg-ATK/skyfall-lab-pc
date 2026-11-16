"""Update only the native camera, camera map instance and HUD."""
import json
import sys
from pathlib import Path
import unreal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from free_camera import build_free_camera, install_free_camera_in_level
from hud_v2 import build_hud

unreal.InternationalizationLibrary.set_current_language('en', False)
levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
levels.load_level('/Game/Skyfall/Maps/DemolitionYard')
pieces_before = sorted(a.get_path_name() for a in actors.get_all_level_actors() if a.actor_has_tag('SkyfallPiece'))
controller = unreal.load_asset('/Game/Skyfall/Blueprints/BP_SkyfallControllerV2')
assert controller, 'Existing combat controller is required'
blueprint = build_free_camera()
camera = install_free_camera_in_level(blueprint)
hud = build_hud(controller)
pieces_after = sorted(a.get_path_name() for a in actors.get_all_level_actors() if a.actor_has_tag('SkyfallPiece'))
assert pieces_before == pieces_after, 'Building actors changed during camera installation'
assert levels.save_current_level(), 'Could not save updated camera instance'
report = {'ok':True, 'camera_status':str(blueprint.get_editor_property('status')),
          'hud_status':str(hud['hud_blueprint'].get_editor_property('status')),
          'building_pieces_preserved':len(pieces_after), 'combat_rebuilt':False,
          'focus':camera.get_editor_property('CameraOrbitFocus').to_tuple(),
          'radius':camera.get_editor_property('CameraOrbitRadius'),
          'location':camera.get_actor_location().to_tuple()}
(ROOT/'Saved/orbit-camera-install.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
unreal.log('SKYFALL_ORBIT_CAMERA_INSTALLED '+json.dumps(report))
