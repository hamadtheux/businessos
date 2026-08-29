# Provider acceptance matrix

Snapshot: Phase 8B engineering review, 2026-08-29.

No real client data or provider mutations were used. One configured OpenAI
credential completed a minimal structured-response probe through the production
adapter; only safe request/token-presence metadata was emitted. No OAuth
provider authorization, live provider resource read, webhook delivery, write,
or production approval is claimed. Unit/contract tests prove all other
engineering behavior only.

Allowed status vocabulary is used exactly as defined by the Phase 7 brief.

| Provider | Auth implementation | Read support | Webhook support | Write support | Engineering evidence | Live auth/read/webhook/write | Blocker | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Shopify | Tenant-bound Admin API credential in secure credential store | Bounded paginated store/catalog/inventory/customer/order/refund/fulfillment sync | HMAC, exact configured shop-host binding, provider event ID dedupe | No commerce mutation adapter | Commerce adapter/HTTP/ingress/sync and wrong-shop tests | No / No / No / No | Controlled development store and non-client credential | `BLOCKED_EXTERNAL_CREDENTIALS` |
| WooCommerce | Tenant-bound HTTPS store URL plus key/secret | Bounded paginated store/catalog/inventory/customer/order/refund/fulfillment sync | HMAC, exact configured source-host binding, delivery dedupe | No commerce mutation adapter | Commerce adapter/HTTP/ingress/sync and wrong-source tests | No / No / No / No | Controlled test store and non-client credential | `BLOCKED_EXTERNAL_CREDENTIALS` |
| BigCommerce | Tenant-bound store hash/access token | Bounded paginated store/catalog/inventory/customer/order/refund/fulfillment sync | Signed timestamp/freshness, exact producer/store binding, event dedupe | No commerce mutation adapter | Commerce adapter/HTTP/ingress/sync and wrong-store tests | No / No / No / No | Sandbox store/token and webhook endpoint | `BLOCKED_EXTERNAL_CREDENTIALS` |
| Magento / Adobe Commerce | Tenant-bound HTTPS base URL and bearer credential | Bounded catalog/inventory/customer/order sync | Adobe public-key or shared-secret verification and event dedupe | No commerce mutation adapter | Commerce adapter/HTTP/ingress/sync tests | No / No / No / No | Controlled Commerce instance and non-client credential | `BLOCKED_EXTERNAL_CREDENTIALS` |
| Custom API | Constrained HTTPS endpoint/token configuration | Bounded canonical catalog/customer/order reads | Shared-secret HMAC and event dedupe | No generic provider mutation | Custom adapter, SSRF, response-bound, webhook, and sync tests | No / No / No / No | Operator-controlled fixture endpoint and credential | `BLOCKED_EXTERNAL_CREDENTIALS` |
| Google Ads | OAuth state + PKCE, secure token storage/refresh/revoke; developer token | Selected customer accounts, campaign/status/performance reads | Not used | Governed campaign create/status/budget mutations; unknown outcomes require reconciliation | OAuth, selection, action-boundary, adapter, attempt, uncertainty tests | No / No / N/A / No | Test Google Cloud project, OAuth consent, developer token, test account | `BLOCKED_EXTERNAL_CREDENTIALS` |
| Google Merchant | Google OAuth and selected Merchant resource through Google commerce connector | Destination/resource discovery and feed reconciliation | Not used | Owner-triggered feed/product-group sync, globally write-gated; no AI bypass | Ad-commerce service/adapter, tenant, idempotency, write-mode and uncertainty tests | No / No / N/A / No | Test Merchant Center account, OAuth credential, required API access | `BLOCKED_EXTERNAL_CREDENTIALS` |
| Meta Ads | OAuth state + PKCE, secure token storage/refresh/revoke | Selected business/ad account and campaign performance/status | Meta challenge + HMAC, payload resource binding, dedupe | Governed campaign create/status/budget mutations; uncertain outcomes reconcile | OAuth, Meta webhook/account-binding, action/attempt/uncertainty tests | No / No / No / No | Meta test app, system test users/assets, permissions/app review as required | `BLOCKED_EXTERNAL_CREDENTIALS` |
| Meta Catalog | Meta OAuth and selected business/catalog resources | Catalog/destination discovery and reconciliation | Meta signed ingress through the Meta commerce connection | Owner-triggered catalog/feed sync, globally write-gated | Ad-commerce, resource selection, account binding, idempotency/write-mode tests | No / No / No / No | Meta test catalog/assets and approved permissions | `BLOCKED_EXTERNAL_CREDENTIALS` |
| Gmail | OAuth state + PKCE, secure token storage/refresh/revoke | Mailbox identity/resource discovery; no claimed push mailbox ingestion | No production Gmail push verifier/receiver | Governed plain-text send with durable attempt and uncertain-outcome handling | OAuth, resource, Gmail adapter, boundary, execution and safe-error tests | No / No / N/A / No | Test Workspace/Google account and OAuth consent | `BLOCKED_EXTERNAL_CREDENTIALS` |
| Microsoft Outlook | OAuth state + PKCE and secure token-reference foundation | Mail and calendar read scopes/resource selection are modeled but the connector is foundation-only | No accepted subscription/webhook receiver | No Outlook send adapter is registered | OAuth boundary, resource contract, tenant isolation, and truthful setup-state tests | No / No / N/A / No | Complete provider adapter engineering, Azure app configuration, test account, and acceptance | `NOT_CONFIGURED` |
| WhatsApp | Meta OAuth and selected business/WABA/phone resources | Resource discovery plus signed inbound/status normalization | Meta challenge + HMAC, WABA/phone binding, fan-out and dedupe | Governed text send; free-form send requires an open customer-service window | OAuth, normalization, account binding, customer identity, action and uncertainty tests | No / No / No / No | Meta test app, test WABA/number, approved permissions/templates where needed | `BLOCKED_EXTERNAL_CREDENTIALS` |
| Facebook | Meta OAuth and selected Page resources | Selected Page/content-performance resource foundation | Meta challenge + HMAC, Page binding, event normalization, and dedupe | Governed Page publication with selected resource and mandatory approval | OAuth/resource, signed webhook, social action, durable attempt, and uncertainty tests | No / No / No / No | Meta test app, test Page, permissions/app review, and public webhook endpoint | `BLOCKED_EXTERNAL_CREDENTIALS` |
| Instagram | Meta OAuth and selected Page/professional account | Selected account/content-performance resource foundation | Meta challenge + HMAC, Page/account binding and dedupe | Governed image publication with selected account | OAuth/resource, webhook binding, social action/attempt/uncertainty tests | No / No / No / No | Meta test app, Page/professional account, permissions/app review | `BLOCKED_EXTERNAL_CREDENTIALS` |
| Messenger | No standalone connector; limited signed Facebook Page message normalization is shared with the Facebook connector | Limited inbound Page-message foundation only | Uses Facebook/Meta signed ingress and Page binding | No Messenger outbound adapter | Generic Meta/Facebook normalization and binding tests only | No / No / No / No | Dedicated Messenger contract, test Page/app, outbound policy and acceptance are not configured | `NOT_CONFIGURED` |
| OpenAI | Server-only API key; never returned to browser or tenant data | Structured Responses API generation with `store=False` | Not used | AI output cannot directly bypass governed business-action execution | Construction, structured output, timeout/retry configuration, SDK failure classes, cancellation, metadata and API persistence/error tests; bounded live structured response with request/token metadata on 2026-08-29 | `TEST_AUTHENTICATED` / `READ_ACCEPTED` / N/A / N/A | Deploy an approved credential through the selected production secret platform and record production operations evidence | `READ_ACCEPTED` |

## Promotion rules

- `CODE_READY`: engineering contract and tests pass; no live provider proof.
- `TEST_AUTHENTICATED`: a dedicated non-client test account completed auth and
  token storage/revocation evidence was recorded.
- `READ_ACCEPTED`: authenticated bounded reads against known fixture resources
  matched expected normalized records and pagination/rate-limit behavior.
- `WEBHOOK_ACCEPTED`: a real provider test event passed public HTTPS signature,
  freshness/account binding/dedupe checks and produced the expected durable job.
- `WRITE_ACCEPTED`: an explicitly approved harmless test mutation completed,
  was reconciled, and proved idempotency/uncertain-outcome procedures.
- `PRODUCTION_ACCEPTED`: provider production prerequisites, monitoring,
  ownership, revocation, and operational runbook were reviewed after all lower
  applicable gates.

Evidence must include date, reviewer, code release, test tenant, provider test
resource IDs (safe/bounded), request/event identifiers, expected/actual result,
cleanup/reversal, and links to sanitized logs. Never retain access tokens,
provider response bodies, personal messages, or client data in evidence.

## Controlled live acceptance sequence

1. Obtain written approval for a dedicated provider test account/project with
   no client data. Confirm cost limits and provider app-review status.
2. Keep external writes `disabled`. Validate redirect URIs/webhook URLs, OAuth
   state/PKCE, least-privilege scopes, selected resources, disconnect, revoke,
   expired-token behavior, and bounded reads.
3. Deliver one real test webhook over the public HTTPS endpoint. Prove signature,
   provider-account binding, duplicate suppression, safe unsupported-event
   handling, and retry recovery.
4. If write acceptance is required, change to `test` only for write-scope auth;
   prove mutations are still blocked. Then use a separately approved release
   with both write controls `enabled`, one test tenant/provider/resource, and one
   harmless idempotent or reconcilable action. Disable immediately afterward.
5. Reconcile provider state, inspect the durable attempt/audit log, remove test
   content, revoke credentials, and record the final allowed status.
