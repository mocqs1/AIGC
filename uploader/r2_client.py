"""Cloudflare R2 client with safe keys, bounded files, and explicit errors."""

from __future__ import annotations

import os
import logging
import mimetypes
import re
import stat
import time
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Callable, Iterator
from urllib.parse import quote

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    HTTPClientError,
    ReadTimeoutError,
)

from uploader.config import R2Config
from uploader.exceptions import R2UploadError, R2ValidationError


logger = logging.getLogger(__name__)

MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}
IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".webm"})
ALLOWED_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
PRIVATE_FILENAMES = frozenset({".env", ".env.example", "config.py", "credentials", "credentials.json"})
RETRYABLE_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
RETRYABLE_CLIENT_CODES = frozenset(
    {"InternalError", "RequestTimeout", "RequestTimeoutException", "ServiceUnavailable", "SlowDown", "Throttling", "ThrottlingException"}
)


def content_type_for(path_or_key: str | Path) -> str:
    suffix = Path(str(path_or_key)).suffix.lower()
    content_type = MIME_TYPES.get(suffix)
    if content_type is None:
        guessed, _ = mimetypes.guess_type(str(path_or_key))
        if suffix not in ALLOWED_EXTENSIONS or not guessed:
            raise R2ValidationError(f"Unsupported file type: {suffix or '<none>'}")
        content_type = guessed
    return content_type


def _safe_stem(stem: str) -> str:
    normalized = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode("ascii")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", normalized).strip("-._")
    return safe[:80] or "file"


def normalize_object_key(object_key: str) -> str:
    if not isinstance(object_key, str) or not object_key.strip():
        raise R2ValidationError("Object key must be a non-empty string")
    raw = object_key.strip().replace("\\", "/").lstrip("/")
    parts = PurePosixPath(raw).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise R2ValidationError("Object key contains an unsafe path segment")
    safe_parts = [_safe_stem(part) for part in parts[:-1]]
    filename = PurePosixPath(parts[-1])
    suffix = filename.suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise R2ValidationError(f"Unsupported file type: {suffix or '<none>'}")
    safe_name = _safe_stem(filename.stem) + suffix
    return "/".join([*safe_parts, safe_name])


def generate_object_key(path: str | Path, now: datetime | None = None) -> str:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise R2ValidationError(f"Unsupported file type: {suffix or '<none>'}")
    category = "images" if suffix in IMAGE_EXTENSIONS else "videos"
    current = now or datetime.now(timezone.utc)
    unique = uuid.uuid4().hex[:8]
    filename = f"{_safe_stem(source.stem)}_{current:%Y%m%d%H%M%S}_{unique}{suffix}"
    return f"{category}/{current:%Y/%m/%d}/{filename}"


def _is_link_or_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction and is_junction())


def _contains_link_or_junction(path: Path) -> bool:
    """Reject all reparse-point components, not just the final path."""
    absolute = path.absolute()
    parts = absolute.parts
    if not parts:
        return False
    current = Path(parts[0])
    for part in parts[1:]:
        current /= part
        try:
            if _is_link_or_junction(current):
                return True
        except OSError:
            # A missing/unreadable component is reported by the regular file check.
            continue
    return False


class _BoundedReader:
    """Expose only the fstat-verified byte range of a source file."""

    def __init__(self, stream: BinaryIO, size: int) -> None:
        self._stream = stream
        self._size = size
        self._remaining = size

    def read(self, size: int = -1) -> bytes:
        if self._remaining <= 0:
            return b""
        requested = self._remaining if size is None or size < 0 else min(size, self._remaining)
        data = self._stream.read(requested)
        if not data:
            raise OSError("Local file changed while it was being uploaded")
        self._remaining -= len(data)
        return data

    def seek(self, offset: int, whence: int = 0) -> int:
        position = self._stream.seek(offset, whence)
        if position < 0 or position > self._size:
            raise OSError("Local file changed while it was being uploaded")
        self._remaining = self._size - position
        return position

    def tell(self) -> int:
        return self._stream.tell()


class R2Client:
    """Upload media to one configured Cloudflare R2 bucket."""

    def __init__(
        self,
        config: R2Config | None = None,
        *,
        s3_client=None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config or R2Config.from_env()
        self._sleeper = sleeper
        self._s3 = s3_client or boto3.client(
            "s3",
            endpoint_url=self.config.endpoint_url,
            aws_access_key_id=self.config.access_key_id,
            aws_secret_access_key=self.config.secret_access_key,
            region_name="auto",
            config=BotoConfig(
                # Keep retry ownership here so the configured attempt limit is exact.
                retries={"total_max_attempts": 1, "mode": "standard"},
                signature_version="s3v4",
            ),
        )

    def public_url(self, object_key: str) -> str:
        normalized = normalize_object_key(object_key)
        return f"{self.config.public_base_url.rstrip('/')}/{quote(normalized, safe='/')}"

    def upload_file(self, file_path: str | Path, object_key: str | None = None, *, overwrite: bool = False) -> str:
        path = self._validate_local_file(file_path)
        key = normalize_object_key(object_key) if object_key is not None else generate_object_key(path)
        if object_key is not None and Path(key).suffix.lower() != path.suffix.lower():
            raise R2ValidationError("Object key extension must match the local file extension")
        content_type = content_type_for(key)
        self._ensure_target_available(key, overwrite=overwrite)
        try:
            with self._open_verified_file(path) as (stream, size):
                self._upload_stream(stream, key, content_type, size=size, overwrite=overwrite)
        except OSError as error:
            raise R2ValidationError(f"Could not read local file: {path}") from error
        return self.public_url(key)

    def upload_bytes(
        self,
        data: bytes,
        object_key: str,
        content_type: str | None = None,
        *,
        overwrite: bool = False,
    ) -> str:
        if not isinstance(data, bytes):
            raise R2ValidationError("data must be bytes")
        if not data:
            raise R2ValidationError("Cannot upload empty bytes")
        if len(data) > self.config.max_file_size_bytes:
            raise R2ValidationError(f"Upload exceeds maximum size of {self.config.max_file_size_bytes} bytes")
        key = normalize_object_key(object_key)
        resolved_type = content_type_for(key)
        if content_type is not None and content_type != resolved_type:
            raise R2ValidationError(f"Content type must match file extension ({resolved_type})")
        self._ensure_target_available(key, overwrite=overwrite)
        self._put_object(data, key, resolved_type, size=len(data), overwrite=overwrite)
        return self.public_url(key)

    def delete_file(self, object_key: str) -> None:
        key = normalize_object_key(object_key)
        self._call_with_retry("delete", lambda: self._s3.delete_object(Bucket=self.config.bucket_name, Key=key))

    def object_exists(self, object_key: str) -> bool:
        key = normalize_object_key(object_key)
        try:
            self._s3.head_object(Bucket=self.config.bucket_name, Key=key)
            return True
        except ClientError as error:
            status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            code = str(error.response.get("Error", {}).get("Code", ""))
            if status == 404 or code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise R2UploadError("R2 existence check failed") from error
        except BotoCoreError as error:
            raise R2UploadError("R2 existence check failed") from error

    def _ensure_target_available(self, key: str, *, overwrite: bool) -> None:
        if not overwrite and self.object_exists(key):
            raise R2ValidationError("Object key already exists; pass overwrite=True to replace it")

    def _validate_local_file(self, file_path: str | Path) -> Path:
        path = Path(file_path).expanduser().absolute()
        if path.name.lower() in PRIVATE_FILENAMES or path.name.lower().startswith(".env"):
            raise R2ValidationError("Private configuration files cannot be uploaded")
        if _contains_link_or_junction(path):
            raise R2ValidationError("Symlinks and junctions cannot be uploaded")
        try:
            metadata = path.stat()
        except OSError as error:
            raise R2ValidationError(f"File does not exist: {path}") from error
        if not stat.S_ISREG(metadata.st_mode):
            raise R2ValidationError(f"Path is not a regular file: {path}")
        content_type_for(path)
        size = metadata.st_size
        if size <= 0:
            raise R2ValidationError("Cannot upload an empty file")
        if size > self.config.max_file_size_bytes:
            raise R2ValidationError(f"File exceeds maximum size of {self.config.max_file_size_bytes} bytes")
        return path

    @contextmanager
    def _open_verified_file(self, path: Path) -> Iterator[tuple[_BoundedReader, int]]:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as error:
            raise R2ValidationError(f"Could not open local file: {path}") from error
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise R2ValidationError(f"Path is not a regular file: {path}")
            size = metadata.st_size
            if size <= 0:
                raise R2ValidationError("Cannot upload an empty file")
            if size > self.config.max_file_size_bytes:
                raise R2ValidationError(f"File exceeds maximum size of {self.config.max_file_size_bytes} bytes")
            yield _BoundedReader(stream, size), size

    def _upload_stream(self, stream: _BoundedReader, key: str, content_type: str, *, size: int, overwrite: bool) -> None:
        self._put_object(stream, key, content_type, size=size, overwrite=overwrite, rewind=stream)

    def _put_object(
        self,
        body: bytes | _BoundedReader,
        key: str,
        content_type: str,
        *,
        size: int,
        overwrite: bool,
        rewind: _BoundedReader | None = None,
    ) -> None:
        arguments = {
            "Bucket": self.config.bucket_name,
            "Key": key,
            "Body": body,
            "ContentType": content_type,
            "ContentLength": size,
        }
        if not overwrite:
            # The conditional write prevents a preflight HEAD race from replacing an object.
            arguments["IfNoneMatch"] = "*"
        self._call_with_retry("upload", lambda: self._s3.put_object(**arguments), rewind=rewind)

    @staticmethod
    def _is_retryable(error: BotoCoreError | ClientError) -> bool:
        if isinstance(error, (ConnectionClosedError, ConnectTimeoutError, EndpointConnectionError, HTTPClientError, ReadTimeoutError)):
            return True
        if isinstance(error, ClientError):
            status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            code = str(error.response.get("Error", {}).get("Code", ""))
            return status in RETRYABLE_HTTP_STATUSES or code in RETRYABLE_CLIENT_CODES
        return False

    @staticmethod
    def _is_precondition_failure(error: ClientError) -> bool:
        status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        code = str(error.response.get("Error", {}).get("Code", ""))
        return status == 412 or code in {"PreconditionFailed", "412"}

    def _call_with_retry(self, action: str, call: Callable[[], object], rewind: _BoundedReader | None = None) -> object:
        last_error: Exception | None = None
        for attempt in range(1, self.config.max_attempts + 1):
            if rewind is not None:
                rewind.seek(0)
            try:
                return call()
            except (BotoCoreError, ClientError) as error:
                if isinstance(error, ClientError) and self._is_precondition_failure(error):
                    raise R2ValidationError("Object key already exists; pass overwrite=True to replace it") from error
                if not self._is_retryable(error):
                    raise R2UploadError(f"R2 {action} failed") from error
                last_error = error
                logger.warning("R2 %s attempt %s/%s failed", action, attempt, self.config.max_attempts)
                if attempt < self.config.max_attempts:
                    self._sleeper(float(2 ** (attempt - 1)))
        raise R2UploadError(f"R2 {action} failed after {self.config.max_attempts} attempts") from last_error


CloudflareR2Client = R2Client
