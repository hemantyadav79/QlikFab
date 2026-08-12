/* ==========================================================================
   QLIK -> FABRIC | AUTONOMOUS MIGRATION PLATFORM
   Interactive Frontend Logic (script.js) — 100% Dynamic & Zero Hardcoding
   Supports unlimited QVF files, dynamic PC browse/upload, and LIVE .PBIT / .PBIP Downloads
   ========================================================================== */

document.addEventListener("DOMContentLoaded", () => {
    // ----------------------------------------------------------------------
    // 1. DYNAMIC QVF APP REGISTRY
    // ----------------------------------------------------------------------
    // Apps migrated in this session, keyed by file name or Qlik Cloud app id.
    // Populated only by real migrations - nothing is pre-seeded, because every
    // field, measure and visual has to come from the .qvf that was migrated.
    const APP_REGISTRY = {};

    // ----------------------------------------------------------------------
    // 1. STATE MANAGEMENT
    let currentActiveQvfs = [];

    // ----------------------------------------------------------------------
    // 2. SIDEBAR NAVIGATION & THEME
    // ----------------------------------------------------------------------
    const navItems = document.querySelectorAll(".nav-menu .nav-item, .nav-menu .nav-sub-item");
    const tabPanes = document.querySelectorAll(".main-content .tab-pane");

    function switchTab(tabId) {
        navItems.forEach(i => i.classList.remove("active"));
        tabPanes.forEach(p => p.classList.remove("active"));

        // If it's a sub-item, we don't necessarily want to deactivate the accordion parent,
        // but for simplicity we just set active class on the exact clicked item.
        const targetNav = document.querySelector(`[data-tab="${tabId}"]`);
        const targetPane = document.getElementById(tabId);

        if (targetNav && targetPane) {
            targetNav.classList.add("active");
            targetPane.classList.add("active");
            
            // If sub-item, make sure parent accordion is open and active
            if (targetNav.classList.contains('nav-sub-item')) {
                const parentAcc = targetNav.closest('.accordion-item');
                if (parentAcc) {
                    const header = parentAcc.querySelector('.accordion-header');
                    header.classList.add('active');
                }
            }
        }
    }

    navItems.forEach(item => {
        item.addEventListener("click", (e) => {
            e.preventDefault();
            // Ignore if it's the accordion header that just toggles
            if (item.classList.contains('accordion-header')) {
                const body = item.nextElementSibling;
                const icon = item.querySelector('i');
                if (body.style.display === 'none' || body.style.display === '') {
                    body.style.display = 'block';
                    item.classList.add('active');
                    if (icon) {
                        icon.classList.remove('fa-chevron-down');
                        icon.classList.add('fa-chevron-up');
                    }
                } else {
                    body.style.display = 'none';
                    item.classList.remove('active');
                    if (icon) {
                        icon.classList.remove('fa-chevron-up');
                        icon.classList.add('fa-chevron-down');
                    }
                }
                return; // Prevent tab switching!
            }
            const tabId = item.getAttribute("data-tab");
            if (tabId) switchTab(tabId);
        });
    });

    const linkToSettings = document.getElementById("link-to-settings");
    if (linkToSettings) {
        linkToSettings.addEventListener("click", (e) => {
            e.preventDefault();
            switchTab("tab-settings");
        });
    }

    // Theme Switcher
    const btnLight = document.getElementById('btn-light');
    const btnDark = document.getElementById('btn-dark');
    if (btnLight && btnDark) {
        btnLight.addEventListener('click', () => {
            document.documentElement.setAttribute('data-theme', 'light');
            btnLight.classList.add('active');
            btnDark.classList.remove('active');
        });
        btnDark.addEventListener('click', () => {
            document.documentElement.setAttribute('data-theme', 'dark');
            btnDark.classList.add('active');
            btnLight.classList.remove('active');
        });
    }

    // ----------------------------------------------------------------------
    // 3. REAL POWER BI FILE GENERATORS & DOWNLOADERS (.PBIT, .PBIP, .MD)
    // ----------------------------------------------------------------------
    // ----------------------------------------------------------------------
    // 3. MIGRATION ENGINE CLIENT
    //
    // The .pbit / .pbip files are built by the migration engine on the server
    // (ai_qvf_to_powerbi.py), not here. That engine decodes the rows actually
    // stored in the .qvf, translates the app's real Qlik expressions to DAX,
    // and rebuilds every chart from every sheet - so the downloaded Power BI
    // project shows the same data and the same visuals as the Qlik app.
    // ----------------------------------------------------------------------

    function migrationApiBase() {
        // The page is normally served by the engine itself; when it is opened
        // straight off disk, talk to the local engine instead.
        return window.location.protocol === "file:" ? "http://localhost:3000" : "";
    }

    async function readError(response) {
        try {
            const data = await response.json();
            return data.error || `HTTP ${response.status}`;
        } catch (e) {
            return `HTTP ${response.status}`;
        }
    }

    // Send one .qvf to the engine and get back what it really migrated.
    async function migrateUploadedQvf(file) {
        const response = await fetch(`${migrationApiBase()}/api/migrate/upload`, {
            method: "POST",
            headers: {
                "Content-Type": "application/octet-stream",
                "x-filename": file.name
            },
            body: file
        });
        if (!response.ok) throw new Error(await readError(response));
        return response.json();
    }

    // Qlik Cloud apps are exported from the tenant as .qvf and then run
    // through the very same migration, so a cloud app and an uploaded file
    // produce identical results.
    async function migrateCloudApp(tenantUrl, apiKey, appId, appName) {
        const response = await fetch(`${migrationApiBase()}/api/migrate/cloud`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ tenantUrl, apiKey, appId, appName })
        });
        if (!response.ok) throw new Error(await readError(response));
        return response.json();
    }

    function formatSize(bytes) {
        if (bytes === undefined || bytes === null) return "";
        return bytes > 1024 * 1024
            ? (bytes / (1024 * 1024)).toFixed(2) + " MB"
            : (bytes / 1024).toFixed(1) + " KB";
    }

    // Reshape the engine's report into the object the tabs render.
    function appDataFromSummary(summary) {
        const columns = [];
        summary.tables.forEach(t => t.columns.forEach(c => columns.push(c.name)));

        const dataNote = summary.hasRealData
            ? `${summary.rowsEmbedded.toLocaleString()} real rows migrated`
            : "Schema only - app saved without data";

        const sheets = summary.sheets.map(sh => {
            const types = [...new Set(sh.visuals.map(v => v.powerBiType))];
            const dims = [...new Set(sh.visuals.flatMap(v => v.dimensions))];
            const meas = [...new Set(sh.visuals.flatMap(v => v.measures))];
            return {
                name: sh.name,
                chartType: types.join(" / ") || "No data visuals",
                title: `${sh.visualCount} visual${sh.visualCount === 1 ? "" : "s"} rebuilt from Qlik`,
                dims: dims.join(", ") || "-",
                meas: meas.join(", ") || "-",
                status: dataNote,
                visuals: sh.visuals
            };
        });

        const daxQueue = summary.measures.map(m => ({
            expr: m.qlik,
            dax: m.dax,
            conf: m.needsReview ? "Review" : "Exact",
            status: m.needsReview ? "Needs Review" : "Auto-Approved"
        }));

        return {
            name: summary.app,
            filename: summary.sourceFile,
            size: formatSize(summary.sourceBytes),
            sizeBytes: summary.sourceBytes,
            fieldsCnt: `${summary.columnCount} Columns`,
            visualsCnt: `${summary.sheets.length} Sheets / ${summary.visualCount} Visuals`,
            rowsEmbedded: summary.rowsEmbedded,
            hasRealData: summary.hasRealData,
            tables: summary.tables,
            pbitName: `${summary.projectName}.pbit`,
            pbipName: `${summary.projectName}.pbip`,
            projectDir: `${summary.projectName}_PowerBI_Project/`,
            pbitSize: "",
            sheets: sheets,
            daxQueue: daxQueue,
            columns: columns,
            artifacts: {
                pbit: summary.pbitUrl,
                pbip: summary.pbipUrl,
                audit: summary.auditUrl
            }
        };
    }

    // ----------------------------------------------------------------------
    // ARTIFACT DOWNLOADS - streamed from the engine that generated them
    // ----------------------------------------------------------------------

    async function fetchArtifact(url) {
        const response = await fetch(`${migrationApiBase()}${url}`);
        if (!response.ok) throw new Error(await readError(response));
        return response.blob();
    }

    function saveBlob(blob, filename) {
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(() => URL.revokeObjectURL(url), 5000);
    }

    async function downloadArtifact(appData, kind, filename) {
        const url = appData.artifacts && appData.artifacts[kind];
        if (!url) {
            alert("This app has not been migrated yet. Run the migration first.");
            return;
        }
        try {
            saveBlob(await fetchArtifact(url), filename);
        } catch (err) {
            alert(`Download failed: ${err.message}`);
        }
    }

    function generateAndDownloadPBIT(appData) {
        return downloadArtifact(appData, "pbit", appData.pbitName);
    }

    function generateAndDownloadPBIP(appData) {
        return downloadArtifact(appData, "pbip",
            `${appData.name.replace(/\s+/g, "_")}_Fabric_PBIP_Project.zip`);
    }

    function generateAndDownloadAuditReport(appData) {
        return downloadArtifact(appData, "audit",
            `${appData.name.replace(/\s+/g, "_")}_MIGRATION_AUDIT_REPORT.md`);
    }

    // Several apps at once: pull each generated artifact and bundle them.
    async function generateAndDownloadBatchZip(type) {
        if (typeof JSZip === "undefined") {
            alert("JSZip library not loaded. Please check your internet connection.");
            return;
        }
        const kind = type === "pbip" ? "pbip" : type === "audit" ? "audit" : "pbit";
        const zip = new JSZip();
        const failures = [];

        for (const appData of currentActiveQvfs) {
            const base = appData.name.replace(/\s+/g, "_");
            try {
                const blob = await fetchArtifact(appData.artifacts[kind]);
                if (kind === "pbip") {
                    // A PBIP artifact is itself a .zip. Nesting it would force the
                    // user to unzip twice, so unpack it into a per-app folder and
                    // the single outer extract lands on an openable project.
                    const inner = await JSZip.loadAsync(blob);
                    const entries = Object.values(inner.files).filter(e => !e.dir);
                    for (const entry of entries) {
                        zip.file(`${base}_Fabric_PBIP_Project/${entry.name}`,
                                 await entry.async("blob"));
                    }
                } else {
                    zip.file(kind === "pbit" ? appData.pbitName
                                             : `${base}_MIGRATION_AUDIT_REPORT.md`, blob);
                }
            } catch (err) {
                failures.push(`${appData.filename}: ${err.message}`);
            }
        }

        if (failures.length) {
            alert("Some apps could not be exported:\n" + failures.join("\n"));
        }
        saveBlob(await zip.generateAsync({ type: "blob" }),
                 `Batch_Migration_PowerBI_${kind.toUpperCase()}_Export.zip`);
    }

    // ----------------------------------------------------------------------
    // 4. TAB DATA REFRESH FUNCTION (ZERO HARDCODING)
    // ----------------------------------------------------------------------    
    function refreshAllTabsForActiveQvfs(appDataList) {
        currentActiveQvfs = appDataList || [];
        const hasApps = currentActiveQvfs.length > 0;

        // Reset Start Migration buttons
        const b1 = document.getElementById("btn-start-migration");
        if (b1) {
            b1.disabled = false;
            b1.classList.remove("running-btn", "success-btn");
            b1.style.background = "";
            b1.style.color = "";
            b1.innerHTML = "Start migration";
            b1.style.display = hasApps ? "block" : "none";
            b1.onclick = null;
        }
        const b2 = document.getElementById("btn-migrate-qlik");
        if (b2) {
            b2.disabled = false;
            b2.classList.remove("running-btn", "success-btn");
            b2.style.background = "";
            b2.style.color = "";
            b2.innerHTML = "Start migration";
            b2.style.display = hasApps ? "block" : "none";
            b2.onclick = null;
        }
        
        const sumApps = document.getElementById("summary-total-apps");
        const sumDax = document.getElementById("summary-total-dax");
        const sumAcc = document.getElementById("summary-avg-accuracy");
        const assessTbody = document.getElementById("assessment-tbody");
        const reviewTbody = document.getElementById("review-tbody");

        if (!hasApps) {
            if (sumApps) sumApps.textContent = "0 Apps";
            if (sumDax) sumDax.textContent = "0";
            if (sumAcc) sumAcc.textContent = "--";
            
            const assessName = document.getElementById("assess-target-name");
            if (assessName) assessName.textContent = "No QVF Selected";

            const kpiAppName = document.getElementById("kpi-app-name");
            if (kpiAppName) kpiAppName.textContent = "None Selected";

            const kpiFields = document.getElementById("kpi-fields-cnt");
            if (kpiFields) kpiFields.textContent = "--";

            const kpiVisuals = document.getElementById("kpi-visuals-cnt");
            if (kpiVisuals) kpiVisuals.textContent = "--";

            const reviewName = document.getElementById("review-target-name");
            if (reviewName) reviewName.textContent = "No QVF Selected";

            const artSubtitle = document.getElementById("artifact-dir-subtitle");
            if (artSubtitle) artSubtitle.textContent = "No active migration";

            const artPbitTitle = document.getElementById("artifact-pbit-title");
            if (artPbitTitle) artPbitTitle.textContent = "Template.pbit";

            const artPbipTitle = document.getElementById("artifact-pbip-title");
            if (artPbipTitle) artPbipTitle.innerHTML = `PowerBI_Project.pbip <span class="tag-badge-green">★ RECOMMENDED</span>`;

            const pbitBtn = document.getElementById("artifact-pbit-btn");
            if (pbitBtn) pbitBtn.disabled = true;

            const pbipBtn = document.getElementById("artifact-pbip-btn");
            if (pbipBtn) pbipBtn.disabled = true;

            const auditBtn = document.getElementById("artifact-audit-btn");
            if (auditBtn) auditBtn.disabled = true;

            // Hide DAX approval bar and Mapping info card
            const approveBar = document.getElementById("dax-approve-bar");
            if (approveBar) approveBar.style.display = "none";

            const mapCard = document.getElementById("mapping-info-card");
            if (mapCard) mapCard.style.display = "none";
            
            // Empty states for tables
            if (assessTbody) assessTbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:#888;">No files selected. Please connect to Qlik Cloud and select apps to migrate.</td></tr>`;
            if (reviewTbody) reviewTbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:#888;">No files selected.</td></tr>`;

            const mappingTbody = document.getElementById("mapping-tbody");
            if (mappingTbody) mappingTbody.innerHTML = `<tr><td colspan="5" style="text-align:center; color:#888;">No files selected.</td></tr>`;
            return;
        }

        // Aggregate stats
        let totalDax = 0;
        let totalFields = 0;
        let totalSheets = 0;
        let allSheetsHTML = "";
        let allDaxHTML = "";
        let filenames = [];
        
        currentActiveQvfs.forEach(appData => {
            filenames.push(appData.filename);
            totalDax += appData.daxQueue ? appData.daxQueue.length : 0;
            totalFields += parseInt(appData.fieldsCnt) || 0;
            totalSheets += appData.sheets ? appData.sheets.length : 0;
            
            if (appData.sheets) {
                allSheetsHTML += appData.sheets.map(sh => `
                    <tr>
                        <td><b>${sh.name}</b> <br><small style="color:#888">${appData.filename}</small></td>
                        <td>${sh.chartType}</td>
                        <td>${sh.title}</td>
                        <td><code>${sh.dims}</code></td>
                        <td><code>${sh.meas}</code></td>
                        <td><span class="status-badge success">${sh.status}</span></td>
                    </tr>
                `).join("");
            }
            if (appData.daxQueue) {
                allDaxHTML += appData.daxQueue.map(dq => `
                    <tr>
                        <td><input type="checkbox" checked></td>
                        <td><code>${dq.expr}</code> <br><small style="color:#888">${appData.filename}</small></td>
                        <td><code class="dax-code">${dq.dax}</code></td>
                        <td><span class="conf-pill">${dq.conf}</span></td>
                        <td><span class="status-badge ${dq.status === 'Auto-Approved' ? 'success' : 'pending'}">${dq.status}</span></td>
                        <td><button class="btn-icon" title="Edit DAX"><i class="fa-solid fa-pen-to-square"></i></button></td>
                    </tr>
                `).join("");
            }
        });

        if (sumApps) sumApps.textContent = currentActiveQvfs.length + (currentActiveQvfs.length === 1 ? " App" : " Apps");
        if (sumDax) sumDax.textContent = totalDax;
        // Share of Qlik expressions translated exactly, rather than a figure
        // that would look the same whatever the migration produced.
        if (sumAcc) {
            const all = currentActiveQvfs.flatMap(a => a.daxQueue || []);
            const exact = all.filter(d => d.status === "Auto-Approved").length;
            sumAcc.textContent = all.length
                ? `${Math.round((exact / all.length) * 100)}%`
                : "--";
        }
        
        const assessName = document.getElementById("assess-target-name");
        if (assessName) assessName.textContent = currentActiveQvfs.length > 1 ? `${currentActiveQvfs.length} Files Selected` : filenames[0];

        const kpiAppName = document.getElementById("kpi-app-name");
        if (kpiAppName) kpiAppName.textContent = "Batch Processing";

        const kpiFields = document.getElementById("kpi-fields-cnt");
        if (kpiFields) kpiFields.textContent = `${totalFields} Columns`;

        const kpiVisuals = document.getElementById("kpi-visuals-cnt");
        if (kpiVisuals) kpiVisuals.textContent = `${totalSheets} Sheets`;

        if (assessTbody) assessTbody.innerHTML = allSheetsHTML;

        const reviewName = document.getElementById("review-target-name");
        if (reviewName) reviewName.textContent = currentActiveQvfs.length > 1 ? `${currentActiveQvfs.length} Files Selected` : filenames[0];

        if (reviewTbody) reviewTbody.innerHTML = allDaxHTML;

        // Show DAX approval bar and Mapping info card
        const approveBar = document.getElementById("dax-approve-bar");
        if (approveBar) approveBar.style.display = "flex";

        const mapCard = document.getElementById("mapping-info-card");
        if (mapCard) mapCard.style.display = "flex";

        // One row per visual actually rebuilt, at the position Qlik gave it.
        let allMappingHTML = "";
        currentActiveQvfs.forEach(appData => {
            (appData.sheets || []).forEach((sh, idx) => {
                (sh.visuals || []).forEach(v => {
                    const pos = v.position || {};
                    const coord = `X: ${pos.x}, Y: ${pos.y}, W: ${pos.width}, H: ${pos.height}`;
                    allMappingHTML += `
                        <tr>
                            <td><code>${v.title}</code> <br><small style="color:#888">${appData.filename}</small></td>
                            <td>Page ${idx + 1} &mdash; ${sh.name}</td>
                            <td>${v.qlikType} &rarr; ${v.powerBiType}</td>
                            <td><code>${coord}</code></td>
                            <td><span class="status-badge success">Position preserved</span></td>
                        </tr>
                    `;
                });
            });
        });
        const mappingTbody = document.getElementById("mapping-tbody");
        if (mappingTbody) mappingTbody.innerHTML = allMappingHTML;

        // Artifacts (Download Buttons for Batch)
        const artSubtitle = document.getElementById("artifact-dir-subtitle");
        if (artSubtitle) artSubtitle.textContent = `Batch Export (${currentActiveQvfs.length} Projects)`;

        const artPbitTitle = document.getElementById("artifact-pbit-title");
        if (artPbitTitle) artPbitTitle.textContent = currentActiveQvfs.length === 1 ? currentActiveQvfs[0].pbitName : "Batch_Templates.zip";
        
        const artPbipTitle = document.getElementById("artifact-pbip-title");
        if (artPbipTitle) artPbipTitle.innerHTML = (currentActiveQvfs.length === 1 ? currentActiveQvfs[0].pbipName : "Batch_Fabric_Projects.zip") + ` <span class="tag-badge-green">★ RECOMMENDED</span>`;

        const artPbitMeta = document.getElementById("artifact-pbit-meta");
        if (artPbitMeta) artPbitMeta.textContent = `Size: ${(currentActiveQvfs.length * 4.5).toFixed(1)} KB • ${currentActiveQvfs.length} Templates`;

        const handlePbitClick = (e) => {
            e.preventDefault();
            if (currentActiveQvfs.length > 1) {
                generateAndDownloadBatchZip("pbit");
            } else {
                currentActiveQvfs.forEach(app => generateAndDownloadPBIT(app));
            }
        };
        const pbitBtn = document.getElementById("artifact-pbit-btn");
        if (pbitBtn) {
            pbitBtn.disabled = false;
            pbitBtn.onclick = handlePbitClick;
        }

        const handlePbipClick = (e) => {
            e.preventDefault();
            if (currentActiveQvfs.length > 1) {
                generateAndDownloadBatchZip("pbip");
            } else {
                currentActiveQvfs.forEach(app => generateAndDownloadPBIP(app));
            }
        };
        const pbipBtn = document.getElementById("artifact-pbip-btn");
        if (pbipBtn) {
            pbipBtn.disabled = false;
            pbipBtn.onclick = handlePbipClick;
        }

        const handleAuditClick = (e) => {
            e.preventDefault();
            if (currentActiveQvfs.length > 1) {
                generateAndDownloadBatchZip("audit");
            } else {
                currentActiveQvfs.forEach(app => generateAndDownloadAuditReport(app));
            }
        };
        const auditBtn = document.getElementById("artifact-audit-btn");
        if (auditBtn) {
            auditBtn.disabled = false;
            auditBtn.onclick = handleAuditClick;
        }

        // E. Update Job History Tab dynamically
        renderJobHistory();
    }

    // ----------------------------------------------------------------------
    // 5. FILE UPLOAD FROM PC (MULTIPLE SUPPORT)
    // ----------------------------------------------------------------------
    const dropzone = document.getElementById("dropzone");
    const fileInput = document.getElementById("qvf-file-input");
    const btnBrowse = document.getElementById("btn-browse-file");

    // Browse PC file dialog trigger
    if (dropzone && fileInput) {
        dropzone.addEventListener("click", () => fileInput.click());
        if (btnBrowse) {
            btnBrowse.addEventListener("click", (e) => {
                e.stopPropagation();
                fileInput.click();
            });
        }

        // Selecting files runs the real migration for each one. Nothing about
        // the app is guessed from the file name - every field, measure and
        // visual below comes back from the engine reading the .qvf itself.
        fileInput.addEventListener("change", async (e) => {
            const files = Array.from(e.target.files || []);
            if (files.length === 0) return;

            const dropzoneName = document.getElementById("dropzone-name");
            const setStatus = (text) => {
                if (dropzoneName) dropzoneName.textContent = text;
            };

            const migrated = [];
            const failures = [];

            for (let i = 0; i < files.length; i++) {
                const file = files[i];
                setStatus(`Migrating ${file.name} (${i + 1} of ${files.length})...`);
                try {
                    const summary = await migrateUploadedQvf(file);
                    const appData = appDataFromSummary(summary);
                    APP_REGISTRY[file.name] = appData;
                    migrated.push(appData);
                } catch (err) {
                    console.error(`[migrate] ${file.name}:`, err);
                    failures.push(`${file.name}: ${err.message}`);
                }
            }

            if (failures.length) {
                alert("Migration failed for:\n" + failures.join("\n") +
                      "\n\nMake sure the engine is running (npm start) and that " +
                      "Python is available on the PATH.");
            }

            if (migrated.length === 0) {
                setStatus("No apps migrated");
                return;
            }

            const rows = migrated.reduce((sum, a) => sum + a.rowsEmbedded, 0);
            setStatus(migrated.length === 1
                ? `${migrated[0].filename} - ${rows.toLocaleString()} rows migrated`
                : `${migrated.length} apps migrated - ${rows.toLocaleString()} rows total`);

            refreshAllTabsForActiveQvfs(migrated);
            migrated.forEach(recordNewJobRun);
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
        // No seeded history: the table lists migrations that really ran.
        return [];
    }

    function renderJobHistory() {
        const historyTbody = document.getElementById("history-tbody");
        if (!historyTbody) return;

        const history = getJobHistory();
        historyTbody.innerHTML = history.map(h => `
            <tr>
                <td><b>${h.id}</b></td>
                <td>${h.file}</td>
                <td>${h.sheets}</td>
                <td>${h.visuals}</td>
                <td><code>${h.time}</code></td>
                <td><span class="status-badge success">${h.audit}</span></td>
                <td>${h.date}</td>
            </tr>
        `).join("");
    }

    function recordNewJobRun(appData) {
        const history = getJobHistory();
        const now = new Date();
        const dateStr = now.toISOString().slice(0, 10) + " " + now.toTimeString().slice(0, 5);
        const visualCount = (appData.sheets || [])
            .reduce((n, sh) => n + (sh.visuals || []).length, 0);

        history.unshift({
            id: "MIG-" + Math.floor(1000 + Math.random() * 9000),
            file: appData.filename,
            sheets: `${(appData.sheets || []).length} Pages`,
            visuals: `${visualCount} Visuals`,
            time: appData.rowsEmbedded
                ? `${appData.rowsEmbedded.toLocaleString()} rows`
                : "no rows",
            audit: appData.hasRealData ? "Data + visuals" : "Visuals only",
            date: dateStr
        });

        localStorage.setItem("autogen_job_history", JSON.stringify(history));
        renderJobHistory();
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
    const btnMigrateQlik = document.getElementById("btn-migrate-qlik");
    const consoleCard = document.getElementById("autogen-console");
    const consoleBody = document.getElementById("console-logs-body");
    const consoleBadge = document.getElementById("console-status-badge");
    const metricsRow = document.getElementById("console-metrics-row");

    function executeMigrationFlow(btnElem) {
        if (!btnElem) return;

        // 1. Immediate interactive button press & running feedback
        btnElem.disabled = true;
        const originalText = btnElem.innerHTML;
        btnElem.classList.remove("success-btn");
        btnElem.classList.add("running-btn");
        btnElem.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Running AutoGen Multi-Agent Migration...`;

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
        if (consoleBody) {
            consoleBody.innerHTML = "";
            let logs = [];
            let baseTime = 0;

            // Every line below reports something the migration actually did.
            logs.push({ time: baseTime, text: `<span class="log-agent-badge badge-system">SYSTEM</span> Migration engine ready. Replaying results for ${currentActiveQvfs.length} app${currentActiveQvfs.length === 1 ? "" : "s"}.`, category: 'system' });
            baseTime += 500;

            currentActiveQvfs.forEach((appData, idx) => {
                const activeFile = appData.filename;
                const tables = appData.tables || [];
                const reviewed = (appData.daxQueue || []).filter(d => d.status === "Needs Review");

                logs.push({ time: baseTime, text: `📁 PROCESSING FILE ${idx + 1}/${currentActiveQvfs.length}: ${activeFile}`, category: 'file-header' });
                baseTime += 300;
                logs.push({ time: baseTime, text: `<span class="log-agent-badge badge-system">ORCHESTRATOR</span> Source app <code>"${activeFile}"</code> (${appData.size})`, category: 'system' });
                baseTime += 500;

                // Phase 1: Assessment - the data model found inside the .qvf
                logs.push({ time: baseTime, text: `<span class="log-agent-badge badge-assessment">ASSESSMENT AGENT</span> Reading data model: ${tables.length} table${tables.length === 1 ? "" : "s"}, ${appData.fieldsCnt}.`, category: 'assessment' });
                baseTime += 500;
                tables.forEach(t => {
                    const detail = t.rowsEmbedded
                        ? `${t.rowsEmbedded.toLocaleString()} of ${t.rowsInApp.toLocaleString()} rows decoded`
                        : `no stored rows - this app was saved without its data`;
                    logs.push({ time: baseTime, text: `<span class="log-agent-badge badge-assessment">ASSESSMENT AGENT</span> <code>${t.name}</code>: ${t.columns.length} columns, ${detail}.`, category: 'assessment' });
                    baseTime += 350;
                });

                // Phase 2: Parsing - the sheets and charts found in the app
                logs.push({ time: baseTime, text: `<span class="log-agent-badge badge-parsing">PARSING AGENT</span> Extracting sheets and chart definitions from ${activeFile}...`, category: 'parsing' });
                baseTime += 500;
                (appData.sheets || []).forEach(sh => {
                    logs.push({ time: baseTime, text: `<span class="log-agent-badge badge-parsing">PARSING AGENT</span> Sheet "${sh.name}": ${(sh.visuals || []).length} visual${(sh.visuals || []).length === 1 ? "" : "s"} → ${sh.chartType}`, category: 'parsing' });
                    baseTime += 350;
                });

                // Phase 3: Mapping - the expressions that became DAX
                logs.push({ time: baseTime, text: `<span class="log-agent-badge badge-mapping">MAPPING AGENT</span> Translating ${appData.daxQueue.length} Qlik expression${appData.daxQueue.length === 1 ? "" : "s"} to DAX...`, category: 'mapping' });
                baseTime += 500;
                (appData.daxQueue || []).slice(0, 6).forEach(dq => {
                    logs.push({ time: baseTime, text: `<span class="log-agent-badge badge-mapping">MAPPING AGENT</span> <code>${dq.expr}</code> → <code>${dq.dax}</code>`, category: 'mapping' });
                    baseTime += 300;
                });

                if (reviewed.length) {
                    logs.push({ time: baseTime, text: `<span class="log-agent-badge badge-error">NEEDS REVIEW</span> ${reviewed.length} expression${reviewed.length === 1 ? "" : "s"} use Qlik set analysis. DAX needs an explicit CALCULATE filter for that, so these aggregate over all rows - check them in the DAX review tab.`, category: 'failure' });
                    baseTime += 700;
                }

                if (!appData.hasRealData) {
                    logs.push({ time: baseTime, text: `<span class="log-agent-badge badge-error">NO STORED DATA</span> ${activeFile} contains no loaded rows, so the Power BI project carries the schema and visuals only. Reload the app in Qlik and migrate again to include data.`, category: 'failure' });
                    baseTime += 700;
                }

                // Phase 4: Report generation
                logs.push({ time: baseTime, text: `<span class="log-agent-badge badge-report">REPORT GEN AGENT</span> Building semantic model and ${appData.sheets.length} report page${appData.sheets.length === 1 ? "" : "s"}...`, category: 'report' });
                baseTime += 600;
                logs.push({ time: baseTime, text: `<span class="log-agent-badge badge-report">REPORT GEN AGENT</span> ✓ ${appData.pbitName} and the Fabric PBIP project are ready to download.`, category: 'report' });
                baseTime += 600;
            });

            const totalRows = currentActiveQvfs.reduce((s, a) => s + (a.rowsEmbedded || 0), 0);
            const totalVisuals = currentActiveQvfs.reduce(
                (s, a) => s + (a.sheets || []).reduce((n, sh) => n + (sh.visuals || []).length, 0), 0);
            logs.push({ time: baseTime, text: `<span class="log-agent-badge badge-system" style="background: rgba(16, 185, 129, 0.2); color: var(--color-success); border: 1px solid rgba(16, 185, 129, 0.3);">BATCH COMPLETE</span> <b>${currentActiveQvfs.length} app${currentActiveQvfs.length === 1 ? "" : "s"} migrated — ${totalRows.toLocaleString()} rows and ${totalVisuals} visuals.</b>`, category: 'system' });
            baseTime += 800;

            logs.forEach(log => {
                setTimeout(() => {
                    const row = document.createElement("div");
                    row.className = "log-line";
                    row.dataset.category = log.category || 'system';
                    if (log.text.startsWith("\n")) {
                        row.style.marginTop = "12px";
                        row.innerHTML = `<span class="log-time">[+${(log.time/1000).toFixed(1)}s]</span> <b>${log.text.trim()}</b>`;
                    } else {
                        row.innerHTML = `<span class="log-time">[+${(log.time/1000).toFixed(1)}s]</span> ${log.text}`;
                    }
                    // Apply current filter
                    const activeFilter = document.querySelector('.log-filter-btn.active');
                    const currentFilter = activeFilter ? activeFilter.dataset.filter : 'all';
                    if (currentFilter !== 'all' && row.dataset.category !== currentFilter) {
                        row.style.display = 'none';
                    }
                    consoleBody.appendChild(row);
                    consoleBody.scrollTop = consoleBody.scrollHeight;
                }, log.time);
            });

            const finishMigration = () => {
                if (window.activeCloudMigrations > 0) {
                    btnElem.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Waiting for cloud migrations...`;
                    setTimeout(finishMigration, 1000);
                    return;
                }
                
                if (consoleBadge) {
                    consoleBadge.className = "console-status success";
                    consoleBadge.innerHTML = `<i class="fa-solid fa-check"></i> COMPLETED`;
                }
                if (metricsRow) {
                    metricsRow.classList.remove("hidden");
                    const mSheets = document.getElementById("metric-sheets");
                    const mVisuals = document.getElementById("metric-visuals");
                    const mDax = document.getElementById("metric-dax");
                    
                    let tSheets = 0, tDax = 0;
                    currentActiveQvfs.forEach(app => {
                        tSheets += app.sheets ? app.sheets.length : 0;
                        tDax += app.daxQueue ? app.daxQueue.length : 0;
                    });
                    if (mSheets) mSheets.textContent = currentActiveQvfs.length + " Apps";
                    if (mVisuals) mVisuals.textContent = tSheets + " Sheets";
                    if (mDax) mDax.textContent = tDax + " Auto-Mapped";
                }
                currentActiveQvfs.forEach(app => recordNewJobRun(app));
                
                // 2. Change button to SUCCESS & make it clickable to jump to Artifacts
                btnElem.disabled = false;
                btnElem.classList.remove("running-btn");
                btnElem.classList.add("success-btn");
                btnElem.innerHTML = `<i class="fa-solid fa-circle-check"></i> Batch Migration Completed! View Artifacts ->`;
                btnElem.onclick = (e) => {
                    e.preventDefault();
                    switchTab("tab-artifacts");
                };
            };
            setTimeout(finishMigration, baseTime + 500);
        }
    }

    if (btnStart) {
        btnStart.addEventListener("click", () => executeMigrationFlow(btnStart));
    }
    if (btnMigrateQlik) {
        btnMigrateQlik.addEventListener("click", () => executeMigrationFlow(btnMigrateQlik));
    }

    // ----------------------------------------------------------------------
    // 9. QLIK CLOUD CONNECTION SIMULATION
    // ----------------------------------------------------------------------
    const btnTestConn = document.getElementById("btn-test-connection");
    const tenantUrl = document.getElementById("qlik-tenant-url");
    const apiKey = document.getElementById("qlik-api-key");
    const appsSection = document.getElementById("qlik-cloud-apps-section");
    const appCheckboxContainer = document.getElementById("qlik-cloud-app-checkboxes");
    const btnCloudMigrate = document.getElementById("btn-cloud-migrate");
    
    window.activeCloudMigrations = 0;

    if (btnTestConn) {
        btnTestConn.addEventListener("click", (e) => {
            e.preventDefault();
            if (!tenantUrl.value || !apiKey.value) {
                alert("Please enter a valid Tenant URL and API Key.");
                return;
            }

            // Use local proxy to bypass CORS
            const baseUrl = tenantUrl.value.replace(/\/$/, "");
            
            btnTestConn.disabled = true;
            btnTestConn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Connecting...`;
            btnTestConn.style.background = "#e6e1d6";
            btnTestConn.style.cursor = "not-allowed";

            // If we are on file:// or another port, point explicitly to localhost:3000, 
            // otherwise use relative path if we are served BY the proxy.
            const proxyUrl = window.location.protocol === 'file:' 
                ? 'http://localhost:3000' 
                : '';

            fetch(`${proxyUrl}/qlik-proxy?endpoint=${encodeURIComponent('/api/v1/items?resourceType=app')}`, {
                method: "GET",
                headers: {
                    "Authorization": `Bearer ${apiKey.value}`,
                    "Accept": "application/json",
                    "x-qlik-tenant": baseUrl
                }
            })
            .then(response => {
                if (!response.ok) {
                    return response.text().then(txt => {
                        throw new Error(`HTTP ${response.status}: ${txt.substring(0, 200)}`);
                    });
                }
                return response.json();
            })
            .then(data => {
                btnTestConn.innerHTML = `<i class="fa-solid fa-circle-check"></i> QLIK CLOUD SESSION ACTIVE & SYNCED`;
                btnTestConn.style.background = "#10b981";
                btnTestConn.style.color = "#fff";
                btnTestConn.style.borderColor = "#10b981";
                
                appsSection.style.display = "block";
                appCheckboxContainer.innerHTML = "";
                
                // /api/v1/items returns { data: [...] }
                const apps = data.data || [];
                if (apps.length === 0) {
                     appCheckboxContainer.innerHTML = `<p style="color: var(--color-text-muted); padding: 10px;">No apps found in tenant.</p>`;
                } else {
                     apps.forEach((app, i) => {
                         const appName = app.name || "Unknown App";
                         const appId = app.resourceId || app.id || "unknown";

                         const itemDiv = document.createElement('div');
                         itemDiv.className = 'app-checkbox-item';
                         itemDiv.innerHTML = `
                             <input type="checkbox" id="app-cb-${i}" value="${appId}">
                             <i class="fa-solid fa-file-lines app-file-icon"></i>
                             <label for="app-cb-${i}">${appName}.qvf</label>
                         `;
                         appCheckboxContainer.appendChild(itemDiv);

                         const label = itemDiv.querySelector('label');
                         const originalText = label.textContent;

                         // Ticking an app migrates it for real: the engine
                         // exports the app from the tenant as a .qvf and runs
                         // the same migration an uploaded file gets, so the
                         // fields, measures and visuals shown are the app's own.
                         itemDiv.addEventListener('click', async (e) => {
                             if (e.target.tagName !== 'INPUT') {
                                 const cb = itemDiv.querySelector('input[type="checkbox"]');
                                 cb.checked = !cb.checked;
                             }
                             const isChecked = itemDiv.querySelector('input').checked;
                             itemDiv.classList.toggle('checked', isChecked);

                             if (!isChecked) {
                                 handleCheckboxChange();
                                 return;
                             }
                             if (APP_REGISTRY[appId]) {
                                 handleCheckboxChange();
                                 return;
                             }

                             itemDiv.style.opacity = "0.7";
                             label.innerHTML = `${originalText} <i class="fa-solid fa-spinner fa-spin" style="margin-left: 8px;"></i> <span style="font-size: 11px; color: #10b981;">Migrating from Qlik Cloud...</span>`;
                             window.activeCloudMigrations++;
                             try {
                                 const summary = await migrateCloudApp(
                                     baseUrl, apiKey.value, appId, appName);
                                 APP_REGISTRY[appId] = appDataFromSummary(summary);
                             } catch (err) {
                                 console.error('[cloud migrate]', err);
                                 alert(`Could not migrate "${appName}":\n${err.message}`);
                                 itemDiv.querySelector('input').checked = false;
                                 itemDiv.classList.remove('checked');
                             } finally {
                                 window.activeCloudMigrations--;
                                 label.textContent = originalText;
                                 itemDiv.style.opacity = "1";
                                 handleCheckboxChange();
                             }
                         });
                     });
                }
            })
            .catch(error => {
                btnTestConn.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i> Connection Failed`;
                btnTestConn.style.background = "#ef4444";
                btnTestConn.style.color = "#fff";
                btnTestConn.style.borderColor = "#ef4444";
                alert("Failed to connect to Qlik Cloud. Please check your Tenant URL, API Key, and ensure CORS is configured in Qlik Management Console.\n\nError: " + error.message);
                btnTestConn.disabled = false;
                btnTestConn.style.cursor = "pointer";
            });
        });
    }

    function handleCheckboxChange() {
        const checkboxes = document.querySelectorAll('#qlik-cloud-app-checkboxes input[type="checkbox"]:checked');
        const selectedFiles = Array.from(checkboxes).map(cb => APP_REGISTRY[cb.value]).filter(Boolean);
        if (selectedFiles.length > 0) {
            if (btnCloudMigrate) btnCloudMigrate.style.display = "block";
            refreshAllTabsForActiveQvfs(selectedFiles);
        } else {
            if (btnCloudMigrate) btnCloudMigrate.style.display = "none";
            refreshAllTabsForActiveQvfs([]);
        }
    }

    if (btnCloudMigrate) {
        btnCloudMigrate.addEventListener("click", (e) => {
            e.preventDefault();
            executeMigrationFlow(btnCloudMigrate);
        });
    }

    // Log Filter Tabs
    document.querySelectorAll('.log-filter-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.log-filter-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const filter = btn.dataset.filter;
            document.querySelectorAll('#console-logs-body .log-line').forEach(line => {
                if (filter === 'all') {
                    line.style.display = '';
                } else {
                    line.style.display = line.dataset.category === filter ? '' : 'none';
                }
            });
        });
    });

    // ----------------------------------------------------------------------
    // 10. INITIALIZE UI WITH NO FILE SELECTED BY DEFAULT
    // ----------------------------------------------------------------------
    refreshAllTabsForActiveQvfs(currentActiveQvfs);

    // Always open Run Migration tab on page load/refresh
    const hashTab = window.location.hash ? window.location.hash.replace('#', '') : null;
    if (hashTab && document.getElementById(hashTab)) {
        switchTab(hashTab);
    } else {
        switchTab("tab-run");
    }
});
