"""Veo video provider adapter."""

from __future__ import annotations

from .rest_client import RestVideoClient


class VeoClient(RestVideoClient):
    provider_name = "veo"


def create_client(**kwargs):
    return VeoClient(**kwargs)
