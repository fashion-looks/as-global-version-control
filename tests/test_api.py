import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from app import main


def signed(body: bytes) -> str:
    return "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()


def test_health():
    with TestClient(main.app) as client:
        assert client.get("/health").json()["status"] == "ok"


def test_dashboard():
    with TestClient(main.app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "ASGV Control Center" in response.text


def test_webhook_rejects_invalid_signature(monkeypatch):
    monkeypatch.setattr(main, "WEBHOOK_SECRET", "test-secret")
    with TestClient(main.app) as client:
        response = client.post(
            "/webhooks/github", content=b'{"repository":{"full_name":"demo/repo"}}',
            headers={"X-GitHub-Event": "push", "X-GitHub-Delivery": "delivery-1",
                     "X-Hub-Signature-256": "sha256=invalid"},
        )
        assert response.status_code == 401


def test_webhook_accepts_signed_payload(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "WEBHOOK_SECRET", "test-secret")
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "test.db")
    body = json.dumps({
        "repository": {"full_name": "fashion-looks/as-global-version-control"},
        "sender": {"login": "fashion-looks"}, "after": "abcdef123456",
    }).encode()
    with TestClient(main.app) as client:
        response = client.post(
            "/webhooks/github", content=body,
            headers={"X-GitHub-Event": "push", "X-GitHub-Delivery": "delivery-test-1",
                     "X-Hub-Signature-256": signed(body)},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "accepted"
        assert client.get("/api/runs").json()["count"] == 1
