"""Minimal pre-change behavioral probe. Does not save any assets or maps."""
from pathlib import Path

_base_path = Path(__file__).with_name("runtime_meteor.py")
_base_source = _base_path.read_text(encoding="utf-8").split("\nprevious = getattr", 1)[0]
_base_source = _base_source.replace('"MeteorAngle": 68.0', '"MeteorAngle": 40.0')
_base_source = _base_source.replace('math.radians(-68)', 'math.radians(-40)')
exec(compile(_base_source, str(_base_path), "exec"), globals())
REPORT_PATH = PROJECT / "Saved" / "runtime-meteor-v2-red.json"
ROUNDS = ({"name": "old_oblique40", "mode": 1, "target_x": 0, "target_y": 0},)


class MeteorV2Red(MeteorChecks):
    def complete_round(self, counts):
        current = self.current
        current["final_counts"] = counts
        observations = current["stopped_samples"]
        self.check("real_contact_observed", self.first_hit_at is not None and current["max_hits"] > 0)
        drift = max((distance(item["position_cm"], observations[0]["position_cm"])
                     for item in observations), default=0)
        self.check("oblique_continues_after_first_contact", drift >= 300,
                   post_contact_displacement_cm=drift, expected_minimum_cm=300)
        self.check("oblique_body_stays_visible_after_contact", bool(observations) and
                   all(item.get("hidden") is False for item in observations),
                   observations=observations[:5])
        self.check("oblique_tick_continues_after_contact", bool(observations) and
                   any(item.get("tick_enabled") is True for item in observations),
                   observations=observations[:5])
        self.levels.editor_request_end_play()
        self.set_stage("ending_round")


previous = getattr(unreal, "_skyfall_meteor_runner", None)
if previous is not None:
    previous.stop_callback()
runner = MeteorV2Red()
unreal._skyfall_meteor_runner = runner
runner.start()
