import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import jwt
from fastapi.testclient import TestClient
from pwdlib import PasswordHash
from starlette.websockets import WebSocketDisconnect

from api.auth import ALGORITHM, AuthSettings, issue_access_token
from api.workspace import SessionWorkspace
from tests.runtime_support import make_test_lifespan

PASSWORD = "route-test-password"
PASSWORD_HASH = PasswordHash.recommended().hash(PASSWORD)
AUTH_ENV = {
    "AUTH_USERNAME": "demo",
    "AUTH_USER_ID": "demo-user",
    "AUTH_PASSWORD_HASH": PASSWORD_HASH,
    "JWT_SECRET_KEY": "route-test-secret-that-is-long-enough-12345",
    "AUTH_TOKEN_EXPIRE_MINUTES": "15",
    "AUTH_JWT_ISSUER": "route-tests",
    "AUTH_JWT_AUDIENCE": "route-tests-api",
    "AUTH_WS_AUTH_TIMEOUT_SECONDS": "1",
}


class ApiAuthenticationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.environment = patch.dict(os.environ, AUTH_ENV, clear=False)
        cls.environment.start()

        import api.server as server

        cls.server = server

        cls.original_lifespan = server.app.router.lifespan_context
        server.app.router.lifespan_context = make_test_lifespan()

    @classmethod
    def tearDownClass(cls):
        cls.server.app.router.lifespan_context = cls.original_lifespan
        cls.environment.stop()

    def setUp(self):
        self.thread_id = str(uuid.uuid4())
        self.client_context = TestClient(self.server.app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)

    def login(self):
        response = self.client.post(
            "/api/login",
            data={"username": "demo", "password": PASSWORD},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["access_token"]

    def test_login_rejects_invalid_credentials(self):
        response = self.client.post(
            "/api/login",
            data={"username": "demo", "password": "wrong"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Invalid credentials")
        self.assertEqual(response.headers["www-authenticate"], "Bearer")

    def test_task_and_resource_routes_require_bearer_token(self):
        requests = [
            ("post", "/api/task", {"json": {"query": "hello"}}),
            ("get", "/api/task/thread-1", {}),
            (
                "post",
                "/api/task/thread-1/files",
                {
                    "files": {"files": ("note.txt", b"hello")},
                },
            ),
            ("get", "/api/task/thread-1/files", {}),
            ("get", "/api/task/thread-1/files/missing", {}),
        ]

        for method, url, kwargs in requests:
            with self.subTest(url=url):
                response = getattr(self.client, method)(url, **kwargs)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers["www-authenticate"], "Bearer")

    def test_invalid_bearer_token_is_rejected(self):
        response = self.client.get(
            "/api/task/thread-1",
            headers={"Authorization": "Bearer not-a-token"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["www-authenticate"], "Bearer")

    def test_valid_token_starts_task_with_existing_response_shape(self):
        token = self.login()
        response = self.client.post(
            "/api/task",
            headers={"Authorization": f"Bearer {token}"},
            json={"query": "hello", "thread_id": self.thread_id},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "started",
                "thread_id": self.thread_id,
                "result_url": f"/api/task/{self.thread_id}",
            },
        )

    def test_file_routes_expose_thread_relative_paths_only(self):
        token = self.login()
        self.server.app.state.runtime.tasks.claim(self.thread_id, "demo-user")
        headers = {"Authorization": f"Bearer {token}"}
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(self.server, "project_root", Path(temp_dir)):
                workspace = SessionWorkspace(Path(temp_dir), self.thread_id)
                workspace.prepare()
                artifact = workspace.resolve_artifact("reports/result.txt")
                artifact.parent.mkdir()
                artifact.write_text("hello", encoding="utf-8")

                listing = self.client.get(
                    f"/api/task/{self.thread_id}/files",
                    headers=headers,
                )
                download = self.client.get(
                    f"/api/task/{self.thread_id}/files/reports/result.txt",
                    headers=headers,
                )

        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["files"][0]["path"], "reports/result.txt")
        self.assertNotIn(temp_dir, str(listing.json()))
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, b"hello")

    def test_websocket_rejects_invalid_token_before_registration(self):
        self.server.app.state.runtime.tasks.claim("thread-1", "demo-user")
        self.server.app.state.runtime.tasks.store.append_event(
            "thread-1", {"type": "monitor_event", "message": "secret history"}
        )

        with self.client.websocket_connect("/ws/thread-1") as websocket:
            websocket.send_json({"type": "auth", "token": "not-a-token"})
            with self.assertRaises(WebSocketDisconnect) as closed:
                websocket.receive_json()

        self.assertEqual(closed.exception.code, 1008)
        self.assertNotIn("thread-1", self.server.manager.active_connections)

    def test_websocket_closes_and_unregisters_when_token_expires(self):
        settings = AuthSettings.from_env()
        now = int(time.time())
        token = jwt.encode(
            {
                "sub": settings.user_id,
                "iss": settings.issuer,
                "aud": settings.audience,
                "iat": now,
                "exp": now + 60,
            },
            settings.jwt_secret,
            algorithm=ALGORITHM,
        )

        with patch("api.server.time.time", return_value=now + 59.95):
            with self.client.websocket_connect(f"/ws/{self.thread_id}") as websocket:
                websocket.send_json({"type": "auth", "token": token})
                self.assertEqual(websocket.receive_json(), {"type": "auth_ok"})
                with self.assertRaises(WebSocketDisconnect) as closed:
                    websocket.receive_json()

        self.assertEqual(closed.exception.code, 1008)
        self.assertNotIn(self.thread_id, self.server.manager.active_connections)

    def test_websocket_registers_and_replays_history_after_authentication(self):
        settings = AuthSettings.from_env()
        token = issue_access_token("demo-user", settings)
        history = {"type": "monitor_event", "message": "history"}
        self.server.app.state.runtime.tasks.claim(self.thread_id, "demo-user")
        stored = self.server.app.state.runtime.tasks.store.append_event(self.thread_id, history)

        with self.client.websocket_connect(f"/ws/{self.thread_id}") as websocket:
            websocket.send_json({"type": "auth", "token": token})
            self.assertEqual(websocket.receive_json(), {"type": "auth_ok"})
            self.assertEqual(websocket.receive_json(), stored)
            websocket.send_text("ping")
            self.assertEqual(websocket.receive_json()["type"], "pong")

        self.assertNotIn(self.thread_id, self.server.manager.active_connections)


if __name__ == "__main__":
    unittest.main()
