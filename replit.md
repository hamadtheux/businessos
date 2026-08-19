# AI Business OS

AI Business OS is a multi-business command center where owners supervise an AI team across sales, support, operations, marketing, customers, orders, automations, approvals, and analytics.

## Run & Operate

- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- Required env: `DATABASE_URL` — Postgres connection string

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)

## Where things live

- `artifacts/ai-business-os/src/App.tsx` — main SPA shell, route rendering, seeded business data, local persistence, and feature interactions.
- `artifacts/ai-business-os/src/index.css` — application theme and responsive visual system.
- `attached_assets/Pasted-PROJECT-AI-BUSINESS-OS-COMPLETE-FRONTEND-REQUIREMENT-0-_1786996796521.txt` — product requirements and feature scope.
- `attached_assets/Image_18-08-2026_at_12.38_AM_1786996820303.png` — visual reference supplied for the clean dashboard direction.

## Architecture decisions

- The first release is a frontend-complete prototype with deterministic AI responses and browser persistence, so the full product loop is usable without external credentials or third-party APIs.
- Business switching is modeled as a first-class state change; business-scoped data resets and persists independently for the seeded workspace experience.
- High-risk AI actions remain visible in approvals, while low-risk actions provide direct feedback through the UI.

## Product

The product includes a responsive dashboard, AI command center, unified inbox, orders, customers, CRM pipeline, AI CMO, agent management, automations, approval center, opportunity center, analytics, integrations, Business Brain, and settings.

## User preferences

- Keep the experience clean and visually close to the supplied dashboard screenshot.
- Use the uploaded product requirement as the source of truth for scope and interactions.

## Gotchas

- The web artifact's Vite config expects `PORT` and `BASE_PATH`; use the managed workflow for previews or provide both when running a manual production build.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
