# Database Schema — AI Customer Support Chatbot

All models use `uuid.uuid4` primary keys unless noted, and every model gets `created_at`/`updated_at` (`auto_now_add`/`auto_now`) even where not spelled out below. Encrypted fields use `django-cryptography`'s `encrypt()` wrapper (or an equivalent Fernet-based custom field) — never store secrets in plaintext.

Every model marked **CRUD: full** gets Create/Read/Update/Delete dashboard views. Where delete is **soft**, "delete" in the UI sets `is_active=False` instead of removing the row, because other data references it.

---

## `platforms` app

### `Channel`
The connection to one external surface (one Instagram account, one Messenger page, one WordPress site). Multiple rows of the same `channel_type` are allowed — this is how a second Instagram account later doesn't require a schema change.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID pk | |
| `channel_type` | CharField, choices: `instagram`, `messenger`, `wordpress` | |
| `name` | CharField | Admin-facing label, e.g. "Main IG Account" |
| `credentials` | JSONField, **encrypted** | Structure varies by type — see below |
| `is_active` | BooleanField, default `True` | Toggles whether the bot responds on this channel |
| `created_at` / `updated_at` | DateTime | |

**`credentials` shape by `channel_type`:**
- `instagram`: `{"page_id", "ig_business_id", "page_access_token", "verify_token"}`
- `messenger`: `{"page_id", "page_access_token", "verify_token", "app_secret"}`
- `wordpress`: `{"site_key", "allowed_domain", "bot_name", "welcome_message", "theme_color", "icon_position"}`

**CRUD:** full, but **delete is soft** (`is_active=False`) — `Conversation` rows reference `Channel` via `Customer`, so a hard delete would orphan history. Dashboard delete action should confirm and then deactivate, not `DELETE FROM`.

---

## `llm` app

### `LLMConfig`
One row per LLM provider setup an admin has configured. Exactly one should be `is_active=True` at a time (enforce in `save()`: when saving a config with `is_active=True`, set all others to `False`).

| Field | Type | Notes |
|---|---|---|
| `id` | UUID pk | |
| `name` | CharField | e.g. "Primary — DeepSeek Chat" |
| `provider` | CharField, choices: `openai_compatible`, `anthropic` | Determines which adapter class handles it |
| `api_base_url` | URLField, blank allowed | Required for `openai_compatible` (e.g. `https://api.deepseek.com/v1`); ignored for `anthropic` |
| `api_key` | CharField, **encrypted** | Masked in the dashboard after save |
| `model_name` | CharField | e.g. `deepseek-chat`, `gpt-4o-mini`, `claude-sonnet-4-6` |
| `system_prompt` | TextField | Base persona/instructions, prepended to every call |
| `temperature` | FloatField, default `0.7` | |
| `max_tokens` | IntegerField, default `1024` | |
| `is_active` | BooleanField, default `False` | Only one true at a time |
| `created_at` / `updated_at` | DateTime | |

**CRUD:** full, **hard delete allowed** — nothing else references `LLMConfig` directly (messages don't FK to it; if you want per-message provider auditing later, add a nullable `used_llm_config_name` snapshot string to `Message` rather than an FK, so deleting a config never breaks history).

---

## `knowledge` app

### `KnowledgeChunk`
One retrievable unit of "the guide" — an FAQ answer, a policy paragraph, a procedure. Keep chunks short (a few sentences to a short paragraph) for better retrieval precision.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID pk | |
| `title` | CharField | |
| `content` | TextField | The actual text the bot can use |
| `category` | CharField, blank | Optional grouping, e.g. "shipping", "returns" |
| `embedding` | `VectorField(dimensions=384)` (via `pgvector.django.VectorField`) | Regenerated automatically whenever `content` changes (Celery task triggered on save) |
| `is_active` | BooleanField, default `True` | Inactive chunks are excluded from retrieval |
| `created_at` / `updated_at` | DateTime | |

**CRUD:** full, **hard delete allowed**. (If you later want to audit which chunks informed a given reply, add a `Message.retrieved_chunk_ids` JSONField snapshot rather than an FK/M2M — keeps deletion simple.)

### `Rule`
Deterministic keyword/condition → response override, checked before the LLM runs.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID pk | |
| `name` | CharField | Admin label |
| `trigger_keywords` | JSONField (list of strings) | Simple case-insensitive substring match for v1; leaves room for smarter matching later without a schema change |
| `response_text` | TextField | What to send when triggered |
| `short_circuits_llm` | BooleanField, default `True` | If `True`, sends `response_text` directly and skips the LLM call; if `False`, folds the rule into the LLM's context instead |
| `priority` | IntegerField, default `100` | Lower runs first; first match wins |
| `is_active` | BooleanField, default `True` | |
| `created_at` / `updated_at` | DateTime | |

**CRUD:** full, hard delete allowed.

### `BotSettings` (singleton)
Global bot behavior not tied to a specific provider. Enforce singleton via a fixed `pk=1` and a `save()` override (or use `django-solo`).

| Field | Type | Notes |
|---|---|---|
| `fallback_message` | TextField | Sent when the bot has low/no confidence, e.g. "I'm not sure about that — let me get a team member to help." |
| `max_context_messages` | IntegerField, default `10` | How many recent messages count as short-term memory |
| `memory_summary_trigger_count` | IntegerField, default `20` | Regenerate `Customer.memory_summary` every N new messages |
| `business_hours` | JSONField, blank | Optional, used by rules/prompt context |
| `profile_collection_enabled` | BooleanField, default `True` | When on, the bot asks new contacts a short set of travel-profile questions after its first reply in a new conversation, and files their answers into a `TravelProfile` |
| `profile_intro_message` | TextField | The scripted question sent right after the bot's first reply in a new conversation (requires `profile_collection_enabled`) |

**CRUD:** single edit form only (no list/create/delete — there's only ever one row).

---

## `conversations` app

### `Customer`
One row per unique end-user per channel. This is where long-term memory lives.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID pk | |
| `channel` | FK → `platforms.Channel` | |
| `external_id` | CharField | Platform-specific user ID (IGSID, PSID, or widget session/visitor ID) |
| `display_name` | CharField, blank | |
| `memory_summary` | TextField, blank | Regenerated periodically by Celery task |
| `created_at` / `updated_at` | DateTime | |

`unique_together = ("channel", "external_id")`

**CRUD:** read-only in dashboard (view/search customers and their history); no manual create/delete — these are created automatically from inbound messages. Deleting a customer (e.g. for a data-deletion request) should cascade-delete their conversations/messages — this is the one place a hard delete of related data is intentional, for privacy-request compliance.

### `Conversation`
| Field | Type | Notes |
|---|---|---|

---

## `profiles` app

### `TravelProfile`
Travel-lead details the bot captures from free-text conversation (1:1 with `Customer`, `related_name="travel_profile"`). The row is created automatically the first time the LLM extraction finds a real detail in a message; staff can also edit everything from the dashboard's Profiles page.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID pk | |
| `customer` | OneToOneField → `conversations.Customer` | |
| `profile_number` | CharField, unique | Human-friendly lead code `BP-000123`, minted from `ProfileNumberCounter` (row-locked increment) when the profile is created |
| `full_name` | CharField, blank | Also backfills `Customer.display_name` when that is still empty/"Website visitor" |
| `whatsapp_number` | CharField, blank | |
| `nationality` | CharField, blank | |
| `residence_country` | CharField, blank | |
| `gcc_residence_card` | BooleanField, null | `null` = not asked yet |
| `residence_card_expiry` | DateField, null | Only meaningful when `gcc_residence_card` is true |
| `travel_date` | DateField, null | Approximate trip start; fuzzy dates ("next month") are resolved best-effort by the LLM |
| `trip_days` | PositiveIntegerField, null | Package length in days |
| `adults` | PositiveIntegerField, null | Adult travellers |
| `children_ages` | CharField, blank | Comma-separated ages, e.g. "5, 8" |
| `is_complete` | BooleanField, default `False` | Auto-computed: all required fields present (name, WhatsApp, nationality, residence, travel date, trip days, adults); `completed_at` stamps the first completion |
| `created_at` / `updated_at` | DateTime | |

A field snapshot ("captured / still missing") is injected into the bot's LLM context each turn so it can ask for missing details naturally. Dashboard exports (CSV + Excel) use explicit columns: Profile Number, Name, WhatsApp, Nationality, Residence Country, GCC Residence Card, Card Expiry, Travel Date, Trip Days, Adults, Children Ages, Total Travellers, Channel, Complete, Completed At, Last Updated. Per-profile PNG downloads: a profile card and a chat transcript image, filenames `<ProfileNumber>_<CustomerName>[.png|_transcript.png]`.

**CRUD:** full (dashboard Profiles page: list + search/filter, detail + edit, CSV/Excel export, PNG downloads); delete is not offered — a lead is never thrown away.

### `ProfileNumberCounter`
Single-row (`id=1`) counter backing `BP-xxxxxx` codes; `next_profile_number()` increments under `select_for_update()` so concurrent extractions can never collide. Not exposed in the dashboard.
| `id` | UUID pk | |
| `customer` | FK → `Customer` | |
| `status` | CharField, choices: `bot`, `escalated`, `human`, default `bot` | Stubbed for future handoff |
| `assigned_agent` | FK → `accounts.Agent`, nullable | Stubbed for future handoff |
| `started_at` | DateTime, `auto_now_add` | |
| `last_message_at` | DateTime | Updated on every new message, used for sorting/idle detection |

**CRUD:** read-only in dashboard (status can be changed manually as a manual override, but conversations aren't created/deleted by hand).

### `Message`
| Field | Type | Notes |
|---|---|---|
| `id` | UUID pk | |
| `conversation` | FK → `Conversation` | |
| `sender_type` | CharField, choices: `customer`, `bot`, `agent`, `system` | |
| `content` | TextField | |
| `raw_payload` | JSONField, blank | Original webhook payload, for debugging — never shown in the customer-facing widget, dashboard-only |
| `created_at` | DateTime, `auto_now_add` | |

**CRUD:** read-only, append-only from the application's perspective.

---

## `accounts` app

### `Agent` (stub for future handoff — build the model now, skip the UI)
| Field | Type | Notes |
|---|---|---|
| `user` | OneToOne → `auth.User` | |
| `display_name` | CharField | |
| `is_available` | BooleanField, default `False` | Unused until handoff UI ships |

**CRUD:** managed via Django's normal user admin for v1; no custom UI needed yet.

---

## Indexes worth adding explicitly

- `Message.conversation_id` + `Message.created_at` (composite) — every read pattern is "last N messages for this conversation, ordered by time."
- `Customer.channel_id` + `Customer.external_id` (already covered by the `unique_together`, but confirm it's backed by an actual DB index).
- `KnowledgeChunk.embedding` — an `ivfflat` or `hnsw` pgvector index once you have more than a few hundred chunks; not needed at small scale but worth setting up the migration pattern early so it's a one-line change later.
