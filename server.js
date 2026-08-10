const express = require('express');
const https = require('https');
const path = require('path');
const cors = require('cors');

const app = express();
const PORT = 3000;

app.use(cors());
app.use(express.static(path.join(__dirname)));

// Simple reverse-proxy: browser hits /qlik-proxy?endpoint=/api/v1/apps
// Server fetches from Qlik Cloud and returns the JSON.
app.get('/qlik-proxy', (req, res) => {
    const tenantUrl = req.headers['x-qlik-tenant'];   // e.g. https://xyz.in.qlikcloud.com
    const authHeader = req.headers['authorization'];   // Bearer <token>
    const endpoint  = req.query.endpoint || '/api/v1/apps';

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
        headers: {
            'Authorization': authHeader,
            'Accept': 'application/json'
        }
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

app.listen(PORT, () => {
    console.log(`\n========================================================`);
    console.log(`  Migration Engine Proxy Server Running!`);
    console.log(`  Open your browser to: http://localhost:${PORT}`);
    console.log(`========================================================\n`);
});
