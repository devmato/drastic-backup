import unittest

from flask import Flask, request

from drastic_server.views.restic_proxy import (
    _forward_headers,
    _forward_request_body,
    _normalize_proxy_path,
)


class ResticProxyTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)

    def test_forward_headers_drops_expect_header(self):
        with self.app.test_request_context(
            "/restic/bootstrap/default/keys/test",
            method="POST",
            headers={
                "Authorization": "Basic abc",
                "Content-Length": "4",
                "Content-Type": "application/octet-stream",
                "Expect": "100-continue",
            },
            data=b"test",
        ):
            headers = _forward_headers(1, 2)

        self.assertNotIn("Authorization", headers)
        self.assertNotIn("Expect", headers)
        self.assertEqual(headers["Content-Length"], "4")
        self.assertEqual(headers["Content-Type"], "application/octet-stream")
        self.assertEqual(headers["X-Drastic-Agent-Id"], "1")
        self.assertEqual(headers["X-Drastic-Repository-Id"], "2")

    def test_forward_request_body_preserves_known_length(self):
        with self.app.test_request_context(
            "/restic/bootstrap/default/keys/test",
            method="POST",
            data=b"test",
        ):
            body = _forward_request_body()

            self.assertEqual(len(body), 4)
            self.assertEqual(body.read(2), b"te")
            self.assertEqual(body.read(), b"st")
            self.assertEqual(body.read(), b"")

    def test_forward_request_body_ignores_get_requests(self):
        with self.app.test_request_context(
            "/restic/bootstrap/default/config",
            method="GET",
        ):
            self.assertIsNone(_forward_request_body())

    def test_forward_request_body_returns_empty_bytes_for_post_without_content_length(self):
        with self.app.test_request_context(
            "/restic/bootstrap/default/",
            method="POST",
        ):
            self.assertIsNone(request.content_length)
            self.assertEqual(_forward_request_body(), b"")

    def test_normalize_proxy_path_preserves_valid_path(self):
        self.assertEqual(
            _normalize_proxy_path("native/repo/config"),
            ("native/repo/config", False),
        )

    def test_normalize_proxy_path_preserves_trailing_slash(self):
        self.assertEqual(
            _normalize_proxy_path("native/repo/"),
            ("native/repo", True),
        )

    def test_normalize_proxy_path_rejects_traversal(self):
        with self.assertRaises(ValueError):
            _normalize_proxy_path("native/repo/../other/config")

    def test_normalize_proxy_path_rejects_encoded_traversal(self):
        with self.assertRaises(ValueError):
            _normalize_proxy_path("native/repo/%2e%2e/other/config")

    def test_normalize_proxy_path_rejects_double_encoded_traversal(self):
        with self.assertRaises(ValueError):
            _normalize_proxy_path("native/repo/%252e%252e/other/config")


if __name__ == "__main__":
    unittest.main()
