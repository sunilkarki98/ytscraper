# YouTube Email Scraper Pro — SaaS Platform

> Extract verified business emails from YouTube creators at scale. Search by niche keyword, get emails + social links in real-time, export CSV/JSON.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Tech Stack](#tech-stack)
- [Directory Structure](#directory-structure)
- [Database Schema](#database-schema)
- [API Reference](#api-reference)
- [Authentication System](#authentication-system)
- [Scraping Engine](#scraping-engine)
- [Credit System & Billing](#credit-system--billing)
- [Queue System](#queue-system)
- [WebSocket Real-Time Updates](#websocket-real-time-updates)
- [Frontend](#frontend)
- [Security](#security)
- [Environment Variables](#environment-variables)
- [Local Development](#local-development)
- [Docker Deployment](#docker-deployment)
- [Coolify / Production Deploy](#coolify--production-deploy)
- [Pricing Tiers](#pricing-tiers)
- [Legal Pages](#legal-pages)
- [Known Issues & TODOs](#known-issues--todos)

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                        BROWSER (SPA)                         │
│  static/index.html + app.js + style.css                      │
│  - Auth (signup/login)   - Scrape form   - Results table     │
│  - History tab           - Export CSV/JSON - Credit display   │
└────────────┬──────────────────┬───────────────────────────────┘
             │ REST API         │ WebSocket (real-time emails)
             ▼                  ▼
┌──────────────────────────────────────────────────────────────┐
│                  FastAPI Server (app.py)                      │
│  Port 8000 — Uvicorn ASGI                                    │
│                                                              │
│  ┌─────────┐  ┌──────────┐  ┌───────────┐  ┌─────────────┐  │
│  │  Auth   │  │  Queue   │  │  Spider   │  │  WebSocket  │  │
│  │ (auth.py│  │ Manager  │  │ (engine/) │  │  Broadcast  │  │
│  │  JWT)   │  │ priority │  │ curl_cffi │  │  per job    │  │
│  └────┬────┘  └────┬─────┘  └─────┬─────┘  └──────┬──────┘  │
│       │            │              │                │          │
│       └────────────┴──────────────┴────────────────┘          │
│                            │                                  │
│                    ┌───────▼────────┐                         │
│                    │   SQLAlchemy   │                         │
│                    │  (database.py) │                         │
│                    └───────┬────────┘                         │
└────────────────────────────┼─────────────────────────────────┘
                             │
              ┌──────────────┴──────────────┐
              │   SQLite (dev) / PostgreSQL  │
              │      (prod — Supabase)      │
              └─────────────────────────────┘
```

---

## Tech Stack

| Layer      | Technology                                      |
|------------|------------------------------------------------|
| **Backend**    | Python 3.12, FastAPI, Uvicorn, SQLAlchemy (async) |
| **Database**   | SQLite (dev) / PostgreSQL via Supabase (prod)  |
| **Auth**       | JWT (PyJWT), PBKDF2-SHA256 password hashing    |
| **Scraping**   | curl_cffi (TLS fingerprint spoofing), aiohttp  |
| **Real-time**  | WebSockets (native FastAPI)                    |
| **Rate Limit** | slowapi                                        |
| **Frontend**   | Vanilla HTML/CSS/JS (single-page app)          |
| **Deploy**     | Docker, Coolify                                |

---

## Directory Structure

```
standalone/
├── app.py                  # Main FastAPI server (all routes, spider launch, WebSocket)
├── auth.py                 # JWT auth, password hashing, credit deduction
├── database.py             # SQLAlchemy async engine (SQLite / PostgreSQL)
├── models.py               # DB models: User, Job, Result, Usage
├── queue_manager.py        # Job queue, retention cleanup, usage tracking
│
├── engine/                 # Scraping engine
│   ├── __init__.py
│   ├── spider.py           # MaxSpeedSpider — core scraping logic (1683 lines)
│   ├── config.py           # Spider configuration (workers, timeouts, etc.)
│   ├── proxy_manager.py    # Proxy rotation, health checking, load balancing
│   ├── fingerprint.py      # Browser TLS fingerprint rotation
│   └── email_validator.py  # Email extraction from HTML, regex validation
│
├── static/                 # Frontend (served by FastAPI)
│   ├── index.html          # Main SPA (auth + dashboard + scraper UI)
│   ├── app.js              # All frontend logic (auth, scraping, WebSocket, UI)
│   ├── style.css           # Full design system (dark sidebar, cards, animations)
│   ├── pricing.html        # Pricing page (4 tiers + enterprise form)
│   ├── terms.html          # Terms of Service (lawful use, CAN-SPAM, GDPR)
│   └── privacy.html        # Privacy Policy (GDPR rights, data retention)
│
├── Dockerfile              # Production Docker image (python:3.12-slim)
├── docker-compose.yml      # Local Docker Compose setup
├── requirements.txt        # Python dependencies
├── .env.example            # Environment variable template
├── .dockerignore           # Docker build excludes
├── .gitignore              # Git excludes
├── proxies.txt             # Proxy list (Webshare format)
└── data/                   # SQLite database directory (auto-created)
    └── scraper.db          # Local SQLite database
```

---

## Database Schema

Four tables — all defined in `models.py`:

### `users`
| Column            | Type         | Description                           |
|-------------------|-------------|---------------------------------------|
| `id`              | String(12)  | Primary key (UUID prefix)             |
| `email`           | String(255) | Unique, indexed                       |
| `password_hash`   | String(255) | PBKDF2-SHA256 with salt               |
| `name`            | String(100) | Display name                          |
| `free_credits`    | Integer     | Free emails remaining (starts at 600) |
| `paid_credits`    | Integer     | Purchased email credits               |
| `has_db_addon`    | Boolean     | $5/mo extended storage active         |
| `db_addon_expires`| DateTime    | When add-on expires                   |
| `is_active`       | Boolean     | Account active flag                   |
| `is_verified`     | Boolean     | Email verified flag                   |
| `created_at`      | DateTime    | Account creation timestamp            |

### `jobs`
| Column            | Type         | Description                           |
|-------------------|-------------|---------------------------------------|
| `id`              | String(12)  | Primary key                           |
| `user_id`         | String(12)  | FK → users.id                         |
| `keyword`         | String(255) | Search keyword                        |
| `status`          | String(20)  | `queued` / `running` / `completed` / `stopped` / `error` |
| `email_count`     | Integer     | Final email count                     |
| `max_emails`      | Integer     | Requested limit (up to 1000)          |
| `country`         | String(5)   | Country filter                        |
| `language`        | String(5)   | Language filter (default: en)         |
| `filters_json`    | Text        | JSON filters config                   |
| `min_subscribers` | Integer     | Min subscriber filter                 |
| `max_subscribers` | Integer     | Max subscriber filter                 |
| `timeout_minutes` | Integer     | Job timeout (1–120 min)               |
| `queue_position`  | Integer     | Position in queue (nullable)          |
| `expires_at`      | DateTime    | Auto-delete date (7 days for free)    |
| `created_at`      | DateTime    | Job creation time                     |
| `started_at`      | DateTime    | When spider started                   |
| `completed_at`    | DateTime    | When spider finished                  |
| `channels_scanned`| Integer     | Total channels checked                |
| `stats_json`      | Text        | Spider stats JSON                     |

### `results`
| Column          | Type         | Description                          |
|-----------------|-------------|--------------------------------------|
| `id`            | Integer     | Auto-increment PK                    |
| `job_id`        | String(12)  | FK → jobs.id (CASCADE delete)        |
| `user_id`       | String(12)  | FK → users.id                        |
| `email`         | String(255) | Extracted email address              |
| `channel_name`  | String(255) | YouTube channel name                 |
| `channel_url`   | String(500) | YouTube channel URL                  |
| `channel_id`    | String(50)  | YouTube channel ID                   |
| `subscribers`   | Integer     | Subscriber count                     |
| `source`        | String(20)  | Always "youtube"                     |
| `extracted_at`  | DateTime    | When email was found                 |
| `search_keyword`| String(255) | Keyword that found this result       |
| `instagram`     | String(500) | Instagram URL                        |
| `twitter`       | String(500) | Twitter/X URL                        |
| `tiktok`        | String(500) | TikTok URL                           |
| `facebook`      | String(500) | Facebook URL                         |
| `linkedin`      | String(500) | LinkedIn URL                         |
| `website`       | String(500) | Personal website URL                 |

**Unique index**: `(user_id, email)` — prevents duplicate emails per user.

### `usage`
| Column          | Type         | Description                          |
|-----------------|-------------|--------------------------------------|
| `id`            | Integer     | Auto-increment PK                    |
| `user_id`       | String(12)  | FK → users.id                        |
| `month`         | String(7)   | Format: "2026-03"                    |
| `emails_scraped`| Integer     | Emails found this month              |
| `jobs_run`      | Integer     | Jobs executed this month             |
| `credits_used`  | Integer     | Credits consumed this month          |

**Unique index**: `(user_id, month)` — one record per user per month.

---

## API Reference

All authenticated endpoints require `Authorization: Bearer <jwt_token>` header.

### Auth

| Method | Endpoint          | Auth | Description                           |
|--------|------------------|------|---------------------------------------|
| POST   | `/api/signup`     | No   | Create account. Body: `{email, password, name}` |
| POST   | `/api/login`      | No   | Login. Body: `{email, password}`. Returns JWT. |
| GET    | `/api/me`         | Yes  | Get current user profile + credits    |

**Signup validation**: Email format check, password min 8 chars with uppercase + number.  
**Login throttle**: Max 10 attempts per 5 minutes per IP.

### Scraping

| Method | Endpoint                | Auth | Description                                    |
|--------|------------------------|------|------------------------------------------------|
| POST   | `/api/start`           | Yes  | Start a scrape job. Body: `StartRequest` below |
| DELETE | `/api/stop/{job_id}`   | Yes  | Stop a running job. Credits deducted for found emails only. |
| GET    | `/api/status/{job_id}` | Yes  | Get job status, email count, elapsed time      |
| GET    | `/api/results/{job_id}`| Yes  | Get all results for a job                      |
| GET    | `/api/export/{job_id}` | Yes  | Export results. Query `?format=csv` or `?format=json` |
| GET    | `/api/jobs`            | Yes  | List last 50 jobs for current user             |
| WS     | `/ws/{job_id}` | Yes  | WebSocket for real-time email streaming (Auth via JSON payload) |

**StartRequest body**:
```json
{
  "keyword": "fitness youtubers",
  "maxEmails": 500,
  "country": "",
  "language": "en",
  "minSubscribers": 1000,
  "maxSubscribers": 0,
  "minViews": 0,
  "minDuration": 0,
  "maxDuration": 0,
  "timeoutMinutes": 30
}
```

### Other

| Method | Endpoint                   | Auth | Description                           |
|--------|---------------------------|------|---------------------------------------|
| GET    | `/health`                 | No   | Health check (returns `{status: ok}`) |
| GET    | `/api/queue/status`       | No   | Queue info: running/queued counts     |
| POST   | `/api/enterprise-inquiry` | No   | Enterprise form submission            |

---

## Authentication System

**File**: `auth.py`

- **Password hashing**: PBKDF2-SHA256 with random 16-byte salt, 100,000 iterations
- **Token format**: JWT with HS256, 72-hour expiry
- **Secret key**: `JWT_SECRET` env var (auto-generated if missing — **must set in production**)
- **Login protection**: IP-based throttle (10 attempts / 5 min window)
- **ToS requirement**: Signup requires Terms of Service checkbox

### Token payload:
```json
{
  "sub": "user_id_12ch",
  "email": "user@example.com",
  "exp": 1710000000,
  "iat": 1709740800
}
```

---

## Scraping Engine

**Directory**: `engine/`

### `spider.py` — MaxSpeedSpider v5.0

The core scraping engine. Key characteristics:

- **Zero browser dependency** — uses `curl_cffi` for TLS fingerprint-spoofed HTTP requests (no Selenium/Playwright)
- **Streaming producer-consumer** — search + channel fetch run simultaneously via `asyncio.Queue`
- **Keyword expansion** — Uses YouTube's autocomplete API to generate related search terms automatically
- **Multi-worker** — Configurable search workers + channel workers running in parallel
- **Deduplication** — In-memory set prevents duplicate emails
- **Graceful stop** — Responds to `asyncio.CancelledError` and stop flags

### Data extraction flow:
```
1. Search YouTube for keyword → get channel URLs from results
2. Expand keywords via YouTube autocomplete → more searches
3. Fetch each channel's /about page
4. Parse ytInitialData JSON → extract:
   - Email addresses (from description)
   - Channel name, subscriber count, channel ID
   - Social links (Instagram, Twitter/X, TikTok, Facebook, LinkedIn, Website)
5. Validate emails (regex + domain check)
6. Push to callback → stored in DB + broadcast via WebSocket
```

### `proxy_manager.py`
- Loads proxies from `proxies.txt` (Webshare `ip:port:user:pass` format)
- Round-robin rotation with health checking
- Automatic dead proxy removal

### `fingerprint.py`
- Rotates browser TLS fingerprints to avoid detection
- Mimics Chrome, Firefox, Safari impersonation profiles

### `email_validator.py`
- Regex-based email extraction from HTML/text
- Live `aiodns` asynchronous SMTP MX record validation
- Filters out common false positives (image files, example domains, etc.)

### `config.py`
- Spider configuration: worker counts, timeouts, retry limits
- YouTube API parameters, consent cookies
- Search parameter encoding (SP params for filters)

---

## Credit System & Billing

### How credits work:

1. **Signup** → User gets **600 free credits** (1 credit = 1 email found)
2. **Starting a job** → Backend checks if user has enough credits, caps job if needed
3. **During scraping** → Emails stream in real-time, no credits deducted yet
4. **Job completion or stop** → Credits deducted for **actual emails found only**
5. **Credit deduction order** → Free credits first, then paid credits

### Credit deduction logic (`auth.py → deduct_credits()`):
```python
# Free credits used first
if user.free_credits >= count:
    user.free_credits -= count
else:
    remaining = count - user.free_credits
    user.free_credits = 0
    user.paid_credits -= remaining
```

### Double-deduction prevention:
The `stop_job` endpoint and spider's `finally` block both handle finalization. A guard in the `finally` block checks if `stop_job` already finalized the job (by checking `running_spiders` dict) to prevent double-charging.

---

## Queue System

**File**: `queue_manager.py`

### Configuration:
```python
MAX_CONCURRENT_JOBS = 3       # Max spiders running at once
MAX_FREE_CONCURRENT = 1       # Max free-user jobs running at once
FREE_USER_MAX_EMAILS = 600    # Free tier cap
RETENTION_DAYS_FREE = 7       # Auto-delete after 7 days
```

### Queue logic:
1. If server has capacity → job starts immediately
2. If server is full → job is queued
3. Paid users are inserted at the **front** of the queue
4. Free users are inserted at the **back**
5. Background task polls every 2 seconds and starts queued jobs when slots open

### Data retention:
- **All users**: 7-day data retention (auto-deleted by background worker)
- **Extended storage**: $5/mo add-on → disables auto-deletion
- Background `retention_worker` runs every hour, deleting expired jobs + results (CASCADE)

---

## WebSocket Real-Time Updates

**Endpoint**: `ws://host/ws/{job_id}`

### Auth:
- JWT passed securely as initial JSON payload (`{"action": "auth", "token": "..."}`)
- Job ownership verified before sending any events

### Messages (server → client):

**New email found**:
```json
{"type": "email", "data": {"email": "...", "channelName": "...", ...}, "total": 15}
```

**Job completed/stopped**:
```json
{"type": "done", "status": "completed", "total": 150, "stats": {...}}
```

### Reconnection support:
When a WebSocket connects to a running job, all existing results are replayed immediately.

---

## Frontend

**Files**: `static/index.html`, `static/app.js`, `static/style.css`

### Single-page app with sections:
1. **Auth screen** — Login/Signup with ToS checkbox, "View Pricing" link
2. **Scraper tab** — Keyword input, filters (subscribers, country, language), max emails slider
3. **Results tab** — Real-time email table as scraping runs, export buttons (CSV/JSON)
4. **History tab** — Past jobs with status, re-download capability
5. **Sidebar** — Credit display, navigation, Pricing · Terms · Privacy links

### Key frontend functions (`app.js`):
| Function              | Description                                       |
|----------------------|---------------------------------------------------|
| `handleAuth()`       | Login/signup with validation                      |
| `toggleAuthMode()`   | Switch between login/signup (shows ToS checkbox)  |
| `launchScrape()`     | Start scraping job, connect WebSocket              |
| `stopScraping()`     | Stop running job via API                          |
| `connectWebSocket()` | Real-time email streaming from spider             |
| `exportResults()`    | Download CSV/JSON                                 |
| `loadHistory()`      | Fetch and display past jobs                       |
| `updateCreditDisplay()` | Show remaining credits in sidebar              |

---

## Security

### Implemented protections:

| Feature                  | Implementation                                |
|--------------------------|----------------------------------------------|
| **CORS lockdown**        | Explicit allowed origins (not wildcard `*`)   |
| **Rate limiting**        | slowapi — per-endpoint limits                |
| **Login throttle**       | 10 attempts / 5 min per IP                   |
| **Password validation**  | Min 8 chars, uppercase + number required      |
| **Email validation**     | Regex format check on signup                  |
| **Input sanitization**   | Pydantic models with Field constraints       |
| **JWT auth**             | Bearer token required on all protected routes |
| **Job ownership**        | Users can only access their own jobs          |
| **WebSocket auth**       | JWT verified before accepting connection      |
| **SQL injection**        | SQLAlchemy parameterized queries              |
| **Health check**         | `/health` endpoint for uptime monitoring      |

---

## Environment Variables

Copy `.env.example` to `.env` and configure:

```env
# Required
JWT_SECRET=<random-64-char-hex-string>

# Database (choose one)
DATABASE_URL=sqlite+aiosqlite:///./data/scraper.db          # Local dev (default)
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/db     # Production (Supabase)

# Server
PORT=8000

# Proxies (loaded from proxies.txt file, Webshare format: ip:port:user:pass)
```

### Generate a JWT secret:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## Local Development

### Prerequisites:
- Python 3.12+
- pip

### Setup:
```bash
cd standalone

# Install dependencies
pip install -r requirements.txt

# Run (SQLite auto-created at data/scraper.db)
python app.py
```

Open `http://localhost:8000` in your browser.

### Requirements (`requirements.txt`):
```
fastapi
uvicorn
websockets
aiohttp
curl_cffi
sqlalchemy
aiosqlite
pyjwt
slowapi
asyncpg
```

---

## Docker Deployment

### Build & run locally:
```bash
cd standalone
docker build -t yt-scraper .
docker run -p 8000:8000 -e JWT_SECRET=your_secret_here yt-scraper
```

### Docker Compose:
```bash
docker-compose up --build
```

### Dockerfile details:
- Base: `python:3.12-slim`
- System deps: `gcc`, `g++`, `curl`, `libcurl4-openssl-dev`, `libssl-dev` (for curl_cffi)
- Health check: `curl -f http://localhost:${PORT}/health` every 30s
- Entrypoint: `python app.py`

---

## Coolify / Production Deploy

### Steps:
1. **Create Supabase project** → get connection string
2. **In Coolify**, create new resource →  Docker → point to Git repo
3. **Set environment variables**:
   ```
   DATABASE_URL=postgresql+asyncpg://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres
   JWT_SECRET=<generated-secret>
   PORT=8000
   ```
4. **Set domain** in Coolify
5. **Deploy** — Coolify builds Docker image and runs it

### Database auto-setup:
On first start, `init_db()` runs `CREATE TABLE IF NOT EXISTS` for all 4 tables. No migrations needed for fresh deploys.

### PostgreSQL connection pool (auto-configured in `database.py`):
```python
pool_size=20
max_overflow=10
pool_pre_ping=True       # Detect stale connections
pool_recycle=3600        # Recycle connections hourly
```

---

## Pricing Tiers

All plans have **identical features** — the only difference is email volume.

| Plan       | Price    | Emails           | Notes                              |
|-----------|---------|------------------|-------------------------------------|
| Free       | $0      | 600 (one-time)   | No credit card required             |
| Starter    | $29/mo  | 4,000 / month    | All features included               |
| Pro        | $59/mo  | 8,000 / month    | All features included (most popular)|
| Business   | $99/mo  | 14,000 / month   | All features included               |
| Enterprise | Custom  | Custom volume     | Contact via form                    |

**Rate**: ~$0.0073 per email across all tiers (same rate).

### All plans include:
- YouTube email extraction + social links
- CSV & JSON export
- Real-time progress tracking
- 7-day data retention
- Subscriber & engagement data
- Advanced filters (subscribers, country, language)
- Email support

### Add-ons:
- **Extended storage**: $5/mo — keeps data beyond 7-day retention

### Enterprise:
- Contact form at `/static/pricing.html` → hits `POST /api/enterprise-inquiry`
- Currently logs to server console (can be extended to store in DB or send email)

---

## Legal Pages

| Page                     | URL                        | Purpose                                |
|--------------------------|---------------------------|----------------------------------------|
| Terms of Service         | `/static/terms.html`      | Lawful use clause, CAN-SPAM, GDPR, liability |
| Privacy Policy           | `/static/privacy.html`    | Data collection, GDPR rights, retention |
| ToS Checkbox (signup)    | Built into `index.html`   | Required before account creation        |

---

## Known Issues & TODOs

### Not yet implemented (future roadmap):
- [ ] **Stripe integration** — payment processing for paid tiers
- [ ] **Email verification** — verify email on signup (field exists, not wired)
- [ ] **Password reset** — forgot password flow
- [ ] **Admin panel** — use Supabase dashboard for now
- [ ] **API keys** — for programmatic access (power users)
- [ ] **Webhook/Zapier integration** — enterprise feature
- [ ] **Email deliverability validation** — ZeroBounce/NeverBounce integration
- [ ] **Enrichment data** — engagement rates, sponsor history

### Architecture notes:
- **Single-process**: All spiders run in the same Python process via asyncio tasks. For high scale, consider worker processes.
- **In-memory tracking**: `running_spiders` and `active_websockets` are in-memory dicts — they reset on restart. Running jobs will be lost on deploy.
- **Proxy dependency**: Without proxies, YouTube will rate-limit/block after ~50-100 requests. Always use proxies in production.
- **curl_cffi**: Required for TLS fingerprint spoofing. Without it, YouTube detects automated requests quickly.

---

## Quick Reference

### Start the server:
```bash
python app.py
```

### Run with Docker:
```bash
docker build -t yt-scraper . && docker run -p 8000:8000 yt-scraper
```

### Generate JWT secret:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### Proxy format (Webshare):
```
ip:port:username:password
```

### Test health:
```bash
curl http://localhost:8000/health
```

---

*Built by LAGIC • © 2026*
