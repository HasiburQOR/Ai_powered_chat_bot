# AI Chatbot — Build Doc Set (Index)

This is a set of five documents meant to be fed to an AI coding agent (Claude Code, Cursor, etc.) to generate a working application. Give them to the agent **in this order**, one at a time, and let it fully absorb each before moving to the next:

1. `01_PRD.md` — what we're building and why (scope, features, out-of-scope)
2. `02_ARCHITECTURE.md` — tech stack, system design, data flow, security
3. `03_DATABASE_SCHEMA.md` — every Django model, field-by-field
4. `04_API_WEBHOOKS_SPEC.md` — every endpoint: webhooks, widget, dashboard CRUD
5. `05_BUILD_PLAN.md` — phased task list + Docker/Compose/requirements scaffolding
6. `06_DEPLOYMENT.md` — taking the app to production (server, HTTPS, email, go-live checklist)

A good first prompt to the agent: *"Read all 5 documents in /docs before writing any code. Then start with Phase 1 of the build plan and confirm the scaffold before moving to Phase 2."* Building phase-by-phase with a checkpoint after each phase will give far better results than asking it to generate everything in one shot.

## Key decisions already locked in (don't let the agent relitigate these)

- **Stack:** Django + HTMX (server-rendered, partial swaps — no separate React/DRF frontend) + Docker Compose + PostgreSQL + Celery/Redis.
- **LLM is provider-agnostic and admin-configurable.** No provider is hardcoded. Admin picks a provider, pastes a base URL + API key + model name, and the bot uses it. This is the single most important architectural constraint — see `03_DATABASE_SCHEMA.md` → `LLMConfig`.
- **Channels:** Instagram DMs, Messenger, and a WordPress chat-bubble widget. **WhatsApp is explicitly out of scope — do not add it.**
- **Human handoff** is stubbed (a `status` field + `Agent` model) but not fully built in v1. It's cheap to add now and expensive to bolt on later, so the schema supports it from day one even though the UI doesn't.
- **Scope assumption:** this serves **one company** with possibly multiple pages/accounts per channel (e.g. two Instagram accounts), not a multi-tenant SaaS for many separate client businesses. If that changes, flag it before Phase 2 — it affects the schema (would need an `Organization` model wrapping everything).

## What "editable and deletable" means here

Every admin-managed entity (LLM configs, knowledge base entries, rules, channel connections) gets full CRUD through the HTMX dashboard — create, edit, and delete, not just create. Where a delete could silently break conversation history (e.g. deleting a `Channel` that has conversations attached), the spec uses **soft delete** (`is_active=False`) instead of a hard delete. This is called out per-model in `03_DATABASE_SCHEMA.md`.
