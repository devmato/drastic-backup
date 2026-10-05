import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread, local
from unittest.mock import Mock

import bcrypt
import pytest
import requests
from flask import Flask, request

from drastic_server.extensions import db
from drastic_server.models import Agent, Repository, User
from drastic_server.views import restic_proxy
from drastic_server.views.restic_proxy import (
    _forward_headers,
    _forward_request_body,
    _normalize_proxy_path,
)


@pytest.fixture
def proxy_app(monkeypatch):
    monkeypatch.setattr(restic_proxy, "_state", local())
    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite:///:memory:")
    db.init_app(app)
    app.register_blueprint(restic_proxy.blp)
    with app.app_context():
        db.create_all()
        user = User(name="proxy-test", password="unused", encrypted_recovery_key={})
        repository = Repository(user=user, name="Repo", kind=Repository.KIND_NATIVE, location="native/repo")
        agent = Agent(user=user, secret=bcrypt.hashpw(b"agent-secret", bcrypt.gensalt(rounds=4)).decode(),
                      repositories=[repository])
        db.session.add(agent)
        db.session.commit()
        app.config["TEST_AGENT_ID"] = agent.id
    try:
        yield app
    finally:
        session = getattr(restic_proxy._state, "session", None)
        if session:
            session.close()
        with app.app_context():
            db.session.remove()
            db.drop_all()


@pytest.fixture
def mock_upstream(monkeypatch):
    def respond(**kwargs):
        response = Mock(status_code=200, headers={})
        response.iter_content.return_value = iter([b"backup"])
        return response

    request_mock = Mock(side_effect=respond)
    monkeypatch.setattr(requests.Session, "request", request_mock)
    return request_mock


def test_auth_reuses_only_successful_checks(proxy_app, mock_upstream, monkeypatch):
    check = Mock(wraps=bcrypt.checkpw)
    monkeypatch.setattr(bcrypt, "checkpw", check)
    client = proxy_app.test_client()
    auth = (str(proxy_app.config["TEST_AGENT_ID"]), "agent-secret")
    for _ in range(2):
        assert client.get("/restic/native/repo/config", auth=auth, buffered=True).status_code == 200
    assert check.call_count == 1
    for _ in range(2):
        assert client.get("/restic/native/repo/config", auth=(auth[0], "wrong"), buffered=True).status_code == 401
    assert check.call_count == 3
    assert mock_upstream.call_count == 2
    assert client.get("/restic/native/repo/config", auth=auth, buffered=True).status_code == 200
    assert check.call_count == 3


def test_diagnostic_proxy_summary_counts_completed_streams_only_when_enabled(proxy_app, mock_upstream):
    from drastic_server.models.diagnostic import DiagnosticEvent
    from drastic_server.services.diagnostics import flush_proxy

    client = proxy_app.test_client()
    auth = (str(proxy_app.config["TEST_AGENT_ID"]), "agent-secret")
    client.get("/restic/native/repo/config", auth=auth, buffered=True)
    with proxy_app.app_context():
        flush_proxy()
        assert DiagnosticEvent.query.count() == 0
        user = User.query.first()
        user.debug_token_hash = "enabled"
        db.session.commit()
    client.get("/restic/native/repo/config", auth=auth, buffered=True)
    with proxy_app.app_context():
        flush_proxy()
        event = DiagnosticEvent.query.one()
        assert event.payload["requests"] == 1
        assert event.payload["response_bytes"] == len(b"backup")


@pytest.mark.parametrize("change,expected", [("secret", 401), ("repository", 403), ("agent", 401)])
def test_auth_cache_respects_database_changes(proxy_app, mock_upstream, change, expected):
    client = proxy_app.test_client()
    agent_id = proxy_app.config["TEST_AGENT_ID"]
    auth = (str(agent_id), "agent-secret")
    assert client.get("/restic/native/repo/config", auth=auth, buffered=True).status_code == 200
    with proxy_app.app_context():
        agent = db.session.get(Agent, agent_id)
        if change == "secret":
            agent.secret = bcrypt.hashpw(b"new-secret", bcrypt.gensalt(rounds=4)).decode()
        elif change == "repository":
            agent.repositories.clear()
        else:
            db.session.delete(agent)
        db.session.commit()
    assert client.get("/restic/native/repo/config", auth=auth, buffered=True).status_code == expected
    assert mock_upstream.call_count == 1
    if change == "secret":
        assert client.get("/restic/native/repo/config", auth=(auth[0], "new-secret"), buffered=True).status_code == 200


def test_sessions_and_auth_cache_are_thread_local(proxy_app, mock_upstream, monkeypatch):
    check = Mock(wraps=bcrypt.checkpw)
    monkeypatch.setattr(bcrypt, "checkpw", check)

    def request_twice():
        client = proxy_app.test_client()
        auth = (str(proxy_app.config["TEST_AGENT_ID"]), "agent-secret")
        for _ in range(2):
            assert client.get("/restic/native/repo/config", auth=auth, buffered=True).status_code == 200
        session = restic_proxy._upstream_session()
        assert restic_proxy._upstream_session() is session
        return session

    main_session = request_twice()
    with ThreadPoolExecutor(max_workers=1) as executor:
        worker_session = executor.submit(request_twice).result()
    try:
        assert worker_session is not main_session
        assert check.call_count == 2
    finally:
        worker_session.close()


def test_proxy_reuses_http_connection_and_streams_without_credentials_or_cookies(proxy_app):
    records = []
    payload = b"backup" * (32 * 1024)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            records.append((self.client_address, dict(self.headers), body))
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Set-Cookie", "upstream=private")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

        do_POST = do_GET
        do_HEAD = do_GET

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    proxy_app.config["REST_SERVER_URL"] = f"http://127.0.0.1:{server.server_port}"
    try:
        client = proxy_app.test_client()
        client.set_cookie("client", "private")
        auth = (str(proxy_app.config["TEST_AGENT_ID"]), "agent-secret")
        for method in ("POST", "GET", "HEAD"):
            with client.open("/restic/native/repo/data/pack", method=method, auth=auth,
                             data=payload if method == "POST" else None, buffered=True) as response:
                assert response.status_code == 200
                assert response.data == (b"" if method == "HEAD" else payload)
        assert len({address for address, _, _ in records}) == 1
        assert records[0][2] == payload
        for _, headers, _ in records:
            assert "Authorization" not in headers
            assert "Cookie" not in headers
            assert headers["X-Drastic-Agent-Id"] == auth[0]
    finally:
        restic_proxy._upstream_session().close()
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("mode", ["abort", "error", "unstarted", "head"])
def test_proxy_closes_upstream_stream(proxy_app, monkeypatch, mode):
    upstream = Mock(status_code=200, headers={})

    def chunks(**kwargs):
        yield b"first"
        if mode == "error":
            raise requests.ConnectionError("interrupted download")
        yield b"second"

    upstream.iter_content.side_effect = chunks
    monkeypatch.setattr(requests.Session, "request", Mock(return_value=upstream))
    auth = (str(proxy_app.config["TEST_AGENT_ID"]), "agent-secret")
    if mode == "unstarted":
        with proxy_app.test_request_context("/restic/native/repo/data/pack", auth=auth):
            restic_proxy.proxy_restic("native/repo/data/pack").close()
    else:
        response = proxy_app.test_client().open("/restic/native/repo/data/pack", auth=auth,
                                                method="HEAD" if mode == "head" else "GET")
        try:
            if mode == "error":
                with pytest.raises(requests.ConnectionError):
                    response.get_data()
                assert upstream.close.called
            elif mode == "head":
                assert response.data == b""
                upstream.iter_content.assert_not_called()
        finally:
            response.close()
    assert upstream.close.called


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
