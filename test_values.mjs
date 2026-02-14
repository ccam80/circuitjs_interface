// Test: does the bridge actually stream voltage values?
import http from 'http';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DEPLOY = path.join(__dirname, 'deploy');
const PORT = 8378;

const CTZ = 'CQAgjCAMB0l3BWEA2aAWB8CcBmSy0AOBBAdkJE0sskoFMBaMMAKAGMQAmNWsZWnr34g0UWPAidomSFgSccpTmBwICyMXAiQWAJxFxwwwV0IVaqlgHkDQ2gjCcjtFywDmt0xRM5OLqCxgpLTcdlyQFHwW4eDw1DCsfqSeUSJ84JzeIABmAJYANgAuLElcSs5lTo5ZAIIAwiwA9iJGUAZYFEgw8LKkyGqcXVwtOCC5AHZNw+kCshRSXeJwWH0DSBAQjQCuxQAWIKPaLEA';

const MIME = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css',
  '.txt': 'text/plain', '.png': 'image/png', '.gif': 'image/gif', '.json': 'application/json' };

const server = http.createServer((req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  let filePath = path.join(DEPLOY, decodeURIComponent(url.pathname));
  if (filePath.endsWith(path.sep)) filePath += 'index.html';
  fs.readFile(filePath, (err, data) => {
    if (err) { res.writeHead(404); res.end('Not Found'); return; }
    const ext = path.extname(filePath);
    res.writeHead(200, { 'Content-Type': MIME[ext] || 'application/octet-stream' });
    res.end(data);
  });
});

server.listen(PORT, async () => {
  const puppeteer = await import('puppeteer');
  const browser = await puppeteer.default.launch({ headless: true, args: ['--no-sandbox'] });

  // Load the test-bridge.html page (full integration)
  console.log('Loading test-bridge.html...');
  const page = await browser.newPage();
  page.on('pageerror', err => console.log('PAGE ERROR:', err.message));

  await page.goto(`http://localhost:${PORT}/test-bridge.html`, { waitUntil: 'domcontentloaded' });
  await new Promise(r => setTimeout(r, 8000));  // let sim run 8 sec

  // Read the readout div
  const readout = await page.evaluate(() => document.getElementById('readout')?.textContent);
  console.log('\nReadout content:');
  console.log(readout);

  // Also try reading values directly from the bridge's inner iframe
  console.log('\n--- Direct API test on bridge iframe ---');
  const bridgeFrame = page.frames().find(f => f.url().includes('bridge.html'));
  if (bridgeFrame) {
    const simFrame = bridgeFrame.childFrames().find(f => f.url().includes('circuitjs.html'));
    if (simFrame) {
      const vals = await simFrame.evaluate(() => {
        if (!window.CircuitJS1) return { error: 'no CircuitJS1' };
        const sim = window.CircuitJS1;
        try {
          return {
            AC: sim.getNodeVoltage('AC'),
            filt: sim.getNodeVoltage('filt'),
            elementCount: sim.getElements().length,
          };
        } catch(e) {
          return { error: e.message };
        }
      });
      console.log('Direct API values:', JSON.stringify(vals, null, 2));
    } else {
      console.log('Could not find circuitjs.html frame');
      console.log('Bridge child frames:', bridgeFrame.childFrames().map(f => f.url()));
    }
  } else {
    console.log('Could not find bridge frame');
    console.log('All frames:', page.frames().map(f => f.url()));
  }

  await browser.close();
  server.close();
});
