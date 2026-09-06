# Build Plan — AI Customer Support Chatbot

Work through these phases in order. Stop after each and sanity-check before continuing — don't generate the entire app in one pass.

## Phase 1 — Scaffold & Docker

- `django-admin startproject core .` with apps: `platforms`, `llm`, `knowledge`, `conversations`, `bot`, `webhooks`, `widget`, `dashboard`, `accounts`.
- Get the full Docker Compose stack running with a bare Django "it works" page before writing any real models.

**`docker-compose.yml`:**
```yaml
services:
  db:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    restart: unless-stopped

  web:
    build: .
    command: gunicorn core.wsgi:application --bind 0.0.0.0:8000
    env_file: .env
    depends_on:
      - db
      - redis
    ports:
      - "8000:8000"
    restart: unless-stopped

  worker:
    build: .
    command: celery -A core worker -l info
    env_file: .env
    depends_on:
      - db
      - redis
    restart: unless-stopped

  beat:
    build: .
    command: celery -A core beat -l info
    env_file: .env
    depends_on:
      - db
      - redis
    restart: unless-stopped

volumes:
  pgdata:
```

**`Dockerfile`:**
```dockerfile
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["gunicorn", "core.wsgi:application", "--bind", "0.0.0.0:8000"]
```

**`requirements.txt`:**
```
Django>=5.0,<6.0
gunicorn
psycopg[binary]
pgvector
django-htmx
django-cryptography
celery[redis]
redis
requests
sentence-transformers
python-dotenv
```

**`.env.example`:**
```
DEBUG=False
SECRET_KEY=change-me
ALLOWED_HOSTS=your-domain.com

POSTGRES_DB=chatbot
POSTGRES_USER=chatbot
POSTGRES_PASSWORD=change-me
DATABASE_URL=postgres://chatbot:change-me@db:5432/chatbot

REDIS_URL=redis://redis:6379/0

FIELD_ENCRYPTION_KEY=generate-with-fernet-and-keep-secret
```

- Note for whoever deploys this: no reverse proxy / TLS termination is included in this compose file, since it's expected to sit behind whatever's already routing traffic to other services on the VPS (e.g. a Dokploy-managed proxy or Cloudflare Tunnel) — just make sure `web`'s port is reachable from that layer and `ALLOWED_HOSTS` is set correctly.

## Phase 2 — Models & Django admin

- Implement every model from `03_DATABASE_SCHEMA.md`, app by app.
- Register them all in Django's built-in `admin.py` too (in addition to the future HTMX dashboard) — useful as a fallback/debug tool even after the custom dashboard exists.
- Get migrations running cleanly, including enabling the `vector` extension (`CREATE EXTENSION IF NOT EXISTS vector;`) via a migration's `RunSQL`.
- Checkpoint: can create an `LLMConfig`, a `Channel`, a `KnowledgeChunk` through `/admin/` and see them persist.

## Phase 3 — LLM adapter layer

- Implement `OpenAICompatibleAdapter` and `AnthropicAdapter` per `02_ARCHITECTURE.md` §5, plus a factory function `get_adapter(llm_config) -> LLMAdapter`.
- Write a tiny management command (`send_test_message`) that loads the active `LLMConfig` and sends a hardcoded prompt, so this can be verified in isolation before anything else depends on it.
- Checkpoint: swapping which `LLMConfig` is active actually changes which provider gets called, with zero code changes.

## Phase 4 — Knowledge base & retrieval

- Implement embedding generation (local `sentence-transformers` model, 384-dim) as a Celery task triggered on `KnowledgeChunk` save (via a `post_save` signal that enqueues the task rather than blocking the save itself).
- Implement a `retrieve_relevant_chunks(query_text, top_k=3)` function using pgvector's cosine distance operator against active chunks only.
- Implement rule matching: `match_rule(message_text) -> Rule | None`, simple case-insensitive substring check against `trigger_keywords`, ordered by `priority`.
- Checkpoint: given a raw string, you can get back the top-k relevant knowledge chunks and know whether a rule fired.

## Phase 5 — Bot engine & memory

- Implement the orchestration function in `bot/`: given a `Conversation` and new inbound text —
  1. Save inbound `Message`.
  2. Check rules; if a short-circuiting rule matches, save and return its `response_text` directly.
  3. Otherwise: pull short-term history (last `BotSettings.max_context_messages`), pull `Customer.memory_summary`, retrieve relevant knowledge chunks, assemble the full prompt (system prompt + memory summary + retrieved chunks + recent history + new message).
  4. Call the active `LLMConfig` via the adapter.
  5. Save outbound `Message`, update `Conversation.last_message_at`.
  6. If message count since last summary crosses `BotSettings.memory_summary_trigger_count`, enqueue the memory-summarization task.
- Implement the memory-summarization Celery task: feed recent transcript to the LLM, ask for a short durable-facts summary, overwrite `Customer.memory_summary`.
- Checkpoint: can drive a multi-turn conversation through this function directly (no channel involved yet) and see sensible replies plus a growing/updating memory summary.

## Phase 6 — Instagram & Messenger webhooks

- Implement `/webhooks/meta/` per `04_API_WEBHOOKS_SPEC.md` §1 — verification GET, signature-checked POST, Celery task enqueue.
- Implement the outbound send functions for each platform.
- This phase needs a real (or sandboxed/test) Meta App + Page + Instagram Business Account to fully verify — check current Meta for Developers docs for exact permission names and API versions at build time, as these change.
- Checkpoint: a real DM sent to the connected test Page/Instagram account gets a bot reply.

## Phase 7 — WordPress widget

- Implement `/widget/embed.js`, `/widget/chat/`, and `/widget/chat/<session_id>/send/` per `04_API_WEBHOOKS_SPEC.md` §2.
- Build the chat UI templates (HTMX + Tailwind) — message list, input box, typing/loading indicator via `hx-indicator`.
- Set the `Content-Security-Policy: frame-ancestors` header dynamically from the matching `Channel`'s `allowed_domain`.
- Checkpoint: drop the embed script into a real (or local test) WordPress page and have a full conversation through the widget.

## Phase 8 — HTMX dashboard

- Build CRUD views + templates for `LLMConfig`, `KnowledgeChunk`, `Rule`, `Channel` per `04_API_WEBHOOKS_SPEC.md` §3 — list/create/edit/delete(or deactivate), all via HTMX partial swaps, no full page reloads on these actions.
- Build the read-only conversation browser (list + transcript detail view, with raw payload inspector).
- Build the `BotSettings` single-record edit form.
- Checkpoint: a non-technical staff member could add a knowledge base entry, switch the active LLM, and review yesterday's conversations without touching the Django admin or any code.

## Phase 9 — Human handoff groundwork (schema only, confirm not skipped)

- Confirm `Conversation.status` and `Conversation.assigned_agent` exist and are exposed (even just as a manual dropdown in the dashboard) — this is the hook a future agent-inbox feature will build on. No real-time UI required for v1.

## Phase 10 — Hardening & deploy

- Encrypt-at-rest check: confirm `LLMConfig.api_key` and `Channel.credentials` are genuinely encrypted in the database, not just masked in the UI.
- Rate limiting on `/widget/chat/<session_id>/send/` (basic per-session throttle) to prevent abuse.
- Confirm webhook signature verification actually rejects tampered payloads (write a test for this specifically — it's the easiest thing to accidentally get wrong).
- `docker compose up -d --build`, run migrations, create the first superuser, confirm all three channels end-to-end against the real deployed instance.
