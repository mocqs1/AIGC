"""Mock-only tests for the Cloudflare R2 upload subsystem."""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import tempfile
import unittest
import urllib.request
import uuid
from base64 import b64decode
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from botocore.exceptions import ClientError, EndpointConnectionError

from uploader.config import R2Config
from uploader.exceptions import R2ConfigurationError, R2UploadError, R2ValidationError
from uploader.r2_client import R2Client, content_type_for, generate_object_key, normalize_object_key
from uploader.service import upload_image, upload_video
from uploader.upload import main as cli_main


def test_config(**overrides) -> R2Config:
    values = {
        "account_id": "account",
        "access_key_id": "access",
        "secret_access_key": "secret",
        "bucket_name": "media",
        "public_base_url": "https://img.example.com",
        "max_file_size_bytes": 1024,
        "max_attempts": 3,
    }
    values.update(overrides)
    return R2Config(**values)


class FakeS3:
    def __init__(self) -> None:
        self.uploads = []
        self.puts = []
        self.deleted = []
        self.existing = set()
        self.upload_failures = 0
        self.client_error = None
        self.create_during_head = set()

    def put_object(self, **kwargs):
        if self.upload_failures:
            self.upload_failures -= 1
            raise EndpointConnectionError(endpoint_url="https://r2.example.test")
        if self.client_error is not None:
            raise self.client_error
        key = kwargs["Key"]
        if kwargs.get("IfNoneMatch") == "*" and key in self.existing:
            raise ClientError(
                {"Error": {"Code": "PreconditionFailed", "Message": "Already exists"}, "ResponseMetadata": {"HTTPStatusCode": 412}},
                "PutObject",
            )
        body = kwargs["Body"]
        data = body.read() if hasattr(body, "read") else body
        self.uploads.append((data, kwargs["Bucket"], key, {"ContentType": kwargs["ContentType"], "ContentLength": kwargs["ContentLength"]}))
        self.puts.append(kwargs)
        self.existing.add(key)

    def delete_object(self, **kwargs):
        self.deleted.append(kwargs)
        self.existing.discard(kwargs["Key"])

    def head_object(self, **kwargs):
        key = kwargs["Key"]
        if key not in self.existing:
            self.existing.update(self.create_during_head)
            raise ClientError(
                {"Error": {"Code": "404", "Message": "Not Found"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
                "HeadObject",
            )
        return {}


class R2ConfigTests(unittest.TestCase):
    def test_reads_complete_environment(self) -> None:
        env = {
            "R2_ACCOUNT_ID": "abc",
            "R2_ACCESS_KEY_ID": "key",
            "R2_SECRET_ACCESS_KEY": "secret",
            "R2_BUCKET_NAME": "bucket",
            "R2_PUBLIC_BASE_URL": "https://img.example.com/",
        }
        with patch.dict(os.environ, env, clear=True):
            config = R2Config.from_env()
        self.assertEqual(config.endpoint_url, "https://abc.r2.cloudflarestorage.com")
        self.assertEqual(config.public_base_url, "https://img.example.com")
        self.assertNotIn("'secret'", str(config.redacted()).lower())
        self.assertNotIn("'secret'", repr(config).lower())

    def test_reports_missing_environment_names_without_values(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch("uploader.config.load_dotenv"):
            with self.assertRaisesRegex(R2ConfigurationError, "R2_ACCOUNT_ID.*R2_PUBLIC_BASE_URL"):
                R2Config.from_env()

    def test_rejects_non_https_public_base_url(self) -> None:
        env = {
            "R2_ACCOUNT_ID": "abc",
            "R2_ACCESS_KEY_ID": "key",
            "R2_SECRET_ACCESS_KEY": "secret",
            "R2_BUCKET_NAME": "bucket",
            "R2_PUBLIC_BASE_URL": "http://img.example.com",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(R2ConfigurationError, "HTTPS"):
                R2Config.from_env()

    def test_rejects_public_base_url_with_path_or_credentials(self) -> None:
        base = {
            "R2_ACCOUNT_ID": "abc",
            "R2_ACCESS_KEY_ID": "key",
            "R2_SECRET_ACCESS_KEY": "secret",
            "R2_BUCKET_NAME": "bucket",
        }
        for value in ("https://img.example.com/media", "https://user:pass@img.example.com"):
            with self.subTest(value=value), patch.dict(os.environ, {**base, "R2_PUBLIC_BASE_URL": value}, clear=True):
                with self.assertRaisesRegex(R2ConfigurationError, "HTTPS origin"):
                    R2Config.from_env()


class R2ClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeS3()
        self.sleeps = []
        self.client = R2Client(test_config(), s3_client=self.fake, sleeper=self.sleeps.append)

    def test_mime_types(self) -> None:
        expected = {
            "a.jpg": "image/jpeg",
            "a.jpeg": "image/jpeg",
            "a.png": "image/png",
            "a.webp": "image/webp",
            "a.gif": "image/gif",
            "a.mp4": "video/mp4",
            "a.mov": "video/quicktime",
            "a.webm": "video/webm",
        }
        for name, mime in expected.items():
            with self.subTest(name=name):
                self.assertEqual(content_type_for(name), mime)

    def test_object_key_is_unique_safe_and_categorized(self) -> None:
        moment = datetime(2026, 8, 25, 14, 30, 25, tzinfo=timezone.utc)
        first = generate_object_key("output/images/产品 hero 001.jpg", now=moment)
        second = generate_object_key("output/images/产品 hero 001.jpg", now=moment)
        self.assertRegex(first, r"^images/2026/08/25/hero-001_20260825143025_[0-9a-f]{8}\.jpg$")
        self.assertNotEqual(first, second)
        self.assertTrue(generate_object_key("clip.mp4", now=moment).startswith("videos/2026/08/25/"))

    def test_object_key_rejects_traversal_and_unsupported_type(self) -> None:
        for key in ("../secret.jpg", "images/../secret.jpg", "images/file.exe"):
            with self.subTest(key=key), self.assertRaises(R2ValidationError):
                normalize_object_key(key)

    def test_public_url_encodes_and_avoids_double_slashes(self) -> None:
        self.assertEqual(
            self.client.public_url("images/product hero.jpg"),
            "https://img.example.com/images/product-hero.jpg",
        )

    def test_upload_file_and_service_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "image.jpg"
            video = Path(temp_dir) / "video.mp4"
            image.write_bytes(b"jpeg")
            video.write_bytes(b"mp4")
            image_url = upload_image(image, "images/image.jpg", client=self.client)
            video_url = upload_video(video, "videos/video.mp4", client=self.client)
        self.assertEqual(image_url, "https://img.example.com/images/image.jpg")
        self.assertEqual(video_url, "https://img.example.com/videos/video.mp4")
        self.assertEqual(self.fake.uploads[0][3]["ContentType"], "image/jpeg")
        self.assertEqual(self.fake.uploads[1][3]["ContentType"], "video/mp4")
        self.assertEqual(self.fake.uploads[0][3]["ContentLength"], 4)

    def test_upload_bytes_delete_and_exists(self) -> None:
        url = self.client.upload_bytes(b"image", "images/a.png")
        self.assertEqual(url, "https://img.example.com/images/a.png")
        self.assertEqual(self.fake.puts[0]["ContentType"], "image/png")
        self.assertTrue(self.client.object_exists("images/a.png"))
        self.client.delete_file("images/a.png")
        self.assertEqual(self.fake.deleted[0]["Key"], "images/a.png")
        self.assertFalse(self.client.object_exists("images/a.png"))

    def test_missing_private_unsupported_and_oversized_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            private = root / ".env"
            private.write_text("secret", encoding="utf-8")
            unsupported = root / "notes.txt"
            unsupported.write_text("notes", encoding="utf-8")
            oversized = root / "large.jpg"
            oversized.write_bytes(b"x" * 1025)
            cases = [root / "missing.jpg", private, unsupported, oversized]
            for path in cases:
                with self.subTest(path=path), self.assertRaises(R2ValidationError):
                    self.client.upload_file(path)

    def test_symlink_is_rejected_when_supported_by_platform(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "target.jpg"
            link = root / "link.jpg"
            target.write_bytes(b"jpeg")
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("creating symlinks is not permitted on this host")
            with self.assertRaisesRegex(R2ValidationError, "Symlinks"):
                self.client.upload_file(link)

    def test_symlinked_parent_directory_is_rejected_when_supported_by_platform(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "target"
            target.mkdir()
            (target / "image.jpg").write_bytes(b"jpeg")
            linked_directory = root / "linked"
            try:
                linked_directory.symlink_to(target, target_is_directory=True)
            except OSError:
                self.skipTest("creating directory symlinks is not permitted on this host")
            with self.assertRaisesRegex(R2ValidationError, "Symlinks"):
                self.client.upload_file(linked_directory / "image.jpg")

    @unittest.skipUnless(os.name == "nt", "Windows junction test")
    def test_junction_parent_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "target"
            target.mkdir()
            (target / "image.jpg").write_bytes(b"jpeg")
            junction = root / "junction"
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                self.skipTest("creating junctions is not permitted on this host")
            try:
                self.assertTrue(junction.is_junction())
                with self.assertRaisesRegex(R2ValidationError, "junctions"):
                    self.client.upload_file(junction / "image.jpg")
            finally:
                if junction.exists() or junction.is_junction():
                    junction.rmdir()

    def test_explicit_key_extension_must_match_source_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "image.jpg"
            image.write_bytes(b"jpeg")
            with self.assertRaisesRegex(R2ValidationError, "extension"):
                self.client.upload_file(image, "videos/claimed-video.mp4")

    def test_existing_key_is_protected_unless_overwrite_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "image.jpg"
            image.write_bytes(b"jpeg")
            self.client.upload_file(image, "images/image.jpg")
            with self.assertRaisesRegex(R2ValidationError, "already exists"):
                self.client.upload_file(image, "images/image.jpg")
            self.client.upload_file(image, "images/image.jpg", overwrite=True)
        self.assertEqual(len(self.fake.uploads), 2)
        self.assertNotIn("IfNoneMatch", self.fake.puts[-1])

    def test_conditional_write_rejects_a_concurrent_key_collision(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "image.jpg"
            image.write_bytes(b"jpeg")
            self.fake.create_during_head.add("images/image.jpg")
            with self.assertRaisesRegex(R2ValidationError, "already exists"):
                self.client.upload_file(image, "images/image.jpg")
        self.assertEqual(self.fake.uploads, [])

    def test_upload_retries_twice_then_succeeds_and_rewinds(self) -> None:
        self.fake.upload_failures = 2
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "image.jpg"
            image.write_bytes(b"jpeg")
            self.client.upload_file(image, "images/image.jpg")
        self.assertEqual(self.sleeps, [1.0, 2.0])
        self.assertEqual(self.fake.uploads[0][0], b"jpeg")

    def test_permanent_client_error_is_not_retried(self) -> None:
        self.fake.client_error = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Denied"}, "ResponseMetadata": {"HTTPStatusCode": 403}},
            "PutObject",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "image.jpg"
            image.write_bytes(b"jpeg")
            with self.assertRaises(R2UploadError):
                self.client.upload_file(image, "images/image.jpg")
        self.assertEqual(self.sleeps, [])

    def test_upload_failure_is_clear_and_hides_credentials(self) -> None:
        self.fake.upload_failures = 3
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "image.jpg"
            image.write_bytes(b"jpeg")
            with self.assertRaises(R2UploadError) as raised:
                self.client.upload_file(image, "images/image.jpg")
        self.assertNotIn("secret", str(raised.exception).lower())


class R2CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeS3()
        self.client = R2Client(test_config(max_file_size_bytes=2048), s3_client=self.fake, sleeper=lambda _: None)

    def test_cli_single_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "product.jpg"
            image.write_bytes(b"jpeg")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = cli_main([str(image), "--key", "products/product-001.jpg"], client=self.client)
        self.assertEqual(status, 0)
        self.assertIn("SUCCESS", output.getvalue())
        self.assertIn("https://img.example.com/products/product-001.jpg", output.getvalue())

    def test_cli_reports_overwrite_mode_and_existing_key_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "product.jpg"
            image.write_bytes(b"jpeg")
            first_status = cli_main([str(image), "--key", "products/product-001.jpg"], client=self.client)
            self.assertEqual(first_status, 0)
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                blocked_status = cli_main([str(image), "--key", "products/product-001.jpg"], client=self.client)
            self.assertEqual(blocked_status, 1)
            self.assertIn("rerun with --overwrite", error.getvalue())
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                overwrite_status = cli_main(
                    [str(image), "--key", "products/product-001.jpg", "--overwrite"],
                    client=self.client,
                )
        self.assertEqual(overwrite_status, 0)
        self.assertIn("Overwrite mode:\nENABLED", output.getvalue())

    def test_cli_batch_uploads_supported_files_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "one.jpg").write_bytes(b"one")
            (root / "two.webp").write_bytes(b"two")
            (root / "ignore.txt").write_text("ignore", encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = cli_main(["--dir", str(root)], client=self.client)
        self.assertEqual(status, 0)
        self.assertEqual(len(self.fake.uploads), 2)
        self.assertIn("one.jpg\tSUCCESS", output.getvalue())
        self.assertIn("two.webp\tSUCCESS", output.getvalue())
        self.assertNotIn("ignore.txt", output.getvalue())

    def test_cli_overwrite_requires_an_explicit_key(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli_main(["input/product.jpg", "--overwrite"], client=self.client)


class RealR2SmokeTest(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("RUN_R2_SMOKE_TEST") == "1", "set RUN_R2_SMOKE_TEST=1 explicitly")
    def test_real_upload_is_public_and_deleted(self) -> None:
        client = R2Client()
        key = f"smoke/aigc-r2-{uuid.uuid4().hex}.png"
        uploaded = False
        try:
            url = client.upload_bytes(
                b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/9ZRcrwAAAABJRU5ErkJggg=="),
                key,
            )
            uploaded = True
            self.assertTrue(client.object_exists(key))
            with urllib.request.urlopen(url, timeout=15) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers.get_content_type(), "image/png")
        finally:
            if uploaded:
                client.delete_file(key)
                self.assertFalse(client.object_exists(key))


if __name__ == "__main__":
    unittest.main()
