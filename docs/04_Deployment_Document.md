# Deployment Document

**Project:** QlikFab — Autonomous Qlik Sense to Microsoft Fabric Migration Platform
**Prepared by:** SegueIT — https://segueit.com/
**Document ID:** SEG-QF-DEP-001
**Version:** 1.0
**Date:** 13 August 2026
**Status:** Issued for use
**Classification:** Client Confidential

---

## Document Control

| Version | Date | Author | Change summary |
| --- | --- | --- | --- |
| 0.6 | 08 Aug 2026 | SegueIT Engineering | Initial deployment steps |
| 0.9 | 12 Aug 2026 | SegueIT Engineering | Added OneLake token and interpreter guidance |
| 1.0 | 13 Aug 2026 | SegueIT Engineering | Issued for use |

**Audience:** platform engineers and BI analysts responsible for installing and
operating the platform.

---

## 1. Deployment Overview

QlikFab is a single-host application. It runs a local control-plane server; the
operator uses it through a browser on the same machine. There is no build step,
no application database, and no external runtime service.

```
Migration host
├── Python 3.12+ runtime
├── QlikFab application files
├── Local control-plane server  (default port 5173)
└── Temporary working directories (auto-reclaimed)

Outbound connections:
├── Qlik Cloud tenant        HTTPS + WSS
├── login.microsoftonline.com HTTPS   (Entra token exchange)
├── api.fabric.microsoft.com  HTTPS   (Fabric Items API)
└── onelake.dfs.fabric.microsoft.com HTTPS (Lakehouse staging)
```

---

## 2. Prerequisites

### 2.1 Migration host

| Requirement | Specification |
| --- | --- |
| OS | Windows 10/11, or Linux |
| Python | 3.12 or later |
| Memory | 8 GB minimum; 16 GB recommended for large fact tables |
| Free disk | 10 GB recommended (working directories are reclaimed after each run) |
| Browser | Any current Chromium- or Firefox-based browser |
| Network | Outbound HTTPS (443) and WSS to the endpoints in Section 2.4 |

> Row data is held in memory during a migration. Memory, not disk, is the
> practical ceiling on the largest single table.

### 2.2 Microsoft Fabric

| Requirement | Notes |
| --- | --- |
| Fabric capacity | Active; trial capacity is sufficient for pilot work |
| Target workspace | Assigned to that capacity |
| Entra ID app registration | Service principal for non-interactive publishing |
| Client secret | The secret **value**, not the secret ID |
| Workspace role | Service principal added as **Contributor** (or Member/Admin) |
| Tenant setting | **Service principals can use Fabric APIs** — enabled |

> The tenant setting is the most common cause of a first-run failure. Without it,
> authentication succeeds and the workspace list returns empty.

### 2.3 Qlik Cloud

| Requirement | Notes |
| --- | --- |
| Tenant URL | For example `https://yourtenant.region.qlikcloud.com` |
| API key | Issued for a user with access to the applications in scope |
| Application state | Applications must have been **reloaded** and hold data |
| Websocket egress | The QIX engine is reached over WSS; some proxies block this |

> An application that has never been reloaded migrates with correct structure and
> zero rows. This is reported, not hidden — but it is worth confirming in
> advance.

### 2.4 Firewall allowances

| Endpoint | Protocol | Purpose |
| --- | --- | --- |
| `<your-tenant>.qlikcloud.com` | HTTPS 443, WSS 443 | App export and QIX data read |
| `login.microsoftonline.com` | HTTPS 443 | Entra token exchange |
| `api.fabric.microsoft.com` | HTTPS 443 | Fabric Items API |
| `onelake.dfs.fabric.microsoft.com` | HTTPS 443 | Lakehouse staging |

> `onelake.dfs.fabric.microsoft.com` resolves to a **rotating pool of
> addresses**. Firewall rules must allow the hostname rather than a pinned IP,
> or connections will fail intermittently and inexplicably.

---

## 3. Installation

### 3.1 Obtain the application

Place the application files on the migration host, for example
`C:\QlikFab` or `/opt/qlikfab`.

### 3.2 Identify the interpreter — do this first

This step prevents the single most confusing failure mode in the platform.

```bash
python -c "import sys; print(sys.executable)"
```

Record the path it prints. **Dependencies must be installed into this exact
interpreter**, because the migration engine runs as a subprocess of the server
and inherits its interpreter.

A Windows host commonly has two Python 3.12 installations:

| Installation | Typical path |
| --- | --- |
| python.org | `%LOCALAPPDATA%\Programs\Python\Python312\python.exe` |
| Microsoft Store | `%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe` |

If dependencies land in one and the server runs under the other, migrations
complete "successfully" with **every table empty**. The platform's error messages
name the offending interpreter explicitly, but confirming it now is cheaper.

To be unambiguous, use the full path everywhere below:

```bash
"C:\Users\<you>\AppData\Local\Programs\Python\Python312\python.exe" -m pip install -r requirements.txt
```

### 3.3 Install dependencies

```bash
python -m pip install -r requirements.txt
```

`requirements.txt`:

| Package | Purpose | If missing |
| --- | --- | --- |
| `websocket-client` | QIX engine session for reading real rows | Migration runs; every table is empty |
| `pyarrow` | Parquet writer for Lakehouse staging | Direct Lake path unavailable |

Everything else the platform uses is in the Python standard library.

### 3.4 Verify the installation

```bash
python -c "import websocket, pyarrow, sys; print(sys.executable); print('websocket-client', websocket.__version__); print('pyarrow', pyarrow.__version__)"
```

Confirm the interpreter path matches the one recorded in Section 3.2.

---

## 4. Configuration

### 4.1 Entra ID application registration

1. Entra ID → **App registrations** → **New registration**.
2. Name it (for example `QlikFab-Migration`), single tenant, no redirect URI.
3. Record the **Application (client) ID** and **Directory (tenant) ID**.
4. **Certificates & secrets** → **New client secret**. Record the secret
   **Value** immediately — it is not shown again.

> Copy the secret **Value**, not the **Secret ID**. They sit adjacent in the
> portal and are easily confused; using the ID produces an authentication failure
> that does not explain itself.

### 4.2 Grant workspace access

1. Fabric → target workspace → **Manage access**.
2. **Add people or groups** → search for the app registration by name.
3. Assign **Contributor**.

### 4.3 Enable the tenant setting

Fabric Admin portal → **Tenant settings** → **Developer settings** →
**Service principals can use Fabric APIs** → **Enabled**.

Scope it to a security group containing the service principal if preferred.

### 4.4 Qlik Cloud API key

1. Qlik Cloud → your profile → **API keys** → **Generate new key**.
2. Record the key value.
3. Confirm the owning user can open the applications in scope.

### 4.5 Environment variables (optional)

| Variable | Purpose | Default |
| --- | --- | --- |
| `QLIKFAB_EMBED_ROWS` | Set to `1` to embed rows inline instead of staging to a Lakehouse | Unset (staging enabled) |
| `QLIKFAB_MIN_FREE_MB` | Minimum free disk required to begin a run | 500 |
| `QLIKFAB_RESERVE_MB` | Disk reserve never consumed by a run | 512 |
| `GROQ_API_KEY` | Expression-translation provider key | Built-in default |

> `QLIKFAB_EMBED_ROWS=1` is useful for small applications: it removes the need
> for a Storage-audience token and the Lakehouse entirely.

---

## 5. Starting the Platform

```bash
python dev_server.py 5173
```

Then open `http://localhost:5173`.

The port argument is optional and defaults to 8777. Use a consistent port —
firewall rules and bookmarks depend on it.

### 5.1 Stopping

`Ctrl+C` in the terminal running the server.

### 5.2 Restarting after a code change — important

The server imports `engine_runner` and `fabric_publisher` **once at startup**.
Editing either while the server runs has no effect until restart.

The platform enforces this:

- A **migration** started on stale code logs a warning.
- A **publish** on stale code is **refused**, with restart instructions.

The refusal is deliberate. Publishing with an out-of-date publisher produces
workspace items that appear successful and do not work — discovered only by
opening the report, after several minutes of data reading.

---

## 6. First-Run Verification

Work through these in order. Each isolates one failure class.

### Step 1 — Qlik connection

Enter the tenant URL and API key, then **Test Connection**.

**Expected:** connection confirmed, applications listed.

### Step 2 — Fabric connection

Enter Tenant ID, Client ID, and client secret, then **Test Fabric Connection**.

**Expected — full capability:**

> Connected. Loaded N workspaces — pick the destination below.
> **OneLake access confirmed — large tables will be staged to a Lakehouse.**

**Expected — reduced capability:**

> ⚠ No OneLake (Storage) token could be obtained, so an app whose data is staged
> to a Lakehouse cannot be published. Small apps that embed their rows are
> unaffected.

The Storage token is obtained during this exchange because the client secret is
only in hand at that moment — it is never retained and cannot be minted later at
publish time. Reporting it here avoids discovering the gap after a migration that
may run for minutes.

### Step 3 — Smoke-test migration

Select a **small** application and run a migration. Watch for:

| Log line | Confirms |
| --- | --- |
| `Reading data for N table(s) from the Qlik engine...` | QIX session opened |
| `[OK] Read N row(s) across M table(s)` | Real data read — **N must be non-zero** |
| `[OK] Staged <table>: N row(s), X MB` | Parquet staging (Direct Lake path only) |
| `[OK] Saved: model.bim` | Semantic model generated |
| `[OK] Saved: MIGRATION_AUDIT_REPORT.md` | Audit report written |

> A `[WARN] Could not read data from the tenant` line means the run will produce
> empty tables. Resolve it before publishing — the message names the cause.

### Step 4 — Publish

Select the destination workspace and publish. Watch for:

| Log line | Confirms |
| --- | --- |
| `[onelake] Reusing/Creating lakehouse '<name>'` | Lakehouse ready |
| `[onelake] <table> is now a Delta table.` | Staged data registered |
| `[publish] Semantic model created (<guid>)` | Model published |
| `[OK] Published to <workspace>` | Complete |

### Step 5 — Confirm in Fabric

Open the report in the Fabric workspace. **Expected:** visuals render with data.

| Symptom | Meaning |
| --- | --- |
| Populated visuals | Success |
| Empty axes, no error | Model published with zero rows — check Step 3 |
| *"Something's wrong with one or more fields"* | Field references unresolved — see Section 7 |

### Step 6 — Review the audit report

Open `MIGRATION_AUDIT_REPORT.md` from the run output. Confirm the sections for
unreadable tables, trimmed rows, columns written as text, and expressions needing
review reflect expectations.

---

## 7. Troubleshooting

### 7.1 `websocket-client` not found / every table empty

**Cause:** the package is missing from the interpreter running the server.

The error names the exact interpreter. Install into **that** path:

```bash
"<path from the error message>" -m pip install websocket-client
```

Generic `pip install websocket-client` frequently installs into a *different*
interpreter — one that already had it — and the error returns unchanged.

### 7.2 Fabric connects but no workspaces appear

| Check | Action |
| --- | --- |
| Tenant setting | Enable **Service principals can use Fabric APIs** |
| Workspace role | Add the service principal as Contributor |
| Personal workspaces | Excluded by design; a service principal cannot publish to one |

### 7.3 No OneLake (Storage) token

**Cause:** the service principal cannot obtain a `https://storage.azure.com/.default`
token.

**Impact:** applications staged to a Lakehouse cannot be published. Applications
embedding rows inline are unaffected.

**Workarounds:** review the app registration's permitted scopes, or set
`QLIKFAB_EMBED_ROWS=1` for small applications.

### 7.4 `Something's wrong with one or more fields` — Missing_References

The report references fields the semantic model does not contain. Causes, in
likelihood order:

| Cause | Resolution |
| --- | --- |
| Direct Lake model published with unresolved placeholders | Should now be refused pre-publish; if seen, the server is running stale code — restart it |
| Publish ran on a stale `fabric_publisher` | Restart the server and republish |
| Legacy report artefact reached the workspace | Should be withheld automatically; verify the deployed version |

Click **See details** on the error — it names the specific missing field, which
distinguishes these cases immediately.

### 7.5 `WinError 10060` / connection timeout during publish

**Cause:** transient network failure, commonly a rotating OneLake endpoint.

**Behaviour:** polling now retries within the operation window, so isolated blips
recover automatically. If the window expires, the error states the item **may
have been created**.

> Before republishing, check the workspace. A second publish creates a
> **duplicate** item rather than replacing the first.

### 7.6 Publish refused — stale module

Expected behaviour after editing `fabric_publisher.py` with the server running.
Restart:

```bash
python dev_server.py 5173
```

The migration output is complete and remains downloadable; nothing needs
re-running.

### 7.7 Publish refused — verification failure

The pre-publish check found a structural defect. The message lists specific
findings. This is working as designed: Fabric's own error would name the symptom
once per file and never the cause.

### 7.8 Insufficient disk space

Runs are refused before anything is written. Free space, or lower
`QLIKFAB_MIN_FREE_MB`. Orphaned working directories are swept automatically at
the start of each run.

### 7.9 Port already in use

Another process holds the port. Either stop it, or start on a different port:

```bash
python dev_server.py 5174
```

---

## 8. Operations

### 8.1 Routine operation

| Activity | Frequency | Notes |
| --- | --- | --- |
| Start the server | Per session | Restart after any code change |
| Verify connections | Per session | Tokens are session-scoped and expire |
| Review audit reports | Per migration | The record of what was and was not carried over |
| Confirm published reports | Per migration | Open in Fabric and confirm visuals render |

### 8.2 Housekeeping

| Item | Behaviour |
| --- | --- |
| Working directories | Reclaimed after each run; orphans swept at next run start |
| Run history | Held in memory; lost on restart. Generated artefacts remain on disk |
| Credentials | Never written to disk |
| Lakehouse staging files | Retained in the lakehouse under `Files/qlik_migration` |

Staged Parquet files remain in the lakehouse after loading. Periodically prune
them once the Delta tables are confirmed.

### 8.3 Backup

The platform holds no persistent state requiring backup. Preserve:

- The application source, in version control
- Generated PBIP archives, if retained as migration evidence
- Migration audit reports, as the compliance record

### 8.4 Upgrades

1. Stop the server.
2. Replace the application files.
3. Re-run `pip install -r requirements.txt` — into the correct interpreter.
4. Restart and repeat Section 6 verification.

---

## 9. Security Operations

| Control | Operational responsibility |
| --- | --- |
| Client secret rotation | Per client policy; re-enter at next connection |
| Qlik API key rotation | Per client policy; re-enter at next connection |
| Least privilege | Contributor on the target workspace only |
| Host access | The control plane is unauthenticated and bound locally — restrict host access accordingly |
| Audit retention | Retain audit reports per client records policy |

> **The control plane has no authentication of its own.** It is designed for a
> single operator on a single host. Do not expose the port to a network without
> placing an authenticating reverse proxy in front of it.

> **Migrated reports carry no row-level security.** Qlik section access is not
> translated. Apply equivalent Power BI RLS before releasing any migrated report
> to a wider audience.

---

## 10. Deployment Checklist

**Prerequisites**

- [ ] Migration host meets Section 2.1
- [ ] Fabric capacity active; target workspace assigned
- [ ] Entra app registration created; client secret **value** recorded
- [ ] Service principal is Contributor on the target workspace
- [ ] Tenant setting **Service principals can use Fabric APIs** enabled
- [ ] Qlik API key issued and app access confirmed
- [ ] Source applications reloaded and holding data
- [ ] Firewall allows all four endpoints **by hostname**

**Installation**

- [ ] Application files placed on the host
- [ ] Interpreter path identified and recorded
- [ ] `requirements.txt` installed into **that** interpreter
- [ ] Import check passes and reports the expected interpreter

**Verification**

- [ ] Qlik connection test succeeds
- [ ] Fabric connection test succeeds
- [ ] OneLake access confirmed, or reduced capability accepted
- [ ] Smoke migration reads a **non-zero** row count
- [ ] Smoke migration publishes successfully
- [ ] Published report renders **populated** visuals in Fabric
- [ ] Audit report reviewed

**Handover**

- [ ] Operator trained
- [ ] Troubleshooting section walked through
- [ ] RLS responsibility (Section 9) formally accepted by the client
- [ ] Escalation contact agreed

---

*Prepared by SegueIT — https://segueit.com/*
*This document is confidential and intended solely for the named client engagement.*
