# Production operations runbook

Use this runbook during routine releases and incidents. Commands are examples;
replace `<compose>` with the authenticated deployment wrapper for the selected
host. Never paste secret values into tickets, chat, command history, or logs.

## Normal release

1. Record backend/frontend immutable image digests and the release ID.
2. Confirm encrypted database backup completion and record its restore point.
3. Set connector writes and the first-client activation gate to `false`.
4. Run `python scripts/check_production_readiness.py --configuration-only` in
   the release image. It must emit only safe status fields.
5. Run `alembic heads`, `alembic current`, and `alembic check` against the
   target database. Require one head and no model drift.
6. Run exactly one `alembic upgrade head` release task.
7. Start API, wait for `/health/ready`, then start workers and scheduler.
8. Confirm fresh database heartbeats and queue movement. Start the frontend and
   perform an external HTTPS/same-origin smoke test.
9. Complete provider and first-client gates before enabling writes or activation.

## Rollback

1. Disable connector writes and the first-client activation gate.
2. Pause affected workflows; leave audit/history records intact.
3. Stop scheduler first, then workers, to prevent new work while preserving the
   API for operator visibility when safe.
4. Deploy the previous compatible image digests. Do not automatically run an
   Alembic downgrade. Prefer a reviewed forward fix.
5. If the schema is incompatible, stop all writers and follow the migration-
   specific reviewed rollback plan. Restore to a new database only when a data
   restore is required; never overwrite the sole production database as a test.
6. Re-run readiness, heartbeats, queue, auth, and tenant smoke tests before traffic.

## Backup and restore

- Enable encrypted daily snapshots plus continuous WAL/PITR on PostgreSQL.
  Set retention, RPO, and RTO with the data owner; 30 daily restore points is a
  starting recommendation, not an accepted configuration.
- Enable object versioning and lifecycle retention for the asset bucket.
- Back up infrastructure definitions and secret metadata, never plaintext
  provider tokens or `.env` files.
- Before every migration, record the latest recoverable database point and
  verify backup monitoring is green.
- Quarterly, restore database and object data into an isolated environment.
  Run migrations, readiness, tenant counts, asset samples, auth, golden
  scenarios, and queue processing. Record the elapsed recovery time and owner.
- A backup is not accepted until that restore drill succeeds.

Restore procedure:

1. Declare the incident and stop scheduler, workers, and API writers.
2. Restore the selected point into a new isolated database.
3. Validate checksums, Alembic head, tenant counts, foreign-key integrity, and
   provider-neutral aggregates without invoking providers.
4. Point a nonproduction release at the restored database and run readiness and
   smoke tests.
5. Approve a controlled connection cutover; retain the old database read-only
   until the recovery owner closes the incident.

## Secret rotation

1. Disable the affected connector/write path.
2. Create a new secret version in the configured secret manager.
3. Rotate the opaque credential reference through the server-side credential
   interface; never return material to the browser or database.
4. Run bounded authentication/read acceptance. Revoke the old version only
   after the new version is proven. Re-enable writes after a separately approved
   bounded write acceptance.
5. Rotate the JWT signing secret only with a planned session-invalidation window.

## Process recovery

- API: restart only after repeated liveness failures; remove from traffic on
  readiness failure and investigate database/schema state.
- Worker: restart the worker role, then verify a fresh heartbeat. Expired safe
  internal jobs may be reclaimed; a dispatching external attempt becomes
  uncertain and must not be blindly replayed.
- Scheduler: restart the scheduler role and verify its heartbeat. Deterministic
  idempotency prevents duplicate scheduled occurrences.

## Queue or provider incident

1. Open Processing and record queue counts, oldest queued age, dead letters,
   automation backlog, and both heartbeats.
2. For provider failures, inspect safe failure codes and provider health without
   logging response bodies or credentials.
3. On OAuth `401`/revocation, disable the connection and reconnect through the
   one-time OAuth state/PKCE flow.
4. On `429`, honor bounded retry guidance only for operations known not to have
   mutated provider state.
5. On timeout/unknown outcome after a write may have been sent, retain
   `uncertain`, disable the affected workflow if necessary, reconcile at the
   provider, and never blind-retry.
6. Retry only jobs whose server-owned job policy declares them safely retryable.

## Emergency controls

- Global connector kill switch:
  `AIBOS_EXTERNAL_CONNECTOR_WRITES_ENABLED=false` and
  `AIBOS_EXTERNAL_CONNECTOR_WRITE_MODE=disabled`, followed by a controlled role
  restart.
- First-client kill switch: `AIBOS_FIRST_CLIENT_ACTIVATION_ENABLED=false`.
- Tenant workflow kill switch: pause the affected workflow in Business Autopilot.
- Provider kill switch: disable/revoke the tenant connection; preserve history.
- Ads: pause only through an explicitly approved governed action unless the
  provider incident procedure requires manual provider-console containment.

## Incident response

Record severity, start time, affected tenants, release, safe identifiers,
containment owner, data-protection owner, provider status, recovery decision,
and customer-communication owner. Preserve audit logs and action attempts.
Never add raw prompts, emails, phone numbers, messages, tokens, cookies, or
provider response bodies to the incident timeline. After recovery, document the
root cause, whether any external outcome stayed uncertain, and the prevention
work before re-enabling automation.
