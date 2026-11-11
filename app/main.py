from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.queueing import job_queue, redis_client
from app.secrets import get_secret

APP_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("ASGV_DB_PATH", str(APP_DIR.parent / "asgv.db")))
DATABASE_URL = get_secret("DATABASE_URL") or "postgresql://asgv:asgv_dev_password@postgres:5432/asgv"
WEBHOOK_SECRET = get_secret("GITHUB_WEBHOOK_SECRET") or ""
ARTIFACT_DIR = Path(os.getenv("ARTIFACT_DIR", "/data/artifacts"))
MAX_BODY_BYTES = 1_000_000

app = FastAPI(title="AS Global Version CI/CD", version="2.0.0")


class JobRequest(BaseModel):
    job_type: str = Field(pattern="^(sample_build|health_check)$")


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(db()) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS webhook_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, delivery_id TEXT UNIQUE NOT NULL,
                event_name TEXT NOT NULL, action TEXT, repository TEXT, ref TEXT,
                commit_sha TEXT, sender TEXT, received_at TEXT NOT NULL, payload TEXT NOT NULL
            )
        """)
        connection.commit()
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS build_jobs (
                id TEXT PRIMARY KEY, job_type TEXT NOT NULL, status TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(), started_at TIMESTAMPTZ,
                finished_at TIMESTAMPTZ, artifact_path TEXT, error_code TEXT
            )
        """)


@app.on_event("startup")
def startup() -> None:
    init_db()


def verify_signature(body: bytes, signature: str | None) -> bool:
    if not WEBHOOK_SECRET or not signature:
        return False
    expected = "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
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
            "SELECT id, delivery_id, event_name, action, repository, ref, commit_sha, sender, received_at "
            "FROM webhook_events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return {"items": [dict(row) for row in rows], "count": len(rows)}


@app.post("/api/jobs", status_code=202)
def create_job(request: JobRequest) -> dict[str, str]:
    job_id = str(uuid.uuid4())
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            "INSERT INTO build_jobs (id, job_type, status) VALUES (%s,%s,'queued')",
            (job_id, request.job_type),
        )
    try:
        job_queue().enqueue("app.worker.execute_job", job_id, request.job_type, job_id=job_id)
    except Exception:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute("UPDATE build_jobs SET status='queue_error' WHERE id=%s", (job_id,))
        raise HTTPException(status_code=503, detail="Job queue unavailable") from None
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/jobs")
def list_jobs(limit: int = 25) -> dict[str, Any]:
    limit = max(1, min(limit, 100))
    with psycopg.connect(DATABASE_URL, row_factory=psycopg.rows.dict_row) as connection:
        rows = connection.execute(
            "SELECT id, job_type, status, created_at, started_at, finished_at, error_code "
            "FROM build_jobs ORDER BY created_at DESC LIMIT %s", (limit,)
        ).fetchall()
    return {"items": [dict(row) for row in rows], "count": len(rows)}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    with psycopg.connect(DATABASE_URL, row_factory=psycopg.rows.dict_row) as connection:
        row = connection.execute(
            "SELECT id, job_type, status, created_at, started_at, finished_at, artifact_path, error_code "
            "FROM build_jobs WHERE id=%s", (job_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    artifact_path = row.pop("artifact_path")
    row["artifact_available"] = bool(
        artifact_path and Path(artifact_path).is_file()
        and ARTIFACT_DIR.resolve() in Path(artifact_path).resolve().parents
    )
    return row


@app.get("/api/jobs/{job_id}/logs")
def job_logs(job_id: str) -> dict[str, Any]:
    logs = redis_client().lrange(f"asgv:logs:{job_id}", 0, -1)\n    return {"job_id": job_id, "logs": [line.decode("utf-8") if isinstance(line, bytes) else line for line in logs]}


@app.get("/api/jobs/{job_id}/logs/stream")
def stream_job_logs(job_id: str):
    def events():
        client = redis_client()
        key = f"asgv:logs:{job_id}"
        cursor = 0
        deadline = time.monotonic() + 55
        while time.monotonic() < deadline:
            lines = client.lrange(key, cursor, -1)
            for line in lines:
                cursor += 1
                yield f"data: {json.dumps({'line': line})}\n\n"
            time.sleep(1)
        yield "event: end\ndata: stream-timeout\n\n"
    return StreamingResponse(events(), media_type="text/event-stream")


@app.get("/api/jobs/{job_id}/artifact")
def download_artifact(job_id: str):
    with psycopg.connect(DATABASE_URL) as connection:
        row = connection.execute("SELECT artifact_path FROM build_jobs WHERE id=%s", (job_id,)).fetchone()
    if not row or not row[0]:
        raise HTTPException(status_code=404, detail="Artifact not available")
    path = Path(row[0]).resolve()
    if ARTIFACT_DIR.resolve() not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not available")
    return FileResponse(path, media_type="application/json", filename=path.name)


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
        str(repository.get("full_name", ""))[:200], str(payload.get("ref", ""))[:300],
        str(head_commit.get("id", payload.get("after", "")))[:100],
        str(sender.get("login", ""))[:100], datetime.now(timezone.utc).isoformat(),
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
    )
    with closing(db()) as connection:
        try:
            connection.execute(
                "INSERT INTO webhook_events (delivery_id,event_name,action,repository,ref,commit_sha,sender,received_at,payload) "
                "VALUES (?,?,?,?,?,?,?,?,?)", record
            )
            connection.commit()
        except sqlite3.IntegrityError:
            return {"status": "duplicate", "delivery_id": x_github_delivery}
    return {"status": "accepted", "delivery_id": x_github_delivery}
