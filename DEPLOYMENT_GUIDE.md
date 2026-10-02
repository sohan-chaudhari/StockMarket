# Deployment Guide — LEVERAGE

## Overview

**LEVERAGE** is a high-performance Indian stock market analytics, real-time charting, and trading intelligence platform designed for the National Stock Exchange (NSE) and Bombay Stock Exchange (BSE).

This guide provides the complete, production-ready deployment procedure for hosting LEVERAGE on **Amazon Web Services (AWS)** targeting a **1 GB RAM / Single-Worker Architecture**.

### Key Production Architecture Characteristics

* **Compute:** AWS Lightsail ($5/month) or AWS EC2 (`t4g.micro` / `t3.micro`) with 1 GB RAM, 1 vCPU, 30–40 GB SSD.
* **Operating System:** Ubuntu 24.04 LTS (x86_64 or ARM64 / Graviton).
* **Reverse Proxy:** Nginx (HTTP/HTTPS, WebSockets, Security Headers, Static Asset Caching).
* **ASGI Server:** Gunicorn with `uvicorn.workers.UvicornWorker` hard-pinned to **1 worker**.
* **Application Framework:** FastAPI on Python 3.13.
* **Database:** PostgreSQL 15+ / 16 (Local managed or Amazon RDS PostgreSQL) with connection pooling (`pool_size=20`, `max_overflow=30`, `statement_timeout=30s`).
* **Migrations:** Alembic version-controlled schema migrations (`alembic upgrade head`).
* **Market-Data Pipeline:** Real-time streaming via Angel One SmartAPI WebSocket (`SmartWebSocketV2`), background REST polling fallback (`PricePoller`), and asynchronous historical gap-fill recovery (`yfinance`).
* **Memory Bounds:** Glibc memory fragmentation constrained via `MALLOC_ARENA_MAX=2`, AnyIO worker thread pool bounded to 64 tokens, LRU-capped in-memory candle caches (5,000 entries), and pre-rendered UTF-8 byte stream for `/api/all-stocks` (~1.34 MB).

```text
Expected Deployment Time: ~45–60 minutes
Target Baseline Memory:   ~450 MB – 520 MB RAM (providing ~480 MB headroom under 1 GB limit)
```

---

## 1. Create AWS Server

### Option A: AWS Lightsail (Recommended for Simplest Setup & Fixed Pricing)

1. Open the [AWS Lightsail Console](https://lightsail.aws.amazon.com/).
2. Select **Create instance**.
3. Choose instance settings:
   * **Location:** `ap-south-1` (Mumbai) — *Essential for lowest latency to NSE/BSE and broker API endpoints*.
   * **Platform:** Linux/Unix
   * **Blueprint:** OS Only → **Ubuntu 24.04 LTS**
   * **Instance Plan:** **$5/month** (1 GB RAM, 1 vCPU, 40 GB SSD, 2 TB Transfer).
   * **Instance Name:** `leverage-prod-mumbai`
4. Click **Create instance** and wait for the status to turn green (**Running**).

### Option B: AWS EC2 (For Standard AWS VPC / RDS Environments)

* **Instance Type:** `t4g.micro` (ARM64 / Graviton2, 1 GB RAM) or `t3.micro` (x86_64, 1 GB RAM).
* **AMI:** Ubuntu Server 24.04 LTS.
* **Storage:** 30 GB General Purpose SSD (gp3).
* **Region:** `ap-south-1` (Mumbai).

---

## 2. Static IP / Elastic IP

A persistent public IP is required so DNS records remain stable across reboots.

### For AWS Lightsail:
1. Navigate to **Networking** in the Lightsail console.
2. Click **Create static IP**.
3. Select the `leverage-prod-mumbai` instance to attach it.
4. Note your assigned static public IP: `YOUR_SERVER_IP`.

### For AWS EC2:
1. Open the **EC2 Console** → **Network & Security** → **Elastic IPs**.
2. Click **Allocate Elastic IP address** (`ap-south-1`).
3. Select the allocated IP → **Actions** → **Associate Elastic IP address** → Choose your LEVERAGE EC2 instance.

---

## 3. Firewall / Security Group

Configure inbound firewall rules to allow only necessary public traffic.

| Application | Protocol | Port | Source | Purpose |
| :--- | :---: | :---: | :---: | :--- |
| **SSH** | TCP | `22` | `YOUR_ADMIN_IP/32` (or `0.0.0.0/0`) | Secure server administration |
| **HTTP** | TCP | `80` | `0.0.0.0/0` | Let's Encrypt ACME challenges & HTTP-to-HTTPS redirect |
| **HTTPS** | TCP | `443` | `0.0.0.0/0` | Secure public web, REST API, and WebSocket traffic |

> [!IMPORTANT]
> **PostgreSQL (Port 5432) must NOT be exposed publicly.** All database communication must occur locally (`localhost` / `127.0.0.1`) or across a private AWS VPC Security Group.

---

## 4. Connect via SSH

Download your private key (`.pem` file) from AWS, set permissions, and connect:

```bash
chmod 400 /path/to/your-key.pem
ssh -i /path/to/your-key.pem ubuntu@YOUR_SERVER_IP
```

---

## 5. Initial Server Setup

Update system repositories, configure the timezone to **Asia/Kolkata (IST)**, and install required system dependencies.

```bash
# 1. Update and upgrade package cache
sudo apt update && sudo apt upgrade -y

# 2. Set server timezone to Indian Standard Time (IST)
sudo timedatectl set-timezone Asia/Kolkata

# 3. Install core system packages, Python 3, PostgreSQL client libraries, Nginx, and tools
sudo apt install -y \
  python3 \
  python3-pip \
  python3-venv \
  python3-dev \
  libpq-dev \
  libpq5 \
  postgresql \
  postgresql-contrib \
  nginx \
  git \
  certbot \
  python3-certbot-nginx \
  htop \
  curl \
  unzip \
  logrotate

# 4. Verify installed tool versions
python3 --version   # Python 3.12+ / 3.13
psql --version      # PostgreSQL 16+
nginx -v            # Nginx 1.24+
git --version
```

---

## 6. PostgreSQL Setup & 1 GB RAM Tuning

LEVERAGE stores market metadata, multi-timeframe candles (`5m`, `15m`, `30m`, `1h`, `1D`, `1W`, `1M`), user authentication records, and trade history in PostgreSQL.

### 6.1 Initialize Database & User

```bash
# Start and enable PostgreSQL service
sudo systemctl start postgresql
sudo systemctl enable postgresql

# Create the application database and user
sudo -u postgres psql << 'EOF'
CREATE DATABASE stock_data;
CREATE USER leverage_user WITH ENCRYPTED PASSWORD 'YOUR_DB_PASSWORD';
GRANT ALL PRIVILEGES ON DATABASE stock_data TO leverage_user;
ALTER DATABASE stock_data OWNER TO leverage_user;
\c stock_data
GRANT ALL ON SCHEMA public TO leverage_user;
EOF
```

### 6.2 Optimize PostgreSQL for 1 GB Total System RAM

By default, PostgreSQL memory configurations are tuned conservatively. Configure memory bounds to ensure database operations never starve the FastAPI/Gunicorn worker process:

```bash
# Detect PostgreSQL version directory (e.g. /etc/postgresql/16/main/postgresql.conf)
PG_CONF=$(ls /etc/postgresql/*/main/postgresql.conf | head -n 1)

sudo sed -i \
  -e 's/^#*shared_buffers = .*/shared_buffers = 128MB/' \
  -e 's/^#*effective_cache_size = .*/effective_cache_size = 384MB/' \
  -e 's/^#*work_mem = .*/work_mem = 4MB/' \
  -e 's/^#*maintenance_work_mem = .*/maintenance_work_mem = 32MB/' \
  -e 's/^#*max_connections = .*/max_connections = 80/' \
  -e 's/^#*wal_buffers = .*/wal_buffers = 4MB/' \
  -e 's/^#*checkpoint_completion_target = .*/checkpoint_completion_target = 0.9/' \
  "$PG_CONF"

# Restart PostgreSQL to apply memory tuning
sudo systemctl restart postgresql

# Test local database connectivity
PGPASSWORD="YOUR_DB_PASSWORD" psql -U leverage_user -d stock_data -h localhost -c "SELECT 1;"
```

---

## 7. Deploy LEVERAGE Codebase

### 7.1 Clone Repository and Prepare Directory Structure

```bash
# Create dedicated application directory
sudo mkdir -p /opt/leverage
sudo chown -R ubuntu:ubuntu /opt/leverage

# Clone your repository
git clone YOUR_GIT_REPOSITORY_URL /opt/leverage
cd /opt/leverage
```

### 7.2 Create Python Virtual Environment & Install Locked Dependencies

```bash
# Create virtual environment
python3 -m venv /opt/leverage/venv

# Activate environment and upgrade packaging tools
source /opt/leverage/venv/bin/activate
pip install --upgrade pip setuptools wheel

# Install backend dependencies from requirements.txt
pip install -r backend/requirements.txt
```

### 7.3 Complete Production Environment Configuration (`backend/.env`)

Inspect and configure all production environment variables.

Create `/opt/leverage/backend/.env`:

```bash
nano /opt/leverage/backend/.env
```

Populate the file with the following variables:

```ini
# =============================================================================
# LEVERAGE PRODUCTION CONFIGURATION (.env)
# =============================================================================

# Deployment Environment: Disables /docs, /redoc, /openapi.json in production
ENVIRONMENT=production

# Database Connection (PostgreSQL)
DB_HOST=localhost
DB_PORT=5432
DB_NAME=stock_data
DB_USER=leverage_user
DB_PASSWORD=YOUR_DB_PASSWORD
DB_SSLMODE=prefer
DB_SSLROOTCERT=

# Cryptographic Authentication (JWT)
# Generate via: python3 -c "import secrets; print(secrets.token_hex(32))"
JWT_SECRET_KEY=YOUR_GENERATED_64_CHARACTER_HEX_SECRET
JWT_ALGORITHM=HS256
JWT_EXPIRATION_HOURS=24

# Angel One SmartAPI Live Trading / WebSocket Credentials
ANGELONE_API_KEY=YOUR_ANGELONE_API_KEY
ANGELONE_CLIENT_ID=YOUR_ANGELONE_CLIENT_ID
ANGELONE_PASSWORD=YOUR_ANGELONE_PASSWORD
ANGELONE_TOTP_TOKEN=YOUR_ANGELONE_TOTP_BASE32_SECRET

# Historical Data API Credentials (usually same as live)
HISTORICAL_API_KEY=YOUR_ANGELONE_API_KEY
HISTORICAL_CLIENT_ID=YOUR_ANGELONE_CLIENT_ID
HISTORICAL_PASSWORD=YOUR_ANGELONE_PASSWORD
HISTORICAL_TOTP_TOKEN=YOUR_ANGELONE_TOTP_BASE32_SECRET

# Optional Email / SMTP Credentials (for user password reset & verification)
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your_email@gmail.com
SMTP_PASSWORD=your_app_password
FRONTEND_URL=https://YOUR_DOMAIN

# Memory & Diagnostic Flags
MEMORY_TRACE=0
CSRF_SECURE=true
```

Secure permissions on the `.env` file:
```bash
chmod 600 /opt/leverage/backend/.env
```

### 7.4 Environment Variable Reference Table

| Variable | Required | Default | Purpose / Source |
| :--- | :---: | :---: | :--- |
| `ENVIRONMENT` | **Yes** | `development` | Setting `production` disables `/docs`, `/redoc`, and Swagger endpoints. |
| `DB_HOST` | **Yes** | `localhost` | PostgreSQL host (`localhost` or RDS hostname). |
| `DB_PORT` | **Yes** | `5432` | PostgreSQL port. |
| `DB_NAME` | **Yes** | `stock_data` | Application database name. |
| `DB_USER` | **Yes** | `postgres` | Database user. |
| `DB_PASSWORD` | **Yes** | `""` | Database password. |
| `DB_SSLMODE` | No | `prefer` | SSL negotiation mode (`prefer` locally, `verify-full` for RDS). |
| `DB_SSLROOTCERT`| No | `""` | Path to RDS CA certificate if `verify-full` is used. |
| `JWT_SECRET_KEY`| **Yes** | — | Min 32-char secret for HS256 JWT signing. |
| `ANGELONE_API_KEY` | **Yes** | — | SmartAPI broker API key. |
| `ANGELONE_CLIENT_ID`| **Yes** | — | Angel One client username. |
| `ANGELONE_PASSWORD` | **Yes** | — | Angel One PIN / password. |
| `ANGELONE_TOTP_TOKEN`| **Yes** | — | Base-32 TOTP authentication seed. |
| `SMTP_SERVER` | No | `smtp.gmail.com` | Outbound mail server. |
| `SMTP_PORT` | No | `587` | Outbound mail port. |
| `SMTP_USERNAME` | No | `""` | SMTP sender address. |
| `SMTP_PASSWORD` | No | `""` | SMTP app password. |
| `FRONTEND_URL` | No | `http://127.0.0.1:8000` | Canonical frontend domain for email links. |
| `MEMORY_TRACE` | No | `0` | Set `1` only to enable tracemalloc memory diagnostics. |

---

## 8. Database Migrations & Initial Data Seeding

LEVERAGE uses **Alembic** to manage database schemas and indexes.

```bash
cd /opt/leverage/backend
source /opt/leverage/venv/bin/activate

# Execute all versioned migrations to bring the database schema to latest HEAD
alembic upgrade head
```

Verify that all tables and indexes are created:

```bash
PGPASSWORD="YOUR_DB_PASSWORD" psql -U leverage_user -d stock_data -h localhost -c "\dt"
```

*Expected output includes:* `candles`, `stock_metadata`, `users`, `trades`, `token_blacklist`, `login_attempts`, `email_verification_tokens`, `password_reset_tokens`.

---

## 9. Frontend Serving Configuration

LEVERAGE features a high-speed Multi-Page Application (MPA) frontend built with native JavaScript, TradingView / Lightweight Charts, and responsive stylesheets.

* All frontend files (`index.html`, `stock.html`, `screener.html`, `news.html`, `portfolio.html`, etc.) reside in `/opt/leverage/frontend/`.
* The FastAPI backend serves these static files directly via `StaticFiles(directory=FRONTEND_DIR, html=True)` mounted at `/`.
* All API calls (`/api/*`) and WebSocket connections (`/ws/*`) use relative, same-origin paths, eliminating cross-origin CORS latency.

Create the static logs directory required by backend audit handlers:
```bash
mkdir -p /opt/leverage/backend/logs
```

---

## 10. Gunicorn & Worker Configuration

The Gunicorn configuration file is located at `/opt/leverage/backend/gunicorn.conf.py`.

### Why 1 Worker is Required:
LEVERAGE’s real-time market data pipeline (`AngelOne` WebSocket connection, `PricePoller`, `CandleAggregator`, `LiveTimeframeManager`, `CandleCache`, and the daily prefill engine) maintains in-memory state. Hard-pinning `workers = 1` prevents split-brain tick divergence and keeps the total memory footprint within 1 GB.

```python
# Key parameters in backend/gunicorn.conf.py:
bind = "127.0.0.1:8000"
workers = 1
worker_class = "uvicorn.workers.UvicornWorker"
timeout = 120
keepalive = 5
graceful_timeout = 30
max_requests = 8000
max_requests_jitter = 800
```

---

## 11. Systemd Service Setup

Create a systemd unit to ensure LEVERAGE starts on boot, recovers from unexpected failures, and enforces glibc memory arena limits.

Create `/etc/systemd/system/leverage.service`:

```bash
sudo nano /etc/systemd/system/leverage.service
```

Paste the following configuration:

```ini
[Unit]
Description=LEVERAGE Stock Market Platform (FastAPI + Gunicorn)
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/leverage/backend
Environment="PATH=/opt/leverage/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="PYTHONPATH=/opt/leverage/backend"
Environment="PYTHONUNBUFFERED=1"
Environment="PYTHONDONTWRITEBYTECODE=1"
# Crucial for 1 GB RAM: Restricts glibc malloc arenas to prevent multi-thread heap bloat
Environment="MALLOC_ARENA_MAX=2"
ExecStart=/opt/leverage/venv/bin/gunicorn main:app -c gunicorn.conf.py
Restart=always
RestartSec=5s
KillMode=mixed
TimeoutStopSec=30s
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

Reload systemd, enable, and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable leverage
sudo systemctl start leverage

# Verify service status
sudo systemctl status leverage
```

---

## 12. Nginx Reverse Proxy Configuration

Nginx acts as the front-facing reverse proxy, handling SSL termination, WebSocket protocol upgrades (`/ws/dashboard` and `/ws/user`), static asset caching, and request timeouts.

Create `/etc/nginx/sites-available/leverage`:

```bash
sudo nano /etc/nginx/sites-available/leverage
```

Paste the following production configuration:

```nginx
upstream leverage_app {
    server 127.0.0.1:8000;
    keepalive 32;
}

server {
    listen 80;
    server_name YOUR_DOMAIN www.YOUR_DOMAIN;

    client_max_body_size 2m;

    # Security Headers
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Permissions-Policy "geolocation=(), camera=(), microphone=(), payment=(), usb=(), magnetometer=(), gyroscope=(), accelerometer=()" always;
    add_header Content-Security-Policy-Report-Only "default-src 'self'; script-src 'self' https://cdnjs.cloudflare.com; style-src 'self' https://fonts.googleapis.com https://cdnjs.cloudflare.com; img-src 'self' data:; font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com; connect-src 'self' ws: wss:; worker-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'self'; form-action 'self';" always;

    # WebSocket Endpoints (Live Dashboard Ticks and User Orders)
    location ~ ^/ws/(dashboard|user)$ {
        proxy_pass http://leverage_app;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Long-lived WebSocket timeouts
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_buffering off;
    }

    # REST Endpoints, Static Frontend, and Assets
    location / {
        proxy_pass http://leverage_app;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_read_timeout 60s;
        proxy_send_timeout 60s;
        proxy_connect_timeout 10s;
    }
}
```

Enable the site configuration and test Nginx syntax:

```bash
sudo ln -sf /etc/nginx/sites-available/leverage /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

---

## 13. SSL / HTTPS Setup via Let's Encrypt (Certbot)

Once your DNS A-records point to `YOUR_SERVER_IP`, obtain and configure a free SSL/TLS certificate:

```bash
# Request and install SSL certificate for your domain
sudo certbot --nginx -d YOUR_DOMAIN -d www.YOUR_DOMAIN --non-interactive --agree-tos -m your_email@domain.com --redirect

# Test automated certificate renewal
sudo certbot renew --dry-run
```

---

## 14. Log Rotation & Disk Space Management

Create a logrotate policy for LEVERAGE application and Gunicorn logs to prevent disk exhaustion.

Create `/etc/logrotate.d/leverage`:

```bash
sudo nano /etc/logrotate.d/leverage
```

```text
/opt/leverage/backend/logs/*.log {
    daily
    missingok
    rotate 7
    compress
    delaycompress
    notifempty
    create 0640 ubuntu ubuntu
    sharedscripts
    postrotate
        systemctl reload leverage > /dev/null 2>&1 || true
    endscript
}
```

Test logrotate:
```bash
sudo logrotate -d /etc/logrotate.d/leverage
```

---

## 15. Production Verification & Health Checks

Verify that all subsystems are operating correctly.

### 15.1 API & System Health Check

```bash
curl -s http://127.0.0.1:8000/api/health | python3 -m json.tool
```

*Expected JSON payload fields:*
* `status`: `"healthy"` or `"degraded"`
* `event_loop.lag_ms`: `<= 5` (typically ~1–2 ms)
* `database.reachable`: `true`
* `database.pool_checkedin`: `20`
* `timeframe_manager`: active builder metrics
* `cache.hit_rate_pct`: L1/L2 cache hit percentage

### 15.2 WebSocket Connectivity Test

Test the real-time price broadcast WebSocket using Python:

```bash
python3 -c "
import asyncio, websockets, json

async def test_ws():
    uri = 'ws://127.0.0.1:8000/ws/dashboard'
    async with websockets.connect(uri) as ws:
        print('Connected to /ws/dashboard successfully.')
        msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
        print('Received initial frame:', msg[:100])

asyncio.run(test_ws())
"
```

### 15.3 Core REST Endpoints Verification

```bash
# Test /api/all-stocks (pre-rendered byte cache)
curl -s -o /dev/null -w "HTTP Status: %{http_code} | Time: %{time_total}s\n" http://127.0.0.1:8000/api/all-stocks

# Test Chart Data for RELIANCE (5m timeframe)
curl -s -o /dev/null -w "HTTP Status: %{http_code} | Time: %{time_total}s\n" "http://127.0.0.1:8000/api/stock-data/chart?ticker=RELIANCE&timeframe=5m&limit=10"
```

---

## 16. Deploying Application Updates

To deploy future updates safely without data loss or prolonged downtime, follow this step-by-step procedure:

```bash
#!/bin/bash
set -e

echo "=== [1/5] Pulling latest updates ==="
cd /opt/leverage
git pull origin main

echo "=== [2/5] Updating Python dependencies ==="
source /opt/leverage/venv/bin/activate
pip install -r backend/requirements.txt

echo "=== [3/5] Running database migrations ==="
cd /opt/leverage/backend
alembic upgrade head

echo "=== [4/5] Reloading application service ==="
sudo systemctl restart leverage

echo "=== [5/5] Verifying health ==="
sleep 3
curl -s http://127.0.0.1:8000/api/health | grep '"reachable": true' && echo "DEPLOYMENT SUCCESSFUL"
```

---

## 17. Automated Backup & Disaster Recovery

### 17.1 Create Automated Daily Database Backup Script

Create `/opt/leverage/scripts/backup_db.sh`:

```bash
mkdir -p /opt/leverage/scripts /opt/leverage/backups
nano /opt/leverage/scripts/backup_db.sh
```

```bash
#!/bin/bash
BACKUP_DIR="/opt/leverage/backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="$BACKUP_DIR/stock_data_$TIMESTAMP.sql.gz"

# Create compressed PostgreSQL dump
PGPASSWORD="YOUR_DB_PASSWORD" pg_dump -U leverage_user -h localhost stock_data | gzip > "$BACKUP_FILE"

# Delete backups older than 14 days
find "$BACKUP_DIR" -type f -name "*.sql.gz" -mtime +14 -delete

echo "Backup completed: $BACKUP_FILE"
```

Make the script executable and add to crontab:
```bash
chmod +x /opt/leverage/scripts/backup_db.sh
(crontab -l 2>/dev/null; echo "0 3 * * * /opt/leverage/scripts/backup_db.sh >> /opt/leverage/backend/logs/backup.log 2>&1") | crontab -
```

### 17.2 Database Restore Procedure

To restore the database from a backup snapshot:

```bash
gunzip -c /opt/leverage/backups/stock_data_YYYYMMDD_HHMMSS.sql.gz | PGPASSWORD="YOUR_DB_PASSWORD" psql -U leverage_user -h localhost -d stock_data
```

---

## 18. Server Resource Monitoring (1 GB RAM Target)

Monitor memory and event-loop lag to ensure headroom is preserved:

```bash
# Check memory allocation
free -h

# Check process-specific RSS memory
ps -o pid,user,%mem,rss,command -C gunicorn -C postgres

# Monitor real-time logs
sudo journalctl -u leverage -f -n 50
```

---

## 19. Troubleshooting Guide

| Symptom | Diagnostic Command | Likely Cause | Resolution |
| :--- | :--- | :--- | :--- |
| **502 Bad Gateway** | `sudo systemctl status leverage` | Gunicorn process crashed or not started | Check `sudo journalctl -u leverage -n 50`. Fix `.env` syntax or start service via `sudo systemctl restart leverage`. |
| **DB Connection Refused** | `sudo systemctl status postgresql` | PostgreSQL service is stopped | Start database via `sudo systemctl start postgresql`. |
| **Database Pool Exhausted** | Check `/api/health` `database.pool_checkedout` | Queries stalling or unclosed connections | Pool is 20+30. Verify `database.py` session context managers (`finally: db.close()`). |
| **High Memory / OOM Kill** | `dmesg -T \| grep -i oom` | Glibc arena bloat or memory leak | Ensure `Environment="MALLOC_ARENA_MAX=2"` is in `leverage.service`. Restart service. |
| **AngelOne Authentication Failure** | Check `/opt/leverage/backend/logs/auth.log` | Expired TOTP secret or invalid API Key | Verify `ANGELONE_TOTP_TOKEN` and `ANGELONE_API_KEY` in `backend/.env`. |
| **WebSocket Reconnecting Constantly** | `sudo tail -f /var/log/nginx/error.log` | Missing Nginx Upgrade headers | Verify `proxy_set_header Upgrade $http_upgrade;` and `proxy_read_timeout 3600s;` in Nginx. |
| **404 on Static Assets** | `ls -la /opt/leverage/frontend` | Working directory mismatch | Ensure `WorkingDirectory=/opt/leverage/backend` so `main.py` resolves `../frontend`. |

---

## 20. Production Architecture Reference

```text
                           Internet / Client Browsers
                                       │
                                       ▼ (Port 80 / 443 HTTPS & WSS)
                                ┌──────────────┐
                                │    Nginx     │
                                └──────┬───────┘
                                       │ (127.0.0.1:8000)
                                       ▼
                       ┌───────────────────────────────┐
                       │           Gunicorn            │
                       │    (workers=1, UvicornWorker) │
                       └───────────────┬───────────────┘
                                       │
                                       ▼
                       ┌───────────────────────────────┐
                       │      FastAPI Application      │
                       │   (main.py + Routers + Auth)  │
                       └───────┬───────────────┬───────┘
                               │               │
            ┌──────────────────┴──┐         ┌──┴──────────────────┐
            │                     │         │                     │
            ▼                     ▼         ▼                     ▼
┌───────────────────────┐ ┌──────────────┐ ┌────────────────┐ ┌────────────────┐
│  LiveTimeframeManager │ │ CandleCache  │ │   Angel One    │ │    yfinance    │
│ (In-memory Builders)  │ │ (L1/L2 LRU)  │ │  WebSocket V2  │ │  (Gap-Recovery)│
└───────────┬───────────┘ └──────┬───────┘ └────────┬───────┘ └────────┬───────┘
            │                    │                  │                  │
            └────────────────────┼──────────────────┴──────────────────┘
                                 │
                                 ▼ (SQLAlchemy Pool: 20 + 30)
                    ┌─────────────────────────┐
                    │  PostgreSQL (Local/RDS) │
                    │    Database: stock_data │
                    └─────────────────────────┘
```

---

## 21. Final Production Sign-Off Checklist

- [ ] **AWS Instance Provisioned**: Ubuntu 24.04 LTS (1 GB RAM) in `ap-south-1` (Mumbai).
- [ ] **Firewall**: Ports 22, 80, 443 open. Port 5432 closed to public.
- [ ] **PostgreSQL 16**: Initialized, tuned for 1 GB RAM, user permissions verified.
- [ ] **Repository Cloned**: Installed at `/opt/leverage` with Python virtual environment.
- [ ] **Dependencies**: `pip install -r backend/requirements.txt` installed cleanly.
- [ ] **Environment**: `/opt/leverage/backend/.env` configured with real secrets and permissions set to `600`.
- [ ] **Database Migrations**: `alembic upgrade head` executed cleanly.
- [ ] **Systemd**: `leverage.service` running with `MALLOC_ARENA_MAX=2` and auto-restart enabled.
- [ ] **Nginx**: Configured with WebSocket proxying and security headers.
- [ ] **SSL / TLS**: Let's Encrypt certificate active with automated renewal.
- [ ] **Health Check**: `curl https://YOUR_DOMAIN/api/health` returns `200 OK` and healthy event-loop latency.
- [ ] **Backups**: Daily database backup cron job active.