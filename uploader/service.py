"""Small workflow-facing API for uploading AIGC output media."""

from __future__ import annotations

from pathlib import Path

from uploader.exceptions import R2ValidationError
from uploader.r2_client import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, R2Client


def upload_file(
    path: str | Path,
    object_key: str | None = None,
    *,
    overwrite: bool = False,
    client: R2Client | None = None,
) -> str:
    return (client or R2Client()).upload_file(path, object_key, overwrite=overwrite)


def upload_image(
    path: str | Path,
    object_key: str | None = None,
    *,
    overwrite: bool = False,
    client: R2Client | None = None,
) -> str:
    if Path(path).suffix.lower() not in IMAGE_EXTENSIONS:
        raise R2ValidationError("upload_image only accepts supported image files")
    return upload_file(path, object_key, overwrite=overwrite, client=client)


def upload_video(
    path: str | Path,
    object_key: str | None = None,
    *,
    overwrite: bool = False,
    client: R2Client | None = None,
) -> str:
    if Path(path).suffix.lower() not in VIDEO_EXTENSIONS:
        raise R2ValidationError("upload_video only accepts supported video files")
    return upload_file(path, object_key, overwrite=overwrite, client=client)
