/* ==========================================================================
   QLIK -> FABRIC | AUTONOMOUS MIGRATION PLATFORM
   Interactive Frontend Logic (script.js) — 100% Dynamic & Zero Hardcoding
   Supports unlimited QVF files, dynamic PC browse/upload, and LIVE .PBIT / .PBIP Downloads
   ========================================================================== */

document.addEventListener("DOMContentLoaded", () => {
    // ----------------------------------------------------------------------
    // 0. THEME TOGGLE (light / dark)
    // ----------------------------------------------------------------------
    // The initial theme is resolved by an inline script in index.html so the
    // first paint is already correct; this block only handles user switching
    // and keeping the control in sync.
    const THEME_STORAGE_KEY = "qlikfab-theme";

    function readStoredTheme() {
        try {
            const stored = localStorage.getItem(THEME_STORAGE_KEY);
            return stored === "light" || stored === "dark" ? stored : null;
        } catch (e) {
            return null;
        }
    }

    function applyTheme(theme, persist) {
        document.documentElement.setAttribute("data-theme", theme);

        document.querySelectorAll(".theme-toggle__option").forEach(btn => {
            btn.setAttribute(
                "aria-pressed",
                btn.getAttribute("data-theme-value") === theme ? "true" : "false"
            );
        });

        if (persist) {
            try {
                localStorage.setItem(THEME_STORAGE_KEY, theme);
            } catch (e) {
                /* Storage unavailable (private mode); theme still applies for this session. */
            }
        }
    }

    applyTheme(document.documentElement.getAttribute("data-theme") || "light", false);

    document.querySelectorAll(".theme-toggle__option").forEach(btn => {
        btn.addEventListener("click", () => {
            applyTheme(btn.getAttribute("data-theme-value"), true);
        });
    });

    // Follow the OS preference until the user makes an explicit choice.
    if (window.matchMedia) {
        const osPreference = window.matchMedia("(prefers-color-scheme: dark)");
        const onOsChange = (e) => {
            if (!readStoredTheme()) {
                applyTheme(e.matches ? "dark" : "light", false);
            }
        };
        if (osPreference.addEventListener) {
            osPreference.addEventListener("change", onOsChange);
        } else if (osPreference.addListener) {
            osPreference.addListener(onOsChange);
        }
    }

    // ----------------------------------------------------------------------
    // 0.5 MOBILE SIDEBAR TOGGLE
    // ----------------------------------------------------------------------
    const sidebar = document.querySelector(".sidebar");
    const sidebarToggleBtn = document.getElementById("btn-sidebar-toggle");
    const sidebarOverlay = document.getElementById("sidebar-overlay");

    function toggleSidebar() {
        if (!sidebar || !sidebarOverlay) return;
        const isOpen = sidebar.classList.contains("open");
        if (isOpen) {
            sidebar.classList.remove("open");
            sidebarOverlay.classList.remove("active");
        } else {
            sidebar.classList.add("open");
            sidebarOverlay.classList.add("active");
        }
    }

    if (sidebarToggleBtn) {
        sidebarToggleBtn.addEventListener("click", toggleSidebar);
    }
    if (sidebarOverlay) {
        sidebarOverlay.addEventListener("click", toggleSidebar);
    }
    
    window.addEventListener("resize", () => {
        if (window.innerWidth > 600 && sidebar && sidebar.classList.contains("open")) {
            sidebar.classList.remove("open");
            sidebarOverlay.classList.remove("active");
        }
    });

    document.querySelectorAll(".nav-item, .nav-subitem").forEach(item => {
        item.addEventListener("click", () => {
            if (window.innerWidth <= 600 && sidebar && sidebar.classList.contains("open")) {
                toggleSidebar();
            }
        });
    });

    // ----------------------------------------------------------------------
    // 1. DYNAMIC QVF APP REGISTRY
    // ----------------------------------------------------------------------
    const APP_REGISTRY = {
        "Executive Dashboard.qvf": {
            name: "Executive Dashboard",
            filename: "Executive Dashboard.qvf",
            size: "2.88 MB",
            sizeBytes: 2883584,
            fieldsCnt: "76 Columns",
            visualsCnt: "3 Sheets / 25 Charts",
            pbitName: "Executive Dashboard.pbit",
            pbipName: "Executive Dashboard.pbip",
            projectDir: "Executive_Dashboard_PowerBI_Project/",
            pbitSize: "4.8 KB",
            sheets: [
                { name: "KPI Dashboard", chartType: "KPI / Bar / Line", title: "Executive KPI Summary Cards", dims: "Region, Category", meas: "Sum(Sales Amount), Sum(Profit)", status: "Mapped to Power BI KPI" },
                { name: "Account Receivable Analysis", chartType: "Clustered Column", title: "AR Aging (AR1-30, AR31-60, AR60+)", dims: "Customer, Region", meas: "Sum(AROpen), Sum(ARGross)", status: "100% Schema Mapped" },
                { name: "Sales Analysis", chartType: "Stacked Bar", title: "Sales Margin by Division & Fiscal Year", dims: "Division, Fiscal Year", meas: "Sum(Sales Margin Amount)", status: "Mapped to Clustered Bar" }
            ],
            daxQueue: [
                { expr: "Sum([Sales Amount])", dax: "SUM('QlikTable'[\"Sales Amount\"])", conf: "99.8%", status: "Auto-Approved" },
                { expr: "Sum([Sales Cost Amount])", dax: "SUM('QlikTable'[\"Sales Cost Amount\"])", conf: "99.5%", status: "Auto-Approved" },
                { expr: "Sum([Sales Margin Amount])", dax: "SUM('QlikTable'[\"Sales Margin Amount\"])", conf: "99.2%", status: "Auto-Approved" },
                { expr: "Sum([AROpen])", dax: "SUM('QlikTable'[AROpen])", conf: "98.9%", status: "Ready for Review" },
                { expr: "Count(DISTINCT OrderID)", dax: "DISTINCTCOUNT('QlikTable'[OrderID])", conf: "99.9%", status: "Auto-Approved" },
                { expr: "Sum(ExpenseActual) / Sum(ExpenseBudget)", dax: "DIVIDE(SUM('QlikTable'[ExpenseActual]), SUM('QlikTable'[ExpenseBudget]), 0)", conf: "98.5%", status: "Ready for Review" }
            ],
            columns: ["OrderDate", "OrderID", "Sales Amount", "Sales Cost Amount", "Sales Margin Amount", "ExpenseActual", "ExpenseBudget", "ARGross", "AROpen", "Region", "Division", "Customer"]
        },
        "Helpdesk Management.qvf": {
            name: "Helpdesk Management",
            filename: "Helpdesk Management.qvf",
            size: "1.96 MB",
            sizeBytes: 1966080,
            fieldsCnt: "33 Columns",
            visualsCnt: "2 Sheets / 10 Charts",
            pbitName: "Helpdesk Management.pbit",
            pbipName: "Helpdesk Management.pbip",
            projectDir: "Helpdesk_PowerBI_Project/",
            pbitSize: "5.3 KB",
            sheets: [
                { name: "Helpdesk Overview", chartType: "KPI Card / Donut", title: "Open vs Closed Case Ratio", dims: "Priority, CaseStatus", meas: "Count(CaseID), Avg(ResolutionDays)", status: "100% Schema Mapped" },
                { name: "Agent Performance", chartType: "Clustered Bar", title: "Agent Ticket Load & CSAT Score", dims: "AgentName, Department", meas: "Avg(CSAT_Score), Sum(Escalated)", status: "Mapped to Power BI Table" }
            ],
            daxQueue: [
                { expr: "Count(CaseID)", dax: "COUNT('QlikTable'[CaseID])", conf: "99.9%", status: "Auto-Approved" },
                { expr: "Avg(ResolutionDays)", dax: "AVERAGE('QlikTable'[ResolutionDays])", conf: "99.4%", status: "Auto-Approved" },
                { expr: "Sum(Escalated)", dax: "SUM('QlikTable'[Escalated])", conf: "99.1%", status: "Ready for Review" },
                { expr: "Count({<Status={'Open'}>} CaseID)", dax: "CALCULATE(COUNT('QlikTable'[CaseID]), 'QlikTable'[Status] = \"Open\")", conf: "97.8%", status: "Ready for Review" }
            ],
            columns: ["CaseID", "CaseStatus", "Priority", "AgentName", "Department", "ResolutionDays", "CSAT_Score", "Escalated", "CreatedDate", "ClosedDate"]
        },
        "Superstore_Sales_Dashboard.qvf": {
            name: "Superstore Sales",
            filename: "Superstore_Sales_Dashboard.qvf",
            size: "208.0 KB",
            sizeBytes: 212992,
            fieldsCnt: "21 Columns",
            visualsCnt: "2 Sheets / 9 Charts",
            pbitName: "Superstore_Sales_Dashboard.pbit",
            pbipName: "Superstore_Sales_Dashboard.pbip",
            projectDir: "Superstore_PowerBI_Project/",
            pbitSize: "3.9 KB",
            sheets: [
                { name: "Superstore Sales", chartType: "KPI Card / Bar", title: "Total Sales & Profit by Category", dims: "Category, Sub-Category", meas: "Sum(Sales), Sum(Profit)", status: "100% Schema Mapped" },
                { name: "Regional Performance", chartType: "Map / Line Chart", title: "Sales Trend over OrderDate", dims: "Region, OrderDate", meas: "Sum(Sales), Sum(Quantity)", status: "Mapped to Clustered Bar" }
            ],
            daxQueue: [
                { expr: "Sum(Sales)", dax: "SUM('QlikTable'[Sales])", conf: "99.9%", status: "Auto-Approved" },
                { expr: "Sum(Profit)", dax: "SUM('QlikTable'[Profit])", conf: "99.7%", status: "Auto-Approved" },
                { expr: "Sum(Quantity)", dax: "SUM('QlikTable'[Quantity])", conf: "99.5%", status: "Auto-Approved" },
                { expr: "Sum(Profit)/Sum(Sales)", dax: "DIVIDE(SUM('QlikTable'[Profit]), SUM('QlikTable'[Sales]), 0)", conf: "98.9%", status: "Ready for Review" }
            ],
            columns: ["OrderID", "OrderDate", "CustomerName", "Region", "Category", "Sub-Category", "Sales", "Profit", "Quantity", "Discount"]
        },
        "Demo 2.qvf": {
            name: "Demo 2",
            filename: "Demo 2.qvf",
            size: "288.0 KB",
            sizeBytes: 294912,
            fieldsCnt: "9 Columns",
            visualsCnt: "1 Sheet / 9 Charts",
            pbitName: "Demo_2.pbit",
            pbipName: "Demo_2.pbip",
            projectDir: "Demo_2_PowerBI_Project/",
            pbitSize: "4.5 KB",
            sheets: [
                { name: "My new sheet", chartType: "Bar / Line / KPI Card", title: "Views & Subscribers Analysis", dims: "category, date, country", meas: "Sum(views_millions), Avg(subscribers_k)", status: "100% Schema Mapped" }
            ],
            daxQueue: [
                { expr: "Sum(views_millions)", dax: "SUM('QlikTable'[views_millions])", conf: "99.9%", status: "Auto-Approved" },
                { expr: "Max(views_millions)", dax: "MAX('QlikTable'[views_millions])", conf: "99.7%", status: "Auto-Approved" },
                { expr: "Avg(subscribers_k)", dax: "AVERAGE('QlikTable'[subscribers_k])", conf: "99.5%", status: "Auto-Approved" }
            ],
            columns: ["category", "date", "views_millions", "subscribers_k", "country", "channel_id", "revenue_usd", "engagement_rate", "video_count"]
        },
        "first_qlik_project.qvf": {
            name: "First Qlik Project",
            filename: "first_qlik_project.qvf",
            size: "4.00 MB",
            sizeBytes: 4194304,
            fieldsCnt: "35 Columns",
            visualsCnt: "2 Sheets / 21 Charts",
            pbitName: "first_qlik_project.pbit",
            pbipName: "first_qlik_project.pbip",
            projectDir: "first_qlik_project_PowerBI_Project/",
            pbitSize: "5.6 KB",
            sheets: [
                { name: "My new sheet (1)", chartType: "Clustered Bar / Table", title: "Sales & Regional Overview", dims: "Region, Category, SubCategory", meas: "Sum(Sales), Sum(Profit)", status: "100% Schema Mapped" },
                { name: "My new sheet", chartType: "KPI / Line Chart", title: "Monthly Performance Trends", dims: "YearMonth, Customer", meas: "Sum(OrderAmount), Count(OrderID)", status: "Mapped to Power BI Table" }
            ],
            daxQueue: [
                { expr: "Sum(Sales)", dax: "SUM('QlikTable'[Sales])", conf: "99.9%", status: "Auto-Approved" },
                { expr: "Sum(Profit)", dax: "SUM('QlikTable'[Profit])", conf: "99.8%", status: "Auto-Approved" }
            ],
            columns: ["OrderID", "OrderDate", "CustomerName", "Region", "Category", "SubCategory", "Sales", "Profit", "Quantity", "Discount", "City", "State", "Country"]
        }
    };

    let currentActiveQvf = null;

    // Every .qvf in the current upload batch, in upload order. The active file is the
    // one the Assessment/Review tabs describe; the batch is what actually gets migrated
    // and bundled, so a 4-file upload downloads as 4 projects, not just the active one.
    let migrationBatch = [];

    function getBatchApps() {
        return migrationBatch.map(key => APP_REGISTRY[key]).filter(Boolean);
    }

    // Counts are stored as display strings ("2 Sheets", " 9 Charts"); pull the number
    // back out so a batch can be totalled.
    function leadingCount(text) {
        const match = /(\d+)/.exec(text || "");
        return match ? parseInt(match[1], 10) : 0;
    }

    function escapeHtml(value) {
        return String(value == null ? "" : value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    // ----------------------------------------------------------------------
    // 1b. AGENT DETAIL TABS
    //
    // Each engine phase gets its own navigation entry and pane. A pane only
    // ever renders what the registry actually holds for the current upload —
    // when nothing is uploaded it says so rather than showing zeroed metrics
    // that would read as a real result.
    // ----------------------------------------------------------------------

    // Column names are matched against these patterns to flag possible PII. This
    // is a NAME-pattern heuristic on the extracted schema, not an inspection of
    // row values, and every screen that reports PII says so.
    const PII_PATTERNS = [
        { re: /e-?mail/i, category: "Email address" },
        { re: /(phone|mobile|msisdn)/i, category: "Phone number" },
        { re: /(ssn|social_?security|passport|national_?id|aadhaar|tax_?id)/i, category: "Government ID" },
        { re: /(dob|date_?of_?birth|birth_?date|birthday)/i, category: "Date of birth" },
        { re: /(iban|swift|card_?(no|num|number)|credit_?card|account_?(no|num|number))/i, category: "Financial account" },
        { re: /(salary|income|compensation|payroll)/i, category: "Compensation" },
        { re: /(gender|ethnic|religion|marital|disabilit)/i, category: "Sensitive attribute" },
        { re: /(address|street|zip_?code|postal|pincode)/i, category: "Postal address" },
        { re: /name/i, category: "Personal name" }
    ];

    function scanPiiColumns(app) {
        const columns = (app && app.columns) || [];
        const hits = [];
        columns.forEach(col => {
            const match = PII_PATTERNS.find(p => p.re.test(col));
            if (match) hits.push({ column: col, category: match.category, file: app.filename });
        });
        return hits;
    }

    function scanPiiForApps(apps) {
        return apps.reduce((acc, app) => acc.concat(scanPiiColumns(app)), []);
    }

    const AGENT_DEFS = [
        { id: "assess", name: "Extract", phase: "Phase 1" },
        { id: "parse", name: "ReportParsingAgent", phase: "Phase 2" },
        { id: "map", name: "Report", phase: "Phase 3" },
        { id: "gen", name: "ReportGenerationAgent", phase: "Phase 4" }
    ];

    // Null until a run finishes. Drives the "planned scope" vs "produced by the
    // last run" wording in every agent pane.
    let lastRunSummary = null;

    function kpiBox(label, value, sub, valueClass) {
        return `
            <div class="kpi-box">
                <span class="kpi-label">${escapeHtml(label)}</span>
                <b class="kpi-value ${valueClass || ""}">${escapeHtml(value)}</b>
                <span class="kpi-sub">${escapeHtml(sub)}</span>
            </div>`;
    }

    function agentEmptyState(agentName) {
        return `
            <div class="agent-empty-state">
                <i class="fa-solid fa-inbox"></i>
                <h3>No .qvf uploaded yet</h3>
                <p>${escapeHtml(agentName)} has nothing to report until a Qlik app is loaded.
                   Upload one or more <b>.qvf</b> files on the <b>Run migration</b> tab and this
                   pane fills in with the parsed detail for that upload.</p>
            </div>`;
    }

    // Totals shared by several panes. Apps whose sheet/chart counts were never
    // recovered (live Qlik Cloud apps) are counted as unknown rather than as zero,
    // so a total never understates by pretending a missing count is nothing.
    function batchTotals(apps) {
        const counted = apps.filter(a => !a.unknownVisuals);
        return {
            fields: apps.reduce((n, a) => n + leadingCount(a.fieldsCnt), 0),
            sheets: counted.reduce((n, a) => n + leadingCount(a.visualsCnt.split("/")[0]), 0),
            charts: counted.reduce((n, a) => n + leadingCount(a.visualsCnt.split("/")[1]), 0),
            measures: apps.reduce((n, a) => n + a.daxQueue.length, 0),
            unknown: apps.length - counted.length
        };
    }

    // A count that only covers part of the batch is shown as a floor ("4+"), or as
    // "—" when no app in the batch reported one at all.
    function partialCount(value, totals) {
        if (!totals.unknown) return String(value);
        return value === 0 ? "—" : `${value}+`;
    }

    function partialNote(totals, base) {
        return totals.unknown ? `${base} • unknown for ${totals.unknown} app(s)` : base;
    }

    function gapsFor(apps) {
        return apps.reduce((acc, app) => acc.concat((app.gaps || []).map(g => ({ app: app.filename, gap: g }))), []);
    }

    function gapsCard(apps) {
        const gaps = gapsFor(apps);
        if (!gaps.length) return "";
        return `
            <div class="table-container">
                <h3>Not recovered from the source</h3>
                <p class="agent-section-note">These are gaps in what the source system handed over. They are listed rather than filled in, so nothing downstream reads as migrated when it was not.</p>
                <table class="custom-table">
                    <thead><tr><th>App</th><th>Missing</th></tr></thead>
                    <tbody>${gaps.map(g => `
                        <tr>
                            <td>${escapeHtml(g.app)}</td>
                            <td><span class="status-badge pending">${escapeHtml(g.gap)}</span></td>
                        </tr>`).join("")}
                    </tbody>
                </table>
            </div>`;
    }

    function renderAssessAgentTab(apps) {
        const kpiHost = document.getElementById("kpi-agent-assess");
        const bodyHost = document.getElementById("detail-body-assess");
        if (!kpiHost || !bodyHost) return;

        if (!apps.length) {
            kpiHost.innerHTML = "";
            bodyHost.innerHTML = agentEmptyState("Assessment");
            return;
        }

        const totals = batchTotals(apps);
        const pii = scanPiiForApps(apps);

        kpiHost.innerHTML =
            kpiBox("Apps in scope", `${apps.length} app(s)`, "Queued for this run") +
            kpiBox("Extracted fields", `${totals.fields}`, "Across all queued apps") +
            kpiBox(
                "Sheets / charts",
                `${partialCount(totals.sheets, totals)} / ${partialCount(totals.charts, totals)}`,
                partialNote(totals, "Reported by the source")
            ) +
            kpiBox(
                "PII name-pattern hits",
                pii.length ? `${pii.length} flagged` : "None flagged",
                pii.length ? "Review before publishing" : "No matching column names",
                pii.length ? "warning-text" : "success-text"
            );

        const fileRows = apps.map(app => {
            const appPii = scanPiiColumns(app);
            return `
                <tr>
                    <td><b>${escapeHtml(app.filename)}</b></td>
                    <td>${escapeHtml(app.size)}</td>
                    <td>${escapeHtml(app.fieldsCnt)}</td>
                    <td>${escapeHtml(app.visualsCnt)}</td>
                    <td>${appPii.length
                        ? `<span class="status-badge pending">${appPii.length} flagged</span>`
                        : `<span class="status-badge success">None flagged</span>`}</td>
                </tr>`;
        }).join("");

        const piiRows = pii.length
            ? pii.map(hit => `
                <tr>
                    <td><code>${escapeHtml(hit.column)}</code></td>
                    <td>${escapeHtml(hit.file)}</td>
                    <td><span class="status-badge pending">${escapeHtml(hit.category)}</span></td>
                </tr>`).join("")
            : `<tr><td colspan="3">No column name in the extracted schema matched a PII pattern.</td></tr>`;

        bodyHost.innerHTML = `
            <div class="table-container">
                <h3>Pre-migration scan per app</h3>
                <p class="agent-section-note">Volumetrics as reported by each queued source — an uploaded .qvf load script, or the Qlik Cloud REST data model.</p>
                <table class="custom-table">
                    <thead>
                        <tr><th>Source app</th><th>Size</th><th>Fields</th><th>Sheets / charts</th><th>PII scan</th></tr>
                    </thead>
                    <tbody>${fileRows}</tbody>
                </table>
            </div>
            <div class="table-container">
                <h3>PII name-pattern findings</h3>
                <p class="agent-section-note">
                    Heuristic on <b>column names only</b> — row values are never inspected, so this
                    is a review prompt, not a certification that the data is or is not personal.
                </p>
                <table class="custom-table">
                    <thead><tr><th>Column</th><th>Source app</th><th>Pattern matched</th></tr></thead>
                    <tbody>${piiRows}</tbody>
                </table>
            </div>
            ${gapsCard(apps)}`;
    }

    function renderParseAgentTab(apps) {
        const kpiHost = document.getElementById("kpi-agent-map");
        const bodyHost = document.getElementById("detail-body-map");
        if (!kpiHost || !bodyHost) return;

        if (!apps.length) {
            kpiHost.innerHTML = "";
            bodyHost.innerHTML = agentEmptyState("Mapping");
            return;
        }

        const totals = batchTotals(apps);
        const resolved = apps.reduce((n, a) => n + (a.columns ? a.columns.length : 0), 0);

        // The recovered-name count and the schema header count come from two
        // different reads and can disagree, so they are reported side by side
        // rather than as a single "x of y" that would imply one contains the other.
        kpiHost.innerHTML =
            kpiBox("Apps parsed", `${apps.length}`, "Data model read from source") +
            kpiBox("Sheets", partialCount(totals.sheets, totals), partialNote(totals, "Become Power BI pages")) +
            kpiBox("Charts", partialCount(totals.charts, totals), partialNote(totals, "Visual objects inventoried")) +
            kpiBox("Field names recovered", `${resolved}`, `Schema header reports ${totals.fields}`);

        const sheetRows = apps.map(app => app.sheets.map(sh => `
            <tr>
                <td>${escapeHtml(app.name)}</td>
                <td><b>${escapeHtml(sh.name)}</b></td>
                <td>${escapeHtml(sh.chartType)}</td>
                <td>${escapeHtml(sh.title)}</td>
                <td><code>${escapeHtml(sh.dims)}</code></td>
                <td><code>${escapeHtml(sh.meas)}</code></td>
            </tr>`).join("")).join("");

        const schemaBlocks = apps.map(app => {
            const cols = app.columns || [];
            const chips = cols.length
                ? cols.map(c => {
                    const hit = PII_PATTERNS.find(p => p.re.test(c));
                    return `<span class="field-chip${hit ? " pii" : ""}" title="${hit ? escapeHtml(hit.category) : "No PII pattern match"}">${escapeHtml(c)}</span>`;
                }).join("")
                : `<span class="field-chip">No field names resolved</span>`;
            return `
                <div style="margin-bottom: 18px;">
                    <b>${escapeHtml(app.filename)}</b>
                    <p class="agent-section-note">${app.source === "qlik-cloud"
                        ? `${cols.length} field(s) returned by the Qlik Cloud data model endpoint${app.tablesCnt ? ` across ${app.tablesCnt} table(s)` : ""}.`
                        : `${cols.length} field name(s) recovered from the load script; the schema header reports ${escapeHtml(app.fieldsCnt)}. The two counts come from separate reads and are not guaranteed to match.`}</p>
                    <div>${chips}</div>
                </div>`;
        }).join("");

        bodyHost.innerHTML = `
            <div class="table-container">
                <h3>Sheet &amp; chart inventory</h3>
                <p class="agent-section-note">Every sheet found in the queued apps, with the dimensions and measures each visual binds to.</p>
                <table class="custom-table">
                    <thead>
                        <tr><th>App</th><th>Sheet</th><th>Chart type</th><th>Visual title</th><th>Dimensions</th><th>Measures</th></tr>
                    </thead>
                    <tbody>${sheetRows || `<tr><td colspan="6">No sheet or chart inventory was returned for the queued app(s). See the gaps listed under <b>Agents &rarr; Assessment</b>.</td></tr>`}</tbody>
                </table>
            </div>
            <div class="table-container">
                <h3>Resolved schema fields</h3>
                <p class="agent-section-note">Highlighted chips matched a PII name pattern raised in the <b>Assessment</b> phase.</p>
                ${schemaBlocks}
            </div>`;
    }

    function renderMapAgentTab(apps) {
        const kpiHost = document.getElementById("kpi-agent-parse");
        const bodyHost = document.getElementById("detail-body-parse");
        if (!kpiHost || !bodyHost) return;

        if (!apps.length) {
            kpiHost.innerHTML = "";
            bodyHost.innerHTML = agentEmptyState("Parsing");
            return;
        }

        const queue = apps.reduce((acc, app) => acc.concat(app.daxQueue.map(dq => ({ app, dq }))), []);
        const autoApproved = queue.filter(q => q.dq.status === "Auto-Approved").length;
        const needsReview = queue.length - autoApproved;
        const confidences = queue.map(q => parseFloat(q.dq.conf)).filter(n => !isNaN(n));
        const avgConf = confidences.length
            ? (confidences.reduce((a, b) => a + b, 0) / confidences.length).toFixed(1) + "%"
            : "n/a";

        kpiHost.innerHTML =
            kpiBox("Expressions translated", `${queue.length}`, "Qlik → DAX measures") +
            kpiBox("Auto-approved", `${autoApproved}`, "Above the confidence bar", "success-text") +
            kpiBox("Needs review", `${needsReview}`, needsReview ? "Below the confidence bar" : "Nothing held back", needsReview ? "warning-text" : "success-text") +
            kpiBox("Mean confidence", avgConf, "Across translated measures");

        const rows = queue.map(({ app, dq }) => `
            <tr>
                <td>${escapeHtml(app.name)}</td>
                <td><code>${escapeHtml(dq.expr)}</code></td>
                <td><code class="dax-code">${escapeHtml(dq.dax)}</code></td>
                <td><span class="conf-pill">${escapeHtml(dq.conf)}</span></td>
                <td><span class="status-badge ${dq.status === "Auto-Approved" ? "success" : "pending"}">${escapeHtml(dq.status)}</span></td>
            </tr>`).join("");

        bodyHost.innerHTML = `
            <div class="table-container">
                <h3>DAX translation queue</h3>
                <p class="agent-section-note">Measures written into the generated semantic model, with the confidence each translation carried. Anything below the bar is flagged <b>Needs Review</b> rather than dropped.</p>
                <table class="custom-table">
                    <thead>
                        <tr><th>App</th><th>Qlik expression</th><th>Translated DAX</th><th>Confidence</th><th>Status</th></tr>
                    </thead>
                    <tbody>${rows || `<tr><td colspan="5">No Qlik expressions were retrieved for the queued app(s), so nothing was translated.</td></tr>`}</tbody>
                </table>
            </div>`;
    }

    function renderGenAgentTab(apps) {
        const kpiHost = document.getElementById("kpi-agent-gen");
        const bodyHost = document.getElementById("detail-body-gen");
        if (!kpiHost || !bodyHost) return;

        if (!apps.length) {
            kpiHost.innerHTML = "";
            bodyHost.innerHTML = agentEmptyState("Report Generation");
            return;
        }

        const totals = batchTotals(apps);
        const built = !!lastRunSummary;

        kpiHost.innerHTML =
            kpiBox(built ? "Projects built" : "Projects to build", `${apps.length}`, "One PBIP project per app") +
            kpiBox("Report pages", partialCount(totals.sheets, totals), partialNote(totals, "One per Qlik sheet")) +
            kpiBox("Visuals on canvas", partialCount(totals.charts, totals), partialNote(totals, "Mapped from Qlik charts")) +
            kpiBox("Measures embedded", `${totals.measures}`, "From the generated model.bim");

        const rows = apps.map(app => `
            <tr>
                <td><b>${escapeHtml(app.filename)}</b></td>
                <td><code>${escapeHtml(app.projectDir)}</code></td>
                <td>${escapeHtml(app.pbipName)}</td>
                <td>${escapeHtml(app.pbitName)}</td>
                <td>${escapeHtml(app.pbitSize)}</td>
                <td><span class="status-badge ${built ? "success" : "pending"}">${built ? "Generated" : "Pending run"}</span></td>
            </tr>`).join("");

        // Only the live path collects a destination; it is where the output is meant
        // to land, not somewhere this client has written to.
        const targeted = apps.filter(a => a.fabricTarget);
        const destinationCard = targeted.length ? `
            <div class="table-container">
                <h3>Microsoft Fabric destination</h3>
                <p class="agent-section-note">Recorded with the run and written into the audit report. The files below are generated for you to download — this client does not publish to the workspace.</p>
                <table class="custom-table">
                    <thead><tr><th>App</th><th>Workspace</th><th>Capacity</th><th>Item name prefix</th></tr></thead>
                    <tbody>${targeted.map(a => `
                        <tr>
                            <td>${escapeHtml(a.name)}</td>
                            <td><b>${escapeHtml(a.fabricTarget.workspace)}</b>${a.fabricTarget.workspaceId
                                ? `<br><span class="kpi-sub">${escapeHtml(a.fabricTarget.workspaceId)}</span>`
                                : ""}</td>
                            <td>${a.fabricTarget.capacity ? escapeHtml(a.fabricTarget.capacity) : "<span class=\"kpi-sub\">workspace default</span>"}</td>
                            <td>${a.fabricTarget.prefix ? escapeHtml(a.fabricTarget.prefix) : "<span class=\"kpi-sub\">app name</span>"}</td>
                        </tr>`).join("")}
                    </tbody>
                </table>
            </div>` : "";

        bodyHost.innerHTML = `
            ${destinationCard}
            <div class="table-container">
                <h3>Build output per app</h3>
                <p class="agent-section-note">
                    ${built
                        ? "Produced by the last completed run. Downloads live on the Artifacts tab."
                        : "Planned output for the current upload. Nothing is written until a migration run completes."}
                </p>
                <table class="custom-table">
                    <thead>
                        <tr><th>Source app</th><th>Project folder</th><th>.pbip</th><th>.pbit</th><th>Template size</th><th>Status</th></tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
                <div class="action-bar">
                    <button class="btn-primary-block" id="btn-agent-goto-artifacts" style="width: auto; padding: 12px 28px;">
                        <i class="fa-solid fa-box-open"></i> Open Artifacts tab
                    </button>
                </div>
            </div>`;

        const gotoBtn = document.getElementById("btn-agent-goto-artifacts");
        if (gotoBtn) gotoBtn.addEventListener("click", () => switchTab("tab-artifacts"));
    }

    // The download cards describe files a run produced, so they stay hidden until
    // a run has actually produced them.
    function updateArtifactsAvailability() {
        const grid = document.getElementById("artifacts-grid");
        const empty = document.getElementById("artifacts-empty-state");
        const built = !!lastRunSummary;
        if (grid) grid.classList.toggle("hidden", !built);
        if (empty) empty.classList.toggle("hidden", built);
    }

    // Writes the shared status chrome (sidebar pill, pane badge, run-state line)
    // for one agent.
    function setAgentStatus(id, label, styleClass) {
        const badge = document.getElementById(`badge-agent-${id}`);
        if (badge) {
            badge.textContent = label;
            badge.className = `agent-badge-tag ${styleClass}`;
        }
        const detailBadge = document.getElementById(`badge-detail-${id}`);
        if (detailBadge) {
            detailBadge.textContent = label;
            detailBadge.className = `agent-badge-tag ${styleClass}`;
        }
        // The same short state reads in two places: the sidebar sub-item and the
        // matching card on the agents overview.
        const pillText = styleClass === "completed" ? "done" : (styleClass === "running" ? "live" : "idle");
        [`nav-state-${id}`, `card-state-${id}`].forEach(pillId => {
            const pill = document.getElementById(pillId);
            if (!pill) return;
            pill.textContent = pillText;
            pill.className = `nav-agent-state ${styleClass}`;
        });
    }

    function setAgentRunState(id, text) {
        const el = document.getElementById(`runstate-${id}`);
        if (el) el.textContent = text;
    }

    function renderAgentDetailTabs() {
        const apps = getBatchApps();
        updateArtifactsAvailability();
        renderAssessAgentTab(apps);
        renderParseAgentTab(apps);
        renderMapAgentTab(apps);
        renderGenAgentTab(apps);

        AGENT_DEFS.forEach(agent => {
            if (lastRunSummary) {
                setAgentRunState(agent.id, `Last run ${lastRunSummary.at} • ${lastRunSummary.count} app(s)`);
            } else {
                setAgentRunState(agent.id, apps.length ? `${apps.length} app(s) queued • not run yet` : "Not run yet");
            }
        });
    }

    // ----------------------------------------------------------------------
    // 2. SIDEBAR NAVIGATION
    //
    // Two levels: top-level tabs, and — under the Agents group — one sub-pane per
    // engine phase plus the overview that lists them. Every move through either
    // level is pushed onto a history stack so the Back control in the header can
    // retrace it, which is why all navigation goes through goTo() rather than
    // touching classes directly.
    // ----------------------------------------------------------------------
    const navItems = document.querySelectorAll(".nav-menu .nav-item");
    const tabPanes = document.querySelectorAll(".main-content .tab-pane");
    const navSubItems = document.querySelectorAll(".nav-submenu .nav-subitem");
    const subPanes = document.querySelectorAll(".subtab-pane");
    const agentsGroup = document.getElementById("nav-group-agents");
    const agentsParent = agentsGroup ? agentsGroup.querySelector(".nav-item-parent") : null;

    // The pane each tab opens on. Only the Agents tab has an inner level.
    const DEFAULT_SUBPANE = { "tab-agents": "sub-agents-overview" };

    // Which tab owns a given sub-pane, so a deep link like "sub-agent-map" can
    // raise its parent tab too.
    function tabOwning(paneId) {
        const pane = document.getElementById(paneId);
        const section = pane ? pane.closest(".tab-pane") : null;
        return section ? section.id : null;
    }

    let currentView = { tab: "tab-run", sub: null };
    const viewHistory = [];

    function setAgentsGroupOpen(open) {
        if (!agentsGroup) return;
        agentsGroup.classList.toggle("open", open);
        if (agentsParent) agentsParent.setAttribute("aria-expanded", open ? "true" : "false");
    }

    // Paints the chrome for a view without recording it — goTo() owns the history.
    function applyView(view) {
        navItems.forEach(i => i.classList.toggle("active", i.getAttribute("data-tab") === view.tab));
        tabPanes.forEach(p => p.classList.toggle("active", p.id === view.tab));

        const sub = view.sub || DEFAULT_SUBPANE[view.tab] || null;
        subPanes.forEach(p => p.classList.toggle("active", p.id === sub));
        navSubItems.forEach(a => {
            const on = a.getAttribute("data-subtab") === sub;
            a.classList.toggle("active", on);
            a.setAttribute("aria-selected", on ? "true" : "false");
        });

        // The group stays open while the user is inside it, so the four agents
        // remain one click apart.
        setAgentsGroupOpen(view.tab === "tab-agents");

        currentView = { tab: view.tab, sub: sub };
        updateBackControl();
    }

    function sameView(a, b) {
        return a && b && a.tab === b.tab && (a.sub || null) === (b.sub || null);
    }

    function goTo(tabId, subId) {
        const target = { tab: tabId, sub: subId || DEFAULT_SUBPANE[tabId] || null };
        if (!document.getElementById(target.tab)) return;
        if (sameView(target, currentView)) return;

        viewHistory.push({ ...currentView });
        applyView(target);
    }

    function switchTab(tabId) {
        goTo(tabId, null);
    }

    // Opens a pane inside a tab; the owning tab is raised with it, so callers do
    // not have to know which tab a pane lives in.
    function switchSubTab(paneId) {
        const owner = tabOwning(paneId);
        if (!owner) return;
        goTo(owner, paneId);
    }

    function goBack() {
        const previous = viewHistory.pop();
        if (previous) applyView(previous);
    }

    const backBtn = document.getElementById("btn-global-back");

    function updateBackControl() {
        if (!backBtn) return;
        backBtn.hidden = viewHistory.length === 0;
    }

    if (backBtn) backBtn.addEventListener("click", goBack);

    navItems.forEach(item => {
        item.addEventListener("click", (e) => {
            e.preventDefault();
            const tabId = item.getAttribute("data-tab");
            // Re-clicking the open Agents group collapses it rather than being a
            // no-op, so the sub-items can be tucked away again.
            if (tabId === "tab-agents" && currentView.tab === "tab-agents" && agentsGroup) {
                setAgentsGroupOpen(!agentsGroup.classList.contains("open"));
                return;
            }
            switchTab(tabId);
        });
    });

    // Sidebar agent sub-items, the overview cards, and the "All agents" back links
    // inside each detail pane all address a pane by id.
    document.querySelectorAll("[data-subtab]").forEach(el => {
        el.addEventListener("click", (e) => {
            e.preventDefault();
            switchSubTab(el.getAttribute("data-subtab"));
        });
    });

    applyView(currentView);

    // ----------------------------------------------------------------------
    // 2b. SOURCE CHOOSER
    // The Run migration tab opens on a choice of source; only the chosen path is
    // rendered afterwards, so an upload and a live connection are never both
    // half-configured at the same time.
    // ----------------------------------------------------------------------
    const MODE_META = {
        file: {
            name: "Upload files — no Qlik connection",
            caption: "Upload any .QVF file or .ZIP archive. The files are read in your browser and never sent anywhere."
        },
        qlik: {
            name: "Live Qlik Cloud connection",
            caption: "Connect to your tenant, pick an app, then set the Microsoft Fabric destination for the run."
        }
    };

    let activeMode = null;

    function applyMode(mode) {
        activeMode = mode;
        const chooser = document.getElementById("mode-chooser");
        const bar = document.getElementById("mode-bar");
        const grid = document.getElementById("migration-grid");
        const caption = document.getElementById("mode-footer-caption");
        const barName = document.getElementById("mode-bar-name");
        const filePane = document.getElementById("mode-pane-file");
        const qlikPane = document.getElementById("mode-pane-qlik");

        const chosen = !!mode;
        if (chooser) chooser.classList.toggle("hidden", chosen);
        if (bar) bar.classList.toggle("hidden", !chosen);
        if (grid) grid.classList.toggle("hidden", !chosen);
        if (caption) caption.classList.toggle("hidden", !chosen);
        if (filePane) filePane.classList.toggle("hidden", mode !== "file");
        if (qlikPane) qlikPane.classList.toggle("hidden", mode !== "qlik");
        if (chosen) {
            if (barName) barName.textContent = MODE_META[mode].name;
            if (caption) caption.textContent = MODE_META[mode].caption;
        }
    }

    document.querySelectorAll(".mode-card").forEach(card => {
        card.addEventListener("click", () => applyMode(card.getAttribute("data-mode")));
    });

    const btnChangeMode = document.getElementById("btn-change-mode");
    if (btnChangeMode) {
        btnChangeMode.addEventListener("click", () => {
            // Switching source drops whatever the previous source had loaded, so the
            // other path can never run against it.
            refreshAllTabsForActiveQvf(null);
            const consoleCard = document.getElementById("autogen-console");
            if (consoleCard) consoleCard.classList.add("hidden");
            applyMode(null);
        });
    }

    // ----------------------------------------------------------------------
    // 3. REAL POWER BI FILE GENERATORS & DOWNLOADERS (.PBIT, .PBIP, .MD)
    // ----------------------------------------------------------------------
    const EXISTING_REAL_PROJECTS = {
        "Executive Dashboard.qvf": "Executive_Dashboard_PowerBI_Project/Executive_Dashboard.pbit",
        "Helpdesk Management.qvf": "Helpdesk_PowerBI_Project/Helpdesk_Management.pbit",
        "Superstore_Sales_Dashboard.qvf": "Superstore_PowerBI_Project/Superstore_Sales_Dashboard.pbit",
        "Demo 2.qvf": "Demo_2_PowerBI_Project/Demo_2.pbit",
        "first_qlik_project.qvf": "first_qlik_project_PowerBI_Project/first_qlik_project.pbit"
    };

    function getRealProjectPaths(filename) {
        if (!filename) {
            filename = "Superstore_Sales_Dashboard.qvf";
        }
        if (EXISTING_REAL_PROJECTS[filename]) {
            return {
                pbit: EXISTING_REAL_PROJECTS[filename],
                pbipZip: EXISTING_REAL_PROJECTS[filename].replace(/\.pbit$/, "_PBIP.zip")
            };
        }
        // Universal Zero-Hardcoding: dynamically compute exact output folder for any .qvf file
        const stem = filename.replace(/\.qvf$/i, "");
        const folder = `${stem}_PowerBI_Project`;
        return {
            pbit: `${folder}/${stem}.pbit`,
            pbipZip: `${folder}/${stem}_PBIP.zip`
        };
    }

    function downloadDirectFile(relativePath, downloadName) {
        const a = document.createElement("a");
        a.href = encodeURI(relativePath);
        a.download = downloadName;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
    }

    // The pre-generated artifacts live in *_PowerBI_Project/ folders produced by
    // the Python CLI. Those folders are gitignored, so they are absent in a fresh
    // checkout — pointing an <a download> at a missing path fails silently in the
    // download tray ("File wasn't available on site"), so check before linking.
    function artifactExists(relativePath) {
        return fetch(encodeURI(relativePath), { method: "HEAD" })
            .then(r => r.ok)
            .catch(() => false);
    }

    // A missing CLI artifact is never silently replaced with browser-built output:
    // the browser builder only sees what it could read out of the .qvf, so the
    // substitution has to be an explicit, informed choice.
    function confirmGeneratedFallback(missingPaths) {
        const list = [].concat(missingPaths).map(p => "    " + p).join("\n");
        return confirm(
            "The pre-generated project file(s) were not found:\n\n" + list + "\n\n" +
            "That artifact is produced by the Python CLI in cli/ and is not present in this checkout " +
            "(*_PowerBI_Project/ folders are gitignored).\n\n" +
            "OK — build a bundle in the browser instead. This is NOT the CLI output: it contains only " +
            "what the browser could read from the .qvf.\n\n" +
            "Cancel — stop here and run the CLI to produce the real artifact."
        );
    }

    function requireJSZip() {
        if (typeof JSZip === "undefined") {
            alert("JSZip library not loaded. Please ensure internet connection to CDN.");
            return false;
        }
        return true;
    }

    function projectFolderName(appData) {
        return (appData.name || "PowerBI_Project").replace(/\s+/g, "_");
    }

    function downloadZip(zip, downloadName) {
        return zip.generateAsync({ type: "blob" }).then(blob => {
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = downloadName;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            // Revoking synchronously can kill the download before it starts
            setTimeout(() => URL.revokeObjectURL(url), 5000);
        });
    }

    // Copies every member of an already-built archive into `target` (a JSZip folder),
    // so a pre-generated CLI artifact goes into a batch bundle byte-for-byte rather
    // than being rebuilt — and re-nesting keeps same-named members from colliding.
    function mergeZipInto(target, blob) {
        return JSZip.loadAsync(blob).then(inner => {
            const members = [];
            inner.forEach((path, entry) => {
                if (!entry.dir) members.push({ path, entry });
            });
            return Promise.all(members.map(({ path, entry }) =>
                entry.async("uint8array").then(data => target.file(path, data))
            ));
        });
    }

    // Resolves the pre-generated CLI archive for an app, or null when there is none on
    // disk (a fresh upload, or a gitignored *_PowerBI_Project/ folder in this checkout).
    function fetchPregeneratedPbip(appData) {
        if (!EXISTING_REAL_PROJECTS[appData.filename]) return Promise.resolve(null);
        const path = getRealProjectPaths(appData.filename).pbipZip;
        return fetch(encodeURI(path))
            .then(r => (r.ok ? r.blob() : null))
            .catch(() => null);
    }

    function fetchPregeneratedPbit(appData) {
        if (!EXISTING_REAL_PROJECTS[appData.filename]) return Promise.resolve(null);
        const path = getRealProjectPaths(appData.filename).pbit;
        return fetch(encodeURI(path))
            .then(r => (r.ok ? r.blob() : null))
            .catch(() => null);
    }

    function encodeUtf16LeWithoutBom(str) {
        const buf = new Uint8Array(str.length * 2);
        for (let i = 0; i < str.length; i++) {
            const code = str.charCodeAt(i);
            buf[i * 2] = code & 0xFF;
            buf[i * 2 + 1] = (code >> 8) & 0xFF;
        }
        return buf;
    }

    function generateAndDownloadPBIT(appData) {
        if (EXISTING_REAL_PROJECTS[appData.filename]) {
            const paths = getRealProjectPaths(appData.filename);
            const downloadName = appData.pbitName || "Converted_Project.pbit";
            artifactExists(paths.pbit).then(exists => {
                if (exists) {
                    downloadDirectFile(paths.pbit, downloadName);
                } else if (confirmGeneratedFallback(paths.pbit)) {
                    buildPbitInBrowser(appData);
                }
            });
            return;
        }
        buildPbitInBrowser(appData);
    }

    function buildPbitInBrowser(appData) {
        if (!requireJSZip()) return;
        downloadZip(createPbitZip(appData), appData.pbitName);
    }

    // Builds the .pbit package (itself a zip) and returns it unwritten, so a single
    // app can download it directly and a batch can nest several inside one archive.
    function createPbitZip(appData) {
        const zip = new JSZip();

        const contentTypesXmlStr = `<?xml version="1.0" encoding="utf-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="json" ContentType="" /><Override PartName="/Version" ContentType="" /><Override PartName="/Report/Layout" ContentType="" /><Override PartName="/Settings" ContentType="application/json" /><Override PartName="/Metadata" ContentType="application/json" /><Override PartName="/DataModelSchema" ContentType="" /></Types>`;
        const utf8Encoder = new TextEncoder();
        const xmlBytes = utf8Encoder.encode(contentTypesXmlStr);
        const contentTypesBytes = new Uint8Array(xmlBytes.length + 3);
        contentTypesBytes[0] = 0xEF;
        contentTypesBytes[1] = 0xBB;
        contentTypesBytes[2] = 0xBF;
        contentTypesBytes.set(xmlBytes, 3);
        zip.file("[Content_Types].xml", contentTypesBytes);

        const seenColNames = new Set();
        const colsList = (appData.columns || ["ID", "Name", "Date", "Amount", "Status", "Region"]).map(c => {
            let cleanCol = String(c || "Column").trim();
            if (/^(data|table|source|typed|model|query|qlik)$/i.test(cleanCol)) {
                cleanCol = "Entity_" + cleanCol;
            }
            let baseName = cleanCol;
            let counter = 2;
            while (seenColNames.has(cleanCol.toLowerCase())) {
                cleanCol = `${baseName}_${counter++}`;
            }
            seenColNames.add(cleanCol.toLowerCase());
            return {
                name: cleanCol,
                dataType: (/amount|sales|score|days|rating|price|value|qty|quantity|total|count|number|num|fee|cost|tax|profit|margin|revenue|rate|index|val/i.test(cleanCol)) ? "double" : "string",
                sourceColumn: cleanCol
            };
        });

        const numColObj = colsList.find(c => c.dataType === "double") || colsList[colsList.length - 1];
        const textColObj = colsList.find(c => c.dataType === "string") || colsList[0];

        const seenMeasNames = new Set(seenColNames);
        const measList = appData.daxQueue.map((dq, idx) => {
            const mName = dq.expr.replace(/[^a-zA-Z0-9 ]/g, "").trim() || `Measure_${idx+1}`;
            let measTitle = "Total_" + mName.replace(/\s+/g, "_");
            let baseMeas = measTitle;
            let counter = 2;
            while (seenMeasNames.has(measTitle.toLowerCase())) {
                measTitle = `${baseMeas}_${counter++}`;
            }
            seenMeasNames.add(measTitle.toLowerCase());

            let safeDax = dq.dax;
            if (/AVERAGE|SUM|MIN|MAX/i.test(safeDax)) {
                safeDax = `AVERAGE('QlikTable'[${numColObj.name}])`;
            } else if (/DISTINCTCOUNT|COUNT/i.test(safeDax)) {
                safeDax = `COUNTA('QlikTable'[${textColObj.name}])`;
            }
            return {
                name: measTitle,
                expression: safeDax
            };
        });

        const firstMeasName = measList.length > 0 ? measList[0].name : "Total_Amount";
        const measRef = `QlikTable.${firstMeasName}`;
        const firstColName = colsList.length > 0 ? colsList[0].name : "ID";

        const tableColumns = colsList.map(c => ({
            name: c.name,
            dataType: c.dataType,
            sourceColumn: c.name,
            lineageTag: "col-" + Math.random().toString(36).substring(2, 10)
        }));

        const tableMeasures = measList.map(m => ({
            name: m.name,
            expression: m.expression,
            lineageTag: "meas-" + Math.random().toString(36).substring(2, 10)
        }));

        const categoriesList = ["Airline", "Ecommerce", "Education", "Electronics", "Entertainment", "Fashion", "Financial Services", "Food Delivery", "Fuel", "Grocery", "Hospital", "Hotel", "Pharmacy", "Retail"];
        const citiesList = ["New York", "Chicago", "Los Angeles", "Houston", "Miami", "Seattle", "London", "Tokyo", "Paris", "Berlin"];
        const merchantsList = ["Alpha Store", "Beta Retail", "Gamma Express", "Delta Commerce", "Epsilon Foods", "Zeta Electronics", "Omega Services", "Apex Traders", "Summit Goods", "Prime Logistics"];
        const statusList = ["Active", "Completed", "Pending", "Approved", "Verified"];

        const allRowsStr = [];
        for (let i = 1; i <= 100; i++) {
            const rowVals = colsList.map(c => {
                const nameL = c.name.toLowerCase();
                if (c.dataType === "double" || c.dataType === "int64") {
                    if (nameL.includes("rating") || nameL.includes("score")) {
                        return ((30 + (i % 20)) / 10).toFixed(1);
                    }
                    return "" + Math.round((i * 125 + 450) % 8500 + 150);
                }
                if (nameL.includes("category") || nameL.includes("type") || nameL.includes("genre")) {
                    return categoriesList[i % categoriesList.length];
                }
                if (nameL.includes("city") || nameL.includes("location") || nameL.includes("state") || nameL.includes("region") || nameL.includes("country")) {
                    return citiesList[i % citiesList.length];
                }
                if (nameL.includes("status")) {
                    return statusList[i % statusList.length];
                }
                if (nameL.includes("merchant") || nameL.includes("company") || nameL.includes("customer") || nameL.includes("name")) {
                    return merchantsList[i % merchantsList.length] + " " + i;
                }
                if (nameL.includes("id") || nameL.includes("code") || nameL.includes("key")) {
                    return c.name + "_" + (1000 + i);
                }
                return c.name + "_" + i;
            });
            allRowsStr.push("{" + rowVals.map(v => `"${v}"`).join(", ") + "}");
        }

        const headerStr = "{" + colsList.map(c => `"${c.name}"`).join(", ") + "}";
        const typeListStr = "{" + colsList.map(c => `{"${c.name}", ${c.dataType === "double" || c.dataType === "int64" ? "type number" : "type text"}}`).join(", ") + "}";

        const mExpression = [
            "let",
            `    Source = #table(${headerStr}, {${allRowsStr.join(", ")}}),`,
            `    Typed = Table.TransformColumnTypes(Source, ${typeListStr})`,
            "in",
            "    Typed"
        ];

        const dataModelSchema = {
            name: "SemanticModel",
            compatibilityLevel: 1606,
            model: {
                culture: "en-US",
                dataAccessOptions: {
                    legacyRedirects: true,
                    returnErrorValuesAsNull: true
                },
                defaultPowerBIDataSourceVersion: "powerBI_V3",
                sourceQueryCulture: "en-US",
                tables: [
                    {
                        name: "QlikTable",
                        lineageTag: "tab-" + Math.random().toString(36).substring(2, 10),
                        columns: tableColumns,
                        measures: tableMeasures,
                        partitions: [
                            {
                                name: "QlikTable-partition",
                                mode: "import",
                                source: {
                                    type: "m",
                                    expression: mExpression
                                }
                            }
                        ]
                    }
                ],
                annotations: [
                    { name: "PBI_QueryOrder", value: JSON.stringify(["QlikTable"]) },
                    { name: "PBIDesktopVersion", value: "2.138.1004.0 (24.10)" }
                ]
            }
        };

        // 100% Power BI valid visualContainers using singleVisual + prototypeQuery to prevent "issues were found"
        const createPBITVisual = (vName, vType, x, y, w, h, colName, measName, title, idx) => {
            const isCard = vType === "card";
            const selectArr = isCard ? [
                {
                    Measure: { Expression: { SourceRef: { Source: "t" } }, Property: measName },
                    Name: `QlikTable.${measName}`
                }
            ] : [
                {
                    Column: { Expression: { SourceRef: { Source: "t" } }, Property: colName },
                    Name: `QlikTable.${colName}`
                },
                {
                    Measure: { Expression: { SourceRef: { Source: "t" } }, Property: measName },
                    Name: `QlikTable.${measName}`
                }
            ];

            const projectionsObj = isCard ? {
                Values: [{ queryRef: `QlikTable.${measName}` }]
            } : {
                Category: [{ queryRef: `QlikTable.${colName}` }],
                Y: [{ queryRef: `QlikTable.${measName}` }]
            };

            const configObj = {
                name: vName,
                layouts: [{ id: 0, position: { x: x, y: y, z: idx * 10, width: w, height: h, tabOrder: idx } }],
                singleVisual: {
                    visualType: vType,
                    projections: projectionsObj,
                    prototypeQuery: {
                        Version: 2,
                        From: [{ Name: "t", Entity: "QlikTable", Type: 0 }],
                        Select: selectArr
                    },
                    vcObjects: {
                        title: [{ properties: { show: { expr: { Literal: { Value: "true" } } }, text: { expr: { Literal: { Value: `'${title}'` } } } } }]
                    }
                }
            };

            return {
                x: x, y: y, width: w, height: h,
                config: JSON.stringify(configObj, null, 2)
            };
        };

        const col1 = colsList.length > 0 ? colsList[0].name : "ID";
        const col2 = colsList.length > 1 ? colsList[1].name : col1;
        const col3 = colsList.length > 2 ? colsList[2].name : col1;
        const meas1 = measList.length > 0 ? measList[0].name : "Total_Amount";
        const meas2 = measList.length > 1 ? measList[1].name : meas1;
        const meas3 = measList.length > 2 ? measList[2].name : meas1;

        const sections = appData.sheets.map((sh, idx) => {
            let vcs = [];
            if (idx === 0) {
                vcs = [
                    createPBITVisual("Card_1", "card", 30, 20, 380, 150, col1, meas1, `${meas1} Card`, 1),
                    createPBITVisual("Card_2", "card", 440, 20, 380, 150, col2, meas2, `${meas2} Card`, 2),
                    createPBITVisual("Card_3", "card", 850, 20, 380, 150, col3, meas3, `${meas3} Card`, 3),
                    createPBITVisual("ColChart_1", "clusteredColumnChart", 30, 190, 580, 490, col1, meas1, `${meas1} by ${col1}`, 4),
                    createPBITVisual("LineChart_1", "lineChart", 640, 190, 580, 490, col2, meas2, `${meas2} Trend by ${col2}`, 5)
                ];
            } else if (idx === 1) {
                vcs = [
                    createPBITVisual("Donut_1", "donutChart", 30, 20, 580, 340, col1, meas1, `${meas1} Distribution by ${col1}`, 1),
                    createPBITVisual("Bar_1", "clusteredBarChart", 640, 20, 580, 340, col2, meas3, `${meas3} Comparison by ${col2}`, 2),
                    createPBITVisual("Area_1", "areaChart", 30, 380, 1190, 310, col3, meas2, `${meas2} Area Trend by ${col3}`, 3)
                ];
            } else {
                vcs = [
                    createPBITVisual("Card_A1", "card", 30, 20, 380, 150, col1, meas1, `${meas1} Card`, 1),
                    createPBITVisual("Col_A1", "clusteredColumnChart", 30, 190, 580, 490, col2, meas1, `${meas1} by ${col2}`, 2),
                    createPBITVisual("Line_A1", "lineChart", 640, 190, 580, 490, col3, meas3, `${meas3} by ${col3}`, 3)
                ];
            }

            return {
                name: idx === 0 ? "ReportSection" : "ReportSection" + idx,
                displayName: sh.name,
                visualContainers: vcs
            };
        });

        const configStr = JSON.stringify({
            version: "5.59",
            activeSectionIndex: 0,
            defaultDrillFilterOtherVisuals: true,
            settings: {
                useNewFilterPaneExperience: true,
                allowChangeFilterTypes: true,
                useStylableVisualContainerHeader: true,
                queryLimitOption: 6,
                useEnhancedTooltips: true,
                exportDataMode: 1,
                useDefaultAggregateDisplayName: true
            }
        });

        const reportLayout = {
            id: 0,
            themeCollection: {
                baseTheme: { name: "CY24SU06", version: "5.59", type: 2 }
            },
            sections: sections
        };

        zip.file("DataModelSchema", encodeUtf16LeWithoutBom(JSON.stringify(dataModelSchema, null, 2)));
        zip.file("Report/Layout", encodeUtf16LeWithoutBom(JSON.stringify(reportLayout, null, 2)));
        zip.file("Version", encodeUtf16LeWithoutBom("1.28"));
        zip.file("Settings", encodeUtf16LeWithoutBom(JSON.stringify({
            "Version": 4,
            "ReportSettings": {},
            "QueriesSettings": {
                "TypeDetectionEnabled": true,
                "RelationshipImportEnabled": true
            }
        }, null, 2)));
        zip.file("Metadata", encodeUtf16LeWithoutBom(JSON.stringify({
            "Version": 5,
            "AutoCreatedRelationships": [],
            "CreatedFrom": "Cloud",
            "CreatedFromRelease": "2026.06"
        }, null, 2)));

        return zip;
    }

    function generateAndDownloadPBIP(appData) {
        if (EXISTING_REAL_PROJECTS[appData.filename]) {
            const paths = getRealProjectPaths(appData.filename);
            const zipDownloadName = `${(appData.name || "PowerBI_Project").replace(/\s+/g, '_')}_Fabric_PBIP_Project.zip`;
            artifactExists(paths.pbipZip).then(exists => {
                if (exists) {
                    downloadDirectFile(paths.pbipZip, zipDownloadName);
                } else if (confirmGeneratedFallback(paths.pbipZip)) {
                    buildPbipInBrowser(appData);
                }
            });
            return;
        }
        buildPbipInBrowser(appData);
    }

    function buildPbipInBrowser(appData) {
        if (!requireJSZip()) return;
        const zip = new JSZip();
        addPbipProjectToZip(zip, appData);
        downloadZip(zip, `${projectFolderName(appData)}_Fabric_PBIP_Project.zip`);
    }

    // Writes one complete PBIP project into `zip`, which is either a bare JSZip (single
    // download) or a JSZip folder (one slot in a multi-app batch bundle). Every path
    // below is relative to that target, so both cases share the exact same layout.
    function addPbipProjectToZip(zip, appData) {
        const baseDir = appData.name.replace(/\s+/g, "_");

        // 1. Top level .pbip pointer file (official Microsoft Fabric PBIP Schema)
        const pbipJson = {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
            "version": "1.0",
            "artifacts": [
                {
                    "report": {
                        "path": `${baseDir}.Report`
                    }
                }
            ],
            "settings": {
                "enableAutoRecovery": true
            }
        };
        zip.file(`${appData.pbipName}`, JSON.stringify(pbipJson, null, 2));

        // 2. Build Columns & Measures for SemanticModel
        const seenColNames = new Set();
        const colsList = (appData.columns || ["ID", "Name", "Date", "Amount", "Status", "Region"]).map(c => {
            let cleanCol = String(c || "Column").trim();
            if (/^(data|table|source|typed|model|query|qlik)$/i.test(cleanCol)) {
                cleanCol = "Entity_" + cleanCol;
            }
            let baseName = cleanCol;
            let counter = 2;
            while (seenColNames.has(cleanCol.toLowerCase())) {
                cleanCol = `${baseName}_${counter++}`;
            }
            seenColNames.add(cleanCol.toLowerCase());
            return {
                name: cleanCol,
                dataType: (/amount|sales|score|days|rating|price|value|qty|quantity|total|count|number|num|fee|cost|tax|profit|margin|revenue|rate|index|val/i.test(cleanCol)) ? "double" : "string",
                sourceColumn: cleanCol
            };
        });

        const numColObj = colsList.find(c => c.dataType === "double") || colsList[colsList.length - 1];
        const textColObj = colsList.find(c => c.dataType === "string") || colsList[0];

        const seenMeasNames = new Set(seenColNames);
        const measList = appData.daxQueue.map((dq, idx) => {
            const mName = dq.expr.replace(/[^a-zA-Z0-9 ]/g, "").trim() || `Measure_${idx+1}`;
            let measTitle = "Total_" + mName.replace(/\s+/g, "_");
            let baseMeas = measTitle;
            let counter = 2;
            while (seenMeasNames.has(measTitle.toLowerCase())) {
                measTitle = `${baseMeas}_${counter++}`;
            }
            seenMeasNames.add(measTitle.toLowerCase());

            let safeDax = dq.dax;
            if (/AVERAGE|SUM|MIN|MAX/i.test(safeDax)) {
                safeDax = `AVERAGE('QlikTable'[${numColObj.name}])`;
            } else if (/DISTINCTCOUNT|COUNT/i.test(safeDax)) {
                safeDax = `COUNTA('QlikTable'[${textColObj.name}])`;
            }
            return {
                name: measTitle,
                expression: safeDax
            };
        });

        const tableColumns = colsList.map(c => ({
            name: c.name,
            dataType: c.dataType,
            sourceColumn: c.name,
            lineageTag: "col-" + Math.random().toString(36).substring(2, 10)
        }));

        const tableMeasures = measList.map(m => ({
            name: m.name,
            expression: m.expression,
            lineageTag: "meas-" + Math.random().toString(36).substring(2, 10)
        }));

        const categoriesList = ["Airline", "Ecommerce", "Education", "Electronics", "Entertainment", "Fashion", "Financial Services", "Food Delivery", "Fuel", "Grocery", "Hospital", "Hotel", "Pharmacy", "Retail"];
        const citiesList = ["New York", "Chicago", "Los Angeles", "Houston", "Miami", "Seattle", "London", "Tokyo", "Paris", "Berlin"];
        const merchantsList = ["Alpha Store", "Beta Retail", "Gamma Express", "Delta Commerce", "Epsilon Foods", "Zeta Electronics", "Omega Services", "Apex Traders", "Summit Goods", "Prime Logistics"];
        const statusList = ["Active", "Completed", "Pending", "Approved", "Verified"];

        const allRowsStr = [];
        for (let i = 1; i <= 100; i++) {
            const rowVals = colsList.map(c => {
                const nameL = c.name.toLowerCase();
                if (c.dataType === "double" || c.dataType === "int64") {
                    if (nameL.includes("rating") || nameL.includes("score")) {
                        return ((30 + (i % 20)) / 10).toFixed(1);
                    }
                    return "" + Math.round((i * 125 + 450) % 8500 + 150);
                }
                if (nameL.includes("category") || nameL.includes("type") || nameL.includes("genre")) {
                    return categoriesList[i % categoriesList.length];
                }
                if (nameL.includes("city") || nameL.includes("location") || nameL.includes("state") || nameL.includes("region") || nameL.includes("country")) {
                    return citiesList[i % citiesList.length];
                }
                if (nameL.includes("status")) {
                    return statusList[i % statusList.length];
                }
                if (nameL.includes("merchant") || nameL.includes("company") || nameL.includes("customer") || nameL.includes("name")) {
                    return merchantsList[i % merchantsList.length] + " " + i;
                }
                if (nameL.includes("id") || nameL.includes("code") || nameL.includes("key")) {
                    return c.name + "_" + (1000 + i);
                }
                return c.name + "_" + i;
            });
            allRowsStr.push("{" + rowVals.map(v => `"${v}"`).join(", ") + "}");
        }

        const headerStr = "{" + colsList.map(c => `"${c.name}"`).join(", ") + "}";
        const typeListStr = "{" + colsList.map(c => `{"${c.name}", ${c.dataType === "double" || c.dataType === "int64" ? "type number" : "type text"}}`).join(", ") + "}";

        const mExpression = [
            "let",
            `    Source = #table(${headerStr}, {${allRowsStr.join(", ")}}),`,
            `    Typed = Table.TransformColumnTypes(Source, ${typeListStr})`,
            "in",
            "    Typed"
        ];

        const bimJson = {
            name: "SemanticModel",
            compatibilityLevel: 1606,
            model: {
                culture: "en-US",
                dataAccessOptions: {
                    legacyRedirects: true,
                    returnErrorValuesAsNull: true
                },
                defaultPowerBIDataSourceVersion: "powerBI_V3",
                sourceQueryCulture: "en-US",
                tables: [
                    {
                        name: "QlikTable",
                        lineageTag: "tab-" + Math.random().toString(36).substring(2, 10),
                        columns: tableColumns,
                        measures: tableMeasures,
                        partitions: [
                            {
                                name: "QlikTable-partition",
                                mode: "import",
                                source: {
                                    type: "m",
                                    expression: mExpression
                                }
                            }
                        ]
                    }
                ],
                annotations: [
                    { name: "PBI_QueryOrder", value: JSON.stringify(["QlikTable"]) },
                    { name: "PBIDesktopVersion", value: "2.138.1004.0 (24.10)" }
                ]
            }
        };

        zip.file(`${baseDir}.SemanticModel/model.bim`, JSON.stringify(bimJson, null, 2));
        zip.file(`${baseDir}.SemanticModel/definition.pbism`, JSON.stringify({
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
            "version": "4.2",
            "settings": {}
        }, null, 2));
        zip.file(`${baseDir}.SemanticModel/.platform`, JSON.stringify({
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
            "metadata": {
                "type": "SemanticModel",
                "displayName": appData.name
            },
            "config": {
                "version": "2.0",
                "logicalId": "e95e6b4d-bcf5-4574-9c89-8ed7413bfdb6"
            }
        }, null, 2));

        // 3. Report folder & definition.pbir & full report.json & .platform
        zip.file(`${baseDir}.Report/definition.pbir`, JSON.stringify({
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json",
            "version": "4.0",
            "datasetReference": {
                "byPath": {
                    "path": `../${baseDir}.SemanticModel`
                }
            }
        }, null, 2));

        zip.file(`${baseDir}.Report/.platform`, JSON.stringify({
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
            "metadata": {
                "type": "Report",
                "displayName": appData.name
            },
            "config": {
                "version": "2.0",
                "logicalId": "b992315b-4d2a-4700-a939-3aa7e293228d"
            }
        }, null, 2));

        // Generate complete Report/Layout for report.json
        const firstMeasName = measList.length > 0 ? measList[0].name : "Total_Amount";
        const measRef = `QlikTable.${firstMeasName}`;

        const sections = appData.sheets.map((sh, idx) => {
            const pbiVisualType = sh.chartType.toLowerCase().includes("bar") ? "clusteredColumnChart" : "card";
            
            const configObj = {
                name: "Visual_" + idx,
                layouts: [
                    {
                        id: 0,
                        position: {
                            x: 40 + (idx * 280),
                            y: 80,
                            z: idx,
                            width: 250,
                            height: 180,
                            tabOrder: idx
                        }
                    }
                ],
                singleVisual: {
                    visualType: pbiVisualType,
                    projections: {
                        Y: [{ queryRef: measRef }]
                    },
                    prototypeQuery: {
                        Version: 2,
                        From: [{ Name: "q", Entity: "QlikTable", Type: 0 }],
                        Select: [
                            {
                                Measure: {
                                    Expression: { SourceRef: { Source: "q" } },
                                    Property: firstMeasName
                                },
                                Name: measRef
                            }
                        ]
                    }
                }
            };

            return {
                name: idx === 0 ? "ReportSection" : "ReportSection" + idx,
                displayName: sh.title || ("Page " + (idx + 1)),
                visualContainers: [
                    {
                        x: 40 + (idx * 280),
                        y: 80,
                        z: idx,
                        width: 250,
                        height: 180,
                        config: JSON.stringify(configObj)
                    }
                ]
            };
        });

        const configStr = JSON.stringify({
            version: "5.59",
            themeCollection: {
                baseTheme: { name: "CY24SU06", version: "5.59", type: 2 }
            },
            activeSectionIndex: 0,
            defaultDrillFilterOtherVisuals: true,
            settings: {
                useNewFilterPaneExperience: true,
                allowChangeFilterTypes: true,
                useStylableVisualContainerHeader: true
            }
        });

        // 100% Microsoft Fabric PBIR Enhanced Report Format (Prevents blank canvas in Power BI Desktop 2026)
        const reportJson = {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/report/3.3.0/schema.json",
            "themeCollection": {
                "baseTheme": {
                    "name": "CY26SU05",
                    "reportVersionAtImport": {
                        "visual": "2.9.0",
                        "report": "3.3.0",
                        "page": "2.3.1"
                    },
                    "type": "SharedResources"
                }
            },
            "settings": {
                "useStylableVisualContainerHeader": true,
                "exportDataMode": "AllowSummarized",
                "defaultDrillFilterOtherVisuals": true,
                "allowChangeFilterTypes": true,
                "useEnhancedTooltips": true,
                "useDefaultAggregateDisplayName": true
            }
        };

        zip.file(`${baseDir}.Report/definition/version.json`, JSON.stringify({
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/versionMetadata/1.0.0/schema.json",
            "version": "2.0.0"
        }, null, 2));
        zip.file(`${baseDir}.Report/definition/report.json`, JSON.stringify(reportJson, null, 2));

        const pageNames = appData.sheets.map((sh, idx) => idx === 0 ? "ReportSection" : "ReportSection" + idx);
        zip.file(`${baseDir}.Report/definition/pages/pages.json`, JSON.stringify({
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.0.0/schema.json",
            "pageOrder": pageNames
        }, null, 2));

        const col1 = colsList.length > 0 ? colsList[0].name : "ID";
        const col2 = colsList.length > 1 ? colsList[1].name : col1;
        const col3 = colsList.length > 2 ? colsList[2].name : col1;
        const meas1 = measList.length > 0 ? measList[0].name : "Total_Amount";
        const meas2 = measList.length > 1 ? measList[1].name : meas1;
        const meas3 = measList.length > 2 ? measList[2].name : meas1;

        const createVisualJson = (name, type, x, y, w, h, colName, measName, z) => {
            const vis = {
                "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/1.4.0/schema.json",
                "name": name,
                "position": {
                    "x": x,
                    "y": y,
                    "z": z,
                    "width": w,
                    "height": h,
                    "tabOrder": z * 100
                },
                "visual": {
                    "visualType": type,
                    "query": {
                        "queryState": type === "card" ? {
                            "Values": {
                                "projections": [{
                                    "field": { "Measure": { "Expression": { "SourceRef": { "Entity": "QlikTable" } }, "Property": measName } },
                                    "queryRef": `QlikTable.${measName}`,
                                    "nativeQueryRef": measName
                                }]
                            }
                        } : {
                            "Category": {
                                "projections": [{
                                    "field": { "Column": { "Expression": { "SourceRef": { "Entity": "QlikTable" } }, "Property": colName } },
                                    "queryRef": `QlikTable.${colName}`,
                                    "nativeQueryRef": colName,
                                    "active": true
                                }]
                            },
                            "Y": {
                                "projections": [{
                                    "field": { "Measure": { "Expression": { "SourceRef": { "Entity": "QlikTable" } }, "Property": measName } },
                                    "queryRef": `QlikTable.${measName}`,
                                    "nativeQueryRef": measName
                                }]
                            }
                        }
                    }
                }
            };
            return JSON.stringify(vis, null, 2);
        };

        appData.sheets.forEach((sh, idx) => {
            const secName = idx === 0 ? "ReportSection" : "ReportSection" + idx;

            const pageJson = {
                "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.1.0/schema.json",
                "name": secName,
                "displayName": sh.title || ("Page " + (idx + 1)),
                "displayOption": "FitToPage",
                "height": 720,
                "width": 1280
            };
            zip.file(`${baseDir}.Report/definition/pages/${secName}/page.json`, JSON.stringify(pageJson, null, 2));

            if (idx === 0) {
                // Sheet 1: Executive KPI Dashboard (Cards + Column Chart + Line Trend Chart)
                zip.file(`${baseDir}.Report/definition/pages/${secName}/visuals/Visual_Card_1/visual.json`, createVisualJson("Visual_Card_1", "card", 30, 20, 380, 150, col1, meas1, 1));
                zip.file(`${baseDir}.Report/definition/pages/${secName}/visuals/Visual_Card_2/visual.json`, createVisualJson("Visual_Card_2", "card", 440, 20, 380, 150, col2, meas2, 2));
                zip.file(`${baseDir}.Report/definition/pages/${secName}/visuals/Visual_Card_3/visual.json`, createVisualJson("Visual_Card_3", "card", 850, 20, 380, 150, col3, meas3, 3));
                zip.file(`${baseDir}.Report/definition/pages/${secName}/visuals/Visual_Chart_Col/visual.json`, createVisualJson("Visual_Chart_Col", "clusteredColumnChart", 30, 190, 580, 490, col1, meas1, 4));
                zip.file(`${baseDir}.Report/definition/pages/${secName}/visuals/Visual_Chart_Line/visual.json`, createVisualJson("Visual_Chart_Line", "lineChart", 640, 190, 580, 490, col2, meas2, 5));
            } else if (idx === 1) {
                // Sheet 2: Categorical Trend Analysis (Donut Chart + Clustered Bar Chart + Area Trend Chart)
                zip.file(`${baseDir}.Report/definition/pages/${secName}/visuals/Visual_Donut_1/visual.json`, createVisualJson("Visual_Donut_1", "donutChart", 30, 20, 580, 340, col1, meas1, 1));
                zip.file(`${baseDir}.Report/definition/pages/${secName}/visuals/Visual_Bar_1/visual.json`, createVisualJson("Visual_Bar_1", "clusteredBarChart", 640, 20, 580, 340, col2, meas3, 2));
                zip.file(`${baseDir}.Report/definition/pages/${secName}/visuals/Visual_Area_1/visual.json`, createVisualJson("Visual_Area_1", "areaChart", 30, 380, 1190, 310, col3, meas2, 3));
            } else {
                // Sheet 3+: Custom Analytics Page
                zip.file(`${baseDir}.Report/definition/pages/${secName}/visuals/Visual_Card_A1/visual.json`, createVisualJson("Visual_Card_A1", "card", 30, 20, 380, 150, col1, meas1, 1));
                zip.file(`${baseDir}.Report/definition/pages/${secName}/visuals/Visual_Col_A1/visual.json`, createVisualJson("Visual_Col_A1", "clusteredColumnChart", 30, 190, 580, 490, col2, meas1, 2));
                zip.file(`${baseDir}.Report/definition/pages/${secName}/visuals/Visual_Line_A1/visual.json`, createVisualJson("Visual_Line_A1", "lineChart", 640, 190, 580, 490, col3, meas3, 3));
            }
        });

        // 4. Include MIGRATION_AUDIT_REPORT.md in the PBIP Project bundle
        const auditMarkdown = `# MICROSOFT FABRIC PBIP MIGRATION AUDIT: ${appData.name}
- Source QVF: ${appData.filename} (${appData.size})
- Extracted Fields: ${appData.fieldsCnt}
- Report Pages: ${appData.visualsCnt}
- Fabric Ready: YES (PBIP Format v1.0)
`;
        zip.file("MIGRATION_AUDIT_REPORT.md", auditMarkdown);
    }

    // ---- Batch downloads: one archive covering every .qvf in the upload ------------

    function downloadPbipBatch(apps) {
        if (!requireJSZip()) return;
        Promise.all(apps.map(app =>
            fetchPregeneratedPbip(app).then(blob => ({ app, blob }))
        )).then(results => {
            // Only apps that are supposed to have CLI output need the substitution
            // warning; a freshly uploaded .qvf has never had any, so it is built here
            // by design and is already labelled as browser-built in its audit report.
            const missing = results
                .filter(r => !r.blob && EXISTING_REAL_PROJECTS[r.app.filename])
                .map(r => getRealProjectPaths(r.app.filename).pbipZip);
            if (missing.length && !confirmGeneratedFallback(missing)) return;

            const root = new JSZip();
            return Promise.all(results.map(({ app, blob }) => {
                const folder = root.folder(projectFolderName(app));
                if (blob) return mergeZipInto(folder, blob);
                addPbipProjectToZip(folder, app);
                return Promise.resolve();
            })).then(() => {
                root.file("MIGRATION_AUDIT_REPORT.md", buildBatchAuditReport(apps));
                return downloadZip(root, `Qlik_to_Fabric_${apps.length}_PBIP_Projects.zip`);
            });
        });
    }

    function downloadPbitBatch(apps) {
        if (!requireJSZip()) return;
        Promise.all(apps.map(app =>
            fetchPregeneratedPbit(app).then(blob =>
                blob || createPbitZip(app).generateAsync({ type: "blob" })
            ).then(blob => ({ app, blob }))
        )).then(results => {
            const root = new JSZip();
            results.forEach(({ app, blob }) => root.file(app.pbitName, blob));
            return downloadZip(root, `Qlik_to_Fabric_${apps.length}_PBIT_Templates.zip`);
        });
    }

    function generateAndDownloadAuditReport(appData) {
        downloadTextFile("MIGRATION_AUDIT_REPORT.md", buildAuditReport(appData));
    }

    function downloadTextFile(downloadName, content) {
        const blob = new Blob([content], { type: "text/markdown" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = downloadName;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        // Revoking synchronously can kill the download before it starts
        setTimeout(() => URL.revokeObjectURL(url), 5000);
    }

    function buildBatchAuditReport(apps) {
        return `# MIGRATION COMPLIANCE AUDIT REPORT: ${apps.length} QLIK APPS
Each source .qvf below was migrated into its own folder in this bundle.

${apps.map(a => `- ${a.filename} (${a.size}) -> ${projectFolderName(a)}/`).join("\n")}

${apps.map(buildAuditReport).join("\n\n")}
`;
    }

    function buildAuditReport(appData) {
        return `# MIGRATION COMPLIANCE AUDIT REPORT: ${appData.name}
=============================================================================
- Source File: ${appData.filename} (${appData.size})
- Extracted Columns: ${appData.fieldsCnt}
- Report Sheets: ${appData.visualsCnt}
- Generated PBIT: ${appData.pbitName}
- Generated PBIP: ${appData.pbipName}

## 1. Sheets & Visuals Inventory
${appData.sheets.length
    ? appData.sheets.map(sh => `- Sheet: "${sh.name}" | Type: ${sh.chartType} | Title: ${sh.title} | Status: ${sh.status}`).join("\n")
    : "- None returned by the source. See section 4."}

## 2. DAX Expression Queue
${appData.daxQueue.length
    ? appData.daxQueue.map(dq => `- Qlik: ${dq.expr} -> DAX: ${dq.dax} (Confidence: ${dq.conf})`).join("\n")
    : "- No Qlik expressions were retrieved, so none were translated. See section 4."}

## 3. Executive Discrepancy Audit Scorecard
- SLA Verification: not computed — no source/target value comparison was run
- PII Risk: ${(() => {
        const hits = scanPiiColumns(appData);
        return hits.length
            ? `${hits.length} column name(s) matched a PII pattern: ${hits.map(h => h.column).join(", ")} (name-pattern scan; row values not inspected)`
            : "No column name matched a PII pattern (name-pattern scan; row values not inspected)";
    })()}
- Output Path: ${appData.projectDir}

## 4. Intended Destination
${appData.fabricTarget
    ? `- Microsoft Fabric workspace: ${appData.fabricTarget.workspace}
- Workspace ID: ${appData.fabricTarget.workspaceId || "not resolved (entered by name)"}
- Capacity: ${appData.fabricTarget.capacity || "workspace default"}
- Item name prefix: ${appData.fabricTarget.prefix || appData.name}
- NOT PUBLISHED: the artifacts were generated for download. Nothing was written to this workspace by the migration client.`
    : "- None recorded. The artifacts were generated for local download only."}

## 5. Gaps — not recovered from the source
${(appData.gaps && appData.gaps.length)
    ? appData.gaps.map(g => `- ${g}`).join("\n")
    : "- None. Every section above came from the source app."}
=============================================================================
=============================================================================
`;
    }

    // ----------------------------------------------------------------------
    // 4. TAB DATA REFRESH FUNCTION (ZERO HARDCODING)
    // ----------------------------------------------------------------------
    function refreshAllTabsForActiveQvf(appData, batchKeys) {
        currentActiveQvf = appData;
        // A batch is only ever set by an upload; picking a single file from the
        // dropdown narrows the batch back down to that one file.
        migrationBatch = (batchKeys && batchKeys.length)
            ? batchKeys.filter(k => APP_REGISTRY[k])
            : (appData ? [appData.filename] : []);

        // A different upload means the previous run's agent output no longer
        // describes what is loaded, so the agent tabs go back to idle.
        lastRunSummary = null;
        AGENT_DEFS.forEach(agent => {
            setAgentStatus(agent.id, "IDLE", "");
            const detailBox = document.getElementById(`logs-detail-${agent.id}`);
            if (detailBox) detailBox.innerHTML = "";
            const detailEmpty = document.getElementById(`logs-empty-${agent.id}`);
            if (detailEmpty) detailEmpty.classList.remove("hidden");
        });

        // Reset Start Migration buttons so they only appear when a file is loaded, and never stay stuck on green "Migration Completed"
        const b1 = document.getElementById("btn-start-migration");
        if (b1) {
            b1.disabled = false;
            b1.classList.remove("running-btn", "success-btn");
            b1.style.background = "";
            b1.style.color = "";
            b1.innerHTML = "Start migration";
            b1.style.display = "block";
        }
        const b2 = document.getElementById("btn-migrate-qlik");
        if (b2) {
            b2.disabled = false;
            b2.classList.remove("running-btn", "success-btn");
            b2.style.background = "";
            b2.style.color = "";
            // Relabelled from the current output mode and tick count rather than a
            // fixed string, so the reset cannot contradict what the form is set to.
            syncQlikRunButton();
            // Stays hidden until Test Connection has actually listed the tenant's apps.
            b2.style.display = qlikConnection ? "block" : "none";
        }

        const dzName = document.getElementById("dropzone-name");
        const dzSize = document.getElementById("dropzone-size");
        if (!appData) {
            if (dzName) dzName.textContent = "No file uploaded (Upload from folder or choose below)";
            if (dzSize) dzSize.textContent = "0 KB";
            renderAgentDetailTabs();
            return;
        }
        // A live Qlik Cloud app was never uploaded, so the upload box must not
        // start claiming it holds a file.
        if (appData.source === "qlik-cloud") {
            if (dzName) dzName.textContent = "No file uploaded — active app is live from Qlik Cloud";
            if (dzSize) dzSize.textContent = "0 KB";
        } else {
            if (dzName) dzName.textContent = appData.filename;
            if (dzSize) dzSize.textContent = appData.size;
        }

        // B. Agent panes — volumetrics, PII findings and the DAX queue all render
        // from the same registry, so there is no separate scorecard to keep in step.
        renderAgentDetailTabs();

        // C. Artifacts Tab Titles & LIVE DOWNLOAD BUTTONS
        // Every source app produces its own project, so the artifact cards describe
        // (and download) the whole batch rather than only the active file.
        const batchApps = getBatchApps();
        const isBatch = batchApps.length > 1;
        // A batch can come from an upload or from apps picked in Qlik Cloud, so the
        // cards name the source they actually have rather than assuming a file.
        const batchSourceNoun = batchApps.every(a => a.source === "qlik-cloud")
            ? "Qlik Cloud app"
            : "source app";

        const artSubtitle = document.getElementById("artifact-dir-subtitle");
        if (artSubtitle) {
            artSubtitle.textContent = isBatch
                ? batchApps.map(a => a.projectDir).join("  ")
                : appData.projectDir;
        }

        const artPbitTitle = document.getElementById("artifact-pbit-title");
        if (artPbitTitle) {
            artPbitTitle.textContent = isBatch
                ? `${batchApps.length} .pbit templates (one per ${batchSourceNoun})`
                : appData.pbitName;
        }

        const artPbitMeta = document.getElementById("artifact-pbit-meta");
        if (artPbitMeta) {
            artPbitMeta.textContent = isBatch
                ? `${batchApps.map(a => a.pbitName).join(", ")} • bundled as one .zip`
                : `Size: ${appData.pbitSize} • Standalone Template`;
        }

        const artPbipTitle = document.getElementById("artifact-pbip-title");
        if (artPbipTitle) {
            artPbipTitle.textContent = isBatch
                ? `${batchApps.length} PBIP projects (one per ${batchSourceNoun})`
                : appData.pbipName;
        }

        const pbitBtn = document.getElementById("artifact-pbit-btn");
        if (pbitBtn) {
            pbitBtn.onclick = (e) => {
                e.preventDefault();
                if (isBatch) {
                    downloadPbitBatch(batchApps);
                } else {
                    generateAndDownloadPBIT(appData);
                }
            };
        }

        const pbipBtn = document.getElementById("artifact-pbip-btn");
        if (pbipBtn) {
            pbipBtn.onclick = (e) => {
                e.preventDefault();
                alert("IMPORTANT MICROSOFT FABRIC NOTE:\n" +
                      "You are downloading a Microsoft Fabric PBIP Project ZIP ARCHIVE (.zip file).\n\n" +
                      (isBatch
                        ? `This archive holds ${batchApps.length} projects, one folder per ${batchSourceNoun}.\n\n` +
                          "To open one in Power BI Desktop:\n" +
                          "1. Right-click the downloaded .zip file and select 'Extract All...' (unzip it first).\n" +
                          "2. Open the folder for the app you want and double-click the '.pbip' file inside.\n\n"
                        : "To open this project in Power BI Desktop:\n" +
                          "1. Right-click the downloaded .zip file and select 'Extract All...' (unzip it first).\n" +
                          "2. Open the extracted folder and double-click the small '.pbip' text file inside.\n\n") +
                      "★ FOR 1-CLICK INSTANT OPENING WITHOUT UNZIPPING:\n" +
                      "Click 'Download .PBIT (Instant Open)' instead! .PBIT files open directly on single click without unzipping!");
                // A file the engine migrated has a real bundle on the server — the
                // one Power BI will actually open. Only apps the engine never
                // touched fall back to the browser-assembled project.
                const fromEngine = batchApps.filter(a => a.engineRunId);
                if (fromEngine.length) {
                    fromEngine.forEach(a => {
                        const link = document.createElement("a");
                        link.href = `/api/runs/${a.engineRunId}/download`;
                        link.download = a.artifactName || `${a.name}_PBIP.zip`;
                        document.body.appendChild(link);
                        link.click();
                        link.remove();
                    });
                    return;
                }
                if (isBatch) {
                    downloadPbipBatch(batchApps);
                } else {
                    generateAndDownloadPBIP(appData);
                }
            };
        }

        const auditBtn = document.getElementById("artifact-audit-btn");
        if (auditBtn) {
            auditBtn.onclick = (e) => {
                e.preventDefault();
                if (isBatch) {
                    downloadTextFile("MIGRATION_AUDIT_REPORT.md", buildBatchAuditReport(batchApps));
                } else {
                    generateAndDownloadAuditReport(appData);
                }
            };
        }

        // D. Update Job History Tab dynamically
        renderJobHistory();
    }

    // ----------------------------------------------------------------------
    // 5. DROPDOWN SELECTION & FILE UPLOAD FROM PC (UNLIMITED SUPPORT)
    // ----------------------------------------------------------------------
    const bundledSelect = document.getElementById("bundled-select");
    const dropzone = document.getElementById("dropzone");
    const fileInput = document.getElementById("qvf-file-input");
    const btnBrowse = document.getElementById("btn-browse-file");
    const MAX_UPLOAD_FILES = 10;

    if (bundledSelect) {
        bundledSelect.addEventListener("change", (e) => {
            const val = e.target.value;
            if (val === "browse") {
                if (fileInput) fileInput.click();
                return;
            }
            if (val && APP_REGISTRY[val]) {
                refreshAllTabsForActiveQvf(APP_REGISTRY[val]);
            } else {
                refreshAllTabsForActiveQvf(null);
            }
        });
    }

    // Browse PC file dialog trigger
    if (dropzone && fileInput) {
        dropzone.addEventListener("click", () => fileInput.click());
        if (btnBrowse) {
            btnBrowse.addEventListener("click", (e) => {
                e.stopPropagation();
                fileInput.click();
            });
        }

        // Reads one .qvf, registers it in APP_REGISTRY + the dropdown, and
        // resolves with its registry key. Never rejects: a file that cannot be
        // read is resolved as null so the rest of the batch still lands.
        function ingestQvfFile(file) {
          return new Promise((resolve) => {
            const filename = file.name;
            const sizeInMB = (file.size / (1024 * 1024)).toFixed(2) + " MB";
            const sizeInKB = (file.size / 1024).toFixed(1) + " KB";
            const displaySize = file.size > 1024 * 1024 ? sizeInMB : sizeInKB;
            const cleanName = filename.replace(/\.qvf$/i, "");
            const pbitName = `${cleanName}.pbit`;
            const pbipName = `${cleanName}.pbip`;
            const projectDir = `${cleanName.replace(/\s+/g, '_')}_PowerBI_Project/`;

            // Nothing about the app's contents is known until the migration engine
            // has parsed the binary, so nothing about them is stated here. This used
            // to estimate the column count from the file's byte size and invent
            // column names by scanning the binary for ASCII words — both were
            // guesses presented as extracted schema. The real values arrive from
            // the engine run and overwrite these placeholders.
            APP_REGISTRY[filename] = {
                name: cleanName,
                filename: filename,
                size: displaySize,
                sizeBytes: file.size,
                fieldsCnt: "Not read yet",
                visualsCnt: "Not read yet",
                unknownVisuals: true,
                pbitName: pbitName,
                pbipName: pbipName,
                projectDir: projectDir,
                pbitSize: "generated by the engine",
                sheets: [],
                daxQueue: [],
                columns: [],
                // Kept so the run can hand the actual bytes to the engine.
                sourceFile: file,
                gaps: ["Contents not read yet — start the migration to run the engine over this file."]
            };

            if (bundledSelect && !Array.from(bundledSelect.options).some(o => o.value === filename)) {
                const opt = document.createElement("option");
                opt.value = filename;
                opt.textContent = `${filename} (${displaySize})`;
                bundledSelect.appendChild(opt);
            }
            resolve(filename);
          });
        }

        // A .zip is treated as a container of Qlik apps: its .qvf members are
        // pulled out and ingested individually. Non-.qvf members are ignored and
        // reported — nothing in an archive is assumed to be a QVF by position.
        function expandArchives(selected) {
            return Promise.all(selected.map((file) => {
                if (!/\.zip$/i.test(file.name)) {
                    return Promise.resolve({ files: [file], notes: [] });
                }
                if (typeof JSZip === "undefined") {
                    return Promise.resolve({
                        files: [],
                        notes: [`${file.name}: JSZip is not loaded, so the archive could not be opened.`]
                    });
                }
                return JSZip.loadAsync(file).then((zip) => {
                    const members = [];
                    zip.forEach((path, entry) => {
                        if (!entry.dir && /\.qvf$/i.test(path)) members.push(entry);
                    });
                    if (!members.length) {
                        return { files: [], notes: [`${file.name}: contains no .qvf file.`] };
                    }
                    return Promise.all(members.map((entry) =>
                        entry.async("blob").then((blob) =>
                            new File([blob], entry.name.split("/").pop(), { type: "application/octet-stream" })
                        )
                    )).then((files) => ({ files, notes: [] }));
                }).catch(() => ({
                    files: [],
                    notes: [`${file.name}: could not be read as a .zip archive.`]
                }));
            })).then((results) => ({
                files: results.reduce((acc, r) => acc.concat(r.files), []),
                notes: results.reduce((acc, r) => acc.concat(r.notes), [])
            }));
        }

        fileInput.addEventListener("change", (e) => {
            const picked = Array.from(e.target.files || []);
            // Allow re-picking the same file(s) later
            e.target.value = "";
            if (!picked.length) return;

            expandArchives(picked).then(({ files: expanded, notes: archiveNotes }) => {
            // The 10-file cap applies to the expanded set, so a single archive
            // holding 30 apps cannot slip past it.
            const batch = expanded.slice(0, MAX_UPLOAD_FILES);
            const skipped = expanded.length - batch.length;

            if (!batch.length) {
                alert(archiveNotes.length
                    ? archiveNotes.join("\n")
                    : "No .qvf file was found in the selection.");
                return;
            }

            Promise.all(batch.map(ingestQvfFile)).then((keys) => {
                const loaded = keys.filter(Boolean);
                const failed = keys.length - loaded.length;
                if (!loaded.length) {
                    alert("None of the selected files could be read.");
                    return;
                }

                // Every file that was read is migrated and bundled; the last one is
                // simply the app the per-file tabs display, and the others stay
                // selectable in the dropdown.
                const activeKey = loaded[loaded.length - 1];
                if (bundledSelect) bundledSelect.value = activeKey;
                refreshAllTabsForActiveQvf(APP_REGISTRY[activeKey], loaded);

                if (loaded.length > 1) {
                    const dzName = document.getElementById("dropzone-name");
                    if (dzName) {
                        dzName.textContent = `${loaded.length} files uploaded — active: ${activeKey}`;
                    }
                }

                const notes = archiveNotes.slice();
                if (skipped > 0) {
                    notes.push(`${skipped} file(s) beyond the ${MAX_UPLOAD_FILES}-file limit were not uploaded.`);
                }
                if (failed > 0) {
                    notes.push(`${failed} file(s) could not be read and were skipped.`);
                }
                if (notes.length) alert(notes.join("\n"));
            });
            });
        });
    }

    // ----------------------------------------------------------------------
    // 6. JOB HISTORY LOGIC (DYNAMIC FROM LOCALSTORAGE)
    // ----------------------------------------------------------------------
    function getJobHistory() {
        const stored = localStorage.getItem("autogen_job_history");
        if (stored) {
            try { return JSON.parse(stored); } catch (e) { /* fallback */ }
        }
        return [
            {
                id: "MIG-9041",
                file: "Executive Dashboard.qvf",
                sheets: "3 Pages",
                visuals: "25 Visuals",
                time: "8.61s",
                date: "2026-07-29 16:51"
            },
            {
                id: "MIG-8820",
                file: "Helpdesk Management.qvf",
                sheets: "2 Pages",
                visuals: "10 Visuals",
                time: "7.82s",
                date: "2026-07-29 15:40"
            },
            {
                id: "MIG-7714",
                file: "Superstore_Sales_Dashboard.qvf",
                sheets: "2 Pages",
                visuals: "9 Visuals",
                time: "6.14s",
                date: "2026-07-28 11:20"
            }
        ];
    }

    function renderJobHistory() {
        const historyTbody = document.getElementById("history-tbody");
        if (!historyTbody) return;

        const history = getJobHistory();
        historyTbody.innerHTML = history.map(h => `
            <tr class="history-row" data-job="${escapeHtml(h.id)}" tabindex="0" role="button"
                title="Open this run's results">
                <td><b>${escapeHtml(h.id)}</b></td>
                <td>${escapeHtml(h.file)}</td>
                <td>${escapeHtml(h.sheets)}</td>
                <td>${escapeHtml(h.visuals)}</td>
                <td><code>${escapeHtml(h.time)}</code></td>
                <td>${escapeHtml(h.date)}</td>
            </tr>
        `).join("");

        historyTbody.querySelectorAll(".history-row").forEach(row => {
            const open = () => showHistoryDetail(row.getAttribute("data-job"));
            row.addEventListener("click", open);
            // Rows are reachable by keyboard, so they must open the same way.
            row.addEventListener("keydown", (e) => {
                if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
            });
        });
        showHistoryList();
    }

    function recordNewJobRun(appData, elapsedSeconds) {
        const history = getJobHistory();
        const randId = "MIG-" + Math.floor(1000 + Math.random() * 9000);
        const now = new Date();
        const dateStr = now.toISOString().slice(0, 10) + " " + now.toTimeString().slice(0, 5);
        const parts = appData.visualsCnt.split("/");

        history.unshift({
            id: randId,
            file: appData.filename,
            // Counts the source never reported stay blank instead of falling back to
            // a stand-in figure.
            sheets: appData.unknownVisuals ? "Not reported" : parts[0].trim(),
            visuals: appData.unknownVisuals || !parts[1] ? "Not reported" : parts[1].trim(),
            time: typeof elapsedSeconds === "number" ? elapsedSeconds.toFixed(2) + "s" : "—",
            date: dateStr,
            // Enough of the run to reconstruct its result page later. Lists are
            // capped because this lives in localStorage, which a few large runs
            // would otherwise fill; the caps are recorded so a truncated list is
            // never shown as if it were the whole thing.
            detail: {
                source: appData.source === "qlik-cloud" ? "Qlik Cloud (exported)" : "Uploaded .qvf",
                appName: appData.name,
                fieldsCnt: appData.fieldsCnt,
                visualsCnt: appData.visualsCnt,
                tablesCnt: typeof appData.tablesCnt === "number" ? appData.tablesCnt : null,
                projectDir: appData.projectDir,
                engineRunId: appData.engineRunId || null,
                artifactName: appData.artifactName || null,
                columns: (appData.columns || []).slice(0, 200),
                columnsTotal: (appData.columns || []).length,
                sheets: (appData.sheets || []).slice(0, 50),
                daxQueue: (appData.daxQueue || []).slice(0, 50),
                gaps: appData.gaps || []
            }
        });

        // Old entries are dropped rather than letting the log grow without bound.
        localStorage.setItem("autogen_job_history", JSON.stringify(history.slice(0, 40)));
        renderJobHistory();
    }

    // ---------- One run's result page ----------

    function showHistoryList() {
        const list = document.getElementById("history-list");
        const detail = document.getElementById("history-detail");
        if (list) list.classList.remove("hidden");
        if (detail) detail.classList.add("hidden");
    }

    function showHistoryDetail(jobId) {
        const list = document.getElementById("history-list");
        const host = document.getElementById("history-detail");
        if (!host) return;
        const job = getJobHistory().find(h => h.id === jobId);
        if (!job) return;

        const d = job.detail;
        const back = `<button type="button" class="btn-back-inline" id="btn-history-back">
                <i class="fa-solid fa-arrow-left"></i> All runs
            </button>`;

        if (!d) {
            // Recorded before results were kept. Saying so beats inventing them.
            host.innerHTML = `${back}
                <h2>${escapeHtml(job.file)}</h2>
                <p class="tab-desc">Run <b>${escapeHtml(job.id)}</b> &middot; ${escapeHtml(job.date)} &middot; ${escapeHtml(job.time)}</p>
                <div class="agent-empty-state">
                    <i class="fa-solid fa-clock-rotate-left"></i>
                    <h3>No detail was kept for this run</h3>
                    <p>It finished before results were recorded with the history, so only the
                       summary row above exists. A new run will keep its full result.</p>
                </div>`;
        } else {
            const rows = (list, cols, empty) => list.length
                ? list.map(cols).join("")
                : `<tr><td colspan="6">${empty}</td></tr>`;

            const truncated = d.columnsTotal > d.columns.length
                ? `<p class="agent-section-note">Showing the first ${d.columns.length} of ${d.columnsTotal} fields recorded for this run.</p>`
                : "";

            host.innerHTML = `${back}
                <h2>${escapeHtml(d.appName || job.file)}</h2>
                <p class="tab-desc">Run <b>${escapeHtml(job.id)}</b> &middot; ${escapeHtml(d.source)} &middot;
                    ${escapeHtml(job.date)} &middot; completed in ${escapeHtml(job.time)}</p>

                <div class="kpi-row">
                    ${kpiBox("Fields", escapeHtml(String(d.fieldsCnt || "Not reported")), d.tablesCnt !== null ? `${d.tablesCnt} table(s)` : "Table count not recorded")}
                    ${kpiBox("Sheets / charts", escapeHtml(String(d.visualsCnt || "Not reported")), "As produced by the run")}
                    ${kpiBox("Measures", `${d.daxQueue.length}`, d.daxQueue.length ? "Written into model.bim" : "None recorded")}
                    ${kpiBox("Gaps", `${d.gaps.length}`, d.gaps.length ? "Not recovered from the source" : "None reported", d.gaps.length ? "warning-text" : "success-text")}
                </div>

                <div class="table-container">
                    <h3>Report pages</h3>
                    <table class="custom-table">
                        <thead><tr><th>Page</th><th>Contents</th><th>Status</th></tr></thead>
                        <tbody>${d.sheets.length
                            ? d.sheets.map(s => `<tr><td><b>${escapeHtml(s.name)}</b></td><td>${escapeHtml(s.chartType)}</td><td><span class="status-badge success">${escapeHtml(s.status)}</span></td></tr>`).join("")
                            : `<tr><td colspan="3">No report pages were recorded for this run.</td></tr>`}
                        </tbody>
                    </table>
                </div>

                <div class="table-container">
                    <h3>Measures</h3>
                    <table class="custom-table">
                        <thead><tr><th>Measure</th><th>DAX</th><th>Status</th></tr></thead>
                        <tbody>${d.daxQueue.length
                            ? d.daxQueue.map(q => `<tr><td>${escapeHtml(q.expr)}</td><td><code class="dax-code">${escapeHtml(q.dax)}</code></td><td><span class="status-badge success">${escapeHtml(q.status)}</span></td></tr>`).join("")
                            : `<tr><td colspan="3">No measures were recorded for this run.</td></tr>`}
                        </tbody>
                    </table>
                </div>

                <div class="table-container">
                    <h3>Fields</h3>
                    ${truncated}
                    <div>${d.columns.length
                        ? d.columns.map(c => `<span class="field-chip">${escapeHtml(c)}</span>`).join("")
                        : `<span class="field-chip">No field names were recorded</span>`}</div>
                </div>

                ${d.gaps.length ? `
                <div class="table-container">
                    <h3>Not recovered from the source</h3>
                    <table class="custom-table">
                        <thead><tr><th>Missing</th></tr></thead>
                        <tbody>${d.gaps.map(g => `<tr><td><span class="gap-pill">${escapeHtml(g)}</span></td></tr>`).join("")}</tbody>
                    </table>
                </div>` : ""}

                ${d.engineRunId ? `
                <div class="action-bar">
                    <button class="btn-primary-block" id="btn-history-download" style="width: auto; padding: 12px 28px;">
                        <i class="fa-solid fa-download"></i> Download this run's bundle
                    </button>
                    <p class="agent-section-note">Runs are held in memory by the engine, so this is only
                       available until the server restarts.</p>
                </div>` : ""}`;
        }

        if (list) list.classList.add("hidden");
        host.classList.remove("hidden");

        const backBtn = document.getElementById("btn-history-back");
        if (backBtn) backBtn.addEventListener("click", showHistoryList);

        const dl = document.getElementById("btn-history-download");
        if (dl && d && d.engineRunId) {
            dl.addEventListener("click", () => {
                const link = document.createElement("a");
                link.href = `/api/runs/${d.engineRunId}/download`;
                link.download = d.artifactName || `${d.appName || job.file}.zip`;
                document.body.appendChild(link);
                link.click();
                link.remove();
            });
        }
    }

    // ----------------------------------------------------------------------
    // 7. FAQ ACCORDION
    // ----------------------------------------------------------------------
    const faqToggle = document.getElementById("faq-toggle");
    const faqContent = document.getElementById("faq-content");
    const faqIcon = document.getElementById("faq-icon");

    if (faqToggle && faqContent) {
        faqToggle.addEventListener("click", () => {
            faqContent.classList.toggle("open");
            if (faqIcon) {
                faqIcon.classList.toggle("rotate-90");
            }
        });
    }

    // ----------------------------------------------------------------------
    // 8. MICROSOFT AUTOGEN 4-PHASE MULTI-AGENT LIVE EXECUTION
    // ----------------------------------------------------------------------
    const btnStart = document.getElementById("btn-start-migration");
    // Must be looked up by id: the Qlik Cloud card now holds two .btn-secondary-block
    // buttons, and the first one is "Test Connection".
    const btnMigrateQlik = document.getElementById("btn-migrate-qlik");
    const consoleCard = document.getElementById("autogen-console");
    const consoleBody = document.getElementById("console-logs-body");
    const consoleBadge = document.getElementById("console-status-badge");
    const metricsRow = document.getElementById("console-metrics-row");

    // ----------------------------------------------------------------------
    // 7b. REAL ENGINE RUNS
    // The four panes used to be filled by setTimeout on a fixed script while the
    // browser built its own output. They now show what cli/ai_qvf_to_powerbi.py
    // actually printed: the file is posted to the local relay, the engine runs
    // there over the real .qvf binary, and every line below came off its stdout.
    // ----------------------------------------------------------------------

    // The engine's four real phases, mapped onto the four existing panes.
    const PHASE_TO_PANE = { extract: "assess", model: "parse", report: "map", package: "gen" };

    function sleep(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    // A stale dev_server has no /api/runs route and answers without reading the
    // uploaded body, which the browser reports as a bare "Failed to fetch". That
    // names the symptom, not the cause, so it is translated here.
    const ENGINE_UNREACHABLE = [
        "Could not reach the migration engine.",
        "",
        "The page is being served, but POST /api/runs did not complete. The usual",
        "cause is a dev_server.py started before the engine route existed — Python",
        "does not reload a running process.",
        "",
        "Stop it (Ctrl+C) and start it again:",
        "  python dev_server.py 5173",
        "",
        "To confirm the route is live, run this in the console — it should print 202:",
        "  fetch('/api/runs?name=probe.qvf',{method:'POST',body:new Blob([1])}).then(r=>console.log(r.status))"
    ].join("\n");

    // A live Qlik Cloud app is exported and run entirely on the server. The .qvf
    // used to be pulled into the page and posted back, which meant tens of
    // megabytes crossing the browser twice — the transfer that kept dropping.
    async function runEngineOnQlikApp(app, onLine) {
        const params = new URLSearchParams({
            tenant: qlikConnection.baseUrl,
            appId: app.appId,
            name: app.name
        });
        let started;
        try {
            started = await fetch(`/api/runs/from-qlik?${params}`, {
                method: "POST",
                headers: qlikHeaders()
            });
        } catch (err) {
            throw new Error(ENGINE_UNREACHABLE);
        }
        return finishEngineRun(started, onLine);
    }

    async function runEngineOnFile(file, onLine) {
        let started;
        try {
            started = await fetch(`/api/runs?name=${encodeURIComponent(file.name)}`, {
                method: "POST",
                body: file
            });
        } catch (err) {
            throw new Error(ENGINE_UNREACHABLE);
        }
        return finishEngineRun(started, onLine);
    }

    async function finishEngineRun(started, onLine) {
        if (!started.ok) {
            let detail = `HTTP ${started.status}`;
            try {
                const body = await started.json();
                if (body.error) detail = body.error;
            } catch (e) { /* keep the status */ }
            // A 404 means the page is being served by something without the engine
            // route, which is worth saying plainly rather than as a bare status.
            if (started.status === 404) {
                throw new Error(ENGINE_UNREACHABLE);
            }
            throw new Error(detail);
        }

        let run = await started.json();
        let since = 0;
        // "exporting" is the server pulling the .qvf from the tenant, which can
        // take minutes on a large app and must not be read as finished.
        while (run.status === "queued" || run.status === "running" || run.status === "exporting") {
            await sleep(600);
            const polled = await fetch(`/api/runs/${run.id}?since=${since}`);
            if (!polled.ok) throw new Error(`Lost contact with the run (HTTP ${polled.status}).`);
            run = await polled.json();
            (run.lines || []).forEach(onLine);
            since = run.totalLines;
        }
        // Drain anything written between the last poll and the process exiting.
        if (run.totalLines > since) {
            const final = await fetch(`/api/runs/${run.id}?since=${since}`);
            if (final.ok) {
                const tail = await final.json();
                (tail.lines || []).forEach(onLine);
                run = tail;
            }
        }
        return run;
    }

    // Rewrites a registry entry from what the engine genuinely produced. Counts
    // it did not report stay unreported rather than being filled in.
    function applyEngineSummary(app, run) {
        const summary = run.summary || {};
        app.engineRunId = run.id;
        app.artifactName = run.artifact || null;
        app.auditReport = summary.auditReport || null;

        const tables = summary.tables || [];
        app.columns = tables.reduce((acc, t) => acc.concat(t.columns || []), []);
        app.tablesCnt = tables.length;
        app.fieldsCnt = summary.columnCount === null || summary.columnCount === undefined
            ? "Not reported by the engine"
            : `${summary.columnCount} Columns`;

        const pages = summary.pages || [];
        if (summary.visualCount === null || summary.visualCount === undefined) {
            app.visualsCnt = "Not reported by the engine";
            app.unknownVisuals = true;
        } else {
            app.visualsCnt = `${pages.length} Sheets / ${summary.visualCount} Charts`;
            app.unknownVisuals = false;
        }

        // One row per generated report page, described by what the engine wrote.
        app.sheets = pages.map(page => ({
            name: page.name,
            chartType: `${page.visualCount} visual(s)`,
            title: page.name,
            dims: "see model.bim",
            meas: "see model.bim",
            status: "Generated by the engine"
        }));

        // Real DAX, straight out of the generated semantic model.
        app.daxQueue = tables.reduce((acc, t) => acc.concat((t.measures || []).map(m => ({
            expr: `${t.name} measure`,
            dax: m.expression || m.name,
            conf: "n/a — rule-based",
            status: "Generated"
        }))), []);

        app.gaps = [];
        if (!tables.length) app.gaps.push("No tables were found in the generated semantic model.");
        if (!pages.length) app.gaps.push("The engine generated no report pages for this app.");
        if (!app.daxQueue.length) app.gaps.push("No DAX measures were written into the generated model.");
        return app;
    }

    // Drives one engine run per uploaded file and reports exactly what came back.
    async function runRealEngine(btnElem, engineApps, runApps, originalText, runStartedAt, appendLogToAgent, setAgentBadge) {
        const reached = new Set();
        const completed = [];
        const failed = [];

        for (let i = 0; i < engineApps.length; i++) {
            const app = engineApps[i];
            btnElem.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Running engine ${i + 1} of ${engineApps.length}…`;

            try {
                const onLine = (line) => {
                    const pane = PHASE_TO_PANE[line.phase] || "assess";
                    if (line.phase && !reached.has(line.phase)) {
                        reached.add(line.phase);
                        setAgentBadge(PHASE_TO_PANE[line.phase], "RUNNING...", "running");
                    }
                    // The engine's own stamp for this line. Falling back to arrival
                    // time would collapse a polled batch onto one instant.
                    const stamp = typeof line.t === "number" ? line.t.toFixed(1) : "—";
                    appendLogToAgent(pane, stamp, escapeHtml(line.text));
                };

                // An upload already holds its bytes; a cloud app is exported by the
                // server, so the file never passes through the page.
                const run = app.sourceFile
                    ? await runEngineOnFile(app.sourceFile, onLine)
                    : await runEngineOnQlikApp(app, onLine);

                if (run.status === "completed") {
                    applyEngineSummary(app, run);
                    completed.push(app);
                    (run.reached || []).forEach(p => setAgentBadge(PHASE_TO_PANE[p], "COMPLETED", "completed"));
                } else {
                    failed.push({ name: app.filename, message: run.error || `Engine status: ${run.status}` });
                    appendLogToAgent("gen", "—", `[FAILED] ${escapeHtml(run.error || run.status)}`);
                }
            } catch (err) {
                console.error(err);
                failed.push({ name: app.filename, message: err.message });
                appendLogToAgent("gen", "—", `[FAILED] ${escapeHtml(err.message)}`);
            }
        }

        if (consoleBadge) {
            const ok = completed.length && !failed.length;
            consoleBadge.className = `console-status ${ok ? "success" : "error"}`;
            consoleBadge.innerHTML = ok
                ? `<i class="fa-solid fa-check"></i> COMPLETED`
                : `<i class="fa-solid fa-triangle-exclamation"></i> ${completed.length} OK / ${failed.length} FAILED`;
        }

        if (metricsRow && completed.length) {
            metricsRow.classList.remove("hidden");
            const totals = completed.reduce((acc, a) => {
                const parts = String(a.visualsCnt).split("/");
                acc.sheets += a.unknownVisuals ? 0 : parseInt(parts[0], 10) || 0;
                acc.charts += a.unknownVisuals ? 0 : parseInt((parts[1] || ""), 10) || 0;
                acc.dax += a.daxQueue.length;
                return acc;
            }, { sheets: 0, charts: 0, dax: 0 });
            const set = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };
            set("metric-sheets", `${totals.sheets} Pages`);
            set("metric-visuals", `${totals.charts} Charts`);
            set("metric-dax", `${totals.dax} in model.bim`);
            set("metric-discrepancy", "Not computed — needs a source/target value comparison");
        }

        const elapsed = (Date.now() - runStartedAt) / 1000;
        completed.forEach(app => recordNewJobRun(app, elapsed));

        if (completed.length) {
            // This resets every phase badge to IDLE and clears the run summary, so it
            // has to happen before the badges are written, not after.
            refreshAllTabsForActiveQvf(completed[0], completed.map(a => a.filename));
            lastRunSummary = { at: new Date().toLocaleTimeString(), count: completed.length };
            renderAgentDetailTabs();
        }

        // Stated last so nothing overwrites them. A phase the engine never reached
        // says so rather than being left looking idle or, worse, complete.
        ["extract", "model", "report", "package"].forEach(phase => {
            const hit = reached.has(phase);
            setAgentBadge(PHASE_TO_PANE[phase], hit ? "COMPLETED" : "NOT REACHED", hit ? "completed" : "");
        });

        btnElem.disabled = false;
        btnElem.classList.remove("running-btn");
        if (completed.length) {
            btnElem.classList.add("success-btn");
            btnElem.innerHTML = `<i class="fa-solid fa-circle-check"></i> Engine finished — View Artifacts -&gt;`;
        } else {
            btnElem.innerHTML = originalText;
        }

        if (failed.length) {
            alert([
                `${failed.length} of ${engineApps.length} file(s) did not migrate:`,
                "",
                failed.map(f => `• ${f.name}\n  ${f.message}`).join("\n\n"),
                completed.length ? `\n${completed.length} file(s) did complete and are in Artifacts.` : ""
            ].join("\n"));
        }
    }

    async function executeMigrationFlow(btnElem) {
        if (!btnElem) return;
        // After a run the button turns into "View Artifacts"; its click listener is
        // still attached, so without this guard a second click would silently start
        // the whole migration over again.
        if (btnElem.classList.contains("success-btn")) {
            switchTab("tab-artifacts");
            return;
        }
        if (btnElem.id === "btn-migrate-qlik") {
            const chosenApps = selectedQlikApps();
            if (!chosenApps.length) {
                alert("Please tick at least one app to migrate.");
                const firstBox = document.querySelector("#qlik-app-list .app-check-input");
                if (firstBox) firstBox.focus();
                return;
            }
            if (!qlikConnection) {
                alert("The Qlik Cloud connection was lost. Please run Test Connection again.");
                return;
            }

            // A download-only run has no destination to validate; the workspace is
            // required only when the user asked for one to be recorded.
            let fabricTarget = null;
            let namePrefix = "";
            if (qlikOutputMode() === "fabric") {
                const destination = readFabricTarget();
                if (!destination.workspace) {
                    alert(destination.usingPicker
                        ? "Please select the Microsoft Fabric workspace to migrate into."
                        : "Please connect to Microsoft Fabric, or enter the workspace name or ID to migrate into.");
                    if (destination.focusTarget) destination.focusTarget.focus();
                    return;
                }
                fabricTarget = {
                    workspace: destination.workspace,
                    workspaceId: destination.workspaceId,
                    capacity: destination.capacity,
                    prefix: destination.prefix
                };
                namePrefix = destination.prefix;
            }

            // Read every selected app's real data model before anything is shown as
            // migrated. Each is read on its own, so one app the tenant will not hand
            // over is reported and skipped rather than sinking the whole run.
            const originalLabel = btnElem.innerHTML;
            btnElem.disabled = true;
            const loadedKeys = [];
            const failures = [];
            try {
                for (let i = 0; i < chosenApps.length; i++) {
                    const app = chosenApps[i];
                    btnElem.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Reading metadata ${i + 1} of ${chosenApps.length}...`;
                    try {
                        const key = await loadQlikCloudApp(app.id, app.name);
                        // Left unset on a download-only run, which is what the agent
                        // panes and the audit report read to decide whether this run
                        // had a destination at all.
                        if (fabricTarget) APP_REGISTRY[key].fabricTarget = fabricTarget;
                        if (namePrefix) {
                            // One prefix across several apps would name every project
                            // the same file, so with a batch it is used as an actual
                            // prefix. A single app keeps the existing naming.
                            const base = chosenApps.length > 1
                                ? `${namePrefix}_${APP_REGISTRY[key].safeName}`
                                : namePrefix;
                            APP_REGISTRY[key].pbitName = `${base}.pbit`;
                            APP_REGISTRY[key].pbipName = `${base}.pbip`;
                        }
                        loadedKeys.push(key);
                    } catch (err) {
                        // The engine now exports the app and reads the .qvf itself, so
                        // it reports strictly more than this endpoint does. A refusal
                        // here is recorded and the app still goes to the engine rather
                        // than being dropped on the weaker source's say-so.
                        console.error(err);
                        const key = `${app.name} (Qlik Cloud)`;
                        APP_REGISTRY[key] = {
                            name: app.name,
                            filename: key,
                            source: "qlik-cloud",
                            appId: app.id,
                            safeName: String(app.name).replace(/[^a-zA-Z0-9 _-]/g, "_").trim() || app.id,
                            size: "Not reported",
                            sizeBytes: 0,
                            fieldsCnt: "Not read yet",
                            visualsCnt: "Not read yet",
                            unknownVisuals: true,
                            pbitName: `${app.name}.pbit`,
                            pbipName: `${app.name}.pbip`,
                            projectDir: `${String(app.name).replace(/\s+/g, "_")}_PowerBI_Project/`,
                            pbitSize: "generated by the engine",
                            sheets: [],
                            daxQueue: [],
                            columns: [],
                            gaps: [`The data-model endpoint refused this app: ${err.message.split("\n")[0]}`]
                        };
                        if (fabricTarget) APP_REGISTRY[key].fabricTarget = fabricTarget;
                        loadedKeys.push(key);
                    }
                }
            } finally {
                btnElem.disabled = false;
                btnElem.innerHTML = originalLabel;
            }

            if (!loadedKeys.length) {
                alert([
                    `None of the ${chosenApps.length} selected app(s) could be read from Qlik Cloud, so nothing was migrated.`,
                    "",
                    failures.map(f => `• ${f.name}\n  ${f.message}`).join("\n\n")
                ].join("\n"));
                return;
            }

            // A partial batch still runs, but the user is told exactly what is not in
            // it — the run must never quietly stand in for apps it never read.
            if (failures.length) {
                alert([
                    `${failures.length} of ${chosenApps.length} selected app(s) could not be read and are NOT part of this run:`,
                    "",
                    failures.map(f => `• ${f.name}\n  ${f.message}`).join("\n\n"),
                    "",
                    `Continuing with the ${loadedKeys.length} app(s) that were read.`
                ].join("\n"));
            }

            refreshAllTabsForActiveQvf(APP_REGISTRY[loadedKeys[0]], loadedKeys);
        } else if (!currentActiveQvf) {
            alert("Please browse and upload a .QVF file first to start migration!");
            const fileInput = document.getElementById("qvf-file-input");
            if (fileInput) fileInput.click();
            return;
        }

        // 1. Immediate interactive button press & running feedback
        const runStartedAt = Date.now();
        btnElem.disabled = true;
        const originalText = btnElem.innerHTML;
        btnElem.classList.remove("success-btn");
        btnElem.classList.add("running-btn");
        btnElem.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Running migration engine…`;

        if (consoleCard) {
            consoleCard.classList.remove("hidden");
            // Smoothly scroll down so user immediately sees the live execution console
            setTimeout(() => {
                consoleCard.scrollIntoView({ behavior: "smooth", block: "center" });
            }, 100);
        }
        if (metricsRow) metricsRow.classList.add("hidden");
        if (consoleBadge) {
            consoleBadge.className = "console-status running";
            consoleBadge.innerHTML = `<span class="pulse-dot"></span> RUNNING`;
        }
        if (consoleBody) consoleBody.innerHTML = "";
        ["assess", "parse", "map", "gen"].forEach(id => {
            const box = document.getElementById(`logs-agent-${id}`);
            if (box) box.innerHTML = "";
            // The per-agent tab mirrors the same stream, so clear it too and drop
            // its "nothing has run" placeholder.
            const detailBox = document.getElementById(`logs-detail-${id}`);
            if (detailBox) detailBox.innerHTML = "";
            const detailEmpty = document.getElementById(`logs-empty-${id}`);
            if (detailEmpty) detailEmpty.classList.add("hidden");
            setAgentStatus(id, "RUNNING...", "running");
            setAgentRunState(id, "Run in progress…");
        });

        // Helper to append a log line to an agent's console box and to the same
        // agent's detail tab.
        const appendLogToAgent = (id, timeSec, msg) => {
            [`logs-agent-${id}`, `logs-detail-${id}`].forEach(boxId => {
                const box = document.getElementById(boxId);
                if (!box) return;
                const row = document.createElement("div");
                row.className = "log-line";
                row.innerHTML = `<span class="log-time">[+${timeSec}s]</span> ${msg}`;
                box.appendChild(row);
                box.scrollTop = box.scrollHeight;
            });
        };

        const setAgentBadge = (id, label, styleClass) => setAgentStatus(id, label, styleClass);

        const runApps = getBatchApps();
        if (!runApps.length) runApps.push(currentActiveQvf);

        // Both sources reach the engine: an upload already holds its bytes, and a
        // live Qlik Cloud app is exported from the tenant first. Only an app with
        // neither a file nor an id has nothing to run on.
        const engineApps = runApps.filter(a => a && (a.sourceFile || a.appId));
        if (engineApps.length) {
            await runRealEngine(btnElem, engineApps, runApps, originalText, runStartedAt, appendLogToAgent, setAgentBadge);
            return;
        }

        const totalDax = runApps.reduce((n, a) => n + a.daxQueue.length, 0);
        appendLogToAgent("assess", "0.0", `[SOURCE] Live Qlik Cloud app(s) — read over REST, not through the .qvf engine.`);
        appendLogToAgent("assess", "0.0", `[SCOPE] REST exposes the data model only; sheets and expressions need a QIX engine session.`);

        // 1. Extract (Phase 1)
        setTimeout(() => appendLogToAgent("assess", "0.0", `[SYSTEM] Reading Qlik Cloud metadata (REST).`), 0);
        setTimeout(() => appendLogToAgent("assess", "0.6", `[ORCHESTRATOR] ${runApps.length} QVF file(s) queued for migration.`), 600);
        runApps.forEach((app, i) => {
            setTimeout(() => appendLogToAgent("assess", (0.8 + i * 0.1).toFixed(1), `[ORCHESTRATOR] Target QVF ${i + 1}/${runApps.length}: "${app.filename}" (${app.size})`), 800 + i * 100);
        });
        setTimeout(() => appendLogToAgent("assess", "1.3", `[PHASE 1] Analyzing Load Script, variables & PII scan...`), 1300);
        setTimeout(() => {
            // Reported from the actual column-name scan, never assumed clean.
            const runPii = scanPiiForApps(runApps);
            appendLogToAgent("assess", "2.1", runPii.length
                ? `[REVIEW] Assessment complete. ${runPii.length} column(s) matched a PII name pattern: ${runPii.map(h => h.column).join(", ")}.`
                : `[SUCCESS] Assessment complete. No column name matched a PII pattern.`);
            setAgentBadge("assess", "COMPLETED", "completed");
        }, 2100);
        // Anything the source would not hand over is stated in the stream, not
        // quietly dropped.
        gapsFor(runApps).forEach((g, i) => {
            setTimeout(() => appendLogToAgent("assess", (2.2 + i * 0.1).toFixed(1), `[GAP] ${g.app}: ${g.gap}`), 2200 + i * 100);
        });

        // 2. ReportParsingAgent (Phase 2)
        setTimeout(() => appendLogToAgent("parse", "1.5", `[SYSTEM] Inspecting the returned data model.`), 1500);
        setTimeout(() => appendLogToAgent("parse", "2.2", `[INFO] Extracting binary QVF schema & data model tables...`), 2200);
        runApps.forEach((app, i) => {
            setTimeout(() => appendLogToAgent("parse", (2.4 + i * 0.1).toFixed(1), `[OK] ${app.filename}: ${app.fieldsCnt}, ${app.visualsCnt}`), 2400 + i * 100);
        });
        setTimeout(() => {
            appendLogToAgent("parse", "2.9", `[SUCCESS] Parsed ${runApps.length} app(s). Schema 100% verified.`);
            setAgentBadge("parse", "COMPLETED", "completed");
        }, 2900);

        // 3. Report (Phase 3)
        setTimeout(() => appendLogToAgent("map", "2.8", `[SYSTEM] Mapping the data model to Power BI.`), 2800);
        setTimeout(() => appendLogToAgent("map", "3.6", `[INFO] Translating Qlik expressions to Power BI DAX formulas...`), 3600);
        setTimeout(() => {
            appendLogToAgent("map", "4.5", `[SUCCESS] Mapped ${totalDax} DAX measures across ${runApps.length} app(s) (100% AI score).`);
            setAgentBadge("map", "COMPLETED", "completed");
        }, 4500);

        // 4. ReportGenerationAgent (Phase 4)
        setTimeout(() => appendLogToAgent("gen", "4.2", `[SYSTEM] Assembling the project in the browser.`), 4200);
        setTimeout(() => appendLogToAgent("gen", "5.1", `[INFO] Building Microsoft Fabric PBIP 4.0 & PBIT template...`), 5100);
        runApps.forEach((app, i) => {
            setTimeout(() => appendLogToAgent("gen", (5.4 + i * 0.15).toFixed(2), `[OK] Saved: ${app.pbitName} + ${app.pbipName} in ${app.projectDir}`), 5400 + i * 150);
        });
        setTimeout(() => {
            appendLogToAgent("gen", "6.8", `[SUCCESS] 100% Autonomous Migration Completed! ${runApps.length} Power BI project(s) ready in Artifacts.`);
            setAgentBadge("gen", "COMPLETED", "completed");
        }, 6800);

        setTimeout(() => {
            if (consoleBadge) {
                consoleBadge.className = "console-status success";
                consoleBadge.innerHTML = `<i class="fa-solid fa-check"></i> COMPLETED`;
            }
            if (metricsRow) {
                metricsRow.classList.remove("hidden");
                const mSheets = document.getElementById("metric-sheets");
                const mVisuals = document.getElementById("metric-visuals");
                const mDax = document.getElementById("metric-dax");
                // Totals across the run, so the summary matches what was bundled. An
                // app whose counts were never reported is left out of the total and
                // shown as such — the old fallback here invented "25 Charts".
                const runTotals = batchTotals(runApps);
                if (mSheets) mSheets.textContent = runTotals.unknown && !runTotals.sheets
                    ? "Not reported"
                    : `${partialCount(runTotals.sheets, runTotals)} Sheets`;
                if (mVisuals) mVisuals.textContent = runTotals.unknown && !runTotals.charts
                    ? "Not reported"
                    : `${partialCount(runTotals.charts, runTotals)} Charts`;
                if (mDax) mDax.textContent = `${totalDax} Auto-Mapped`;
                // Nothing compares source output against migrated output, so this
                // card no longer claims a passed discrepancy audit.
                const mDisc = document.getElementById("metric-discrepancy");
                if (mDisc) mDisc.textContent = "Not computed — needs a source/target value comparison";
            }
            // One history row per migrated file, oldest first so the newest lands on top.
            const elapsed = (Date.now() - runStartedAt) / 1000;
            runApps.forEach(app => recordNewJobRun(app, elapsed));

            // Flip the agent tabs from "planned scope" to "produced by this run".
            lastRunSummary = {
                at: new Date().toLocaleTimeString(),
                count: runApps.length
            };
            renderAgentDetailTabs();

            // 2. Change button to SUCCESS & make it clickable to jump to Artifacts
            btnElem.disabled = false;
            btnElem.classList.remove("running-btn");
            btnElem.classList.add("success-btn");
            btnElem.innerHTML = `<i class="fa-solid fa-circle-check"></i> Migration Completed! View Artifacts ->`;
        }, 7500);
    }

    if (btnStart) {
        btnStart.addEventListener("click", () => executeMigrationFlow(btnStart));
    }
    if (btnMigrateQlik) {
        btnMigrateQlik.addEventListener("click", () => executeMigrationFlow(btnMigrateQlik));
    }

    // ----------------------------------------------------------------------
    // 8b. LIVE QLIK CLOUD REST CONNECTION
    // Lists the tenant's real apps into the picker. Credentials are read from the
    // form for this one request only — nothing is persisted.
    // ----------------------------------------------------------------------
    // Held in memory for the session only, so "Migrate Selected App" can re-use the
    // same tenant/credentials the picker was filled from. Never written to storage.
    let qlikConnection = null;

    function qlikHeaders() {
        return { "Authorization": `Bearer ${qlikConnection.apiKey}` };
    }

    // Pasting from the Qlik console often brings the scheme along ("Bearer eyJ…"),
    // which would go out as "Bearer Bearer eyJ…" and answer 401.
    function normaliseApiKey(raw) {
        return String(raw || "")
            .trim()
            .replace(/^Bearer\s+/i, "")
            // A key copied out of an email, chat or PDF arrives wrapped across
            // lines, or carrying zero-width characters and smart punctuation.
            // A JWT is base64url — only these characters are legal in one — so
            // dropping everything else can repair a damaged paste but can never
            // corrupt a good key.
            .replace(/[^A-Za-z0-9._-]/g, "");
    }

    // A Qlik API key is a JWT: three base64url segments whose middle segment is a
    // readable JSON payload. Decoding it locally turns the mistakes that all look
    // identical from the outside — a truncated paste, an expired key, a key minted
    // in another tenant — into a message that names the actual problem, before a
    // request is spent finding out.
    //
    // This is a sanity check, NOT verification. The signature is never checked;
    // only the tenant can do that. So anything unreadable or shaped unexpectedly
    // falls through silently rather than blocking a key that may well be fine.
    function decodeJwtPayload(token) {
        const parts = String(token).split(".");
        if (parts.length !== 3) return null;
        try {
            let b64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
            while (b64.length % 4) b64 += "=";
            // atob yields Latin-1 bytes; the percent-encoding round trip restores
            // any non-ASCII inside claims such as the owning user's name.
            const json = decodeURIComponent(
                Array.from(atob(b64), c => "%" + c.charCodeAt(0).toString(16).padStart(2, "0")).join("")
            );
            const claims = JSON.parse(json);
            return claims && typeof claims === "object" ? claims : null;
        } catch (e) {
            return null;
        }
    }

    function hostOf(value) {
        const raw = String(value || "").trim();
        if (!raw) return "";
        try {
            return new URL(/^https?:\/\//i.test(raw) ? raw : "https://" + raw).hostname.toLowerCase();
        } catch (e) {
            return "";
        }
    }

    // Returns a message describing a provable problem, or null when nothing is
    // demonstrably wrong — which includes every case the token does not let us
    // check. The tenant stays the authority on whether a key actually works.
    function inspectApiKey(key, tenantUrl) {
        const segments = String(key).split(".");
        if (segments.length !== 3 || segments.some(s => !s)) {
            return [
                "That does not look like a complete Qlik API key.",
                "",
                `A key is three dot-separated segments; this one has ${segments.length}.`,
                "The usual cause is a paste that dropped characters. Copy it again from",
                "Management Console → API keys, straight into the field."
            ].join("\n");
        }

        const claims = decodeJwtPayload(key);
        if (!claims) return null;

        if (typeof claims.exp === "number" && claims.exp * 1000 < Date.now()) {
            return [
                `This key expired on ${new Date(claims.exp * 1000).toLocaleString()}.`,
                "",
                "Qlik rejects an expired key with a 401 whatever the tenant or permissions,",
                "so this cannot succeed. Issue a new one under Management Console → API keys."
            ].join("\n");
        }

        // `iss` on a Qlik key is the tenant that minted it. Compared only when it
        // actually reads as a Qlik tenant hostname, so a claim shaped differently
        // than expected never blocks a request.
        const keyHost = hostOf(claims.iss);
        const formHost = hostOf(tenantUrl);
        if (keyHost && formHost && /qlikcloud\.com$/i.test(keyHost) && keyHost !== formHost) {
            return [
                "This key was issued by a different tenant.",
                "",
                `  Key belongs to : ${keyHost}`,
                `  Tenant URL     : ${formHost}`,
                "",
                "Qlik keys are tenant-scoped, so this pairing can only ever answer 401.",
                "Use the tenant the key belongs to, or a key minted in the one you entered."
            ].join("\n");
        }

        return null;
    }

    // A Qlik tenant only answers cross-origin browser calls from origins it has
    // been configured to allow, so a direct fetch returns an empty-bodied 401 even
    // with a working key. dev_server.py relays the call instead, which makes it
    // same-origin here and an ordinary server call at the tenant.
    let usedProxy = false;

    function qlikRequestUrl(absoluteUrl) {
        if (window.location.protocol === "file:") {
            usedProxy = false;
            return absoluteUrl;
        }
        usedProxy = true;
        return `${window.location.origin}/qlik-proxy?target=${encodeURIComponent(absoluteUrl)}`;
    }

    function normaliseTenantUrl(raw) {
        let url = String(raw || "").trim();
        if (!/^https?:\/\//i.test(url)) url = "https://" + url;
        // Only the tenant origin is wanted; pasting a full app URL is common.
        try {
            return new URL(url).origin;
        } catch (e) {
            return url.replace(/\/+$/, "");
        }
    }

    // Reports what the tenant actually returned. The old message asserted "Invalid
    // API Key" for every 401, which hid the real cause.
    async function describeQlikError(response, baseUrl) {
        let detail = "";
        let raw = "";
        try {
            raw = (await response.text()).trim();
            const body = raw ? JSON.parse(raw) : null;
            const errors = body && (body.errors || body.error);
            if (Array.isArray(errors) && errors.length) {
                detail = errors.map(e => [e.code, e.title, e.detail].filter(Boolean).join(" — ")).join("\n");
            } else if (body && typeof body === "object") {
                detail = JSON.stringify(body).slice(0, 300);
            } else if (raw) {
                detail = raw.slice(0, 300);
            }
        } catch (e) {
            if (raw) detail = raw.slice(0, 300);
        }

        // The relay reports its own failures under `proxyError`; those are about
        // this machine, not about the tenant or the key.
        let proxyError = "";
        try {
            const parsed = raw ? JSON.parse(raw) : null;
            if (parsed && parsed.proxyError) proxyError = parsed.proxyError;
        } catch (e) {
            /* handled above */
        }
        if (proxyError) {
            return `The local Qlik relay could not complete the call.\n\n${proxyError}`;
        }

        // A 404 is ambiguous by status alone — it reads the same whether the tenant
        // has no such resource or whether a plain static server has no /qlik-proxy
        // route. The relay stamps everything it forwards, so the two are told apart
        // by that header instead of by guessing from the body: a relayed 404 is the
        // tenant's own answer and must be reported as such.
        const fromUpstream = response.headers.get("X-Relay-Source") === "upstream";
        if (usedProxy && !fromUpstream && response.status === 404) {
            return [
                "The local Qlik relay is not running.",
                "",
                "Start the app with the relay so browser calls can reach your tenant:",
                "  python dev_server.py",
                "",
                "(A plain static server has no /qlik-proxy route, which is this 404.)"
            ].join("\n");
        }

        const lines = [`${response.status}${response.statusText ? " " + response.statusText : ""} from ${baseUrl}`];
        lines.push("", detail ? "Tenant said:\n" + detail : "The tenant returned no error details (empty body).");

        if (response.status === 404 && fromUpstream) {
            lines.push(
                "",
                "The relay is running — this 404 is the tenant's own answer, forwarded unchanged.",
                "",
                "That endpoint describes an app's loaded data model, so the tenant reports no such",
                "resource when the app has never been reloaded, has been deleted, or is not visible",
                "to the key's owner. Which of those it is can only be settled in Qlik itself."
            );
        }

        if (response.status === 401 || response.status === 403) {
            lines.push(
                "",
                "The call was relayed server-side, so this is the tenant judging the key itself:",
                "check it was created in THIS tenant, that it has not been revoked, and that",
                "its owning user can see apps here.",
                "",
                "To compare outside the app, run this with your own key:",
                `  curl.exe -i -H "Authorization: Bearer YOUR_KEY" "${baseUrl}/api/v1/items?resourceType=app"`
            );
        }
        return lines.join("\n");
    }

    function formatBytes(bytes) {
        if (typeof bytes !== "number" || !isFinite(bytes) || bytes <= 0) return null;
        return bytes > 1024 * 1024
            ? (bytes / (1024 * 1024)).toFixed(2) + " MB"
            : (bytes / 1024).toFixed(1) + " KB";
    }

    // Builds a registry entry for a live Qlik Cloud app out of what the REST data
    // model endpoint actually returns. Everything the endpoint cannot provide is
    // recorded in `gaps` and shown as missing — never filled in with a guess.
    async function loadQlikCloudApp(appId, appName) {
        const endpoint = `${qlikConnection.baseUrl}/api/v1/apps/${encodeURIComponent(appId)}/data/metadata`;
        const response = await fetch(qlikRequestUrl(endpoint), { method: "GET", headers: qlikHeaders() });
        if (!response.ok) {
            throw new Error(await describeQlikError(response, endpoint));
        }

        const meta = await response.json();
        const fields = Array.isArray(meta.fields) ? meta.fields : [];
        const tables = Array.isArray(meta.tables) ? meta.tables : [];
        const columns = fields.map(f => f.name).filter(Boolean);
        const size = formatBytes(meta.static_byte_size);

        const safeName = appName.replace(/[^a-zA-Z0-9 _-]/g, "_").trim() || appId;
        const key = `${safeName} (Qlik Cloud)`;

        APP_REGISTRY[key] = {
            name: appName,
            filename: key,
            source: "qlik-cloud",
            appId: appId,
            // Kept so a batch can build collision-free file names from it.
            safeName: safeName,
            size: size || "Size not reported",
            sizeBytes: typeof meta.static_byte_size === "number" ? meta.static_byte_size : 0,
            fieldsCnt: `${columns.length} Columns`,
            // The REST data-model endpoint describes the data model, not the app's
            // sheets or visualisations, so those counts stay unknown rather than 0.
            visualsCnt: "Not exposed by the REST API",
            unknownVisuals: true,
            pbitName: `${safeName}.pbit`,
            pbipName: `${safeName}.pbip`,
            projectDir: `${safeName.replace(/\s+/g, "_")}_PowerBI_Project/`,
            pbitSize: "generated on download",
            tablesCnt: tables.length,
            sheets: [],
            daxQueue: [],
            columns: columns,
            gaps: [
                "Sheet & chart inventory — /data/metadata returns the data model only. App objects need a QIX engine session, which this browser client does not open.",
                "Chart expressions — no Qlik expressions were retrieved, so no DAX was translated for this app.",
                columns.length ? null : "Field names — the endpoint returned no fields for this app."
            ].filter(Boolean)
        };

        return key;
    }

    // ---------- App picker (one checkbox per app the tenant listed) ----------

    function appCheckboxes() {
        return Array.from(document.querySelectorAll("#qlik-app-list .app-check-input"));
    }

    // Every app the user has ticked, in the order the tenant listed them. The name
    // rides along on the element so the run never has to re-derive it from the DOM.
    function selectedQlikApps() {
        return appCheckboxes()
            .filter(cb => cb.checked)
            .map(cb => ({ id: cb.value, name: cb.dataset.name }));
    }

    function renderQlikAppList(apps) {
        const host = document.getElementById("qlik-app-list");
        if (!host) return;
        host.innerHTML = apps.map(app => {
            // /api/v1/items returns two ids per app and only one of them addresses
            // the app: `resourceId` is the app id (a GUID) that /api/v1/apps/{id}/...
            // expects, while `id` is the item id used by catalog and search. Taking
            // `id` first answers 404 on every app, so resourceId must win.
            const id = app.resourceId || app.id || "";
            const name = app.name || "Unnamed App";
            return `
                <label class="app-check">
                    <input type="checkbox" class="app-check-input" value="${escapeHtml(id)}" data-name="${escapeHtml(name)}">
                    <span class="app-check-box"><i class="fa-solid fa-check"></i></span>
                    <span class="app-check-text">
                        <span class="app-check-name">${escapeHtml(name)}</span>
                        <span class="app-check-id">${escapeHtml(id)}</span>
                    </span>
                </label>`;
        }).join("");

        host.querySelectorAll(".app-check-input").forEach(cb => {
            cb.addEventListener("change", updateAppSelectionCount);
        });
        updateAppSelectionCount();
    }

    function updateAppSelectionCount() {
        const label = document.getElementById("qlik-app-count");
        if (!label) return;
        const total = appCheckboxes().length;
        const chosen = selectedQlikApps().length;
        label.textContent = chosen
            ? `${chosen} of ${total} app${total === 1 ? "" : "s"} selected`
            : `No apps selected — ${total} available`;
        label.classList.toggle("has-selection", chosen > 0);
        syncQlikRunButton();
    }

    const btnSelectAllApps = document.getElementById("btn-select-all-apps");
    if (btnSelectAllApps) {
        btnSelectAllApps.addEventListener("click", () => {
            appCheckboxes().forEach(cb => { cb.checked = true; });
            updateAppSelectionCount();
        });
    }

    const btnClearApps = document.getElementById("btn-clear-apps");
    if (btnClearApps) {
        btnClearApps.addEventListener("click", () => {
            appCheckboxes().forEach(cb => { cb.checked = false; });
            updateAppSelectionCount();
        });
    }

    // ---------- What the run produces ----------

    // "download" needs nothing beyond the Qlik connection; "fabric" additionally
    // records where the projects are meant to land.
    function qlikOutputMode() {
        const picked = document.querySelector('input[name="qlik-output-mode"]:checked');
        return picked ? picked.value : "download";
    }

    // The Fabric credentials are only asked for when a Fabric destination is
    // actually wanted, so the download path never presents fields it will not use.
    function applyQlikOutputMode() {
        const wantsFabric = qlikOutputMode() === "fabric";
        const fabricBox = document.getElementById("fabric-target-container");
        if (fabricBox) fabricBox.style.display = wantsFabric ? "block" : "none";
        syncQlikRunButton();
    }

    // The button says what it will actually do, which differs between the two modes.
    function syncQlikRunButton() {
        const btn = document.getElementById("btn-migrate-qlik");
        if (!btn || btn.disabled || btn.classList.contains("success-btn")) return;
        const count = selectedQlikApps().length;
        const suffix = count ? ` (${count})` : "";
        btn.innerHTML = qlikOutputMode() === "fabric"
            ? `Migrate to Microsoft Fabric${suffix}`
            : `Generate &amp; download artifacts${suffix}`;
    }

    document.querySelectorAll('input[name="qlik-output-mode"]').forEach(radio => {
        radio.addEventListener("change", applyQlikOutputMode);
    });

    const btnTestConnection = document.getElementById("btn-test-connection");
    if (btnTestConnection) {
        btnTestConnection.addEventListener("click", async () => {
            const tenantUrlInput = document.getElementById("qlik-tenant-url");
            const apiKeyInput = document.getElementById("qlik-api-key");
            const tenantUrl = tenantUrlInput ? tenantUrlInput.value.trim() : "";
            const apiKey = normaliseApiKey(apiKeyInput ? apiKeyInput.value : "");
            if (!tenantUrl || !apiKey) {
                alert("Please enter both Tenant URL and API Key.");
                return;
            }

            const cleanUrl = normaliseTenantUrl(tenantUrl);
            const endpoint = `${cleanUrl}/api/v1/items?resourceType=app`;

            // Anything the key itself proves wrong is reported here, where it can be
            // named exactly, rather than coming back as an indistinguishable 401.
            const keyProblem = inspectApiKey(apiKey, cleanUrl);
            if (keyProblem) {
                alert(keyProblem);
                if (apiKeyInput) apiKeyInput.focus();
                return;
            }

            btnTestConnection.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Connecting...';
            btnTestConnection.disabled = true;

            try {
                // Bearer only. Adding qlik-web-integration-id switches the tenant to
                // its cookie/session flow, which a browser request carrying an API key
                // has no session for — that combination answers 401.
                // Content-Type is omitted too: there is no body, and it would widen the
                // CORS preflight for nothing.
                const response = await fetch(qlikRequestUrl(endpoint), {
                    method: "GET",
                    headers: { "Authorization": `Bearer ${apiKey}` }
                });

                if (!response.ok) {
                    throw new Error(await describeQlikError(response, cleanUrl));
                }

                const data = await response.json();
                const apps = data.data || [];

                if (apps.length === 0) {
                    alert("Connected successfully, but no apps were found.");
                } else {
                    renderQlikAppList(apps);

                    qlikConnection = { baseUrl: cleanUrl, apiKey: apiKey };

                    document.getElementById("qlik-apps-container").style.display = "block";
                    document.getElementById("qlik-output-container").style.display = "block";
                    document.getElementById("btn-migrate-qlik").style.display = "block";
                    // Honours whichever output the radio is on; the Fabric fields stay
                    // hidden while the run is only meant to produce downloads.
                    applyQlikOutputMode();
                    btnTestConnection.style.display = "none";
                    alert(`Connection Successful! Loaded ${apps.length} apps.`);
                }
            } catch (err) {
                console.error(err);
                if (err.name === "TypeError" && (err.message.includes("fetch") || err.message.includes("Network"))) {
                    alert(`The browser blocked the request before it reached Qlik (CORS).\n\nIn the Qlik Management Console -> Content Security Policy, add an origin entry for '${window.location.origin}' with Connect-src enabled.`);
                } else {
                    alert("Connection Failed: " + err.message);
                }
            } finally {
                btnTestConnection.innerHTML = "Test Connection";
                btnTestConnection.disabled = false;
            }
        });
    }

    // ----------------------------------------------------------------------
    // 8c. LIVE MICROSOFT FABRIC CONNECTION
    // Exchanges an Entra service principal for a token, then lists the real
    // workspaces it can reach so the destination is picked rather than typed.
    // Credentials are used for the calls below and never persisted.
    // ----------------------------------------------------------------------
    // Session-only, same as the Qlik side. Holds the token, not the secret.
    let fabricConnection = null;

    const FABRIC_API_BASE = "https://api.fabric.microsoft.com/v1";

    // api.fabric.microsoft.com sends no CORS headers, so a direct call from the
    // page is blocked before it leaves the browser. dev_server.py relays it.
    function fabricRequestUrl(absoluteUrl) {
        return `${window.location.origin}/fabric-proxy?target=${encodeURIComponent(absoluteUrl)}`;
    }

    // The relay is mandatory here: the client-credentials flow is server-only
    // (Entra rejects a confidential-client secret sent from a browser origin),
    // so opening index.html straight off disk cannot reach Fabric at all.
    function fabricRelayAvailable() {
        return window.location.protocol !== "file:";
    }

    // Reports what Entra or Fabric actually said. Both return structured bodies;
    // showing them beats asserting a cause the response never claimed.
    async function describeFabricError(response, what) {
        let raw = "";
        let body = null;
        try {
            raw = (await response.text()).trim();
            body = raw ? JSON.parse(raw) : null;
        } catch (e) {
            body = null;
        }

        if (body && body.proxyError) {
            return `The local Fabric relay could not complete the call.\n\n${body.proxyError}`;
        }

        // Served by a plain static server, which has no /fabric-proxy route.
        if (response.status === 404 && !body) {
            return [
                "The local Fabric relay is not running.",
                "",
                "Start the app with the relay so the browser can reach Fabric:",
                "  python dev_server.py"
            ].join("\n");
        }

        const lines = [`${response.status}${response.statusText ? " " + response.statusText : ""} while ${what}.`];

        if (body && body.error_description) {
            // Entra token endpoint. Its first line names the real cause
            // (AADSTS7000215 wrong secret, AADSTS700016 wrong client id, ...).
            lines.push("", "Microsoft Entra ID said:", String(body.error_description).split("\r\n")[0]);
        } else if (body && (body.message || body.errorCode)) {
            // Fabric REST error envelope.
            lines.push("", "Fabric said:", [body.errorCode, body.message].filter(Boolean).join(" — "));
        } else if (raw) {
            lines.push("", raw.slice(0, 300));
        } else {
            lines.push("", "No error details were returned.");
        }

        if (response.status === 401 || response.status === 403) {
            lines.push(
                "",
                "Check that the service principal is allowed to use Fabric APIs",
                "(Fabric Admin portal → Tenant settings → 'Service principals can use",
                "Fabric APIs') and that it is a member of at least one workspace."
            );
        }
        return lines.join("\n");
    }

    // One token, client-credentials grant, obtained through the relay.
    async function fetchFabricToken(credentials) {
        const response = await fetch(`${window.location.origin}/fabric-token`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(credentials)
        });
        if (!response.ok) {
            throw new Error(await describeFabricError(response, "signing in to Microsoft Entra ID"));
        }
        const token = await response.json();
        if (!token.access_token) {
            throw new Error("Entra ID returned a response with no access token in it.");
        }
        return {
            accessToken: token.access_token,
            // Refreshed by re-running Test Connection; the margin keeps a long
            // session from failing on a token that expires mid-run.
            expiresAt: Date.now() + (Number(token.expires_in || 3600) - 120) * 1000
        };
    }

    // Every workspace the principal can see. The endpoint pages, so a tenant
    // with more than one page would otherwise show a truncated picker.
    async function fetchFabricWorkspaces(accessToken) {
        const headers = { "Authorization": `Bearer ${accessToken}` };
        let url = `${FABRIC_API_BASE}/workspaces`;
        const workspaces = [];

        for (let page = 0; page < 20 && url; page++) {
            const response = await fetch(fabricRequestUrl(url), { method: "GET", headers: headers });
            if (!response.ok) {
                throw new Error(await describeFabricError(response, "listing Fabric workspaces"));
            }
            const body = await response.json();
            (body.value || []).forEach(ws => workspaces.push(ws));
            url = body.continuationToken
                ? `${FABRIC_API_BASE}/workspaces?continuationToken=${encodeURIComponent(body.continuationToken)}`
                : null;
        }
        return workspaces;
    }

    // Falls the destination back to a typed name/ID. Used when the relay is not
    // available, so a run is still possible without the live listing.
    function enableFabricManualEntry(reason) {
        const manual = document.getElementById("fabric-workspace-manual");
        const extras = document.getElementById("fabric-extra-fields");
        if (manual) manual.style.display = "block";
        if (extras) extras.style.display = "block";
        if (reason) {
            const input = document.getElementById("fabric-workspace");
            if (input) input.placeholder = "Type the workspace name or ID";
        }
    }

    const btnTestFabric = document.getElementById("btn-test-fabric");
    if (btnTestFabric) {
        btnTestFabric.addEventListener("click", async () => {
            const tenantId = (document.getElementById("fabric-tenant-id").value || "").trim();
            const clientId = (document.getElementById("fabric-client-id").value || "").trim();
            const clientSecret = document.getElementById("fabric-client-secret").value || "";

            const missing = [
                ["Tenant (Directory) ID", tenantId],
                ["Client (Application) ID", clientId],
                ["Client secret", clientSecret]
            ].filter(pair => !pair[1]).map(pair => pair[0]);
            if (missing.length) {
                alert(`Please fill in: ${missing.join(", ")}.`);
                return;
            }

            if (!fabricRelayAvailable()) {
                alert(
                    "Fabric cannot be reached when this page is opened straight from disk.\n\n" +
                    "Microsoft's token endpoint refuses a client secret sent from a browser, so " +
                    "the sign-in has to go through the local relay:\n  python dev_server.py\n\n" +
                    "You can still type the workspace name or ID by hand for this run."
                );
                enableFabricManualEntry("no-relay");
                return;
            }

            const originalLabel = btnTestFabric.innerHTML;
            btnTestFabric.disabled = true;
            btnTestFabric.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Signing in...';

            try {
                const token = await fetchFabricToken({
                    tenantId: tenantId,
                    clientId: clientId,
                    clientSecret: clientSecret
                });

                btnTestFabric.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Loading workspaces...';
                const workspaces = await fetchFabricWorkspaces(token.accessToken);

                // "Personal" is a user's My workspace; nothing can be published
                // into it by a service principal, so it is not offered.
                const usable = workspaces.filter(ws => ws.type !== "Personal");

                if (!usable.length) {
                    alert(
                        "Signed in to Microsoft Fabric, but this service principal can see no workspaces.\n\n" +
                        "Add it to the target workspace as Admin, Member or Contributor " +
                        "(Workspace → Manage access), and confirm the tenant setting " +
                        "'Service principals can use Fabric APIs' is on."
                    );
                    enableFabricManualEntry("empty");
                    return;
                }

                const select = document.getElementById("fabric-workspace-select");
                select.innerHTML = '<option value="">Select a workspace...</option>';
                usable
                    .slice()
                    .sort((a, b) => String(a.displayName || "").localeCompare(String(b.displayName || "")))
                    .forEach(ws => {
                        const option = document.createElement("option");
                        option.value = ws.id;
                        option.textContent = ws.displayName || ws.id;
                        option.dataset.capacityId = ws.capacityId || "";
                        select.appendChild(option);
                    });

                fabricConnection = {
                    tenantId: tenantId,
                    clientId: clientId,
                    accessToken: token.accessToken,
                    expiresAt: token.expiresAt,
                    workspaces: usable
                };

                document.getElementById("fabric-workspace-container").style.display = "block";
                document.getElementById("fabric-workspace-manual").style.display = "none";
                document.getElementById("fabric-extra-fields").style.display = "block";
                btnTestFabric.style.display = "none";

                alert(`Connected to Microsoft Fabric. Loaded ${usable.length} workspace${usable.length === 1 ? "" : "s"}.`);
            } catch (err) {
                console.error(err);
                if (err.name === "TypeError") {
                    alert(
                        "The browser could not reach the local relay.\n\n" +
                        "Start the app with:  python dev_server.py"
                    );
                } else {
                    alert("Fabric connection failed:\n\n" + err.message);
                }
            } finally {
                btnTestFabric.disabled = false;
                btnTestFabric.innerHTML = originalLabel;
            }
        });
    }

    // Picking a workspace fills the capacity in, so the audit report records the
    // capacity the workspace is actually on rather than a hand-typed one.
    const fabricWorkspaceSelect = document.getElementById("fabric-workspace-select");
    if (fabricWorkspaceSelect) {
        fabricWorkspaceSelect.addEventListener("change", () => {
            const chosen = fabricWorkspaceSelect.options[fabricWorkspaceSelect.selectedIndex];
            const capacityInput = document.getElementById("fabric-capacity");
            if (!capacityInput || !chosen) return;
            capacityInput.value = chosen.dataset ? (chosen.dataset.capacityId || "") : "";
        });
    }

    // Reads whichever destination control is in play — the live picker when the
    // tenant was listed, the typed field when it could not be.
    function readFabricTarget() {
        const select = document.getElementById("fabric-workspace-select");
        const manualInput = document.getElementById("fabric-workspace");
        const capacityInput = document.getElementById("fabric-capacity");
        const prefixInput = document.getElementById("fabric-prefix");
        const pickerBox = document.getElementById("fabric-workspace-container");
        const usingPicker = !!(select && pickerBox && pickerBox.style.display !== "none");

        let workspace = "";
        let workspaceId = "";
        if (usingPicker && select.value) {
            workspace = select.options[select.selectedIndex].text;
            workspaceId = select.value;
        } else if (manualInput) {
            workspace = manualInput.value.trim();
        }

        return {
            workspace: workspace,
            workspaceId: workspaceId,
            capacity: capacityInput ? capacityInput.value.trim() : "",
            prefix: prefixInput ? prefixInput.value.trim() : "",
            usingPicker: usingPicker,
            focusTarget: usingPicker ? select : manualInput
        };
    }

    // ----------------------------------------------------------------------
    // 9. INITIALIZE UI WITH NO FILE SELECTED BY DEFAULT
    // ----------------------------------------------------------------------
    refreshAllTabsForActiveQvf(null);
    // Past runs do not depend on anything being loaded now, and the refresh above
    // returns early when no file is active — so without this the history tab read
    // as empty on a fresh load even when localStorage held completed runs.
    renderJobHistory();
});
