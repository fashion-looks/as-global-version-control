from __future__ import annotations

import os
from redis import Redis
from rq import Queue

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")


def redis_client() -> Redis:
    return Redis.from_url(REDIS_URL, decode_responses=True)


def job_queue() -> Queue:
    return Queue("asgv-builds", connection=redis_client(), default_timeout=900)
