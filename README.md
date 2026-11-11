# AS Global Version (ASGV) — CI/CD Platform

ASGV is a CI/CD starter platform built around GitHub Actions, FastAPI, PostgreSQL and Redis/RQ. GitHub Actions remains the trusted workflow executor; the API/worker provides a safe job queue, event ledger, status dashboard and artifact reporting. The sample worker deliberately accepts only built-in task types and never executes arbitrary shell commands supplied by an API caller.

## Included
- Cross-platform GitHub Actions CI matrix (Linux, Windows, macOS; Python 3.11/3.12)
- JUnit test reports and downloadable workflow artifacts
- FastAPI signed GitHub webhook receiver (HMAC-SHA256)
- PostgreSQL job metadata and Redis/RQ background queue
- Job status, polling dashboard and live worker-log endpoint
- JSON build report artifacts with download endpoint
- AWS Secrets Manager adapter (IAM role recommended) with environment-variable fallback
- Docker Compose local stack: API, worker, PostgreSQL and Redis
- Automatic branch deployment: `develop → dev`, `main → staging`
- Manual production deployment behind GitHub Environment protection
- Manual rollback to a previously built 40-character Git SHA image tag

## Local setup
```bash
cp .env.example .env
# Edit .env and replace every placeholder with a long random value.
docker compose up --build -d
docker compose ps
```
Open http://localhost:8000. The dashboard can enqueue a sample build or a health-check job.

## API
- `GET /health` — API health
- `GET /api/runs?limit=25` — recent GitHub webhook events
- `POST /api/jobs` — enqueue `sample_build` or `health_check`
- `GET /api/jobs` — recent job records
- `GET /api/jobs/{job_id}` — job status
- `GET /api/jobs/{job_id}/logs` — accumulated worker logs
- `GET /api/jobs/{job_id}/logs/stream` — Server-Sent Events log stream
- `GET /api/jobs/{job_id}/artifact` — download JSON report
- `POST /webhooks/github` — signed webhook endpoint

Example:
```bash
curl -X POST http://localhost:8000/api/jobs \
  -H 'Content-Type: application/json' \
  -d '{"job_type":"sample_build"}'
```

## GitHub deployment configuration

Create GitHub Environments named `dev`, `staging`, and `production`. Add these environment secrets to each environment that deploys:
- `DEPLOY_HOST` — reachable deployment server hostname
- `DEPLOY_USER` — SSH deployment user
- `DEPLOY_PORT` — published API port unique to that environment
- `DEPLOY_SSH_KEY` — private key for the deployment user
- `DATABASE_URL` — PostgreSQL connection URL (unless using AWS Secrets Manager)
- `REDIS_URL` — Redis connection URL
- `GITHUB_WEBHOOK_SECRET` — webhook signing secret (unless stored in AWS Secrets Manager)

For production, also add `GHCR_PULL_TOKEN` with package read permission. Set environment variables (GitHub Environment variables, not secrets) `ASGV_SECRET_BACKEND=aws` and `AWS_REGION` when using AWS Secrets Manager. Configure required reviewers and deployment branch restrictions under Settings → Environments → production. Without required reviewers configured, GitHub will not pause for a human approval.

The deployment host must have Docker installed, network access to the managed PostgreSQL and Redis endpoints, and an SSH account allowed to run the required Docker commands. The workflows deploy the API and RQ worker containers; provision PostgreSQL and Redis as managed services or a separately operated Compose stack. Keep ports, databases, and Redis instances isolated per environment.

## AWS Secrets Manager
Set `ASGV_SECRET_BACKEND=aws`, provide `AWS_REGION`, and grant the container's IAM role `secretsmanager:GetSecretValue` only for the required secret ARNs. Store plain-text values or JSON objects containing `DATABASE_URL` and `GITHUB_WEBHOOK_SECRET`. Optionally set `DATABASE_URL_SECRET_ID` and `GITHUB_WEBHOOK_SECRET_SECRET_ID` to map environment variables to secret names. Do not place AWS access keys in the repository.

## Workflow behavior
- CI runs on push, pull request and manual dispatch.
- `develop` builds and deploys to `dev`; `main` builds and deploys to `staging`.
- Production is a manual workflow. Enter a previously built Git SHA image tag; GitHub Environment protection rules can require reviewer approval.
- Rollback is manual and requires a previously successful image SHA tag. Configure environment-specific secrets and ports before using it.

## Security and production readiness
The queue API currently exposes only two fixed demo task types; it does not accept arbitrary commands. Before exposing the API publicly, add user authentication, rate limiting, network restrictions and audit controls. Use TLS, private networking, managed PostgreSQL/Redis, backups, database migrations, centralized log retention, and non-root deployment accounts. Restrict SSH deployment users to a narrow deployment command set. Pin third-party GitHub Actions to full commit SHAs and review updates before production use.

The current sample worker demonstrates queue execution and report generation; it is not a generic repository build runner. Real repository builds should execute through isolated, ephemeral GitHub Actions runners with least-privilege permissions.
