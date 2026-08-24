# Durable internal processing

AI Business OS uses PostgreSQL for its current-scale durable internal queue.
Run the three process roles independently against the same database:

```bash
# API
uv run fastapi dev app/main.py

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
connector writes remain disabled. API health is independent of worker health;
authenticated business processing health is available under
`/api/v1/businesses/{business_id}/processing/health`.

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
