# API & Webhook Specification — AI Customer Support Chatbot

## 1. Meta webhooks (Instagram + Messenger, shared endpoint)

Both platforms go through the same Meta app and Graph API, so one endpoint handles both; the payload shape tells you which is which.

### `GET /webhooks/meta/`
Verification handshake Meta calls when you register the webhook URL.

- Query params: `hub.mode`, `hub.verify_token`, `hub.challenge`
- Look up the matching `Channel.credentials["verify_token"]` (check both `instagram` and `messenger` channels — or store one shared verify token if using a single Meta app for both, which is the common setup).
- If `hub.mode == "subscribe"` and the token matches, return `hub.challenge` as plain text with status 200. Otherwise, 403.

### `POST /webhooks/meta/`
Actual event delivery.

1. Verify the `X-Hub-Signature-256` header: HMAC-SHA256 of the raw request body using the relevant `Channel.credentials["app_secret"]` (Messenger) — Instagram messaging webhooks under the same Meta app typically share the app secret. Reject with 403 if it doesn't match.
2. Parse `entry[].messaging[]` (Messenger shape) — Instagram Messaging events arrive in a compatible `entry[].messaging[]` structure under the same webhook object; branch on the top-level `object` field (`"page"` vs `"instagram"`) to tag the channel type correctly.
3. For each messaging event, extract: sender platform-scoped ID, message text, timestamp.
4. Enqueue `process_inbound_message.delay(channel_type, page_id, sender_id, text, raw_payload)` as a Celery task.
5. Return `200 OK` immediately — don't wait on the Celery task. Meta will retry aggressively on non-200 or slow responses.

### Sending replies (not a webhook, but the outbound half)
From the Celery task, after the bot engine produces a reply:
- Messenger: `POST https://graph.facebook.com/v19.0/me/messages?access_token=<page_access_token>` with `{"recipient": {"id": sender_id}, "message": {"text": reply}}`
- Instagram: same shape, Instagram-specific Graph API messaging endpoint under the same app — **check the current Meta Graph API docs at build time**, as endpoint paths and required permissions (`instagram_manage_messages`, `pages_messaging`, `pages_show_list`) do shift between API versions.

## 2. WordPress widget

### `GET /widget/embed.js`
Returns a small vanilla-JS snippet (not HTMX — this runs on the *host* WordPress page, outside our control). Reads `data-site-key` off its own `<script>` tag, injects:
- A floating button, fixed bottom-right.
- A hidden `<iframe>` (shown on click) with `src="/widget/chat/?site_key=<key>"`.

This is the only piece of code that touches the WordPress page's DOM directly. Everything else happens inside the iframe, which is same-origin with our backend.

### `GET /widget/chat/?site_key=<key>`
Renders the full chat UI (HTMX-powered) inside the iframe.
- Look up the `Channel` with `channel_type="wordpress"` and matching `credentials["site_key"]`. 404 if not found or inactive.
- Set response header `Content-Security-Policy: frame-ancestors <credentials["allowed_domain"]>` so only that specific WordPress domain is allowed to embed it (don't blanket-disable frame protections).
- Create or resume a `Customer`/`Conversation` keyed by a session ID (cookie, scoped to our own domain since we're inside an iframe — third-party cookie restrictions don't apply here because the iframe *is* the first party from its own perspective).

### `POST /widget/chat/<session_id>/send/`
HTMX endpoint — form-encoded `message` field.
1. Save the inbound `Message`.
2. Run the bot engine (rules → retrieval → LLM) — can run synchronously here since there's no external retry policy to protect against, just keep the LLM call timeout sane (e.g. 20–30s) and show a loading state via `hx-indicator`.
3. Save the outbound `Message`.
4. Return an HTML fragment: the new customer bubble + bot reply bubble. HTMX swaps it in with `hx-swap="beforeend"` on the message list container, and the response can include an out-of-band swap to clear the input field.

## 3. Dashboard (internal, staff-only, HTMX CRUD)

All dashboard views require staff login. Pattern is identical across `LLMConfig`, `KnowledgeChunk`, `Rule`, and `Channel` — shown once for `LLMConfig`, replicate for the others.

| Method | Path | Returns |
|---|---|---|
| `GET` | `/dashboard/llm-configs/` | Full page: table of configs |
| `GET` | `/dashboard/llm-configs/new/` | Modal/partial: empty form |
| `POST` | `/dashboard/llm-configs/` | Creates row; returns updated `<tbody>` fragment (`hx-swap="outerHTML"`) |
| `GET` | `/dashboard/llm-configs/<id>/edit/` | Modal/partial: pre-filled form (API key shown masked, blank = "leave unchanged" on submit) |
| `POST` | `/dashboard/llm-configs/<id>/` | Updates row; returns updated `<tr>` fragment |
| `DELETE` | `/dashboard/llm-configs/<id>/` | Deletes row (`hx-delete` + `hx-confirm="Are you sure?"`); returns empty response, row removed client-side via `hx-swap="outerHTML" hx-target="closest tr"` |
| `POST` | `/dashboard/llm-configs/<id>/activate/` | Sets this config `is_active=True`, all others `False`; returns refreshed table |

For `Channel`, the `DELETE` route instead does a soft delete (`is_active=False`) per the schema doc — same HTMX pattern, different server-side effect, and the button should read "Deactivate" rather than "Delete" in the template to set the right expectation.

### Conversation review (read-only)
| Method | Path | Returns |
|---|---|---|
| `GET` | `/dashboard/conversations/` | Full page: filterable list (by channel, status, date) |
| `GET` | `/dashboard/conversations/<id>/` | Full page: message-by-message transcript, including `raw_payload` inspector for debugging |

## 4. Auth

- **Dashboard:** Django's standard session auth + `@staff_member_required` (or `@login_required` combined with an `is_staff` check) on every `dashboard/` view.
- **Widget:** no login — anonymous, identified by `site_key` (which channel/site) + a session cookie (which visitor). `site_key` should be treated as semi-public (it's in the embed script, visible in any browser's page source) — the real security boundary is the `allowed_domain` CSP check plus rate limiting per session, not secrecy of the key.
- **Webhooks:** no login — authenticated purely via Meta's signature verification (Section 1, step 1). Never trust an unsigned payload.
