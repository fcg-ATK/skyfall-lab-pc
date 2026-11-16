"""Install free camera/input routing while preserving existing destruction assets."""
import json
import sys
from pathlib import Path
import unreal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from combat_v2 import _build_controller, configure_meteor_building_bounds
from aim_v2 import install_aim, bind_placed_guides
from hud_v2 import build_hud
from free_camera import build_free_camera, install_free_camera_in_level

unreal.InternationalizationLibrary.set_current_language('en', False)
levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
levels.load_level('/Game/Skyfall/Maps/DemolitionYard')
before = [a for a in actors.get_all_level_actors() if a.actor_has_tag('SkyfallPiece')]
before_paths = sorted(a.get_path_name() for a in before)
projectiles = [unreal.load_asset('/Game/Skyfall/Blueprints/BP_Skyfall'+name+'V2')
               for name in ('Rocket', 'Meteor', 'Ball')]
assert all(projectiles), 'Existing projectile assets are required'
effects = {
    'launch_class': unreal.load_asset('/Game/Skyfall/CombatV2/Effects/BP_RocketExhaust').generated_class(),
    'meteor_tail_class': unreal.load_asset('/Game/Skyfall/Meteor/BP_MeteorTail').generated_class(),
}
controller = _build_controller(projectiles, effects)
arrow = unreal.load_asset('/Game/Skyfall/CombatV2/Meshes/Weapons/weapons_v2_aim_arrow')
install_aim(controller, arrow)
hud = build_hud(controller)
camera_blueprint = build_free_camera()
camera = install_free_camera_in_level(camera_blueprint)
placed = [a for a in actors.get_all_level_actors() if a.get_class() == controller.generated_class()]
assert len(placed) == 1, 'Expected one production aim controller'
for field, value in {'WeaponMode':1, 'LaunchSpeed':4200.0, 'GravityZ':-1600.0,
                     'ProjectileRadius':170.0, 'MeteorTargetX':0.0,
                     'MeteorTargetY':0.0, 'MeteorAngle':40.0}.items():
    placed[0].set_editor_property(field, value)
after = [a for a in actors.get_all_level_actors() if a.actor_has_tag('SkyfallPiece')]
assert sorted(a.get_path_name() for a in after) == before_paths, 'Building pieces changed'
envelope = configure_meteor_building_bounds(placed[0], after)
bind_placed_guides(controller, arrow)
assert levels.save_current_level(), 'Could not save camera installation'
report = {'ok':True, 'controller_status':str(controller.get_editor_property('status')),
          'camera_status':str(camera_blueprint.get_editor_property('status')),
          'hud_status':str(hud['hud_blueprint'].get_editor_property('status')),
          'building_pieces_preserved':len(after), 'building_envelope':envelope,
          'projectiles_rebuilt':False, 'default_meteor_angle':40,
          'free_camera_count':len([a for a in actors.get_all_level_actors() if a.actor_has_tag('SFFreeCamera')])}
(ROOT/'Saved/free-camera-install.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
unreal.log('SKYFALL_FREE_CAMERA_INSTALLED ' + json.dumps(report))
