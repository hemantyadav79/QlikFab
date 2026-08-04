# 🚀 Universal Qlik-to-Power BI Autonomous Migration Engine
### Enterprise Microsoft Fabric PBIR 4.0 Compliant | Zero Hardcoding Guarantee

![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)
![Platform: Microsoft Fabric](https://img.shields.io/badge/Microsoft%20Fabric-PBIR%204.0-0078D4)
![Power BI Desktop](https://img.shields.io/badge/Power%20BI%20Desktop-2026.07%2b-F2C811)
![Python 3.10+](https://img.shields.io/badge/Python-3.10%2b-3776AB)

---

## 🌟 Executive Summary & Core Value Proposition

The **Universal Qlik-to-Power BI Autonomous Migration Engine** converts arbitrary Qlik Sense / QlikView application files (`.qvf`) into fully structured, enterprise-ready **Microsoft Fabric Power BI Projects (`.pbip`)** or **Power BI Templates (`.pbit`)** with zero human intervention.

### 🔥 Why This Engine?
- **Zero Hardcoding Guarantee:** 100% dynamic schema and metadata discovery. No pre-coded column lists, static tables, or hardcoded visual bindings. Works across Financial, HR, Retail, Supply Chain, or Helpdesk QVF applications.
- **Microsoft Fabric PBIR 4.0 Compliant:** Generates July 2026+ Power BI Enhanced Report Format (PBIR) structures (`definition.pbir` v4.0, `report.json` v3.3.0) for native Git integration and zero deprecation warnings.
- **Unlimited Production Capacity & Instant 100-Row Preview:** Migrates complete schemas, DAX measures, and visual dashboards for **unlimited production dataset sizes (10k to 10M+ rows via Power Query)** while embedding an offline-ready **100-row Instant Sample Preview** so charts render immediately upon opening without requiring an initial database connection.
- **Dynamic Multi-Page Visual Engine:** Automatically creates diverse visual equivalents across report pages:
  - **Page 1 (KPI Executive Dashboard):** KPI Summary Cards (`card`), Clustered Column Chart (`clusteredColumnChart`), and Line Trend Chart (`lineChart`).
  - **Page 2 (Categorical Trend Analysis):** Donut Chart (`donutChart`), Clustered Bar Chart (`clusteredBarChart`), and Area Trend Chart (`areaChart`).
- **Reserved Keyword & Collision Sanitizer:** Automatically sanitizes Analysis Services reserved words (`Data` ➔ `Entity_Data`, `Table` ➔ `Entity_Table`) and appends unique suffixes (`_2`, `_3`) to case-insensitive duplicates to guarantee zero deserialization errors.

---

## 📂 Repository Structure

```
final_demo_qlikfab/
├── index.html                   # Core Interactive Migration Web Application UI
├── script.js                    # Universal Dynamic Qlik-to-PowerBI Migration Engine
├── styles.css                   # Enterprise Stylesheet & UI Tokens
├── README.md                    # Official Project Repository Guide
├── .gitignore                   # Standard Git Ignore Configuration
├── docs/                        # Complete Technical & Stakeholder Documentation
│   ├── UNIVERSAL_QLIK_TO_POWERBI_MIGRATION_ENGINE_DOCUMENTATION.md
│   └── UNIVERSAL_QLIK_TO_POWERBI_MIGRATION_ENGINE_DOCUMENTATION.doc
├── samples/                     # Sample Qlik Applications (.qvf) for instant testing
│   ├── Executive Dashboard.qvf
│   ├── Helpdesk Management.qvf
│   ├── Demo 2.qvf
│   └── Superstore_Sales_Dashboard.qvf
└── cli/                         # Python Autonomous AI & Command-Line Interfaces
    ├── ai_qvf_to_powerbi.py     # AI-assisted QVF to PBIP CLI converter
    ├── qvf_extractor.py         # Qlik Load Script, Schema & Variable extractor
    ├── autogen_qlik_agent.py    # Autonomous LLM agent for Qlik expression parsing
    ├── rebuild_all_pbip_archives.py # Batch zip archive rebuild tool
    └── requirements.txt         # Python dependencies for CLI
```

---

## 🚀 Quickstart 1: Interactive Web UI

1. Open `index.html` in any modern web browser (Chrome, Edge, Firefox).
2. Click **Browse** in the upload zone and select any `.qvf` file from the `samples/` directory (e.g., `samples/Executive Dashboard.qvf`).
3. Review the real-time volumetrics and automatically translated DAX measures in **Phase 2**.
4. Click **Approve All DAX Translations**.
5. Scroll to **Phase 4** and click the green button:  
   👉 **`Download .PBIP (Full Data & Visuals - RECOMMENDED)`**
6. **Extract (Unzip)** the downloaded `.zip` file and double-click the `.pbip` project file to open it in Microsoft Power BI Desktop!

---

## 💻 Quickstart 2: Command-Line Interface (CLI)

You can convert any `.qvf` file directly from Windows Terminal, PowerShell, or CMD using the scripts in the `cli/` directory:

### 1. Install Dependencies (Optional, for LLM / AI parsing features)
```powershell
pip install -r cli/requirements.txt
```

### 2. Convert `.QVF` directly to `.PBIP` Project Bundle
```powershell
python cli/ai_qvf_to_powerbi.py --qvf "samples/Executive Dashboard.qvf"
```

### 3. Extract Qlik Load Script, Variables & Data Model to JSON
```powershell
python cli/qvf_extractor.py "samples/Executive Dashboard.qvf" --output ./extracted_result
```

### 4. Rebuild All `.PBIP` Archives in Bulk
```powershell
python cli/rebuild_all_pbip_archives.py
```

---

## 📊 `.PBIP` vs. `.PBIT`: Which Should You Choose?

| Feature / Specification | `.PBIP` (Power BI Project Bundle) — **★ RECOMMENDED** | `.PBIT` (Power BI Template File) |
| :--- | :--- | :--- |
| **File Structure** | Folder containing `.SemanticModel` & `.Report` definitions | Single XML/JSON compressed template |
| **Offline Data Rows** | **100% Included** (100 rows of enterprise preview data embedded) | **None** (Metadata & schema definition only) |
| **Visual Charts & Cards** | **100% Populated** with live data immediately upon opening | Requires external data source connection to render |
| **Git & CI/CD Ready** | **Yes** (Human-readable JSON/PBIR files for version control) | No (Binary/zipped template format) |
| **How to Open in Power BI** | **Step 1:** Extract (Unzip) folder.<br>**Step 2:** Double-click `.pbip` file. | Open directly (1-click), but requires data loading |

---

## 🛠️ Technical Compliance & Error Prevention

- **No `(X) Error fetching data for this visual`:** Aggregate DAX formulas are bound strictly to their compatible data types (`AVERAGE` / `SUM` for numeric columns; `COUNTA` / `DISTINCTCOUNT` for text columns). Mandatory `queryRef` and `nativeQueryRef` metadata are injected into all visual JSON projections.
- **No `Cannot de-serialize Database` Errors:** An automated sanitizer protects reserved keywords (`Data`, `Table`, `Model`, `Query`) and appends unique suffixes (`_2`, `_3`) to duplicate case-insensitive field names.
- **Unlimited Production Data Loading:** Once the `.pbip` project is opened, click **Transform Data (Power Query Editor)** in Power BI Desktop to point `QlikTable` to your real production database (SQL Server, Snowflake, Excel, QVD) and load unlimited rows!

---

## 📖 Documentation & Support
For detailed architectural diagrams, Analysis Services tabular specifications, and troubleshooting logs, please review the official documentation:
- [Markdown Technical Documentation](file:///docs/UNIVERSAL_QLIK_TO_POWERBI_MIGRATION_ENGINE_DOCUMENTATION.md)
- [Microsoft Word Documentation (.doc)](file:///docs/UNIVERSAL_QLIK_TO_POWERBI_MIGRATION_ENGINE_DOCUMENTATION.doc)

---
*Built with ❤️ for Seamless Qlik-to-Microsoft Fabric Enterprise Migrations.*
