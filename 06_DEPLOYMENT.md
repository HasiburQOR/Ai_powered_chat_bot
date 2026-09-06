# 06 — Production Deployment Guide

This guide takes the chatbot from a dev machine to a live, HTTPS-secured production server. It assumes a fresh Ubuntu 22.04/24.04 VPS (Hetzner, DigitalOcean, Linode, AWS Lightsail, etc.) with at least **2 GB RAM** (the embedding model needs it — add swap if you only have 1 GB).

**Production architecture:**

```
Internet → Caddy (HTTPS, port 443) → Docker web container (Gunicorn, localhost:8001)
                                     → Docker worker + beat (Celery)
                                     → Docker db (PostgreSQL + pgvector) & redis
Meta / WordPress  ──────────────────→ webhooks + widget endpoints on same host
```

---

## 0. Pre-launch checklist (do this BEFORE going live)

| # | Item | How |
|---|------|-----|
| 1 | `DEBUG=False` in `.env` | It breaks on any unhandled error otherwise and leaks settings |
| 2 | New `SECRET_KEY` | `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"` |
| 3 | New `POSTGRES_PASSWORD` | `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| 4 | New `FIELD_ENCRYPTION_KEY` | `python -c "import base64,os; print(base64.b64encode(os.urandom(32)).decode())"` — ⚠️ generate **before** entering channel credentials in the dashboard. Rotating it later makes stored credentials unreadable. |
| 5 | `ALLOWED_HOSTS=your-domain.com` | Exact domain, no `*` in production |
| 6 | `CSRF_TRUSTED_ORIGINS=https://your-domain.com` | Otherwise login/dashboard POSTs fail with 403 |
| 7 | SMTP email configured | Password reset emails must actually send — see §6 |
| 8 | Strong password on every staff account | Dashboard login + password reset are wired at `/accounts/login/` |

Everything above is just filling in `.env` (see `.env.example` for explanations of each variable).

---

## 1A. Deploy with Dokploy (Option A — recommended, easiest)

Dokploy handles git pulls, image builds, the reverse proxy and Let's Encrypt
TLS for you — you can **skip §1–§5 entirely** (no manual server prep, Docker
install, or Caddy). The stack is the same; the Dokploy variant
(`docker-compose.dokploy.yml`) just drops the localhost port binding and lets
Dokploy's Traefik reach `web:8000` over the shared `dokploy-network`.

1. **Push the code to a *private* git repo** (GitHub/GitLab/Bitbucket/Gitea —
   Dokploy supports all of them). The real `.env` is gitignored, so secrets
   never enter git — you'll paste them into the Dokploy UI instead.

2. **Create the resource**: Dokploy panel → *New Resource* → **Docker Compose**.
   - Connect your git provider (GitHub App or personal access token)
   - Repository / branch: your repo, `main`
   - Compose file path: `docker-compose.dokploy.yml`

3. **Environment variables** (resource → *Environment*): paste the production
   values from `.env.example` — at minimum:
   `DEBUG=False`, `SECRET_KEY`, `ALLOWED_HOSTS=chat.yourdomain.com`,
   `CSRF_TRUSTED_ORIGINS=https://chat.yourdomain.com`, `POSTGRES_DB`,
   `POSTGRES_USER`, `POSTGRES_PASSWORD`, `FIELD_ENCRYPTION_KEY`, and the
   `EMAIL_*` SMTP block (needed for password reset).
   `POSTGRES_HOST` / `REDIS_URL` are already baked into the compose file.
   Dokploy writes these to the `.env` file next to the compose file, which is
   exactly what the services load.

4. **Domain**: resource → *Domains* → add `chat.yourdomain.com`, service
   **web**, port **8000**. Traefik fetches the certificate automatically.
   Point the DNS A record of the (sub)domain at the server **first**.

5. **Deploy**. The first build downloads CPU-only PyTorch (~200 MB) — expect a
   few minutes. The entrypoint then waits for Postgres, runs migrations and
   collects static files automatically. No manual steps needed.

6. **Create your first staff login**: resource → containers → `web` → open a
   terminal and run:
   ```
   python manage.py createsuperuser
   ```
   Then log in at `https://chat.yourdomain.com/accounts/login/`.

7. Continue with §6 (SMTP is already covered by the env vars), §7 Meta
   webhook (`https://chat.yourdomain.com/webhooks/meta/`), §8 widget embed,
   and the §9 go-live checklist.

**Notes**
- Updates: push to `main` → Dokploy rebuilds and redeploys; the entrypoint
  re-runs migrations automatically.
- Backups: your data lives in this resource's `pgdata` volume — the §10
  `pg_dump` command works from a terminal on the `db` container.
- If Traefik returns 502/bad gateway, verify the `dokploy-network` exists on
  the server (Dokploy creates it on install) and that `web` is attached —
  it is, in `docker-compose.dokploy.yml`.

## 1. Prepare the server (Option B — plain VPS + Docker + Caddy; skip if using Dokploy §1A)

```bash
# As root on a fresh Ubuntu VPS:

# 1a. Create a deploy user
adduser deploy
usermod -aG sudo deploy

# 1b. Harden SSH (optional but recommended): disable root + password login
#     edit /etc/ssh/sshd_config:
#       PermitRootLogin no
#       PasswordAuthentication no
systemctl restart ssh

# 1c. Basic firewall
ufw allow OpenSSH
ufw allow 80
ufw allow 443
ufw enable

# 1d. Swap (important for the ML embedding model on small VPSes)
fallocate -l 2G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

**DNS:** create an `A` record pointing your subdomain (e.g. `chat.yourcompany.com`) at the server's public IP. Do this first — Caddy needs it to issue an HTTPS certificate.

## 2. Install Docker

```bash
# As root:
curl -fsSL https://get.docker.com | sh
usermod -aG docker deploy
su - deploy   # from now on work as the deploy user
docker compose version   # should print v2.x
```

## 3. Get the code & configure environment

```bash
# As deploy:
sudo apt-get install -y git
git clone <YOUR_REPO_URL> chatbot
cd chatbot

cp .env.example .env
nano .env   # fill in everything from the §0 checklist
```

Generate the three secrets and paste them into `.env`:

```bash
python -c "from django.core.management.utils import get_random_secret_key; print('SECRET_KEY=' + get_random_secret_key())"
python -c "import secrets; print('POSTGRES_PASSWORD=' + secrets.token_urlsafe(32))"
python -c "import base64,os; print('FIELD_ENCRYPTION_KEY=' + base64.b64encode(os.urandom(32)).decode())"
```

Protect the file: `chmod 600 .env`

## 4. Launch the stack

```bash
docker compose up -d --build
```

The first build downloads PyTorch/sentence-transformers (~5 minutes). The `web` container automatically waits for Postgres, runs `migrate`, and runs `collectstatic` (see `entrypoint.sh`) — no manual steps needed.

Create your first staff account:

```bash
docker compose exec web python manage.py createsuperuser
```

Sanity checks:

```bash
docker compose ps              # all services should be Up (healthy)
docker compose logs -f web     # watch it migrate and start Gunicorn
curl http://localhost:8001/    # should return the "It works!" page
```

> **Local development** (on your dev machine) keeps hot-reload via a separate override file:
> `docker compose -f docker-compose.yml -f docker-compose.dev.yml up`
> Run migrations manually in dev: `docker compose exec web python manage.py migrate`

## 5. HTTPS + domain with Caddy (recommended)

Caddy gives you automatic Let's Encrypt certificates with a 5-line config.

```bash
# As root:
apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
apt-get update && apt-get install -y caddy
```

`/etc/caddy/Caddyfile`:

```
chat.yourcompany.com {
    reverse_proxy localhost:8001

    # The chat widget is embedded in an iframe on the customer site and
    # sets its own frame-ancestors CSP, so don't add global frame rules here.
}
```

```bash
systemctl reload caddy
```

Visit `https://chat.yourcompany.com/accounts/login/` — you should see the branded login page with a valid padlock. Log in, then `https://.../dashboard/` works.

**Alternative — Cloudflare Tunnel** (no open ports at all, good if you already use Cloudflare): create a tunnel in the Cloudflare Zero Trust dashboard, map `chat.yourcompany.com` to service `http://localhost:8001`, then on the server:

```bash
docker run -d --name cloudflared --restart unless-stopped \
  --network host \
  cloudflare/cloudflared:latest tunnel --no-autoupdate run --token <TOKEN_FROM_CLOUDFLARE_DASHBOARD>
```

(With `--network host` the tunnel can reach the web container's `localhost:8001` binding.)

## 6. Email (SMTP) — required for password reset

Password reset sends real emails. Cheapest reliable options:

| Provider | Free tier | Notes |
|----------|-----------|-------|
| **Brevo** (brevo.com) | 300 emails/day | Recommended — SMTP works out of the box |
| Gmail | — | Needs a Google **App Password** (not your normal password) |
| Resend / Postmark | 100/day | Modern, simple |

Example with Brevo: create account → SMTP & API → copy host `smtp-relay.brevo.com`, port `587`, your login string and SMTP key into `.env`:

```
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp-relay.brevo.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=your-login
EMAIL_HOST_PASSWORD=your-smtp-key
DEFAULT_FROM_EMAIL=Chatbot Studio <noreply@yourcompany.com>
```

Apply + test (sends a real email to your own inbox):

```bash
docker compose up -d   # apply new .env values
docker compose exec web python manage.py shell -c \
  "from django.core.mail import send_mail; print(send_mail('SMTP test', 'It works.', None, ['you@yourcompany.com']))"
# should print: 1
```

Then test the full flow in the browser: `/accounts/password_reset/` → enter your email → check inbox → open link → set a new password → log in with it.

> Make sure the staff user has an **email address** set (Django admin → Users → edit), otherwise reset lookup won't find the account.

## 7. Wire the Meta webhook (Instagram / Messenger)

1. In the [Meta developer app](https://developers.facebook.com/apps) → *Webhooks* → add callback URL:
   `https://chat.yourcompany.com/webhooks/meta/`
2. Verify token: must match the `verify_token` stored in the channel's credentials (set via the dashboard → Channels).
3. Subscribe to `messages` / `messaging_postbacks` fields for the page/IG account.
4. Paste the app secret + page access token in the dashboard channel (they're encrypted at rest with `FIELD_ENCRYPTION_KEY`).

Until Meta approves the app for public use, Instagram/Messenger work with test accounts only — the website widget is unaffected.

## 8. Embed the website chat widget on WordPress

In the WordPress page (or site-wide footer via your theme / a header-footer plugin):

```html
<script src="https://chat.yourcompany.com/widget/embed.js"
        data-site-key="YOUR_SITE_KEY"
        data-position="right"
        data-theme="#4f46e5"
        defer></script>
```

Create the WordPress channel in the dashboard first (Channels → New) — the `site_key` and the customer's `allowed_domain` (e.g. `https://www.yourcompany.com`) live in its credentials. The domain check scopes iframe embedding to that one site.

## 9. Go-live test checklist

- [ ] `https://your-domain/` loads (status page)
- [ ] `/accounts/login/` — log in with staff account → lands on dashboard
- [ ] Wrong password → clear error message
- [ ] `/accounts/password_reset/` → real email arrives → link opens → new password works
- [ ] Dashboard "Change password" (top-right 🔑) works and old password stops working
- [ ] Log out → all dashboard URLs redirect to login
- [ ] Widget on WordPress: bubble opens, conversation replies, session persists on reload
- [ ] `/webhooks/meta/` GET with verify token returns the challenge
- [ ] Instagram/Messenger test message gets an AI reply
- [ ] `docker compose ps` — everything healthy
- [ ] HTTP redirects to HTTPS (`curl -I http://your-domain` → 301)

## 10. Day-to-day operations

**Logs**
```bash
docker compose logs -f web      # gunicorn + django
docker compose logs -f worker   # celery tasks (bot replies)
docker compose logs -f beat
```

**Backups** — put in `/etc/cron.daily/chatbot-backup` (as root, then `chmod +x`):
```bash
#!/bin/sh
mkdir -p /home/deploy/backups
cd /home/deploy/chatbot && docker compose exec -T db pg_dump -U chatbot chatbot | gzip > /home/deploy/backups/chatbot_$(date +%F).sql.gz
find /home/deploy/backups -name 'chatbot_*.sql.gz' -mtime +14 -delete
```
Restore with: `gunzip -c backup.sql.gz | docker compose exec -T db psql -U chatbot chatbot`

**Deploy an update**
```bash
cd ~/chatbot
git pull
docker compose up -d --build    # rebuilds image, applies new migrations
```

**Rollback**
```bash
git checkout <previous-tag-or-commit>
docker compose up -d --build
```

**Basic uptime monitoring:** point a free service (UptimeRobot, Healthchecks.io) at `https://your-domain/accounts/login/` — a 200 means the whole proxy→gunicorn chain is alive.

## 11. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| 400 Bad Request at the domain | `ALLOWED_HOSTS` missing the domain |
| 403 on login POST | `CSRF_TRUSTED_ORIGINS` missing `https://domain` |
| Redirect loop http↔https | Proxy not sending `X-Forwarded-Proto`; keep `SECURE_PROXY_SSL_HEADER` (already set), or set `SECURE_SSL_REDIRECT=False` if the proxy enforces TLS itself |
| Reset email never arrives | `docker compose logs web` — SMTP errors show there; verify `EMAIL_*` in `.env`, then `docker compose up -d` to apply |
| 502 from Caddy | web container down: `docker compose ps`, `docker compose logs web` |
| Widget 403 "Unknown or inactive site key" | `site_key` mismatch or channel inactive in dashboard |
| Webhook verification fails | Verify token in Meta app ≠ `verify_token` in channel credentials |
| Bot replies slow on first message | Worker downloading the embedding model — check `docker compose logs worker`; afterwards it's cached in the `hf_cache` volume |
| Out of memory | Add/increase swap (§1d); the embedding model needs ~1 GB |


