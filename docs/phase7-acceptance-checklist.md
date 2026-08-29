# Phase 7 controlled acceptance checklist

Use synthetic businesses, users, customers, products, and messages only. This
checklist is evidence guidance; unchecked items are not accepted.

## Local engineering acceptance record — 2026-08-29

This record is for the current intentional dirty Phase 7 tree and the local
production-like environment only. It is not production-infrastructure or live
provider acceptance.

- Backend: `1401 passed, 815 subtests passed`; Python compile passed.
- Frontend: typecheck passed; `182` tests passed; production build passed.
- Database: one Alembic head (`b6f2c8d4e901`); a brand-new PostgreSQL 17
  database migrated from zero to head; `current` is head and `check` reports no
  new upgrade operations.
- Packaging: final backend and frontend images built and run as non-root users.
  The local Docker installation has no Compose v2 command, so both Compose and
  CI YAML were syntax-validated and the equivalent topology was exercised with
  direct Docker containers.
- Runtime: API liveness/readiness/version, frontend health, same-origin `/api`,
  disabled docs, dashboard CSP/frame denial, widget-loader cache/CORS, worker
  and scheduler processes, and fresh database heartbeats passed.
- Synthetic API flow: registration/login/refresh/logout, isolated tenants,
  cross-tenant `404`, empty catalog/CRM/orders/conversations, connector and
  commerce registries, fail-closed chatbot/widget behavior, missing OAuth
  credentials, and structured business-capacity denial passed.
- No real client data, provider credential, live provider call, or external
  connector write was used. External writes stayed disabled.

## Infrastructure and release

- [ ] Immutable API/frontend release built from the reviewed commit; image
      scan/signature policy passes.
- [ ] Production configuration-only readiness passes without printing secrets.
- [ ] PostgreSQL TLS hostname/certificate verification succeeds.
- [ ] One Alembic head; migration task succeeds; `current` and `check` pass.
- [ ] Automated DB backup/PITR and object-storage versioning are active.
- [ ] An isolated restore drill passes and RPO/RTO evidence is recorded.
- [ ] API, worker, scheduler, and frontend run independently and stop cleanly.
- [ ] `/health/live`, `/health/ready`, and `/version` work through intended
      internal/external probe paths.
- [ ] Central JSON logs, alerts, worker/scheduler heartbeat monitoring, and
      queue/dead-letter/uncertain-outcome dashboards are active.
- [ ] HTTPS redirect/HSTS, exact host/CORS rules, CSP, and distributed edge
      limits are verified from the public origin.
- [ ] Dashboard, stable loader, widget iframe, and `/api` use the intended
      same-origin production routing.

## Browser QA

Acceptance status: `BLOCKED BY LOCAL BROWSER AUTOMATION ENVIRONMENT`

Both the in-app browser controller and the local computer-use fallback were
attempted. The browser controller reported that no browser was available, and
the computer-use browser attachment did not return usable application state.
The HTTP/runtime checks above do not substitute for the unchecked visual and
interactive browser items below.

Record viewport, browser version, test identity/business, time, and sanitized
screenshots for each completed item.

- [ ] Register and log in; invalid credentials return a private error; refresh,
      logout, and session revocation behave correctly.
- [ ] Complete onboarding with a synthetic e-commerce business; reload and
      confirm data is API-persisted rather than browser-only.
- [ ] Create a second synthetic business, switch between businesses, and verify
      dashboard, catalog, CRM, conversations, orders, campaigns, analytics,
      Business Brain, memory, jobs, approvals, attempts, integrations, settings,
      and billing never display the other business's records.
- [ ] Dashboard loads real empty/populated/error states without demo data.
- [ ] Catalog create/update/archive and bounded search/pagination work.
- [ ] Customers/CRM leads, conversations, order/refund/fulfillment views, and
      campaigns show tenant-correct records and private error states.
- [ ] AI agent execution shows running/completed/failed states; a provider
      failure is sanitized; proposed actions remain governed and are not
      silently executed.
- [ ] Approval/rejection flow records the actor; duplicate approval/dispatch is
      rejected or idempotent; uncertain execution cannot be blindly retried.
- [ ] Integration catalog/connect/resource selection/reconnect/disconnect flows
      preserve the business and safe redirect target. Without provider test
      credentials, stop before the provider page and record the external gate.
- [ ] Chatbot settings persist; copied embed points at the production origin;
      unapproved host origins are rejected according to configured policy.
- [ ] Widget loader creates one isolated launcher, opens/closes with keyboard,
      and uses the sandboxed iframe without exposing an API key/business ID.
- [ ] Public widget conversation, lead consent, order identity match, handoff,
      appointment availability/booking, expiry, and abuse/error states work
      using synthetic data.
- [ ] Automations, schedules, jobs, notifications, marketing/campaign screens,
      and explicit failure states remain usable after reload.
- [ ] Critical screens and widget pass keyboard/focus checks at 320px, 768px,
      and desktop widths; no secret/private detail appears in the console.

## Multitenant/API acceptance

- [ ] Run the complete automated suite against PostgreSQL.
- [ ] For two businesses, attempt cross-business IDs on integrations,
      connection resources, webhook event lists, customers, CRM, orders,
      campaigns, approvals, execution attempts, jobs, AI agents, Brain, memory,
      analytics, chatbot management, and media. Require safe 404/403 and zero
      mutations.
- [ ] Replay a valid OAuth state, use an expired state, mismatch a legacy
      callback connector, and submit an unsafe redirect. All fail closed.
- [ ] Send invalid/oversized/malformed webhooks, duplicates, a signed payload
      for an unselected provider account, and the same event against another
      connection. No tenant-crossing event/job is created.
- [ ] Simulate DB outage/recovery for API readiness, worker claim/heartbeat, and
      scheduler iteration. Processes recover without a restart loop or lost
      committed domain work.
- [ ] Simulate provider 401/403/404/422/429/5xx, Retry-After, timeout, malformed
      response, and oversized response on reads; verify bounded retry/dead-letter
      behavior.
- [ ] Simulate mutation timeout/network/5xx after possible send. Require
      uncertain state, no automatic retry, and explicit reconciliation.

## First controlled client gate

- [ ] All engineering tests/build/migration checks pass on the release commit.
- [ ] No unresolved engineering/security blocker is open.
- [ ] Production DB, TLS, backups, restore evidence, secrets, migrations,
      readiness, processes, edge policy, monitoring, and incident ownership are
      active—not merely documented.
- [ ] Provider matrix is reviewed. The first required provider has achieved the
      applicable live acceptance gates with a non-client test account.
- [ ] Real provider writes remain disabled by default; any first enabled write
      is provider/resource/tenant scoped and explicitly approved.
- [ ] A rollback/recovery drill and audit-log review are complete.
- [ ] Legal/privacy/data-retention/consent terms and client support contacts are
      approved before any real client data enters the system.
