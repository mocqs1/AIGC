"""Shared still-image compressor for provider reference payloads.

Phone-camera JPEGs at 6000x4000 / 20MB+ encode to tens of megabytes of JSON
and fail in about two seconds on Seedream and GPT Image. Every image module
must shrink local files, data URLs, and downloaded HTTPS stills to the same
4096px / 8MB cap before they leave the workbench. The ladder aims for 4MB.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

IMAGE_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

PROVIDER_IMAGE_MAX_EDGE = 4096
PROVIDER_IMAGE_MAX_BYTES = 8 * 1024 * 1024
PROVIDER_IMAGE_TARGET_BYTES = 4 * 1024 * 1024
PROVIDER_IMAGE_EDGES = (4096, 3072, 2048, 1536)
PROVIDER_IMAGE_JPEG_QUALITIES = (3, 5, 8, 12)
PROVIDER_IMAGE_FALLBACK_EDGE = 2048
PROVIDER_IMAGE_JPEG_QUALITY = 3
_LOGGER = logging.getLogger(__name__)


class ImageTooLargeError(ValueError):
    """Raised when a still cannot be reduced to the provider size cap."""

    def __init__(self, message: str = "reference image exceeds provider size limits") -> None:
        super().__init__(message)
        self.provider_code = "input_image_too_large"
        self.provider_message = "each image must be at most 4096px and 8MB"


@dataclass(frozen=True)
class PreparedImage:
    """Bytes and MIME type that already satisfy the provider size cap."""

    data: bytes
    mime: str

    def as_data_url(self) -> str:
        return f"data:{self.mime};base64,{base64.b64encode(self.data).decode('ascii')}"


def guess_image_mime(name: str, fallback: str = "application/octet-stream") -> str:
    suffix = Path(name).suffix.lower()
    return mimetypes.guess_type(name)[0] or IMAGE_MIME_TYPES.get(suffix, fallback)


def image_dimensions(data: bytes) -> tuple[int, int] | None:
    """Return pixel size for common still formats without Pillow."""
    if len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n"):
        return struct.unpack(">II", data[16:24])
    if len(data) >= 10 and data[:6] in {b"GIF87a", b"GIF89a"}:
        return struct.unpack("<HH", data[6:10])
    if len(data) >= 30 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        if data[12:16] == b"VP8X":
            return (
                1 + int.from_bytes(data[24:27], "little"),
                1 + int.from_bytes(data[27:30], "little"),
            )
        if data[12:16] == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
            return struct.unpack("<HH", data[26:30])
    if data[:2] == b"\xff\xd8":
        index = 2
        length = len(data)
        while index + 8 < length:
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
                index += 2
                continue
            if marker == 0x00:
                index += 1
                continue
            segment = struct.unpack(">H", data[index + 2 : index + 4])[0]
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                height, width = struct.unpack(">HH", data[index + 5 : index + 9])
                return width, height
            if segment < 2:
                break
            index += 2 + segment
    return None


def exceeds_provider_image_limits(data: bytes) -> bool:
    if len(data) > PROVIDER_IMAGE_MAX_BYTES:
        return True
    dimensions = image_dimensions(data)
    return bool(dimensions and max(dimensions) > PROVIDER_IMAGE_MAX_EDGE)


def transcode_image_bytes(
    data: bytes,
    max_edge: int,
    *,
    quality: int = PROVIDER_IMAGE_JPEG_QUALITY,
    source_name: str = "source",
) -> tuple[bytes, str] | None:
    """Downscale still bytes with ffmpeg. Returns JPEG bytes or None."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not data:
        return None
    suffix = Path(source_name).suffix.lower()
    if suffix not in IMAGE_MIME_TYPES:
        suffix = ".bin"
    with tempfile.TemporaryDirectory(prefix="aigc-image-") as temp_root:
        source = Path(temp_root) / f"source{suffix}"
        destination = Path(temp_root) / "prepared.jpg"
        source.write_bytes(data)
        try:
            completed = subprocess.run(
                [
                    ffmpeg,
                    "-nostdin",
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    str(source),
                    "-vf",
                    f"scale={max_edge}:{max_edge}:force_original_aspect_ratio=decrease",
                    "-frames:v",
                    "1",
                    "-q:v",
                    str(quality),
                    str(destination),
                ],
                capture_output=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0 or not destination.is_file():
            return None
        prepared = destination.read_bytes()
        if not prepared:
            return None
        return prepared, "image/jpeg"


def transcode_local_image(
    path: str,
    max_edge: int,
    *,
    quality: int = PROVIDER_IMAGE_JPEG_QUALITY,
) -> tuple[bytes, str] | None:
    artifact = Path(path)
    try:
        data = artifact.read_bytes()
    except OSError:
        return None
    return transcode_image_bytes(data, max_edge, quality=quality, source_name=artifact.name)


def _select_prepared_image(data: bytes, source_name: str) -> tuple[bytes, str] | None:
    """Walk the quality/edge ladder and keep the smallest acceptable JPEG."""
    best: tuple[bytes, str] | None = None
    for max_edge in PROVIDER_IMAGE_EDGES:
        for quality in PROVIDER_IMAGE_JPEG_QUALITIES:
            prepared = transcode_image_bytes(
                data,
                max_edge,
                quality=quality,
                source_name=source_name,
            )
            if prepared is None or exceeds_provider_image_limits(prepared[0]):
                continue
            if len(prepared[0]) <= PROVIDER_IMAGE_TARGET_BYTES:
                return prepared
            if best is None or len(prepared[0]) < len(best[0]):
                best = prepared
    return best


def prepare_image_bytes(data: bytes, *, source_name: str = "reference", mime: str | None = None) -> PreparedImage:
    """Return still bytes that fit the provider cap, transcoding when needed."""
    guessed = mime or guess_image_mime(source_name)
    if not data:
        raise ImageTooLargeError("reference image is empty")
    if not exceeds_provider_image_limits(data):
        return PreparedImage(data, guessed)
    prepared = _select_prepared_image(data, source_name)
    if prepared is None:
        raise ImageTooLargeError()
    _LOGGER.info(
        "image downscaled original_bytes=%s prepared_bytes=%s mime=%s source=%s",
        len(data),
        len(prepared[0]),
        prepared[1],
        Path(source_name).name,
    )
    return PreparedImage(prepared[0], prepared[1])


def prepare_local_image(path: str) -> PreparedImage:
    artifact = Path(path)
    try:
        original = artifact.read_bytes()
    except OSError as error:
        raise OSError(f"could not read reference image: {error}") from error
    return prepare_image_bytes(original, source_name=artifact.name, mime=guess_image_mime(artifact.name))


def encode_local_image(path: str) -> str:
    return prepare_local_image(path).as_data_url()
