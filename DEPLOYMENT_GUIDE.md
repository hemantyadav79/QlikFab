# Enterprise Deployment & Operations Guide
## Qlik → Fabric Autonomous Migration Platform
### Installation, Containerization, Reverse Proxy, CI/CD & Production Operations Manual

**Document Version:** 1.0  
**Status:** Approved for Operations Baseline  
**Date:** August 6, 2026  
**Target Platform:** On-Premises Server, Azure VM, Docker Container, IIS/Nginx Proxy  

---

## 1. Document Control & Overview

| Metadata Field | Specification |
| :--- | :--- |
| **Document Title** | Production Deployment & Operational Management Guide |
| **System Name** | Qlik → Fabric Autonomous Migration Engine |
| **Target Audience** | DevOps Engineers, Cloud Administrators, System Administrators |
| **Repository Branch** | `SHN` |

---

## 2. Infrastructure & System Prerequisites

### 2.1 Supported Operating Systems
- **Windows:** Windows 10/11 Pro/Enterprise, Windows Server 2019 / 2022 (Recommended for Power BI Desktop integration).
- **Linux:** Ubuntu 22.04 LTS, RHEL 8/9, Debian 12 (Containerized deployment).
- **macOS:** macOS Monterey (12.0) or higher (x86_64 and Apple Silicon).

### 2.2 Software Dependencies & Runtimes

| Component | Minimum Version | Recommended Version | Purpose |
| :--- | :--- | :--- | :--- |
| **Python** | 3.10.0 | 3.12.x | Engine execution pipeline & proxy HTTP server. |
| **Node.js / npm** | 18.0.0 LTS | 20.x LTS | Web app dev server & npm package scripts. |
| **Git** | 2.38.0 | 2.45.x | Repository clone & version control. |
| **Docker (Optional)** | 24.0.0 | 26.x | Containerized deployment. |

### 2.3 Network & Firewall Configuration

| Port | Protocol | Direction | Source | Destination | Purpose |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **5173** | TCP / HTTP | Inbound | Client Browsers | Migration Server | Web Application & REST API Proxy. |
| **443** | TCP / HTTPS | Outbound | Migration Server | `api.groq.com` | Primary Tier 1 AI Translation API. |
| **443** | TCP / HTTPS | Outbound | Migration Server | `generativelanguage.googleapis.com` | Secondary Tier 2 AI Fallback API. |
| **443** | TCP / HTTPS | Outbound | Migration Server | `*.qlikcloud.com` | Qlik Cloud SaaS REST API Proxy. |
| **443** | TCP / HTTPS | Outbound | Migration Server | `api.fabric.microsoft.com` | Microsoft Fabric Deployment Proxy. |
| **11434** | TCP / HTTP | Localhost | Migration Server | `127.0.0.1:11434` | Tertiary Tier 3 Local Ollama LLM. |

---

## 3. Environment Variables & Secret Configuration

Create a `.env` file in the project root directory or inject environment variables via system environment manager:

```ini
# ============================================================
# SERVER CONFIGURATION
# ============================================================
PORT=5173
HOST=0.0.0.0
PYTHONIOENCODING=utf-8

# ============================================================
# MULTI-TIER AI AI CREDENTIALS
# ============================================================
# Tier 1 Primary Cloud AI Key (Groq)
GROQ_API_KEY=gsk_YOUR_GROQ_API_KEY_HERE

# Tier 2 Secondary Cloud AI Key (Google Gemini Fallback)
GEMINI_API_KEY=YOUR_GEMINI_API_KEY_HERE

# Tier 3 Tertiary Local AI Endpoint (Ollama)
OLLAMA_URL=http://localhost:11434/api/generate

# ============================================================
# SAAS CONNECTORS & PROXIES (OPTIONAL)
# ============================================================
QLIK_TENANT_URL=https://your-tenant.us.qlikcloud.com
QLIK_API_KEY=your_qlik_api_token
FABRIC_TENANT_ID=your_azure_tenant_id
FABRIC_CLIENT_ID=your_service_principal_id
FABRIC_CLIENT_SECRET=your_service_principal_secret
```

---

## 4. Local / Standalone Server Deployment

Follow these steps to deploy on a Windows Server or standalone Linux machine:

### Step 1: Clone Repository
```bash
git clone https://github.com/hemantyadav79/QlikFab.git
cd QlikFab
git checkout SHN
```

### Step 2: Set Up Python Virtual Environment
```bash
python -m venv venv

# Windows PowerShell:
.\venv\Scripts\Activate.ps1

# Linux / macOS:
source venv/bin/activate
```

### Step 3: Install Node Dependencies
```bash
npm install
```

### Step 4: Verify Python Engine Readiness
```bash
python cli/ai_qvf_to_powerbi.py --help
```

### Step 5: Start Server
```bash
npm run dev
```
The web app will launch and bind to `http://localhost:5173` (or `http://0.0.0.0:5173` for network access).

---

## 5. Docker Container Deployment

For enterprise containerized orchestration, use Docker and Docker Compose.

### 5.1 Dockerfile (`Dockerfile`)
```dockerfile
FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    nodejs \
    npm \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy application files
COPY package*.json ./
RUN npm install --production

COPY . .

# Set environment variables
ENV PORT=5173
ENV PYTHONIOENCODING=utf-8

EXPOSE 5173

CMD ["python", "dev_server.py", "5173"]
```

### 5.2 Docker Compose (`docker-compose.yml`)
```yaml
version: '3.8'

services:
  qlikfab-migration-engine:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: qlikfab_migration_app
    restart: always
    ports:
      - "5173:5173"
    environment:
      - PORT=5173
      - PYTHONIOENCODING=utf-8
      - GROQ_API_KEY=gsk_YOUR_GROQ_API_KEY_HERE
      - GEMINI_API_KEY=YOUR_GEMINI_API_KEY_HERE
    volumes:
      - ./temp_output:/app/output
```

### 5.3 Build & Launch Container
```bash
docker-compose up -d --build
```
Verify container health:
```bash
docker ps
docker logs -f qlikfab_migration_app
```

---

## 6. Windows Service & Production Proxy Setup

### 6.1 Windows Service Setup (NSSM)
To run the server as a background service on Windows Server:

1. Download **NSSM** (Non-Sucking Service Manager) from `https://nssm.cc`.
2. Open PowerShell as Administrator and run:
```powershell
nssm install QlikFabMigration "C:\Python312\python.exe" "C:\QlikFab\dev_server.py 5173"
nssm set QlikFabMigration AppDirectory "C:\QlikFab"
nssm set QlikFabMigration Start SERVICE_AUTO_START
nssm start QlikFabMigration
```

### 6.2 Nginx Reverse Proxy Configuration (`/etc/nginx/sites-available/qlikfab.conf`)
To expose the application securely under SSL/TLS on port 443:

```nginx
server {
    listen 80;
    server_name migration.yourdomain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name migration.yourdomain.com;

    ssl_certificate /etc/letsencrypt/live/migration.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/migration.yourdomain.com/privkey.pem;

    client_max_body_size 500M;

    location / {
        proxy_pass http://127.0.0.1:5173;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
    }
}
```

---

## 7. Operational Monitoring & Diagnostics

### 7.1 Port Conflict Resolution
If port `5173` is occupied by another process:

**Windows PowerShell:**
```powershell
netstat -ano | findstr 5173
taskkill /F /PID <PID_NUMBER>
```

**Linux / macOS:**
```bash
lsof -i :5173
kill -9 <PID_NUMBER>
```

### 7.2 AI Fallback Verification Test
Run this diagnostic command to verify that the Multi-Tier AI failover engine is functional:

```bash
python -c "from cli.ai_qvf_to_powerbi import AIConverterBrain; b = AIConverterBrain(); print(b.translate_expression_to_dax('Sum({<Year={2024}>} Sales)', 'Sales', ['Sales', 'Year']))"
```

---

## 8. Continuous Integration & Continuous Deployment (CI/CD)

### 8.1 GitHub Actions Workflow (`.github/workflows/deploy.yml`)
```yaml
name: QlikFab CI/CD Build & Verification Pipeline

on:
  push:
    branches: [ "SHN", "main" ]
  pull_request:
    branches: [ "main" ]

jobs:
  build-and-test:
    runs-on: ubuntu-latest

    steps:
    - uses: actions/checkout@v3

    - name: Set up Python 3.12
      uses: actions/setup-python@v4
      with:
        python-version: '3.12'

    - name: Install Dependencies
      run: |
        python -m pip install --upgrade pip
        npm install

    - name: Verify CLI Engine Help Command
      run: |
        python cli/ai_qvf_to_powerbi.py --help

    - name: Run Synthetic DAX Translation Unit Test
      env:
        GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}
        GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
      run: |
        python -c "from cli.ai_qvf_to_powerbi import AIConverterBrain; b = AIConverterBrain(); res = b.translate_expression_to_dax('Sum(Sales)', 'Sales', ['Sales']); assert res[0] == 'Sales Sum'"
```

---

## 9. Backup & Disaster Recovery

* **Configuration Backups:** Backup root `.env` configuration file and Custom Rule sets in `.agents/rules/`.
* **Output Archive Retention:** Migration outputs are stored in `output/` as standalone zip files. Retention policy recommended: 30 days.

---

## 10. Operational Sign-Off

| Role | Name | Signature | Date |
| :--- | :--- | :--- | :--- |
| **Lead DevOps Engineer** | Autonomous Infrastructure Team | *Approved* | 2026-08-06 |
| **Director of BI Platform Ops**| Enterprise Operations | *Approved* | 2026-08-06 |
