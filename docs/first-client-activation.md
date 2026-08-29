# First-client activation runbook

This is the evidence checklist for activating one real business. It is not a
demo checklist and it does not authorize provider writes, ad spend, or changes
to an existing client account. Record the release ID, business ID, operator,
timestamp, evidence link, and rollback owner for every completed step.

Keep `AIBOS_FIRST_CLIENT_ACTIVATION_ENABLED=false` until steps 1–17 pass. Keep
external connector writes disabled until the bounded provider-write step for
that specific provider and controlled recipient/resource is approved.

## Activation sequence

1. Create or select the business without altering any unrelated tenant.
2. Complete the business profile, timezone, locale, currency, and brand voice.
3. Add and review trusted Business Brain sources; remove draft/private content
   that is not authorized for AI use.
4. Import or create products/services when the industry requires a catalog.
5. Connect only the provider accounts required for the first use case.
6. Verify that each OAuth identity and selected provider resource belongs to
   the client; record `TEST_AUTHENTICATED` evidence.
7. Run the smallest read/health operation and record `READ_ACCEPTED` evidence.
8. Send a signed webhook from an authorized provider test tool or real bounded
   event; verify tenant binding, deduplication, and background processing before
   recording `WEBHOOK_ACCEPTED`.
9. Obtain explicit approval for one bounded write to a controlled recipient or
   draft/test resource. Ads must not incur spend merely for acceptance. Record
   `WRITE_ACCEPTED` only after the real provider response and audit trail exist.
10. Configure the AI workforce. External and spend actions remain mandatory-
    approval operations regardless of the displayed agent mode.
11. Select only preparation-supported Autopilot packs. Setup-required packs
    remain disabled until their trigger and safety context exist.
12. Simulate each workflow, inspect its runtime bindings, and verify that no raw
    email, phone, credential, provider account, or foreign tenant ID is stored.
13. Activate the approved workflow and run one controlled event through event,
    action, approval, durable attempt, worker, provider result, and workflow
    completion.
14. Validate the dashboard, approvals queue, processing health, provider health,
    and first-client readiness endpoint using the actual business.
15. Validate customer conversation continuity and human handoff. WhatsApp must
    use a same-tenant conversation and valid service window; proactive flows
    remain setup-required without approved templates and consent support.
16. Review business audit records, action attempts, webhook receipts, and safe
    logs. Confirm no secret or customer message body appears in operational logs.
17. Exercise one safe failure: provider preflight rejection or controlled test
    timeout. Confirm failed/uncertain truth, operator visibility, no blind retry,
    and a documented reconciliation decision.
18. Confirm backup monitoring, a recorded restore drill, alert ownership, DNS,
    TLS, external smoke tests, and rollback readiness. Then enable the first-
    client activation gate and re-read the backend readiness result.

## Go-live evidence record

| Gate | Required evidence | Pass condition |
| --- | --- | --- |
| Release | Immutable backend/frontend image digests | Digests match deployed `/version` release |
| Production | External HTTPS smoke | Frontend, same-origin API, auth, readiness, headers pass |
| Database | Migration and recovery | One Alembic head, backup current, restore drill recorded |
| Processing | Worker/scheduler/queue | Current heartbeats; no unexplained dead letters or stale leases |
| AI | Controlled live response | Real key, bounded prompt, safe output, token/cost record |
| Provider | Per-provider acceptance record | Only evidenced vocabulary states are used |
| Automation | Golden use case | Provider terminal result resumes the workflow truthfully |
| Security | Tenant attack and secret review | All cross-tenant attempts fail closed; no secret leakage |
| Operations | Named owner and rollback | On-call, disable-write, disable-workflow, rollback owners recorded |

`PRODUCTION_ACCEPTED` requires evidence from the actual production account and
environment. `Connected`, a successful OAuth callback, or a local adapter test
is not equivalent.
