"""Install meteor mode without regenerating the approved building geometry."""
import json
import sys
from pathlib import Path
import unreal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from combat_v2 import WEAPONS, _build_projectile, _build_controller, configure_meteor_building_bounds
from effects_v2 import build_effects
from meteor_art import build_meteor_art
from aim_v2 import install_aim, bind_placed_guides
from hud_v2 import build_hud
from materials import MATERIAL_NAMES

unreal.InternationalizationLibrary.set_current_language('en', False)
effects = build_effects()
art = build_meteor_art()
effects.update({key:value for key,value in art.items() if key.endswith('_class')})
materials = {role:unreal.load_asset('/Game/Skyfall/Materials/'+name)
             for role,name in MATERIAL_NAMES.items()}
meshes = {'rocket':unreal.load_asset('/Game/Skyfall/CombatV2/Meshes/Weapons/weapons_v2_rocket'),
          'meteor':art['mesh'], 'ball':unreal.load_asset('/Engine/BasicShapes/Sphere')}
projectiles = [_build_projectile(spec,meshes[spec['key']],effects,materials) for spec in WEAPONS]
controller = _build_controller(projectiles,effects)
arrow = unreal.load_asset('/Game/Skyfall/CombatV2/Meshes/Weapons/weapons_v2_aim_arrow')
install_aim(controller,arrow)
hud = build_hud(controller)
levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
levels.load_level('/Game/Skyfall/Maps/DemolitionYard')
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
found = []
for actor in actors.get_all_level_actors():
    if actor.get_class() == controller.generated_class():
        for key,value in {'WeaponMode':1,'LaunchSpeed':4200.0,'GravityZ':-1600.0,
                          'ProjectileRadius':170.0,'MeteorTargetX':0.0,'MeteorTargetY':0.0,
                          'MeteorAngle':40.0}.items():
            actor.set_editor_property(key,value)
        found.append(actor)
if len(found)!=1:
    raise RuntimeError('Expected one placed production controller')
pieces = [actor for actor in actors.get_all_level_actors() if actor.actor_has_tag('SkyfallPiece')]
envelope = configure_meteor_building_bounds(found[0], pieces)
bind_placed_guides(controller,arrow)
if not levels.save_current_level():
    raise RuntimeError('Could not save meteor controller/guide overrides')
report = {'weapons':[spec['name'] for spec in WEAPONS],
          'controller_status':str(controller.get_editor_property('status')),
          'projectiles':{spec['key']:bp.generated_class().get_path_name()
                         for spec,bp in zip(WEAPONS,projectiles)},
          'meteor_mesh':art['mesh'].get_path_name(),
          'default_mode':'METEOR','building_geometry_preserved':True,'meteor_building_envelope':envelope}
(ROOT/'Saved/meteor-install.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
unreal.log('SKYFALL_METEOR_INSTALLED '+json.dumps(report))
