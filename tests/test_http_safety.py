import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

from urllib.request import ProxyHandler

from providers.http_safety import (
    _NoRedirectHandler,
    _PinnedHTTPSConnection,
    _PinnedHTTPSHandler,
    _PinnedProxyHTTPSConnection,
    _PinnedProxyHTTPSHandler,
    _SAFE_OPENER,
    _proxy_opener,
    _resolve_loopback_proxy,
    _resolve_public_target,
    safe_urlopen,
)


class HttpSafetyTests(unittest.TestCase):
    @patch("providers.http_safety.socket.getaddrinfo")
    def test_rejects_private_dns_results(self, getaddrinfo):
        getaddrinfo.return_value = [(2, 1, 6, "", ("10.0.0.8", 443))]
        with self.assertRaisesRegex(ValueError, "public addresses"):
            _resolve_public_target("https://provider.example/v1")

    @patch("providers.http_safety.socket.getaddrinfo")
    def test_rejects_non_global_cgnat_results(self, getaddrinfo):
        getaddrinfo.return_value = [(2, 1, 6, "", ("100.64.0.1", 443))]
        with self.assertRaisesRegex(ValueError, "public addresses"):
            _resolve_public_target("https://provider.example/v1")

    @patch("providers.http_safety.socket.getaddrinfo")
    def test_returns_verified_public_ip(self, getaddrinfo):
        getaddrinfo.return_value = [(2, 1, 6, "", ("93.184.216.34", 443))]
        self.assertEqual(_resolve_public_target("https://provider.example/v1"), ("93.184.216.34", 443))

    def test_rejects_non_public_operator_pinned_ip(self):
        for address in ("127.0.0.1", "10.0.0.8", "100.64.0.1", "::ffff:127.0.0.1"):
            with self.subTest(address=address), self.assertRaisesRegex(ValueError, "must be public"):
                _resolve_public_target("https://provider.example/v1", pinned_ip=address)

    def test_redirect_is_rejected(self):
        handler = _NoRedirectHandler()
        request = Request("https://provider.example/v1")
        for status in (301, 302, 307, 308):
            with self.subTest(status=status), self.assertRaisesRegex(HTTPError, "redirect blocked"):
                handler.redirect_request(
                    request,
                    None,
                    status,
                    "redirect",
                    {},
                    "https://other.example/v1",
                )

    def test_opener_disables_environment_proxies_and_uses_pinned_handler(self):
        inherited = {
            "HTTP_PROXY": "http://untrusted.example:8080",
            "HTTPS_PROXY": "http://untrusted.example:8080",
            "NO_PROXY": "",
        }
        with patch.dict(os.environ, inherited, clear=False):
            proxy_handlers = [handler for handler in _SAFE_OPENER.handlers if isinstance(handler, ProxyHandler)]
            self.assertTrue(all(not handler.proxies for handler in proxy_handlers))
            self.assertTrue(any(isinstance(handler, _PinnedHTTPSHandler) for handler in _SAFE_OPENER.handlers))
            with patch.object(_SAFE_OPENER, "open", return_value=object()) as opener:
                request = Request("https://provider.example/v1")
                safe_urlopen(request, timeout=2)
            opener.assert_called_once_with(request, timeout=2)

    def test_explicit_proxy_accepts_only_credential_free_loopback_http_urls(self):
        accepted = (
            ("http://127.0.0.1:8080", "127.0.0.1"),
            ("http://localhost:8080", "127.0.0.1"),
            ("http://[::1]:8080", "::1"),
        )
        for proxy_url, address in accepted:
            with self.subTest(proxy_url=proxy_url), patch(
                "providers.http_safety.socket.getaddrinfo",
                return_value=[(2, 1, 6, "", (address, 8080))],
            ):
                self.assertEqual(_resolve_loopback_proxy(proxy_url), (address, 8080))

        rejected = (
            "https://127.0.0.1:8080",
            "http://user:pass@127.0.0.1:8080",
            "http://127.0.0.1",
            "http://127.0.0.1:8080/path",
            "http://127.0.0.1:8080?token=value",
        )
        for proxy_url in rejected:
            with self.subTest(proxy_url=proxy_url), self.assertRaises(ValueError):
                _resolve_loopback_proxy(proxy_url)
        with patch(
            "providers.http_safety.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 8080))],
        ), self.assertRaisesRegex(ValueError, "loopback"):
            _resolve_loopback_proxy("http://proxy.example:8080")

    def test_proxy_opener_does_not_inherit_environment_proxies(self):
        inherited = {
            "HTTP_PROXY": "http://untrusted.example:8080",
            "HTTPS_PROXY": "http://untrusted.example:8080",
        }
        _proxy_opener.cache_clear()
        with patch.dict(os.environ, inherited, clear=False), patch(
            "providers.http_safety.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("127.0.0.1", 8080))],
        ):
            opener = _proxy_opener("http://127.0.0.1:8080", "93.184.216.34")
        proxy_handlers = [handler for handler in opener.handlers if isinstance(handler, ProxyHandler)]
        self.assertTrue(all(not handler.proxies for handler in proxy_handlers))
        self.assertTrue(any(isinstance(handler, _PinnedProxyHTTPSHandler) for handler in opener.handlers))

    def test_proxy_mode_rejects_http_provider_origin(self):
        with patch("providers.http_safety._proxy_opener") as opener:
            with self.assertRaisesRegex(ValueError, "HTTPS provider URLs only"):
                safe_urlopen(
                    Request("http://provider.example/v1"),
                    timeout=2,
                    proxy_url="http://127.0.0.1:8080",
                    pinned_ip="93.184.216.34",
                )
        opener.assert_not_called()

    @patch("providers.http_safety.socket.getaddrinfo")
    def test_proxy_handler_uses_pinned_connect_target_and_original_host_header(self, getaddrinfo):
        getaddrinfo.return_value = [(2, 1, 6, "", ("127.0.0.1", 8080))]
        handler = _PinnedProxyHTTPSHandler(
            "http://localhost:8080",
            pinned_ip="93.184.216.34",
        )
        request = Request("https://provider.example/v1")
        captured = {}

        def do_open(factory, forwarded_request):
            captured["host"] = forwarded_request.get_header("Host")
            captured["factory"] = factory
            return object()

        with patch.object(handler, "do_open", side_effect=do_open):
            handler.https_open(request)
        self.assertEqual(captured["host"], "provider.example")
        with patch("providers.http_safety._PinnedProxyHTTPSConnection") as connection:
            captured["factory"]("provider.example", timeout=2)
        connection.assert_called_once_with(
            "provider.example",
            proxy_ip="127.0.0.1",
            proxy_port=8080,
            verified_ip="93.184.216.34",
            origin_hostname="provider.example",
            origin_port=443,
            timeout=2,
        )

    @patch("providers.http_safety.socket.create_connection")
    def test_proxy_https_connects_to_proxy_then_uses_origin_hostname_for_tls(self, create_connection):
        connection = _PinnedProxyHTTPSConnection(
            "provider.example",
            proxy_ip="127.0.0.1",
            proxy_port=8080,
            verified_ip="93.184.216.34",
            origin_hostname="provider.example",
            origin_port=443,
            timeout=2,
        )
        raw_socket = object()
        wrapped_socket = object()
        create_connection.return_value = raw_socket
        with patch.object(connection, "_tunnel") as tunnel, patch.object(
            connection._context,
            "wrap_socket",
            return_value=wrapped_socket,
        ) as wrap_socket:
            connection.connect()
        create_connection.assert_called_once_with(("127.0.0.1", 8080), 2, None)
        self.assertEqual((connection._tunnel_host, connection._tunnel_port), ("93.184.216.34", 443))
        tunnel.assert_called_once_with()
        wrap_socket.assert_called_once_with(raw_socket, server_hostname="provider.example")
        self.assertIs(connection.sock, wrapped_socket)

    @patch("providers.http_safety.socket.create_connection")
    def test_https_connection_pins_ip_and_retains_original_hostname_for_tls(self, create_connection):
        connection = _PinnedHTTPSConnection("provider.example", verified_ip="93.184.216.34", timeout=2)
        raw_socket = object()
        wrapped_socket = object()
        create_connection.return_value = raw_socket
        with patch.object(connection._context, "wrap_socket", return_value=wrapped_socket) as wrap_socket:
            connection.connect()
        create_connection.assert_called_once_with(("93.184.216.34", 443), 2, None)
        wrap_socket.assert_called_once_with(raw_socket, server_hostname="provider.example")
        self.assertIs(connection.sock, wrapped_socket)


if __name__ == "__main__":
    unittest.main()
