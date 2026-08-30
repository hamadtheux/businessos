# Durable internal processing

9D Brain uses PostgreSQL for its current-scale durable internal queue.
Run the three process roles independently against the same database:

```bash
# API (production command; development may use `fastapi dev app/main.py`)
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log

# one or more workers
uv run python -m app.worker

# one or more schedulers
uv run python -m app.scheduler
```

Workers claim bounded batches with `FOR UPDATE SKIP LOCKED`, commit their
lease before work, run a server-owned handler in a new transaction, and commit
the job outcome separately. Expired leases may be reclaimed because every
registered handler performs only idempotent internal work. The uncertain
action handler only marks an already-dispatching attempt uncertain; it never
retries or invokes a connector.

Schedulers also use row locks plus deterministic unique idempotency keys, so
multiple scheduler processes are supported. For a recurring workflow that is
overdue, a scan queues at most the persisted overdue occurrence and advances
directly to the next future occurrence. It never creates an unbounded backlog.

This is safe at-least-once processing, not exactly-once processing. External
connector writes are disabled by default and require both the explicit boolean
switch and write mode `enabled`. API health is independent of worker health;
authenticated business processing health is available under
`/api/v1/businesses/{business_id}/processing/health`.

The API exposes `/health/live` for process liveness and `/health/ready` for
database connectivity plus exact Alembic-head readiness. External SaaS outages
do not make the whole API unready. Worker and scheduler install SIGINT/SIGTERM
handlers, finish the current bounded unit of work, persist a stopped heartbeat
when the database is available, and dispose their database engine.

If a process loses the database during a claim/scheduling iteration, it logs a
safe failure type and retries after its bounded polling interval. External
mutation outcomes that may have reached a provider are marked uncertain and
are never blindly retried by the queue.

Workflow action nodes use the same governed execution path. Compilation stores
no raw recipient contact details: runtime bindings resolve tenant-owned customer
and conversation records, policy creates a mandatory approval when required,
and approval prepares a durable `ActionExecutionAttempt`. The workflow node
remains waiting while the attempt is queued or dispatching. Success, definite
failure, cancellation, or an uncertain outcome atomically enqueues a workflow
resume job, so a workflow cannot report provider completion merely because an
action was approved or queued.

At larger queue volume, monitor PostgreSQL lock and table/index pressure before
moving handlers behind a dedicated broker. The typed handler/idempotency
boundary is the intended migration seam; Redis, Celery, or another broker is
not required by the current topology.

## Deliberate scope decisions

- Existing report generation remains synchronous because it is a bounded
  database aggregation and its API contract does not need a migration yet.
- Appointment reminders are deferred until reminder offsets, recipient
  consent, and a real delivery channel are configured. No fake delivery
  record is created.
- Due social content moves only to `ready_to_publish` and creates an internal
  notification. It is never marked published and no connector is invoked.
- Proactive WhatsApp automation remains setup-required. Free-form WhatsApp
  dispatch requires a tenant-owned conversation bound to the selected provider
  connection and a customer inbound message inside the 24-hour service window.
  Template, broader consent, and opt-out semantics are not inferred.
