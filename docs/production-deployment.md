# 9D Brain production deployment runbook

This runbook is the platform-neutral production contract for the e-commerce
launch. It does not claim that infrastructure is provisioned. Complete and
record every external control before onboarding a client.

## Runtime topology

Deploy four independently restartable roles from one release:

| Role | Canonical command | Scaling rule |
| --- | --- | --- |
| API | `uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log` | Horizontal; health probes below |
| Worker | `python -m app.worker` | One or more; jobs use bounded PostgreSQL leases and `SKIP LOCKED` |
| Scheduler | `python -m app.scheduler` | One or more; deterministic idempotency and row locks prevent duplicate schedules |
| Frontend | Build with `PORT=8080 BASE_PATH=/ pnpm build`, then serve `dist/public` | Static/CDN or the supplied nginx image |

The API does not run worker or scheduler loops. Run migrations as a separate,
one-shot release task: `alembic upgrade head`. Never run concurrent migration
tasks against the same database.

The supplied [production-like compose file](../docker-compose.production-like.yml)
is an acceptance topology, not production infrastructure. It uses deliberately
local credentials, HTTP, local MinIO, and no provider credentials.

The [provider-neutral production compose contract](../docker-compose.production.yml)
uses immutable image references, fail-closed required environment values,
non-public PostgreSQL/API networking, non-root application images, read-only
application filesystems, and loopback-only frontend exposure for a separately
managed HTTPS ingress. It does not select or provision a production host, DNS,
TLS, backups, monitoring, or a secret-injection platform.

Operational procedures are in [production-operations.md](production-operations.md),
and first-client evidence gates are in
[first-client-activation.md](first-client-activation.md).

## Latest local engineering evidence

On 2026-08-29, the current intentional dirty Phase 8B tree passed `1449` backend
tests plus `825` subtests, Python compilation, frontend typecheck, `191`
frontend tests, and the production frontend build (`2471` transformed modules).
A fresh PostgreSQL 17
database migrated from zero to the single Alembic head `b6f2c8d4e901`, and
Alembic reported no model drift.

Fresh Phase 8B backend (`389fac03e514`) and frontend (`50eec3eb473d`) images ran
as non-root users with read-only filesystems in independent API, worker,
scheduler, and frontend containers. API liveness/readiness/version, frontend
health, same-origin `/api`, SPA refresh routing, widget assets, security
headers, JSON request logs, request-size/host/CORS rejection, and fresh
worker/scheduler database heartbeats passed. A local custom-format database
dump restored all `103` public tables, the Alembic head, and worker records into
an isolated database before that temporary restore was removed. This is local
recovery evidence, not an accepted production backup system.

Compose v2 is not installed in this local Docker runtime; the production and
production-like YAML parsed successfully, and the equivalent topology was
exercised with direct Docker containers including the unprivileged tmpfs mount
options declared by the production contract.

Browser QA remains `BLOCKED BY LOCAL BROWSER AUTOMATION ENVIRONMENT`. This
local evidence does not satisfy production TLS, managed database, backups,
restore, monitoring, provider credentials/approvals, image signing/scanning,
or first-client gates.

## Required production configuration

Start from `backend/.env.example`, inject values through the deployment
platform, and never mount a populated `.env` from source control.

Core production requirements enforced at process startup:

- `AIBOS_ENVIRONMENT=production`, `AIBOS_DEBUG=false`, and
  `AIBOS_DOCS_ENABLED=false`.
- A unique, randomly generated `AIBOS_AUTH_SECRET_KEY` of at least 32 bytes.
- `AIBOS_AUTH_REFRESH_COOKIE_SECURE=true`.
- Exact HTTPS `AIBOS_CORS_ORIGINS`, `AIBOS_FRONTEND_BASE_URL`, public API,
  loader, and widget app URLs; no wildcards, URL credentials, paths in origins,
  or local production hosts.
- The public API hostname in `AIBOS_TRUSTED_HOSTS`; never use `*`.
- PostgreSQL through `postgresql+asyncpg://` with
  `AIBOS_DATABASE_SSL_MODE=require`, `verify-ca`, or preferably `verify-full`.
- `AIBOS_LOG_FORMAT=json` and a confirmed edge rate-limit policy through
  `AIBOS_EDGE_RATE_LIMITING_ENABLED=true`.
- Durable S3-compatible object storage with bucket, access configuration, and
  an HTTPS public asset base URL.
- Stable OAuth callback, when OAuth is configured:
  `https://<public-origin>/api/v1/integrations/oauth/callback`.

Provider credentials are optional for core startup. Configure only providers
being accepted. OAuth tokens are stored by the integration credential backend,
not in PostgreSQL. Production provider connections require AWS Secrets Manager
configuration and workload permission scoped to the configured prefix/KMS key.

Run before every production release:

```bash
cd backend
python scripts/check_production_readiness.py --configuration-only
python scripts/check_production_readiness.py
```

The tool prints only safe status fields. It exits nonzero for invalid
production configuration, failed DB connectivity, or schema-head mismatch.

## External write control

The safe default is:

```text
AIBOS_EXTERNAL_CONNECTOR_WRITES_ENABLED=false
AIBOS_EXTERNAL_CONNECTOR_WRITE_MODE=disabled
```

`test` may request provider write scopes for a controlled test authorization,
but adapters and action boundaries still refuse real mutations. A production
mutation requires both the boolean switch and mode `enabled`, plus tenant
membership, billing entitlement, selected provider resource, action policy,
approval where required, a durable execution attempt, and provider-specific
dispatch checks. Enable one provider/test tenant at a time; do not use broad
client data during acceptance.

## PostgreSQL

- Use managed PostgreSQL with encrypted connections, automated backups, PITR,
  alerting, and maintenance ownership.
- Give the migration role DDL permissions. Give runtime roles only required
  DML permissions where the platform supports separate roles.
- Application and Alembic use the same asyncpg TLS, connect timeout, command
  timeout, `application_name`, and UTC session settings.
- The runtime uses `READ COMMITTED`, pre-ping, bounded pool/overflow/checkout
  timeout, and pool recycle.
- Monitor connection saturation, slow queries, deadlocks, lock waits, queue
  depth, oldest ready job, dead-letter count, and stale worker heartbeats.

Migration release gate:

```bash
uv run alembic heads
uv run alembic upgrade head
uv run alembic current
uv run alembic check
```

Require one head, current=head, and no model drift. Never run a destructive
downgrade automatically. Prefer a forward fix. If an incompatible migration
must be reversed, stop writers and follow the reviewed migration-specific plan.

## HTTPS, proxying, and widget origin

Terminate TLS at a trusted load balancer/reverse proxy and redirect HTTP to
HTTPS. Preserve `Host`, generate or forward a bounded `X-Request-ID`, and set
the original scheme. Trust forwarded headers only from known proxy networks.

Serve the dashboard, `widget-loader.js`, `widget.html`, and `/api` from the same
public browser origin. Proxy `/api/*` to the API without stripping `/api`.
This makes the production widget default to its script origin and removes a
second cross-origin API dependency.

The dashboard must deny framing. `widget.html` and `hosted.html` are deliberately
embeddable; do not add `X-Frame-Options: DENY` to those two documents. Retain
their application-level origin, opaque widget ID, and session-token validation.
Cache content-hashed assets immutably, but keep `widget-loader.js` and HTML
entry points revalidating so a rollback takes effect promptly.

## Health, logs, and monitoring

| Endpoint | Meaning | Probe use |
| --- | --- | --- |
| `/health/live` | Process event loop can answer | Liveness; restart only after repeated failures |
| `/health/ready` | DB responds and installed Alembic heads exactly match code | Readiness/traffic gate |
| `/health` | Compatibility health response | Smoke check only |
| `/version` | Deployed application version | Release verification |

OpenAI or Meta downtime does not make the core API unready. Readiness reports
optional integration configuration separately.

Collect JSON logs centrally and alert on safe failure codes. Preserve request,
business, job, connection, provider, and execution-attempt identifiers where
the event already owns them. Never add request bodies, prompts, messages,
tokens, cookies, credential material, provider bodies, or exception text.
Restrict production log access and retention as business-confidential data.

Recommended initial alerts:

- API 5xx/error-code rate and p95/p99 latency.
- Readiness failures or schema mismatch.
- DB pool timeouts, saturation, deadlocks, and storage failures.
- Ready jobs aging past the expected processing SLO.
- Dead-lettered jobs, uncertain external action outcomes, or stale dispatches.
- Missing/stale worker and scheduler heartbeats.
- OAuth callback/state errors and webhook verification/replay rejection spikes.
- Authentication, chatbot, AI, OAuth callback, and webhook edge rate-limit hits.

Edge rate limits must be distributed and keyed appropriately (IP plus account,
widget, or provider connection where available). The in-memory chatbot limiter
is defense in depth for one process and is not a multi-replica guarantee.

## Backup and disaster recovery requirements

These are required controls until infrastructure evidence marks them active:

1. Enable encrypted daily PostgreSQL backups and continuous WAL/PITR. Choose
   retention and RPO/RTO with the business owner; a reasonable starting policy
   is 30 daily restore points plus PITR, subject to legal/data-retention review.
2. Enable object versioning or equivalent backup on the asset bucket. Define
   lifecycle retention and cross-failure-domain copy where required.
3. Back up infrastructure definitions and secret metadata, not plaintext
   secret values. Provider OAuth tokens remain recoverable only through the
   configured credential store or provider reauthorization.
4. Quarterly, restore database and object data into an isolated environment,
   run migrations/readiness/smokes, verify tenant counts and sampled assets,
   and record elapsed recovery time. A backup is not accepted until restore is
   proven.
5. Assign named owners for backup monitoring, restore approval, security
   incident response, provider reconnection, and customer communication.

For a database restore: stop API writers/worker/scheduler, restore to a new DB,
validate checksums/provider-neutral aggregates, point a nonproduction release
at it, run readiness and smoke tests, then perform a controlled connection
cutover. Never test a restore by overwriting the only production database.

## Deployment and rollback

1. Review provider matrix, planned schema change, release diff, and external
   write mode. Confirm real writes remain disabled unless this release has a
   narrowly approved acceptance exercise.
2. Provision/verify DB TLS, object storage, secret manager, HTTPS edge, rate
   limits, logs/metrics/alerts, and backup evidence.
3. Build immutable API and frontend images from the reviewed commit. Scan and
   sign images according to the hosting platform policy.
4. Run configuration-only readiness, the full regression suite, and Alembic
   drift checks.
5. Take/verify a pre-migration recovery point. Run exactly one migration task.
6. Deploy API and wait for readiness. Deploy workers, then scheduler. Confirm
   fresh heartbeats and no unexpected queue growth.
7. Deploy frontend/edge config. Verify version, security headers, same-origin
   `/api`, dashboard, widget loader, and widget iframe.
8. Execute the controlled smoke/browser checklist in
   `docs/phase7-acceptance-checklist.md` with synthetic tenants only.

Rollback application and frontend images to the previous immutable release if
the schema remains backward compatible. Do not downgrade schema by default.
Pause worker/scheduler and disable external writes first if failures involve
jobs or provider mutations. Uncertain outcomes require reconciliation, never
blind replay.
