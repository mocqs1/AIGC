"""HTTP primitives for provider calls that must not cross trust boundaries."""

from __future__ import annotations

import http.client
import ipaddress
import socket
from functools import lru_cache
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPHandler, HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener


def _is_public_address(address: str) -> bool:
    try:
        candidate = ipaddress.ip_address(address)
    except ValueError:
        return False
    return candidate.is_global


def _resolve_public_target(url: str, *, pinned_ip: str | None = None) -> tuple[str, int]:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("provider URL must use http:// or https://")
    if parsed.username or parsed.password:
        raise ValueError("provider URL must not include credentials")
    port = parsed.port or (443 if scheme == "https" else 80)
    if pinned_ip:
        normalized_ip = pinned_ip.strip()
        if not _is_public_address(normalized_ip):
            raise ValueError("pinned provider address must be public")
        return normalized_ip, port
    try:
        records = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except OSError as error:
        raise OSError(f"could not resolve provider host: {error}") from error
    addresses = list(dict.fromkeys(record[4][0] for record in records if record[4]))
    if not addresses or any(not _is_public_address(address) for address in addresses):
        raise ValueError("provider host must resolve exclusively to public addresses")
    return addresses[0], port


def _resolve_loopback_proxy(proxy_url: str) -> tuple[str, int]:
    parsed = urlparse(proxy_url.strip())
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("proxy URL has an invalid port") from error
    if (
        parsed.scheme.lower() != "http"
        or not parsed.hostname
        or port is None
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("proxy URL must be a credential-free loopback http://host:port URL")
    try:
        records = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except OSError as error:
        raise OSError("could not resolve configured proxy host") from error
    addresses = list(dict.fromkeys(record[4][0] for record in records if record[4]))
    if not addresses or any(not ipaddress.ip_address(address).is_loopback for address in addresses):
        raise ValueError("proxy host must resolve exclusively to loopback addresses")
    return addresses[0], port


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, *, verified_ip: str, **kwargs):
        super().__init__(host, **kwargs)
        self._verified_ip = verified_ip

    def connect(self):
        self.sock = socket.create_connection((self._verified_ip, self.port), self.timeout, self.source_address)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, *, verified_ip: str, **kwargs):
        super().__init__(host, **kwargs)
        self._verified_ip = verified_ip

    def connect(self):
        sock = socket.create_connection((self._verified_ip, self.port), self.timeout, self.source_address)
        if self._tunnel_host:
            self.sock = sock
            self._tunnel()
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedProxyHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        host: str,
        *,
        proxy_ip: str,
        proxy_port: int,
        verified_ip: str,
        origin_hostname: str,
        origin_port: int,
        **kwargs,
    ):
        super().__init__(proxy_ip, port=proxy_port, **kwargs)
        self._origin_hostname = origin_hostname
        self.set_tunnel(verified_ip, port=origin_port)

    def connect(self):
        sock = socket.create_connection((self.host, self.port), self.timeout, self.source_address)
        self.sock = sock
        self._tunnel()
        self.sock = self._context.wrap_socket(sock, server_hostname=self._origin_hostname)


class _PinnedHTTPHandler(HTTPHandler):
    def http_open(self, request):
        verified_ip, _ = _resolve_public_target(request.full_url)
        return self.do_open(lambda host, **kwargs: _PinnedHTTPConnection(host, verified_ip=verified_ip, **kwargs), request)


class _PinnedHTTPSHandler(HTTPSHandler):
    def https_open(self, request):
        verified_ip, _ = _resolve_public_target(request.full_url)
        return self.do_open(lambda host, **kwargs: _PinnedHTTPSConnection(host, verified_ip=verified_ip, **kwargs), request)


class _PinnedProxyHTTPSHandler(HTTPSHandler):
    def __init__(self, proxy_url: str, *, pinned_ip: str | None = None):
        super().__init__()
        self._proxy_ip, self._proxy_port = _resolve_loopback_proxy(proxy_url)
        self._pinned_ip = pinned_ip

    def https_open(self, request):
        parsed = urlparse(request.full_url)
        if not parsed.hostname:
            raise ValueError("provider URL must include a hostname")
        verified_ip, origin_port = _resolve_public_target(request.full_url, pinned_ip=self._pinned_ip)
        default_port = 443
        host_header = parsed.hostname if origin_port == default_port else f"{parsed.hostname}:{origin_port}"
        request.add_unredirected_header("Host", host_header)
        return self.do_open(
            lambda host, **kwargs: _PinnedProxyHTTPSConnection(
                host,
                proxy_ip=self._proxy_ip,
                proxy_port=self._proxy_port,
                verified_ip=verified_ip,
                origin_hostname=parsed.hostname,
                origin_port=origin_port,
                **kwargs,
            ),
            request,
        )


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise HTTPError(request.full_url, code, "provider redirect blocked", headers, fp)


# An empty ProxyHandler replaces urllib's environment-backed default.
_SAFE_OPENER = build_opener(ProxyHandler({}), _PinnedHTTPHandler(), _PinnedHTTPSHandler(), _NoRedirectHandler())


@lru_cache(maxsize=8)
def _proxy_opener(proxy_url: str, pinned_ip: str | None):
    return build_opener(ProxyHandler({}), _PinnedProxyHTTPSHandler(proxy_url, pinned_ip=pinned_ip), _NoRedirectHandler())


def safe_urlopen(
    request: Request,
    *,
    timeout: float,
    proxy_url: str | None = None,
    pinned_ip: str | None = None,
):
    """Open one URL without redirects or a DNS-rebinding escape."""
    configured_proxy = (proxy_url or "").strip()
    if not configured_proxy:
        return _SAFE_OPENER.open(request, timeout=timeout)
    if urlparse(request.full_url).scheme.lower() != "https":
        raise ValueError("explicit proxy mode supports HTTPS provider URLs only")
    return _proxy_opener(configured_proxy, (pinned_ip or "").strip() or None).open(request, timeout=timeout)
