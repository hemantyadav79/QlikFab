# Business Requirement Document (BRD)
## Autonomous Migration Platform: Qlik Sense to Microsoft Power BI (Fabric)

**Document Version:** 1.0  
**Status:** Approved for Baseline  
**Date:** August 6, 2026  
**Target System:** Microsoft Fabric & Power BI Desktop (PBIP)  
**Source System:** Qlik Sense (.qvf binary & Qlik Cloud SaaS)  

---

## 1. Document Control & Version History

| Version | Date | Author / Role | Summary of Changes |
| :--- | :--- | :--- | :--- |
| **1.0** | 2026-08-06 | Autonomous AI Engineering Team | Baseline release of Business Requirement Document (BRD) covering End-to-End Automated Migration, Multi-Tier AI Cascade, Power Query M Partitioning, and Fabric PBIP Generation. |

---

## 2. Executive Summary

Organizations modernizing their Business Intelligence (BI) infrastructure face significant manual effort, cost, and risk when migrating legacy Qlik Sense applications to Microsoft Fabric / Power BI. Manual re-engineering of load scripts, expressions, data models, and report visual layouts typically requires 4 to 8 weeks per complex application.

The **Qlik → Fabric Autonomous Migration Platform** is an enterprise-grade solution that automates 90%+ of the end-to-end migration process. By combining deterministic binary parsing (`.qvf` extraction), static script AST analysis, and a **Multi-Tiered Fail-Safe AI Translation Engine** (Groq Cloud API `llama-3.3-70b-versatile` ➔ Google Gemini Cloud API `gemini-2.0-flash` ➔ Local Ollama `llama3.2`), the platform converts Qlik Sense applications directly into native Microsoft Power BI Project (`.pbip`) bundles, semantic models (`model.bim`), Power Query partitions, DAX measures, and report page templates (`.pbit`).

---

## 3. Business Background & Drivers

### 3.1 Context
Enterprise BI landscapes are consolidating onto unified data platforms like Microsoft Fabric to leverage centralized governance, DirectLake storage, copilot integration, and lower total cost of ownership (TCO). However, legacy investments in Qlik Sense (`.qvf` applications and Qlik Cloud SaaS tenants) create migration bottlenecks due to vendor lock-in.

### 3.2 Key Drivers
* **Migration Acceleration:** Reduce time-to-migrate per app from 6 weeks down to **under 2 minutes**.
* **Cost Elimination:** Eliminate expensive third-party manual migration services and redundant Qlik licensing fees.
* **Logic Fidelity:** Ensure 100% accurate conversion of Qlik Load Scripts to Power Query M and Qlik Set Analysis to DAX.
* **Governance & Auditability:** Automatically flag PII (Personally Identifiable Information) column patterns and generate detailed audit trails (`MIGRATION_AUDIT_REPORT.md`).

---

## 4. Project Scope

### 4.1 In-Scope
1. **Source Connectors:**
   - Standalone Qlik Sense binary files (`.qvf`).
   - Live Qlik Cloud SaaS tenants via secure REST proxy (`/qlik-proxy`).
2. **Data Model & Load Script Migration:**
   - Extraction of load script logic, resident tables, parameters, and variable definitions.
   - Translation to Power Query M expressions (`build_partition_expression`).
   - Creation of Tabular Object Model (`model.bim`) with explicit data types.
3. **DAX Expression Translation:**
   - Deterministic translation of Qlik aggregations (`Sum`, `Avg`, `Count`, `Min`, `Max`, `Median`, `StDev`).
   - Single-equality Set Analysis translation to `CALCULATE` statements.
   - Multi-tiered AI translation for complex formulas using Groq API (Primary) and Gemini API (Automatic Fallback).
4. **Report & Visual Layout Generation:**
   - Parsing of Qlik sheet visual containers, charts, KPIs, pie charts, bar charts, slicers, and tables.
   - Mapping to native Power BI visual containers (`visual.json`, `page.json`, `report.json`).
5. **Output Delivery:**
   - Complete Microsoft Fabric Power BI Project (`.pbip`) directory structure.
   - Standalone Power BI Template (`.pbit`).
   - Comprehensive audit report (`MIGRATION_AUDIT_REPORT.md`).
6. **User Interface:**
   - Web-based Control Console with Dual Theme (Light Cream / Dark Obsidian) using modern **Plus Jakarta Sans** typography.
   - 4-Phase Live Output Execution Console with real-time logs and phase badges.

### 4.2 Out-of-Scope
- Direct modification or write-back to live Qlik Cloud source tenants.
- Automatic deployment to production Fabric workspaces without user approval (Fabric REST deployment proxy included for user-initiated publishing).

---

## 5. Business Objectives & Key Performance Indicators (KPIs)

| Objective | Target Metric | Measurement Method |
| :--- | :--- | :--- |
| **Migration Speed** | < 2 minutes per app | Time elapsed from execution trigger to `.pbip` zip availability. |
| **Script & DAX Accuracy** | > 95% automated conversion | Percentage of Qlik measures translated to valid DAX without requiring `[Needs Review]` flag. |
| **System Uptime & Fallback** | 99.99% conversion availability | Seamless failover from Groq API to Gemini API upon 429 Rate Limit / Out-of-Tokens event. |
| **User Adoption** | Zero CLI required for analysts | 100% web-based operation via browser interface. |

---

## 6. User Personas & Key Stakeholders

* **BI Administrator / Lead:** Oversees enterprise migration strategy, approves audit reports, and deploys `.pbip` projects into Fabric workspaces.
* **Data Analyst / BI Developer:** Reviews generated DAX formulas, verifies report pages, and adjusts visual formatting.
* **Executive Sponsor (CIO / VP Analytics):** Tracks overall migration progress, cost savings, and legacy decommission timelines.

---

## 7. Functional Requirements

### 7.1 Input & Ingestion (FR-01 to FR-03)
* **FR-01 (QVF Binary Upload):** System must accept `.qvf` files up to 256 MB via drag-and-drop or file picker without client-side memory exhaustion.
* **FR-02 (Qlik Cloud REST Proxy):** System must support entering Qlik Tenant URL and API Bearer Token to fetch app lists and stream app binaries server-side without CORS restrictions.
* **FR-03 (Batch Processing):** System must allow selecting single or multiple Qlik applications for sequential batch conversion.

### 7.2 Core Migration Engine (FR-04 to FR-08)
* **FR-04 (Load Script AST Parser):** System must parse Qlik script constructs (e.g., `LOAD`, `RESIDENT`, `WHERE`, `QUALIFY`, `UNQUALIFY`) and output Power Query M partitions.
* **FR-05 (Multi-Tier AI Brain):**
  - **Tier 1:** Query Groq Cloud API (`llama-3.3-70b-versatile`) with API key `gsk_...`.
  - **Tier 2:** Upon Groq 429/403/timeout failure, automatically failover to Google Gemini API (`gemini-2.0-flash`) with API key `AQ....`.
  - **Tier 3:** Fallback to local Ollama (`llama3.2`) if internet/API is disconnected.
  - **Tier 4:** Generate `[Needs Review]` measure with original Qlik formula as a DAX comment.
* **FR-06 (Semantic Model Generator):** System must generate `model.bim` containing tables, typed columns, measures, lineage tags, and partition expressions.
* **FR-07 (Visual Layout Mapping):** System must convert Qlik sheet layouts to Power BI `definition.pbir` pages with coordinates (`x`, `y`, `width`, `height`, `z`).
* **FR-08 (PII Scanning):** System must inspect table schema column names against PII regex rules (e.g., `ssn`, `email`, `salary`, `phone`) and log matches in the Assessment phase.

### 7.3 Output & Audit Trail (FR-09 to FR-12)
* **FR-09 (PBIP Project Bundle):** System must package `.pbip`, `.pbism`, `.pbir`, `model.bim`, and `.pbit` into a single downloadable ZIP archive.
* **FR-10 (Audit Report):** System must generate `MIGRATION_AUDIT_REPORT.md` documenting fidelity metrics, table statistics, DAX review queues, and unresolved items.
* **FR-11 (Live Execution Console):** Web UI must display real-time logs categorized into 4 Agent Panes:
  - **Assessment Agent** (`extract`)
  - **Parsing Agent** (`model`)
  - **Mapping Agent** (`report`)
  - **Report Generation Agent** (`package`)
* **FR-12 (Phase Status Badges):** All 4 agent boxes must show a green **`COMPLETED`** badge upon successful run completion.

---

## 8. Non-Functional Requirements

| ID | Category | Requirement |
| :--- | :--- | :--- |
| **NFR-01** | **Performance** | Engine processing time for standard QVF (< 50 MB) must complete within 60 seconds. |
| **NFR-02** | **Reliability** | Multi-tier AI fallback must gracefully handle Groq token depletion without throwing unhandled exceptions to the UI. |
| **NFR-03** | **Security** | API keys (Groq & Gemini) must be transmitted over TLS 1.3 and never written to public client logs. |
| **NFR-04** | **Usability** | Interface must adhere to high-contrast Dark & Light themes with **Plus Jakarta Sans** typography. |
| **NFR-05** | **Scalability** | Support multi-GB .qvf parsing using streaming zip buffer readers. |
| **NFR-06** | **Compatibility** | Generated `.pbip` projects must be compatible with Microsoft Power BI Desktop (2024+ releases) and Microsoft Fabric Workspaces. |
| **NFR-07** | **Maintainability**| Modular architecture separating frontend UI (`script.js`), Python web proxy (`dev_server.py`), engine supervisor (`engine_runner.py`), and conversion brain (`ai_qvf_to_powerbi.py`). |
| **NFR-08** | **Zero Data Corruption** | Raw data rows are never fabricated or synthetic; column types are strictly resolved from metadata. |

---

## 9. System Architecture & AI Cascade Diagram

```
 +-----------------------------------------------------------------------+
 |                            WEB USER INTERFACE                          |
 |            (Plus Jakarta Sans Typography / Dual Theme Console)          |
 +-----------------------------------+-----------------------------------+
                                     |
                                     v
 +-----------------------------------------------------------------------+
 |                     PYTHON DEV SERVER & REST PROXY                    |
 |          (http.server / /api/runs / /qlik-proxy / /fabric-proxy)       |
 +-----------------------------------+-----------------------------------+
                                     |
                                     v
 +-----------------------------------------------------------------------+
 |                       ENGINE SUPERVISOR (engine_runner.py)            |
 |         (Subprocess Supervisor, Line Classifier, Real-time Stream)    |
 +-----------------------------------+-----------------------------------+
                                     |
                                     v
 +-----------------------------------------------------------------------+
 |                CONVERSION BRAIN (cli/ai_qvf_to_powerbi.py)            |
 |                                                                       |
 |   +---------------------------------------------------------------+   |
 |   | TIER 1: Groq Cloud API (llama-3.3-70b-versatile)              |   |
 |   +-------------------------------+-------------------------------+   |
 |                                   | (On 429 / Out of Tokens)          |
 |                                   v                                   |
 |   +-------------------------------+-------------------------------+   |
 |   | TIER 2: Google Gemini API (gemini-2.0-flash)                  |   |
 |   +-------------------------------+-------------------------------+   |
 |                                   | (On Timeout / API Drop)           |
 |                                   v                                   |
 |   +-------------------------------+-------------------------------+   |
 |   | TIER 3: Local Ollama (llama3.2)                               |   |
 |   +-------------------------------+-------------------------------+   |
 |                                   | (On Offline / Unreachable)        |
 |                                   v                                   |
 |   +-------------------------------+-------------------------------+   |
 |   | TIER 4: Deterministic Fallback Rule & [Needs Review] DAX      |   |
 |   +---------------------------------------------------------------+   |
 +-----------------------------------+-----------------------------------+
                                     |
                                     v
 +-----------------------------------------------------------------------+
 |                       OUTPUT ARTIFACT BUILDER                         |
 |      (.pbip Bundle / model.bim / definition.pbir / AUDIT_REPORT.md)   |
 +-----------------------------------------------------------------------+
```

---

## 10. Data Flow & Phase Execution Lifecycle

1. **User Action:** User uploads `.qvf` file or selects Qlik Cloud app from REST chooser.
2. **Phase 1 — Assessment (`extract`):** Engine extracts binary XML/JSON headers, reads load script, counts tables/fields, and performs PII regex scanning.
3. **Phase 2 — Parsing (`model`):** Script AST parser generates Power Query M partition code and invokes the Multi-Tier AI Brain to translate Qlik logic to DAX measures in `model.bim`.
4. **Phase 3 — Mapping (`report`):** Visual generator maps Qlik sheet objects to Power BI visual containers (`clusteredColumnChart`, `slicer`, `card`, `tableEx`).
5. **Phase 4 — Report Generation (`package`):** Engine packages `.pbip` directory, writes `MIGRATION_AUDIT_REPORT.md`, zips the output bundle, and notifies the UI with 4 green **`COMPLETED`** badges.

---

## 11. Security, Governance & Compliance

* **Token Protection:** Credentials passed via web interface are retained strictly in memory for proxy transmission and are never logged to stdout or disk.
* **PII Governance:** Automatic assessment warning when column names match sensitive patterns (`email`, `ssn`, `tax_id`, `credit_card`).
* **Source Protection:** Source `.qvf` files are held in isolated temporary work directories (`tempfile.mkdtemp`) and cleaned up following run completion.

---

## 12. Acceptance Criteria

1. **Successful Execution:** Uploading any valid QVF file must produce a complete `.pbip` folder containing `model.bim` and `definition.pbir`.
2. **UI Fidelity:** Web interface must display live streaming logs in 4 agent boxes, each showing green `COMPLETED` badges upon completion.
3. **AI Failover:** Disabling Groq API access must trigger an immediate, logged failover to Gemini API without failing the migration run.
4. **Download Availability:** The Artifacts tab must enable instant download of the converted `.zip` bundle and audit documentation.

---

## 13. Document Sign-Off & Approval

| Role | Name | Signature | Date |
| :--- | :--- | :--- | :--- |
| **Lead BI Architect** | Autonomous Engineering Team | *Approved* | 2026-08-06 |
| **Enterprise Product Owner** | Migration Platform Sponsor | *Approved* | 2026-08-06 |
