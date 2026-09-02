import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "skills" / "aigc-production-orchestrator" / "SKILL.md"


class AigcProductionOrchestratorSkillTests(unittest.TestCase):
    def test_registry_entry_points_to_project_owned_interfaces(self):
        registry = json.loads((ROOT / "skills" / "registry.json").read_text(encoding="utf-8"))
        entry = next(item for item in registry["skills"] if item["name"] == "aigc-production-orchestrator")
        self.assertEqual(entry["skill_file"], "skills/aigc-production-orchestrator/SKILL.md")
        self.assertEqual(entry["entrypoints"]["image"], "main.generate_image")
        self.assertEqual(entry["entrypoints"]["video"], "main.generate_video")
        self.assertIn("断点续跑", entry["triggers"])
        self.assertEqual(entry["output_root"], "outputs")

    def test_skill_has_routing_gates_and_safe_delivery_contract(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        for required in (
            "## Route First",
            "## Stage Gates",
            "## Resumability And Idempotency",
            "## Evidence And Quality",
            "## Boundaries",
            "## Delivery Contract",
            "diagnose",
            "plan",
            "confirm",
            "execute",
            "quality_check",
            "manual_review",
            "API keys",
            "provider entrypoint",
        ):
            self.assertIn(required, skill)

    def test_skill_has_no_external_relative_references(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        relative_links = re.findall(r"\]\(([^)]+)\)", skill)
        self.assertEqual(relative_links, [])


if __name__ == "__main__":
    unittest.main()
