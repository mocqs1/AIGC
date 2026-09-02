import json
import tempfile
import unittest
from pathlib import Path

from skills.mono_color_poster.runtime import PosterRequestError, build_prompt, generate_image


ROOT = Path(__file__).resolve().parents[1]


class FakeClient:
    def __init__(self):
        self.submitted = []

    def submit_generation(self, prompt, **options):
        self.submitted.append((prompt, options))
        return {"task_id": "poster-task"}

    def query_task(self, task_id):
        return {"status": "completed"}

    def get_result(self, task_id):
        return b"poster-image"


class MonoColorPosterTests(unittest.TestCase):
    def test_registry_and_workflow_are_present(self):
        registry = json.loads((ROOT / "skills" / "registry.json").read_text(encoding="utf-8"))
        entry = next(item for item in registry["skills"] if item["name"] == "mono-color-poster")
        self.assertEqual(entry["entrypoints"]["image"], "skills.mono_color_poster.runtime.generate_image")
        self.assertIn("单色海报", entry["triggers"])
        workflow = json.loads(
            (ROOT / "skills" / "mono-color-poster" / "workflows" / "mono_color_poster.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(workflow["type"], "image")
        self.assertEqual(workflow["output_root"], "outputs/posters")
        self.assertEqual(workflow["reference_images"]["max"], 1)

    def test_prompt_is_deterministic_and_has_two_ink_contract(self):
        request = {
            "subject": "a city street lamp",
            "intent": "an observed cultural note",
            "exact_text": "NIGHT WALK",
            "palette": "palette_cobalt_terracotta",
            "layout": "composition_editorial_cover",
        }
        prompt = build_prompt(request)
        self.assertEqual(prompt, build_prompt(request))
        self.assertEqual(prompt.count("CANVAS AND INK"), 1)
        self.assertIn("exactly two printing inks", prompt)
        self.assertIn("Cobalt #2148B8", prompt)
        self.assertIn("Terracotta Orange #C65F38", prompt)
        self.assertIn("NIGHT WALK", prompt)
        self.assertIn("stable imperfection seed", prompt)

    def test_pure_one_ink_palette_is_supported(self):
        prompt = build_prompt({"subject": "an archival book", "palette": "palette_charcoal"})
        self.assertIn("exactly one printing ink", prompt)
        self.assertIn("no second ink is allowed", prompt)
        self.assertNotIn("accent", prompt.split("CANVAS AND INK", 1)[1].split("\n\n", 1)[0])

    def test_generate_binds_one_reference_and_writes_recipe(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as output_dir:
            manifest = generate_image(
                {
                    "subject": "a folded travel map",
                    "exact_text": "OPEN DIRECTIONS",
                    "ratio": "3:4",
                    "palette": "palette_charcoal_signal_red",
                },
                image="reference.png",
                provider="hermes",
                client=client,
                output_dir=output_dir,
            )
            output = Path(manifest["outputs"][0])
            self.assertEqual(output.read_bytes(), b"poster-image")
            self.assertEqual(manifest["skill"], "mono-color")
            self.assertEqual(manifest["workflow"], "mono_color_poster")
            self.assertTrue(manifest["reference_bound"])
            self.assertEqual(manifest["recipe"]["palette"], "palette_charcoal_signal_red")
            self.assertTrue((Path(output_dir) / "prompt.txt").is_file())
        self.assertEqual(client.submitted[0][1]["image"], "reference.png")
        self.assertEqual(client.submitted[0][1]["aspect_ratio"], "portrait")

    def test_invalid_palette_and_multiple_references_are_rejected(self):
        with self.assertRaises(PosterRequestError):
            build_prompt({"subject": "a poster", "palette": "rainbow"})
        with self.assertRaisesRegex(PosterRequestError, "at most one"):
            generate_image({"subject": "a poster"}, references=["a.png"], client=FakeClient())


if __name__ == "__main__":
    unittest.main()
