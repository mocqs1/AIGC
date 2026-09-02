"""Configuration for Cloudflare R2's S3-compatible API."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from uploader.exceptions import R2ConfigurationError


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=False)


@dataclass(frozen=True)
class R2Config:
    account_id: str
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)
    bucket_name: str
    public_base_url: str
    max_file_size_bytes: int = 500 * 1024 * 1024
    max_attempts: int = 3

    @classmethod
    def from_env(cls) -> "R2Config":
        import os

        names = (
            "R2_ACCOUNT_ID",
            "R2_ACCESS_KEY_ID",
            "R2_SECRET_ACCESS_KEY",
            "R2_BUCKET_NAME",
            "R2_PUBLIC_BASE_URL",
        )
        values = {name: os.environ.get(name, "").strip() for name in names}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise R2ConfigurationError("Missing R2 configuration: " + ", ".join(missing))

        base_url = values["R2_PUBLIC_BASE_URL"].rstrip("/")
        parsed = urlparse(base_url)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.path not in {"", "/"}
            or parsed.params
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise R2ConfigurationError("R2_PUBLIC_BASE_URL must be an HTTPS origin without path, credentials, query, or fragment")

        try:
            max_size = int(os.environ.get("R2_MAX_FILE_SIZE_BYTES", str(500 * 1024 * 1024)))
        except ValueError as error:
            raise R2ConfigurationError("R2_MAX_FILE_SIZE_BYTES must be an integer") from error
        if max_size <= 0:
            raise R2ConfigurationError("R2_MAX_FILE_SIZE_BYTES must be greater than zero")

        return cls(
            account_id=values["R2_ACCOUNT_ID"],
            access_key_id=values["R2_ACCESS_KEY_ID"],
            secret_access_key=values["R2_SECRET_ACCESS_KEY"],
            bucket_name=values["R2_BUCKET_NAME"],
            public_base_url=base_url,
            max_file_size_bytes=max_size,
        )

    @property
    def endpoint_url(self) -> str:
        return f"https://{self.account_id}.r2.cloudflarestorage.com"

    def redacted(self) -> dict[str, str | int]:
        return {
            "account_id": "***",
            "access_key_id": "***",
            "secret_access_key": "***",
            "bucket_name": self.bucket_name,
            "public_base_url": self.public_base_url,
            "max_file_size_bytes": self.max_file_size_bytes,
        }
