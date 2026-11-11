"""Read secrets from AWS Secrets Manager or environment variables.

Never expose secret values through API responses or logs. In production, prefer
an IAM task role / GitHub OIDC role over long-lived AWS access keys.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache


@lru_cache(maxsize=1)
def _aws_client():
    import boto3
    return boto3.client("secretsmanager", region_name=os.getenv("AWS_REGION", "ap-south-1"))


def get_secret(name: str, *, required: bool = False) -> str | None:
    """Resolve a named secret; AWS lookup is opt-in via ASGV_SECRET_BACKEND=aws."""
    if os.getenv("ASGV_SECRET_BACKEND", "env").lower() == "aws":
        secret_id = os.getenv(f"{name}_SECRET_ID", name)
        try:
            response = _aws_client().get_secret_value(SecretId=secret_id)
            value = response.get("SecretString")
            if value is None and response.get("SecretBinary"):
                value = response["SecretBinary"].decode("utf-8")
            if value and value.lstrip().startswith("{"):
                parsed = json.loads(value)
                value = parsed.get(name) or parsed.get("value")
            if value:
                return str(value)
        except Exception:
            if required:
                raise RuntimeError(f"Required secret '{name}' could not be loaded") from None
    value = os.getenv(name)
    if required and not value:
        raise RuntimeError(f"Required secret '{name}' is not configured")
    return value
