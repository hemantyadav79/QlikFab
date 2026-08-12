const express = require('express');
const https = require('https');
const path = require('path');
const fs = require('fs');
const os = require('os');
const crypto = require('crypto');
const { spawn } = require('child_process');
const cors = require('cors');

const app = express();
const PORT = 3000;

// Where migration jobs write their generated Power BI projects.
const WORK_ROOT = path.join(os.tmpdir(), 'qlik-powerbi-migrations');
fs.mkdirSync(WORK_ROOT, { recursive: true });

app.use(cors());
app.use(express.json({ limit: '5mb' }));
app.use(express.static(path.join(__dirname)));

const PYTHON = process.env.PYTHON || 'python';

// ---------------------------------------------------------------------------
// Qlik Cloud reverse-proxy: browser hits /qlik-proxy?endpoint=/api/v1/apps
// ---------------------------------------------------------------------------
app.get('/qlik-proxy', (req, res) => {
    const tenantUrl = req.headers['x-qlik-tenant'];
    const authHeader = req.headers['authorization'];
    const endpoint = req.query.endpoint || '/api/v1/apps';

    if (!tenantUrl || !authHeader) {
        return res.status(400).json({ error: 'Missing x-qlik-tenant or authorization header' });
    }

    let parsedUrl;
    try {
        parsedUrl = new URL(endpoint, tenantUrl);
    } catch (e) {
        return res.status(400).json({ error: 'Invalid tenant URL: ' + e.message });
    }

    const options = {
        hostname: parsedUrl.hostname,
        port: 443,
        path: parsedUrl.pathname + parsedUrl.search,
        method: 'GET',
        headers: { 'Authorization': authHeader, 'Accept': 'application/json' }
    };

    console.log(`[PROXY] => ${parsedUrl.href}`);
    const proxyReq = https.request(options, (proxyRes) => {
        let body = '';
        proxyRes.on('data', chunk => body += chunk);
        proxyRes.on('end', () => {
            console.log(`[PROXY] <= ${proxyRes.statusCode}  (${body.length} bytes)`);
            res.status(proxyRes.statusCode);
            res.setHeader('Content-Type', proxyRes.headers['content-type'] || 'application/json');
            res.send(body);
        });
    });
    proxyReq.on('error', (err) => {
        console.error('[PROXY] Error:', err.message);
        res.status(502).json({ error: 'Proxy request failed: ' + err.message });
    });
    proxyReq.end();
});

// ---------------------------------------------------------------------------
// Migration engine
// ---------------------------------------------------------------------------

/**
 * Run the Python migration over one .qvf and return the summary it writes.
 * All of the real work - decoding the app's rows, translating expressions,
 * rebuilding the visuals - happens in ai_qvf_to_powerbi.py.
 */
function runMigration(qvfPath, outputDir, maxRows) {
    return new Promise((resolve, reject) => {
        const summaryPath = path.join(outputDir, 'migration_summary.json');
        const args = [
            path.join(__dirname, 'migrate_qvf.py'),
            '--qvf', qvfPath,
            '--output', outputDir,
            '--summary', summaryPath
        ];
        if (maxRows) args.push('--max-rows', String(maxRows));

        const proc = spawn(PYTHON, args, { cwd: __dirname });
        let stderr = '';
        proc.stdout.on('data', d => process.stdout.write(d));
        proc.stderr.on('data', d => { stderr += d; });

        proc.on('error', err => reject(new Error(`Could not start Python (${PYTHON}): ${err.message}`)));
        proc.on('close', code => {
            if (code !== 0) {
                return reject(new Error(stderr.trim() || `Migration exited with code ${code}`));
            }
            try {
                resolve(JSON.parse(fs.readFileSync(summaryPath, 'utf-8')));
            } catch (e) {
                reject(new Error('Migration produced no summary: ' + e.message));
            }
        });
    });
}

function newJobDir() {
    const jobId = crypto.randomUUID();
    const dir = path.join(WORK_ROOT, jobId);
    fs.mkdirSync(dir, { recursive: true });
    return { jobId, dir };
}

function artifactUrls(jobId, summary) {
    const enc = encodeURIComponent;
    return {
        pbitUrl: `/api/artifact/${jobId}/${enc(summary.pbit)}`,
        pbipUrl: `/api/artifact/${jobId}/${enc(summary.pbip_zip)}`,
        auditUrl: `/api/artifact/${jobId}/${enc(summary.audit)}`
    };
}

/**
 * Migrate an uploaded .qvf. The browser posts the file bytes raw with the
 * name in a header, which keeps this dependency-free.
 */
app.post('/api/migrate/upload',
    express.raw({ type: '*/*', limit: '512mb' }),
    async (req, res) => {
        const filename = req.headers['x-filename'] || 'app.qvf';
        if (!req.body || !req.body.length) {
            return res.status(400).json({ error: 'No file content received' });
        }

        const { jobId, dir } = newJobDir();
        const qvfPath = path.join(dir, path.basename(filename));
        try {
            fs.writeFileSync(qvfPath, req.body);
            console.log(`[MIGRATE] ${filename} (${req.body.length} bytes) -> job ${jobId}`);
            const summary = await runMigration(qvfPath, path.join(dir, 'out'),
                                               req.query.maxRows);
            res.json({ jobId, ...summary, ...artifactUrls(jobId, summary) });
        } catch (err) {
            console.error('[MIGRATE] failed:', err.message);
            res.status(500).json({ error: err.message });
        }
    });

/**
 * Migrate an app straight out of Qlik Cloud.
 *
 * The tenant can hand back the whole app as a .qvf, so a cloud migration is
 * the same migration as an uploaded file - same real rows, same sheets, same
 * visuals - rather than a second, weaker code path built on REST metadata.
 */
app.post('/api/migrate/cloud', async (req, res) => {
    const { tenantUrl, apiKey, appId, appName } = req.body || {};
    if (!tenantUrl || !apiKey || !appId) {
        return res.status(400).json({ error: 'tenantUrl, apiKey and appId are required' });
    }

    const { jobId, dir } = newJobDir();
    const safeName = String(appName || appId).replace(/[^\w \-.]/g, '_');
    const qvfPath = path.join(dir, `${safeName}.qvf`);

    try {
        console.log(`[CLOUD] exporting app ${appId} from ${tenantUrl}`);
        await exportQlikApp(tenantUrl, apiKey, appId, qvfPath);
        const summary = await runMigration(qvfPath, path.join(dir, 'out'),
                                           req.query.maxRows);
        res.json({ jobId, ...summary, ...artifactUrls(jobId, summary) });
    } catch (err) {
        console.error('[CLOUD] failed:', err.message);
        res.status(502).json({ error: err.message });
    }
});

/** Download a generated artifact (.pbit, PBIP .zip, audit .md). */
app.get('/api/artifact/:jobId/:file', (req, res) => {
    const { jobId, file } = req.params;
    if (!/^[\w-]{8,}$/.test(jobId)) {
        return res.status(400).send('Bad job id');
    }
    const dir = path.join(WORK_ROOT, jobId, 'out');
    const target = path.join(dir, path.basename(file));
    if (!target.startsWith(dir) || !fs.existsSync(target)) {
        return res.status(404).send('Artifact not found');
    }
    res.download(target, path.basename(file));
});

// ---------------------------------------------------------------------------
// Qlik Cloud app export
// ---------------------------------------------------------------------------

function qlikRequest(tenantUrl, apiKey, endpoint, method) {
    return new Promise((resolve, reject) => {
        let url;
        try {
            url = new URL(endpoint, tenantUrl);
        } catch (e) {
            return reject(new Error('Invalid tenant URL: ' + e.message));
        }
        const req = https.request({
            hostname: url.hostname,
            port: 443,
            path: url.pathname + url.search,
            method: method || 'GET',
            headers: { 'Authorization': `Bearer ${apiKey}`, 'Accept': 'application/json' }
        }, res => {
            let body = '';
            res.on('data', c => body += c);
            res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body }));
        });
        req.on('error', reject);
        req.end();
    });
}

function qlikDownload(tenantUrl, apiKey, endpoint, destPath) {
    return new Promise((resolve, reject) => {
        const url = new URL(endpoint, tenantUrl);
        const req = https.request({
            hostname: url.hostname,
            port: 443,
            path: url.pathname + url.search,
            method: 'GET',
            headers: { 'Authorization': `Bearer ${apiKey}` }
        }, res => {
            if (res.statusCode !== 200) {
                res.resume();
                return reject(new Error(`Qlik download failed with HTTP ${res.statusCode}`));
            }
            const out = fs.createWriteStream(destPath);
            res.pipe(out);
            out.on('finish', () => out.close(() => resolve(destPath)));
            out.on('error', reject);
        });
        req.on('error', reject);
        req.end();
    });
}

async function exportQlikApp(tenantUrl, apiKey, appId, destPath) {
    // The export endpoint answers 201 with the temporary download path.
    const started = await qlikRequest(tenantUrl, apiKey,
        `/api/v1/apps/${encodeURIComponent(appId)}/export?NoData=false`, 'POST');

    if (started.status !== 201 && started.status !== 200) {
        throw new Error(`Qlik export failed (HTTP ${started.status}): ` +
                        started.body.slice(0, 300));
    }
    const location = started.headers.location;
    if (!location) {
        throw new Error('Qlik export returned no download location');
    }
    return qlikDownload(tenantUrl, apiKey, location, destPath);
}

// ---------------------------------------------------------------------------

app.listen(PORT, () => {
    console.log(`\n========================================================`);
    console.log(`  Qlik -> Power BI Migration Engine`);
    console.log(`  http://localhost:${PORT}`);
    console.log(`  Python  : ${PYTHON}`);
    console.log(`  Workdir : ${WORK_ROOT}`);
    console.log(`========================================================\n`);
});
