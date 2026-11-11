from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

APP_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("ASGV_DB_PATH", str(APP_DIR.parent / "asgv.db")))
WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")
MAX_BODY_BYTES = 1_000_000

app = FastAPI(title="AS Global Version CI/CD", version="1.0.0")


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(db()) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS webhook_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                delivery_id TEXT UNIQUE NOT NULL,
                event_name TEXT NOT NULL,
                action TEXT,
                repository TEXT,
                ref TEXT,
                commit_sha TEXT,
                sender TEXT,
                received_at TEXT NOT NULL,
                payload TEXT NOT NULL
            )
        """)
        connection.commit()


@app.on_event("startup")
def startup() -> None:
    init_db()


def verify_signature(body: bytes, signature: str | None) -> bool:
    if not WEBHOOK_SECRET or not signature:
        return False
    expected = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return (APP_DIR / "dashboard.html").read_text(encoding="utf-8")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "asgv-api"}


@app.get("/api/runs")
def list_runs(limit: int = 25) -> dict[str, Any]:
    limit = max(1, min(limit, 100))
    with closing(db()) as connection:
        rows = connection.execute(
            "SELECT id, delivery_id, event_name, action, repository, ref, "
            "commit_sha, sender, received_at FROM webhook_events "
            "ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return {"items": [dict(row) for row in rows], "count": len(rows)}


@app.post("/webhooks/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
    x_github_delivery: str | None = Header(default=None),
) -> dict[str, str]:
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")
    if not verify_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    if not x_github_delivery or not x_github_event:
        raise HTTPException(status_code=400, detail="Missing GitHub delivery headers")
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")

    repository = payload.get("repository") or {}
    head_commit = payload.get("head_commit") or {}
    sender = payload.get("sender") or {}
    record = (
        x_github_delivery, x_github_event, str(payload.get("action", ""))[:100],
        str(repository.get("full_name", ""))[:200],
        str(payload.get("ref", ""))[:300],
        str(head_commit.get("id", payload.get("after", "")))[:100],
        str(sender.get("login", ""))[:100],
        datetime.now(timezone.utc).isoformat(),
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
    )
    with closing(db()) as connection:
        try:
            connection.execute(
                "INSERT INTO webhook_events "
                "(delivery_id,event_name,action,repository,ref,commit_sha,sender,received_at,payload) "
                "VALUES (?,?,?,?,?,?,?,?,?)", record
            )
            connection.commit()
        except sqlite3.IntegrityError:
            return {"status": "duplicate", "delivery_id": x_github_delivery}
    return {"status": "accepted", "delivery_id": x_github_delivery}
