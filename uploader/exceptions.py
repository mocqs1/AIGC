"""Exceptions raised by the Cloudflare R2 upload subsystem."""


class R2Error(Exception):
    """Base exception for R2 configuration and operations."""


class R2ConfigurationError(R2Error):
    """Raised when required R2 settings are missing or invalid."""


class R2ValidationError(R2Error, ValueError):
    """Raised when a local file or object key is unsafe or unsupported."""


class R2UploadError(R2Error):
    """Raised when an R2 request fails after retries."""
