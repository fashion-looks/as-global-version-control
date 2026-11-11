# AS Global Version (ASGV) — CI/CD Platform

A secure starter implementation inspired by the supplied CI/CD architecture. GitHub Actions is the execution plane; the FastAPI service is the webhook/status plane. This service deliberately does **not** execute arbitrary repository commands itself.

## Features
- GitHub push, pull request, tag and manual workflow triggers
- CI pipeline: Python setup, dependency install, Ruff lint, pytest
- FastAPI webhook receiver with HMAC-SHA256 signature verification
- SQLite event/run ledger and status API
- Responsive live status dashboard (polls the API)
- Docker deployment and health check

## Quick start
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000

## Configure GitHub webhooks
1. Deploy the API to an HTTPS host.
2. Set environment variable `GITHUB_WEBHOOK_SECRET` to a long random secret.
3. In GitHub repository Settings → Webhooks, add `https://YOUR_HOST/webhooks/github`.
4. Select **application/json**, subscribe to Push, Pull requests, Workflow runs and Releases.
5. Copy the same secret into GitHub's webhook Secret field.

## Run tests
```bash
pip install -r requirements-dev.txt
pytest -q
ruff check .
```

## API
- `GET /health` — service health
- `GET /api/runs?limit=25` — recent webhook events
- `POST /webhooks/github` — signed GitHub webhook receiver
- `GET /` — dashboard

## Production notes
Use managed PostgreSQL instead of SQLite, Redis for queueing, TLS termination, rate limiting, backups, and a dedicated least-privilege GitHub App. Store secrets in your deployment platform's secret manager, never in source control. Add protected GitHub environments and required reviewers before production deployment. Configure self-hosted runners only on isolated, ephemeral infrastructure. The dashboard is an operational starter, not a replacement for GitHub Actions' native job logs or approvals.
