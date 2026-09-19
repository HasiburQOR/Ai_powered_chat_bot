# Meta channels setup — Messenger, Instagram, WhatsApp

A start-to-finish runbook for connecting a deployed chatbot to Facebook
Messenger, Instagram DMs and WhatsApp. Written from the real setup of
`chatbot.binomargroup.com` — every gotcha below actually happened.

> Deploying the server itself (Dokploy/VPS, env vars, domain, HTTPS) is in
> [06_DEPLOYMENT.md](06_DEPLOYMENT.md). Do that first; this guide starts once
> `https://<your-domain>/` loads.

---

## 0. How it fits together (read once)

```
Customer ──DM──▶ Meta ──POST──▶ https://<domain>/webhooks/meta/ ──▶ web container
                                    (checks signature with app_secret)
                                          │ enqueues task
                                          ▼
                                   worker container ──▶ LLM ──▶ reply via Graph API
                                   (finds the channel by page_id / phone_number_id,
                                    sends with page_access_token / access_token)
```

- **One Meta app** serves all three channels and **one URL**:
  `https://<domain>/webhooks/meta/`
- **One verify token** and **one app secret** — the same values go in every
  channel's credentials.
- Each channel in the dashboard is found by an **ID** that must match exactly
  what Meta puts in the webhook:

| Channel | Identity key in credentials | Where the value comes from |
|---|---|---|
| Messenger | `page_id` | Facebook **Page ID** |
| Instagram | `page_id` | **Instagram professional account ID** — *not* the Facebook Page ID (see §4) |
| WhatsApp | `phone_number_id` | **Phone number ID** from WhatsApp → API Setup — *not* the phone number, *not* the WABA ID |

- **One Meta app = one callback URL per product.** A second deployment on a
  different domain (e.g. for another client) needs **its own Meta app**.

---

## 1. Before you start — gather these

- [ ] A **Facebook Page** for the business (you must be admin).
- [ ] An **Instagram professional account** (Business or Creator), linked to
      that Page (§4.1).
- [ ] A **phone number** for WhatsApp that is **not** already used in the
      WhatsApp / WhatsApp Business phone app (or delete it from the app first).
- [ ] A **Meta Business portfolio** (business.facebook.com) that owns the Page.
- [ ] A **privacy policy URL** (any public page — needed to set the app Live).
- [ ] SSH access to the VPS.

Pick a verify token now — any random string, no spaces (e.g.
`binoma-verify-7f3k2x9q`). You'll paste it in two places; they must match.

---

## 2. Create the Meta app

1. developers.facebook.com → **My Apps → Create App**.
2. Name it after the client, pick the business portfolio.
3. Use cases — add all you need:
   - **Engage with customers on Messenger from Meta** (Messenger)
   - **Manage messaging & content on Instagram** (Instagram — use
     **API setup with Instagram login**, see §5A; the Facebook-login variant
     also works but needs a linked Page)
   - **Connect with customers through WhatsApp** (WhatsApp)
4. **App settings → Basic**:
   - Copy the **App ID** and the **App secret** (click *Show*). The App secret
     is what goes into every channel's `app_secret`.
   - Fill **Privacy policy URL**, **Category**, app icon → **Save**.
5. Switch **App Mode → Live** (toggle on the top bar / app list).

> **Live ≠ open to the public for Messenger/Instagram.** Until the app passes
> **App Review** for *Advanced Access* on `pages_messaging` and
> `instagram_manage_messages`, the bot only receives DMs from people who have a
> role on the app (admin/developer/tester). Add yourself + test accounts under
> **App roles** to test before review. WhatsApp does not have this limit.

---

## 3. Get a permanent token (do this once, reuse for all channels)

Temporary tokens from "Generate token" buttons **expire in ~24 hours** — the
bot then silently stops replying. Use a System User token instead:

1. business.facebook.com → **Settings → Users → System users → Add**
   (name: `chatbot`, role: **Admin**).
2. **Assign assets** to it with full control: the **Page**, the **Instagram
   account**, the **WhatsApp account (WABA)**, and the **App**.
3. **Generate new token** → select the app → **Never expire** → permissions:
   `pages_show_list`, `pages_messaging`, `pages_manage_metadata`,
   `pages_read_engagement`, `instagram_basic`, `instagram_manage_messages`,
   `whatsapp_business_messaging`, `whatsapp_business_management`,
   `business_management`.
4. Save the token somewhere safe. This is the **system user token**. It is
   used directly as the WhatsApp `access_token`; for Messenger/Instagram you
   derive a Page token from it (next section).

---

## 4. Collect the IDs and Page token (from the VPS)

SSH into the VPS and set these once per session:

```bash
WEB=$(docker ps --format '{{.Names}}' | grep -E 'chatbot.*-web-1$' | head -1); echo "$WEB"
APP_ID=<your app id>
SYS_TOKEN='<system user token>'
```

(If `echo` prints nothing, run `docker ps` and copy the web container's name.)

### 4.1 Page ID, Page token, Instagram account ID

```bash
curl -s "https://graph.facebook.com/v21.0/me/accounts?fields=id,name,access_token,instagram_business_account&access_token=$SYS_TOKEN"
```

From the output:
- `id` → **Facebook Page ID**
- `access_token` → **Page access token** (never expires when derived from a
  system user token)
- `instagram_business_account.id` → **Instagram account ID** (a long number,
  usually starting `1784…`)

No `instagram_business_account` in the output → the Instagram account isn't
linked to the Page. Fix: in the Instagram app → *Settings → Account type and
tools* → switch to **Professional**, then in Meta Business Suite → *Settings →
Instagram accounts* → connect it to the Page. Rerun the command.

### 4.2 WhatsApp IDs

WhatsApp → **API Setup** (or *Use cases → WhatsApp → Customize → Production
setup*) shows the **Phone number ID** and **WhatsApp Business Account ID**
next to your registered number. Careful: the page also shows the **test
number** with *different* IDs — use the IDs of your **real** number.

Confirm from the VPS:

```bash
curl -s "https://graph.facebook.com/v21.0/<WABA_ID>/phone_numbers?access_token=$SYS_TOKEN"
```

---

## 5. Create the channels in the dashboard

Dashboard → **Channels → New**. One channel per surface. Credentials JSON:

**Messenger**
```json
{
  "page_id": "<Facebook Page ID>",
  "page_access_token": "<Page access token>",
  "app_secret": "<App secret>",
  "verify_token": "<your verify token>"
}
```

**Instagram (Facebook-login variant only)** — skip this if you follow §5A.
Same Page token, but `page_id` is the **Instagram account ID**:
```json
{
  "page_id": "<Instagram account ID, 1784...>",
  "page_access_token": "<Page access token of the linked Page>",
  "app_secret": "<App secret>",
  "verify_token": "<your verify token>"
}
```

**WhatsApp**
```json
{
  "phone_number_id": "<Phone number ID>",
  "waba_id": "<WhatsApp Business Account ID>",
  "access_token": "<system user token>",
  "app_secret": "<App secret>",
  "verify_token": "<your verify token>"
}
```

### 5A. Instagram with "API setup with Instagram login" (recommended)

This is a separate flow with its **own app ID and app secret** — webhooks
from it are signed with the **Instagram app secret**, and replies go through
`graph.instagram.com` with an Instagram token (the code picks this path
automatically when the channel has `access_token` and no `page_access_token`).

1. App dashboard → *Use cases → Instagram API → Customize → **API setup with
   Instagram login***.
2. Top of the page: **Instagram app secret → Show** → copy. (Not the
   Facebook App secret from App settings.)
3. **2. Generate access tokens → Add account** → log in with the business
   Instagram account (must be Professional). Its row shows the **Instagram
   account ID** under the username (`1784…`).
4. In that row: **Generate token** → copy (starts with `IGAA`), and turn
   **Webhook Subscription → On**.
5. **3. Configure webhooks** → Callback URL `https://<domain>/webhooks/meta/`
   + your verify token → **Verify and save** → subscribe the **messages**
   field.
6. Dashboard channel (type Instagram):

```json
{
  "page_id": "<Instagram account ID from step 3, 1784...>",
  "access_token": "<IGAA... token from step 4>",
  "app_secret": "<Instagram app secret from step 2>",
  "verify_token": "<your verify token>"
}
```

7. Until **5. Complete app review** is approved, only accounts with a role
   on the app get replies: add the tester's Instagram username under
   *App roles → Roles → Instagram Testers*, and the tester accepts it in the
   Instagram app (*Settings → Website permissions → Tester invites*, or
   instagram.com → Settings → Apps and websites). Send test DMs **from that
   tester account** to the business account — not from the business account
   itself.
8. **The IGAA token lasts 60 days.** Refresh it before then (any time after
   it's 24 h old) and paste the new one into the channel:

```bash
curl -s "https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token&access_token=<IGAA token>"
```

Tick **Active**. Check the saved credentials: no spaces inside values, keys
spelled exactly as above.

Test the verify token (from anywhere, **all on one line**):

```bash
curl -s "https://<domain>/webhooks/meta/?hub.mode=subscribe&hub.verify_token=<your verify token>&hub.challenge=ok123"
```

Must print `ok123`. `Verify token mismatch` → the token in the dashboard differs.

---

## 6. Register the webhooks

Do it in the dashboard **or** with the commands below (the commands are
faster and can't get the URL wrong).

**Callback URL — always with the trailing slash:**
`https://<domain>/webhooks/meta/`

### Dashboard way
- Messenger: *Use cases → Messenger → Customize → Configure webhooks* → URL +
  verify token → **Verify and save** → subscribe fields **messages**,
  **messaging_postbacks** → under *Generate access tokens*, click
  **Add subscriptions** next to the Page.
- Instagram: *Use cases → Instagram → Customize → Configure webhooks* → URL +
  verify token → **Verify and save** → subscribe field **messages**.
- WhatsApp: *Use cases → WhatsApp → Customize → Configuration* → URL + verify
  token → **Verify and save** → subscribe field **messages**.

### Command way (VPS)

These read the app secret and verify token straight from your saved
channels, so nothing sensitive gets typed:

```bash
docker exec $WEB python manage.py shell -c "
import requests
from platforms.models import Channel
APP_ID='$APP_ID'; URL='https://<domain>/webhooks/meta/'
c = Channel.objects.filter(channel_type__in=['messenger','instagram','whatsapp'], is_active=True).first()
tok = APP_ID + '|' + str(c.credentials['app_secret']).strip()
vt = str(c.credentials['verify_token']).strip()
for obj, fields in [('page','messages,messaging_postbacks'), ('instagram','messages'), ('whatsapp_business_account','messages')]:
    r = requests.post(f'https://graph.facebook.com/v21.0/{APP_ID}/subscriptions', params={'access_token': tok},
                      data={'object': obj, 'callback_url': URL, 'verify_token': vt, 'fields': fields})
    print(obj, r.text)
"
```

Each line should end in `{"success":true}`. Drop a row from the list for a
channel you don't use. **Using Instagram login (§5A)? Drop the `instagram`
row** — that webhook is set on the *API setup with Instagram login* page
(§5A step 5) instead.

### Connect the Page and the WhatsApp account to the app

The app-level webhook alone is not enough — the **Page** and the **WABA** must
also be subscribed to the app, or Meta has nothing to send:

```bash
# Page (covers Messenger AND the Instagram account linked to it)
curl -s -X POST "https://graph.facebook.com/v21.0/<PAGE_ID>/subscribed_apps?subscribed_fields=messages,messaging_postbacks&access_token=<PAGE_ACCESS_TOKEN>"

# WhatsApp account
curl -s -X POST "https://graph.facebook.com/v21.0/<WABA_ID>/subscribed_apps?access_token=$SYS_TOKEN"
```

Both → `{"success":true}`.

### Instagram-only switch (Facebook-login variant; harmless to set for §5A too)

On the phone, in the **Instagram app** of the business account:
*Settings → Messages and story replies → Message controls → Connected tools*
→ turn **Allow access to messages** ON. Without it Meta never forwards DMs.

---

## 7. Check everything in one go

```bash
docker exec $WEB python manage.py shell -c "
import requests
from platforms.models import Channel
APP_ID='$APP_ID'
c = Channel.objects.filter(channel_type__in=['messenger','instagram','whatsapp'], is_active=True).first()
print('APP WEBHOOKS:', requests.get(f'https://graph.facebook.com/v21.0/{APP_ID}/subscriptions',
      params={'access_token': APP_ID + '|' + str(c.credentials['app_secret']).strip()}).text)
for ch in Channel.objects.filter(channel_type__in=['messenger','instagram','whatsapp']):
    cr = ch.credentials or {}
    print('---', ch.channel_type, ch.name, 'active=', ch.is_active, 'keys=', sorted(cr))
    if ch.channel_type == 'whatsapp':
        h = {'Authorization': 'Bearer ' + str(cr.get('access_token','')).strip()}
        print('  phone:', requests.get(f\"https://graph.facebook.com/v21.0/{cr.get('phone_number_id')}\", params={'fields':'display_phone_number,status'}, headers=h).text)
        print('  waba subscribed_apps:', requests.get(f\"https://graph.facebook.com/v21.0/{cr.get('waba_id')}/subscribed_apps\", headers=h).text)
    elif ch.channel_type == 'instagram' and not cr.get('page_access_token'):
        print('  IG token owner:', requests.get('https://graph.instagram.com/v21.0/me', params={'fields':'user_id,username','access_token': str(cr.get('access_token','')).strip()}).text, ' stored page_id=', cr.get('page_id'))
    else:
        print('  token owner:', requests.get('https://graph.facebook.com/v21.0/me', params={'fields':'id,name','access_token': str(cr.get('page_access_token','')).strip()}).text, ' stored page_id=', cr.get('page_id'))
"
```

What good looks like:
- **APP WEBHOOKS** lists `page`, `instagram` and `whatsapp_business_account`,
  each with `callback_url` **ending in `/webhooks/meta/`**, `"active":true`,
  and `messages` in `fields`.
- **WhatsApp**: `"status":"CONNECTED"`, and your app appears under
  `waba subscribed_apps`.
- **Messenger**: `token owner` id **equals** the stored `page_id`.
- **Instagram (Instagram login, §5A)**: `IG token owner` shows your
  username, and its `user_id` **equals** the stored `page_id`.
- **Instagram (Facebook login)**: `token owner` is the Facebook **Page** (the token belongs to
  the Page), while stored `page_id` is the **Instagram account ID** — these
  two are *supposed* to differ.

---

## 8. Live test

Watch the logs:

```bash
docker logs -f --since 1m $WEB 2>&1 | grep -E "method=POST|accepted|signature"
```

Second terminal, the worker:

```bash
docker logs -f --since 1m ${WEB%-web-1}-worker-1 2>&1 | grep -iE "process_inbound|dropping|send failed|error"
```

Send "hi" from a personal account (one with an app role, for
Messenger/Instagram before App Review) to the Page / Instagram account /
WhatsApp number. Expected:

1. web: `Meta webhook request received: method=POST`
2. web: `Meta webhook accepted: object=page|instagram|whatsapp_business_account`
3. worker: `process_inbound_message[...] succeeded`
4. Reply arrives.

---

## 9. Troubleshooting — find your symptom

| Symptom (log line) | Cause | Fix |
|---|---|---|
| **No `method=POST` at all** | Meta isn't sending | Run §7. Check: callback URL has the trailing `/`; the object (`page`/`instagram`/`whatsapp_business_account`) is subscribed with `messages`; Page/WABA `subscribed_apps` done; Instagram *Allow access to messages* ON; sender has an app role (Messenger/IG before App Review) |
| `bad verification request ... ua='facebookexternalua'` | Meta's link-preview crawler | Harmless — ignore |
| `Verify token mismatch` | Dashboard verify token ≠ the one typed in Meta | Make them identical |
| `signature matched no active channel` | Wrong `app_secret` (other app, or reset), key misspelled, or channel inactive. **Instagram-login channels need the *Instagram* app secret**, not the Facebook one | Re-copy the right secret: Facebook *App settings → Basic* for Messenger/WhatsApp; *Instagram API → API setup with Instagram login* for Instagram |
| `X-Hub-Signature-256 header missing` | Proxy stripping headers | Check Traefik/Dokploy config |
| worker: `Dropping inbound message: no active <type> channel with page_id='X' (known: [...])` | Stored ID doesn't match the ID Meta sent | Put **X** (from the log) into the channel's `page_id` / `phone_number_id`. For Instagram X is the **Instagram account ID** |
| `Graph send failed [400]` ... `code 190` / `WhatsApp send failed [401]` | Token expired | Use the system user token (§3) / Page token from §4.1 |
| `Instagram send failed [400]` ... `code 190` | IGAA token expired (60 days) | Refresh it (§5A step 8) or generate a new one |
| `Graph send failed [400]` ... `code 10` or `200` | Missing permission / no Advanced Access | Add the permission to the token; for public users pass App Review |
| `WhatsApp send failed [400]` ... `131030` | Recipient not allowed (test number only) | Add the recipient under API Setup → *To*, or use your real number |
| `WhatsApp send failed` ... `131047` | Customer's last message > 24 h ago | Normal — free-form replies only allowed within 24 h |
| Instagram replies go to the bot itself / junk customer with the business's own ID | Echo events (fixed in code — make sure the server runs a version with the `is_echo` skip) | Redeploy latest code |

**Real incident (Sept 2026):** WhatsApp verified fine but no message ever
arrived. Cause: the WhatsApp callback URL was saved as `/webhooks/meta`
(no slash). The GET verify followed Django's redirect, but Meta never follows
redirects for POSTs. The server now accepts both forms, but still save it
**with** the slash.

---

## 10. Checklist for a new client

- [ ] Server deployed, domain loads over HTTPS (06_DEPLOYMENT.md)
- [ ] Meta app created, App secret copied, privacy policy set, **Live**
- [ ] System user created, assets assigned, never-expiring token generated
- [ ] Page ID, Page token, Instagram account ID collected (§4.1)
- [ ] WhatsApp number registered; **real** Phone number ID + WABA ID collected
- [ ] 3 channels created in the dashboard with correct JSON (§5)
- [ ] Verify curl prints `ok123`
- [ ] App webhooks registered for `page`, `instagram`, `whatsapp_business_account` — URL ends in `/`
- [ ] Page and WABA `subscribed_apps` done
- [ ] Instagram (§5A): Instagram app secret + IGAA token in the channel, Webhook Subscription On, tester added
- [ ] Instagram *Allow access to messages* ON
- [ ] Calendar reminder to refresh the IGAA token every ~50 days
- [ ] §7 check all green
- [ ] Live test on each channel gets a reply
- [ ] App Review submitted for `pages_messaging` + `instagram_manage_messages` (for public customers on Messenger/Instagram)
