import unittest

from agents.prompt_agent import generate_prompt


class GeneratePromptTests(unittest.TestCase):
    def test_image_request_includes_inputs_and_still_direction(self) -> None:
        prompt = generate_prompt(
            {
                "product": "a glass perfume bottle",
                "scene": "a sunlit marble vanity",
                "style": "luxury editorial photography",
                "type": "IMAGE",
            }
        )

        self.assertIn("a glass perfume bottle", prompt)
        self.assertIn("a sunlit marble vanity", prompt)
        self.assertIn("luxury editorial photography", prompt)
        self.assertIn("image", prompt)
        self.assertIn("still image", prompt)
        self.assertIn("composition", prompt)
        self.assertIn("lighting", prompt)
        self.assertIn("materials", prompt)


    def test_image_prompt_rewrites_bedroom_to_studio_and_locks_intimate_apparel(self) -> None:
        prompt = generate_prompt(
            {
                "product": "black high-waist shapewear",
                "scene": "premium bedroom",
                "style": "SKIMS luxury fashion advertisement",
                "type": "image",
            }
        )

        self.assertIn("black high-waist shapewear", prompt)
        self.assertIn("clean neutral studio", prompt)
        self.assertNotIn("premium bedroom", prompt)
        self.assertNotIn("SKIMS", prompt)
        self.assertIn("INTIMATE-APPAREL SAFETY LOCK", prompt)
        self.assertIn("product-only", prompt)
        self.assertIn("headless mannequin", prompt)

    def test_image_prompt_rewrites_chinese_bedroom_defaults(self) -> None:
        prompt = generate_prompt(
            {
                "product": "黑色高腰塑身衣",
                "scene": "高级卧室",
                "style": "SKIMS 高级广告感",
                "type": "image",
            }
        )

        self.assertIn("黑色高腰塑身衣", prompt)
        self.assertIn("clean neutral studio", prompt)
        self.assertNotIn("高级卧室", prompt)
        self.assertNotIn("SKIMS", prompt)
        self.assertIn("INTIMATE-APPAREL SAFETY LOCK", prompt)

    def test_video_request_includes_motion_and_camera_direction(self) -> None:
        prompt = generate_prompt(
            {
                "product": "a red running shoe",
                "scene": "a rain-slick city street",
                "style": "dynamic sports commercial",
                "type": "video",
            }
        )

        self.assertIn("a red running shoe", prompt)
        self.assertIn("a rain-slick city street", prompt)
        self.assertIn("dynamic sports commercial", prompt)
        self.assertIn("video", prompt)
        self.assertIn("camera movement", prompt)
        self.assertIn("subject motion", prompt)
        self.assertIn("environmental movement", prompt)

    def test_missing_and_blank_fields_raise_value_error(self) -> None:
        fields = ("product", "scene", "style", "type")
        for field in fields:
            request = {
                "product": "product",
                "scene": "scene",
                "style": "style",
                "type": "image",
            }
            del request[field]
            with self.subTest(missing=field):
                with self.assertRaisesRegex(ValueError, field):
                    generate_prompt(request)

            request[field] = "   "
            with self.subTest(blank=field):
                with self.assertRaisesRegex(ValueError, field):
                    generate_prompt(request)

    def test_unsupported_type_raises_value_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "type"):
            generate_prompt(
                {
                    "product": "a lamp",
                    "scene": "a studio",
                    "style": "minimal product design",
                    "type": "audio",
                }
            )

    def test_non_mapping_request_raises_useful_value_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "request.*mapping"):
            generate_prompt(None)  # type: ignore[arg-type]

    def test_non_string_field_values_raise_useful_value_error(self) -> None:
        for field in ("product", "scene", "style", "type"):
            request = {
                "product": "product",
                "scene": "scene",
                "style": "style",
                "type": "image",
            }
            request[field] = 42  # type: ignore[assignment]
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, f"{field}.*non-empty string"):
                    generate_prompt(request)

    def test_outer_whitespace_is_normalized(self) -> None:
        prompt = generate_prompt(
            {
                "product": "  a glass bottle  ",
                "scene": "  a studio  ",
                "style": "  clean product photography  ",
                "type": "  IMAGE  ",
            }
        )

        self.assertIn("photograph of a glass bottle. Setting: a studio.", prompt)
        self.assertNotIn("  a glass bottle  ", prompt)


if __name__ == "__main__":
    unittest.main()
