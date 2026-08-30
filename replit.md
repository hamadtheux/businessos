# 9D Brain

9D Brain is a multi-business SaaS where owners supervise an AI workforce
across sales, support, operations, marketing, commerce, automations, approvals,
analytics, Business Brain, and persistent memory.

This repository is no longer a browser-persistence/Express prototype. The
authoritative production backend is the FastAPI application under `backend/`;
the production frontend is `artifacts/ai-business-os/`. Do not use
`artifacts/api-server/` or Drizzle schema-push commands as the production API or
database authority.

## Run locally

Backend (Python 3.12, uv, PostgreSQL):

```bash
cd backend
uv sync --frozen
uv run alembic upgrade head
uv run fastapi dev app/main.py
```

Run worker and scheduler separately against the same database:

```bash
uv run python -m app.worker
uv run python -m app.scheduler
```

Frontend (React, TypeScript, Vite):

```bash
cd artifacts/ai-business-os
pnpm install --frozen-lockfile
PORT=5174 BASE_PATH=/ pnpm dev
```

The Vite development server proxies `/api` to `http://127.0.0.1:8000` by
default. Production uses the same-origin `/api` and widget model documented in
`docs/production-deployment.md`.

## Production authority

- Settings and fail-fast validation: `backend/app/core/config.py`
- API/liveness/readiness: `backend/app/main.py`
- Durable jobs: `backend/app/worker.py` and `backend/app/scheduler.py`
- Schema: SQLAlchemy models plus Alembic migrations under `backend/alembic/`
- Deployment runbook: `docs/production-deployment.md`
- Provider truth/evidence rules: `docs/provider-acceptance.md`
- Controlled acceptance: `docs/phase7-acceptance-checklist.md`
- Local production-like topology: `docker-compose.production-like.yml`

All business-owned data is tenant-scoped by `business_id`. External provider
writes are disabled by default and may occur only through the governed action
execution boundary with both global write controls enabled. Never introduce
browser-stored credentials, fabricated provider success, or client-controlled
tenant/resource identity.

## Verification

```bash
cd backend
uv run python -m pytest -q
uv run python -m compileall app scripts
uv run alembic heads
uv run alembic current
uv run alembic check

cd ../artifacts/ai-business-os
pnpm typecheck
pnpm test
PORT=5174 BASE_PATH=/ pnpm build
```

Keep the UI clean and consistent with the supplied design references, but the
production API/database contracts—not old prototype notes—are authoritative.
