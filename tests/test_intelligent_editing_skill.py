import json
import unittest
from pathlib import Path

from skills.intelligent_editing.runtime import (
    PlanValidationError,
    make_local_plan,
    plan_hash,
    planner_payload,
    validate_plan,
    validate_request,
    validate_managed_asset_id,
)


ROOT = Path(__file__).resolve().parents[1]


def request(**overrides):
    value = {
        "request_id": "req-001",
        "selected_assets": [
            {"asset_id": "images/first.png", "media_type": "image", "duration_ms": 3000},
            {"asset_id": "videos/second.mp4", "media_type": "video", "duration_ms": 4000},
        ],
        "objective": "tight product recap",
        "aspect_ratio": "portrait",
        "target_duration_ms": 6000,
        "image_duration_ms": 3000,
        "transition_mode": "auto",
        "planner": "local",
    }
    value.update(overrides)
    return value


class IntelligentEditingSkillTests(unittest.TestCase):
    def test_registry_skill_and_eval_metadata_are_present(self):
        registry = json.loads((ROOT / "skills" / "registry.json").read_text(encoding="utf-8"))
        entry = next(item for item in registry["skills"] if item["name"] == "intelligent-editing")
        self.assertEqual(entry["skill_file"], "skills/intelligent-editing/SKILL.md")
        self.assertIn("智能混剪", entry["triggers"])
        self.assertIn("harness", entry["entrypoints"])
        skill = (ROOT / "skills" / "intelligent-editing" / "SKILL.md").read_text(encoding="utf-8")
        for phrase in ("plan hash", "explicitly confirms", "opaque managed asset ID", "FFmpeg", "Terra"):
            self.assertIn(phrase, skill)
        evals = json.loads((ROOT / "skills" / "intelligent-editing" / "evals" / "evals.json").read_text(encoding="utf-8"))
        self.assertEqual(evals["skill_name"], "intelligent-editing")
        self.assertGreaterEqual(len(evals["evals"]), 3)
        schema = json.loads(
            (ROOT / "skills" / "intelligent-editing" / "references" / "plan-schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertFalse(schema["$defs"]["request"]["additionalProperties"])
        self.assertFalse(schema["$defs"]["plan"]["additionalProperties"])
        self.assertFalse(schema["$defs"]["runState"]["additionalProperties"])

    def test_request_rejects_duplicates_paths_and_secrets(self):
        with self.assertRaises(PlanValidationError):
            validate_request(request(selected_assets=[{"asset_id": "images/a.png"}, {"asset_id": "images/a.png"}]))
        with self.assertRaises(PlanValidationError):
            validate_request(request(selected_assets=[{"asset_id": "C:/private/a.png"}, {"asset_id": "images/b.png"}]))
        with self.assertRaises(PlanValidationError):
            validate_request(request(api_key="secret-value"))
        for unsafe_id in (
            "videos/clip.mp4?token=secret",
            "videos/clip.mp4#fragment",
            "videos/clip.mp4:alternate-stream",
            "videos/%2e%2e/secret.mp4",
        ):
            with self.subTest(asset_id=unsafe_id), self.assertRaises(PlanValidationError):
                validate_request(
                    request(
                        selected_assets=[
                            {"asset_id": unsafe_id, "media_type": "video", "duration_ms": 3000},
                            {"asset_id": "images/safe.png", "media_type": "image", "duration_ms": 3000},
                        ]
                    )
                )

    def test_planner_payload_contains_only_safe_opaque_fields(self):
        payload = planner_payload(request(planner="codex_terra"))
        self.assertEqual(set(payload), {"objective", "target_duration_ms", "transition_mode", "clips"})
        self.assertEqual(payload["clips"][0], {"asset_id": "images/first.png", "duration_ms": 3000})
        self.assertNotIn("media_type", json.dumps(payload))
        self.assertNotIn("path", json.dumps(payload).lower())

    def test_clothing_image_outputs_are_managed_assets(self):
        self.assertEqual(validate_managed_asset_id("clothing_image/garment.png"), "clothing_image/garment.png")

    def test_local_plan_is_deterministic_and_respects_video_bounds(self):
        value = request(
            target_duration_ms=12000,
            selected_assets=[
                {"asset_id": "videos/one.mp4", "media_type": "video", "trim_bounds_ms": {"start_ms": 1000, "end_ms": 3500}},
                {"asset_id": "videos/two.mp4", "media_type": "video", "trim_bounds_ms": {"start_ms": 0, "end_ms": 8000}},
            ],
        )
        first = make_local_plan(value)
        second = make_local_plan(value)
        self.assertEqual(first, second)
        self.assertEqual(first["planner"], "local")
        for clip, asset in zip(first["clips"], value["selected_assets"]):
            bounds = asset["trim_bounds_ms"]
            self.assertLessEqual(clip["end_ms"], bounds["end_ms"])
            self.assertGreaterEqual(clip["end_ms"] - clip["start_ms"], 500)
        self.assertEqual(plan_hash(first), plan_hash(second))

    def test_plan_requires_exact_envelope_selected_assets_and_duration(self):
        valid = {
            "version": "aigc-mix-plan/v1",
            "objective": "different text is normalized to request objective",
            "target_duration_ms": 6000,
            "clips": [
                {"asset_id": "images/first.png", "duration_ms": 3000, "transition": "hard_cut"},
                {"asset_id": "videos/second.mp4", "duration_ms": 3000, "transition": "hard_cut"},
            ],
            "planner": "local",
            "transition_mode": "auto",
            "warnings": [],
        }
        normalized = validate_plan(valid, request())
        self.assertEqual(normalized["target_duration_ms"], 6000)
        with self.assertRaises(PlanValidationError):
            validate_plan({**valid, "version": "aigc-mix-plan/v2"}, request())
        with self.assertRaises(PlanValidationError):
            validate_plan({**valid, "clips": [{"asset_id": "images/other.png", "duration_ms": 3000}, valid["clips"][1]]}, request())
        with self.assertRaises(PlanValidationError):
            validate_plan({**valid, "clips": [{"asset_id": "images/first.png", "duration_ms": 3000}, {"asset_id": "videos/second.mp4", "duration_ms": 3000, "transition": "fade"}]}, request())

    def test_plan_rejects_source_trim_outside_selected_video_bounds(self):
        bounded_request = request(
            selected_assets=[
                {"asset_id": "images/first.png", "media_type": "image", "duration_ms": 3000},
                {
                    "asset_id": "videos/second.mp4",
                    "media_type": "video",
                    "trim_bounds_ms": {"start_ms": 1000, "end_ms": 4000},
                },
            ]
        )
        unsafe_plan = {
            "version": "aigc-mix-plan/v1",
            "objective": "product recap",
            "target_duration_ms": 6000,
            "clips": [
                {"asset_id": "images/first.png", "duration_ms": 3000},
                {"asset_id": "videos/second.mp4", "start_ms": 0, "end_ms": 3000},
            ],
            "planner": "codex_terra",
            "transition_mode": "auto",
            "warnings": [],
        }
        with self.assertRaisesRegex(PlanValidationError, "outside"):
            validate_plan(unsafe_plan, bounded_request)


if __name__ == "__main__":
    unittest.main()
