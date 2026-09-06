# Product Requirements Document — AI Customer Support Chatbot

## 1. Summary

An AI-powered chatbot for handling customer conversations on behalf of the company. One shared "brain" (memory, rules, knowledge base, LLM) sits behind three customer-facing surfaces: Instagram DMs, Messenger, and a chat-bubble widget embedded on the company WordPress site. Admins manage everything — which LLM provider to use, what the bot knows, what rules it must follow, and its conversation history — through a web dashboard.

## 2. Goals

- Answer common customer questions automatically, 24/7, consistently across every channel.
- Let non-technical staff update what the bot knows and how it behaves without touching code (edit knowledge base entries, edit rules, swap the LLM provider/model, toggle channels on/off).
- Keep running cost low and flexible — the bot must not be locked into one AI vendor.
- Remember relevant context about a returning customer across their conversation (and ideally across sessions).
- Lay groundwork for a human agent to take over a conversation later, without requiring a rebuild.

## 3. Target users

- **End customers** — messaging the company via Instagram, Messenger, or the website widget with questions (orders, product info, policies, support).
- **Admin/staff** — manage the bot's knowledge, rules, LLM configuration, and review conversations via the dashboard. Single company, small internal team (no complex role hierarchy assumed for v1 — see Section 8).

## 4. Channels in scope

| Channel | Surface | Notes |
|---|---|---|
| Instagram | DMs to the business's Instagram account | Via Meta Graph API (Instagram Messaging) |
| Messenger | Messages to the business's Facebook Page | Via Meta Graph API (Messenger Platform) — shares the same Meta app/Page setup as Instagram |
| WordPress website | Floating chat icon, bottom-right corner | Small embed script + iframe pointing at the bot backend. Must not visually interfere with the rest of the site. |

**Explicitly out of scope:** WhatsApp. Do not integrate WhatsApp in any phase of this build.

## 5. Core features (functional requirements)

### 5.1 Conversational AI
- Bot replies to customer messages using an LLM, grounded in the company's knowledge base (RAG) and any hard-coded rules that apply.
- Deterministic **rules** are checked before the LLM is called — e.g. "if the customer mentions X, always respond with Y exactly" or "outside business hours, say Z." Rules can short-circuit the LLM entirely or just get folded into its context.
- If the bot doesn't have a confident answer, it says so and (in v1) offers a fallback message rather than guessing; in a later version this is where human handoff kicks in.

### 5.2 Memory
- **Short-term:** recent message history within the current conversation is sent as context on every LLM call.
- **Long-term:** a per-customer memory summary (key facts learned about that customer — preferences, prior issues, etc.) persists across conversations and is regenerated periodically as the conversation grows, so context doesn't get lost when a customer returns weeks later.

### 5.3 Knowledge base ("the guide")
- Admins can create, edit, and delete knowledge entries (FAQs, policies, product info, procedures) through the dashboard.
- Entries are chunked and embedded for retrieval (RAG) so the bot only pulls in what's relevant to a given question, rather than dumping everything into every prompt.

### 5.4 Rules
- Admins can create, edit, and delete rules — simple keyword/condition → response overrides that don't depend on the LLM's judgment (e.g. legal disclaimers, business hours, escalation keywords).
- Rules have a priority order and can be toggled active/inactive without deleting them.

### 5.5 LLM provider configuration
- Admins can add, edit, and delete LLM configurations: provider, base URL, API key, model name, temperature, max tokens, system prompt.
- Exactly one configuration is "active" at a time; switching providers/models is an admin-panel action, not a code change or redeploy.
- Must support cheap/OpenAI-compatible providers (DeepSeek, Groq, OpenRouter, etc.) as well as Anthropic's native API, since these have slightly different request formats.

### 5.6 Channel management
- Admins can connect/disconnect each channel (Instagram account, Messenger page, WordPress site) and edit its settings (credentials, widget appearance, welcome message) through the dashboard.
- Multiple connections of the same type should be supported without a schema change (e.g. a second Instagram account later).

### 5.7 Conversation history & review
- All conversations and messages, across all channels, are logged and viewable in the dashboard for QA/debugging — who said what, when, and (later) whether a human ever stepped in.

### 5.8 Human handoff (stub only in v1)
- Data model supports marking a conversation as `bot` / `escalated` / `human` and assigning it to an agent, but the actual agent inbox UI and real-time takeover flow is **not required for v1** — just don't design anything that would block adding it later.

## 6. Non-functional requirements

- **Cost control:** default to cheap/open LLM and embedding options; nothing in the design should force use of an expensive provider.
- **Security:** API keys and channel credentials (page access tokens, app secrets) must be encrypted at rest, never logged, never exposed in the dashboard after initial entry (mask on display). Webhook payloads from Meta must be signature-verified.
- **Reliability:** webhook endpoints must acknowledge Meta's requests quickly (Meta expects a fast 200) and do the actual LLM work asynchronously, so a slow LLM call never causes a dropped/retried webhook storm.
- **Portability:** must run via Docker Compose, in a form deployable to a standard VPS setup (single compose stack, config via environment variables, no assumption of a specific managed cloud service).
- **Auditability:** every inbound/outbound message is stored, including the raw webhook payload where applicable, for debugging.

## 7. Success criteria (informal, v1)

- A staff member can add a new FAQ entry and have the bot correctly use it in a reply within minutes, with no deploy.
- A staff member can switch the active LLM provider/model from the dashboard and the bot keeps working immediately.
- The same customer question gets a consistent answer whether asked via Instagram, Messenger, or the website widget.
- The WordPress widget doesn't break or visually clash with the existing site theme.

## 8. Assumptions (flag if wrong before building)

- Single company, not a multi-tenant SaaS for multiple separate client businesses. (If this is wrong, the schema needs an `Organization` layer wrapping channels/knowledge/rules — worth deciding before Phase 2 of the build plan.)
- One shared knowledge base / rule set across all channels for v1 (not a different bot personality per channel).
- No complex staff role/permission system needed yet — Django's built-in staff/superuser distinction is enough for v1.
- Embeddings can use a free local model (no extra API cost) unless you'd rather pay for a hosted embedding API — see `02_ARCHITECTURE.md`.

## 9. Future / explicitly deferred

- Full human agent inbox with real-time takeover and notifications.
- WhatsApp or any other channel beyond the three listed.
- Multi-tenant support for other client businesses.
- Analytics/reporting dashboard beyond raw conversation logs.
