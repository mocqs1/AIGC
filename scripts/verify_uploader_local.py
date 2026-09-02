"""Local, no-network verification of uploader CLI paths and media types."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from uploader.config import R2Config
from uploader.r2_client import R2Client
from uploader.upload import main


@dataclass
class RecordingS3:
    uploads: list[tuple[str, str]] = field(default_factory=list)

    existing: set[str] = field(default_factory=set)

    def put_object(self, **kwargs):  # type: ignore[no-untyped-def]
        body = kwargs["Body"]
        data = body.read() if hasattr(body, "read") else body
        if not data:
            raise ValueError("fixture must not be empty")
        self.existing.add(kwargs["Key"])
        self.uploads.append((kwargs["Key"], kwargs["ContentType"]))

    def head_object(self, **kwargs):  # type: ignore[no-untyped-def]
        from botocore.exceptions import ClientError

        if kwargs["Key"] not in self.existing:
            raise ClientError(
                {"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
                "HeadObject",
            )


def run() -> int:
    config = R2Config(
        account_id="local-test",
        access_key_id="local-test",
        secret_access_key="local-test",
        bucket_name="local-test",
        public_base_url="https://img.example.com",
    )
    storage = RecordingS3()
    client = R2Client(config, s3_client=storage, sleeper=lambda _: None)
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        input_dir = root / "input"
        images_dir = root / "output" / "images"
        videos_dir = root / "output" / "videos"
        input_dir.mkdir()
        images_dir.mkdir(parents=True)
        videos_dir.mkdir(parents=True)
        product = input_dir / "product.jpg"
        image = images_dir / "image_001.png"
        video = videos_dir / "video_001.mp4"
        product.write_bytes(b"jpeg-fixture")
        image.write_bytes(b"png-fixture")
        video.write_bytes(b"mp4-fixture")
        checks = (
            [str(product), "--key", "images/product.jpg"],
            [str(image), "--key", "images/image_001.png"],
            [str(video), "--key", "videos/video_001.mp4"],
            ["--dir", str(images_dir)],
        )
        for command in checks:
            if main(command, client=client) != 0:
                return 1
    expected = {"image/jpeg", "image/png", "video/mp4"}
    actual = {content_type for _, content_type in storage.uploads}
    if not expected.issubset(actual):
        raise AssertionError(f"missing media types: {expected - actual}")
    print(f"LOCAL_VERIFY_OK uploads={len(storage.uploads)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
