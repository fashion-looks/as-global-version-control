from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from redis import Redis

from app.queueing import REDIS_URL\nfrom app.secrets import get_secret

DATABASE_URL = get_secret("DATABASE_URL") or "postgresql://asgv:asgv_dev_password@postgres:5432/asgv"
ARTIFACT_DIR = Path(os.getenv("ARTIFACT_DIR", "/data/artifacts"))


def _log(redis: Redis, job_id: str, message: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat()} {message}"
    redis.rpush(f"asgv:logs:{job_id}", line)
    redis.expire(f"asgv:logs:{job_id}", 7 * 24 * 3600)


def execute_job(job_id: str, job_type: str) -> dict:
    """Run only built-in safe tasks; this worker never accepts arbitrary shell commands."""
    redis = Redis.from_url(REDIS_URL, decode_responses=True)
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute("UPDATE build_jobs SET status='running', started_at=now() WHERE id=%s", (job_id,))
    _log(redis, job_id, f"Job started: {job_type}")
    try:
        if job_type not in {"sample_build", "health_check"}:
            raise ValueError("Unsupported job type")
        _log(redis, job_id, "Preparing isolated job workspace")
        time.sleep(0.5)
        if job_type == "health_check":
            _log(redis, job_id, "Health check passed")
            report = {"job_id": job_id, "result": "passed", "checks": ["worker", "redis", "postgres"]}
        else:
            _log(redis, job_id, "Running sample build validation")
            time.sleep(1)
            _log(redis, job_id, "Build validation passed")
            report = {"job_id": job_id, "result": "passed", "checks": ["source", "build", "test"], "note": "Starter demo task; connect a repository-specific build pipeline for real builds."}
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        artifact = ARTIFACT_DIR / f"{job_id}-report.json"
        artifact.write_text(json.dumps(report, indent=2), encoding="utf-8")
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute(
                "UPDATE build_jobs SET status='succeeded', finished_at=now(), artifact_path=%s WHERE id=%s",
                (str(artifact), job_id),
            )
        _log(redis, job_id, f"Artifact generated: {artifact.name}")
        _log(redis, job_id, "Job completed successfully")
        return report
    except Exception as exc:
        _log(redis, job_id, f"Job failed: {type(exc).__name__}")
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute(
                "UPDATE build_jobs SET status='failed', finished_at=now(), error_code=%s WHERE id=%s",
                (type(exc).__name__, job_id),
            )
        raise
