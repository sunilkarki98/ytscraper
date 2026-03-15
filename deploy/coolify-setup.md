# Coolify Deployment Guide — YTEmail Scraper

## Prerequisites
- GCP VM with Ubuntu 24.04 LTS
- Minimum: `e2-standard-2` (2 vCPU, 8 GB RAM), 50 GB disk
- Recommended: `e2-standard-4` (4 vCPU, 16 GB RAM), 50 GB disk
- Firewall: ports 80, 443, 8080 open (8080 is Coolify dashboard)

---

## Step 1: Install Coolify

SSH into your GCP VM and run:

```bash
curl -fsSL https://cdn.coollabs.io/coolify/install.sh | bash
```

Wait 2-3 minutes. When done, Coolify is running on port `8000`.

> **Port conflict fix:** Your app also uses port 8000. Coolify's default dashboard port can be changed after first login in Settings.

## Step 2: Access Coolify Dashboard

Open in your browser:
```
http://YOUR_VM_IP:8000
```

1. Create your admin account
2. Set a secure password

## Step 3: Create a New Project

1. Go to **Projects** → **+ Add**
2. Name it "YTEmail Scraper"
3. Click into the project → **+ Add Resource**
4. Select **Docker Compose**
5. Choose **GitHub** as source (or paste the repo URL)
6. Repository: `https://github.com/sunilkarki98/ytscraper`
7. Branch: `main`
8. **Docker Compose File:** change from `docker-compose.yml` to `docker-compose.coolify.yml`

## Step 4: Configure Environment Variables

In the project settings → **Environment Variables**, add:

```
DATABASE_URL=postgresql+asyncpg://postgres.YOUR_PROJECT:PASSWORD@pooler.supabase.com:6543/postgres
SUPABASE_JWT_SECRET=your-jwt-secret
SUPABASE_URL=https://YOUR_PROJECT.supabase.co
SUPABASE_ANON_KEY=your-anon-key
REDIS_URL=redis://redis:6379/0
PORT=8000
GUNICORN_WORKERS=2
WORKER_REPLICAS=2
WORKER_LIMIT_CPUS=0.4
WORKER_LIMIT_MEMORY=2G
API_LIMIT_CPUS=0.75
API_LIMIT_MEMORY=1.5G
DOMAIN=http://YOUR_VM_IP
ALLOWED_ORIGINS=http://YOUR_VM_IP
```

## Step 5: Deploy

Click **Deploy**. Coolify will:
1. Clone the repository
2. Build all Docker images
3. Start the API, Workers, and Redis
4. Show build logs in real time

## Step 6: Add Domain (Later)

When you have a domain:
1. Point the domain's DNS A record to your VM IP
2. In Coolify, go to project settings → **Domains**
3. Add your domain (e.g., `emailscraperpro.com`)
4. Coolify auto-provisions SSL via Let's Encrypt
5. Update environment variables:
   ```
   DOMAIN=emailscraperpro.com
   ALLOWED_ORIGINS=https://emailscraperpro.com
   ```

## Step 7: Enable Auto-Deploy

In the project → **Webhooks**, Coolify provides a webhook URL.
Add it to your GitHub repo → **Settings** → **Webhooks**.

Now every `git push origin main` triggers automatic deployment!

---

## How Deployments Work After Setup

```
1. You edit code locally
2. git add . && git commit -m "fix something" && git push
3. GitHub webhook triggers Coolify
4. Coolify pulls latest code
5. Builds new Docker images
6. Swaps old containers with new ones (zero downtime)
7. Done — live in ~2 minutes
```
