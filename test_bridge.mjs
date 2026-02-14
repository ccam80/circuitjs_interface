// Test: compare direct circuitjs.html vs bridge.html loading
// Starts a local HTTP server, opens both in Puppeteer, captures console output

import http from 'http';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DEPLOY = path.join(__dirname, 'deploy');
const PORT = 8377;

const CTZ = 'CQAgjCAMB0l3BWEA2aAWB8CcBmSy0AOBBAdkJE0sskoFMBaMMAKAGMQAmNWsZWnr34g0UWPAidomSFgSccpTmBwICyMXAiQWAJxFxwwwV0IVaqlgHkDQ2gjCcjtFywDmt0xRM5OLqCxgpLTcdlyQFHwW4eDw1DCsfqSeUSJ84JzeIABmAJYANgAuLElcSs5lTo5ZAIIAwiwA9iJGUAZYFEgw8LKkyGqcXVwtOCC5AHZNw+kCshRSXeJwWH0DSBAQjQCuxQAWIKPaLEA';

// Simple static file server
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
  console.log(`Server on http://localhost:${PORT}`);

  const puppeteer = await import('puppeteer');
  const browser = await puppeteer.default.launch({ headless: true, args: ['--no-sandbox'] });

  // ── Test 1: Direct load ──
  console.log('\n=== TEST 1: Direct circuitjs.html?ctz=... ===');
  const page1 = await browser.newPage();
  const logs1 = [], errors1 = [];
  page1.on('console', msg => logs1.push(`[${msg.type()}] ${msg.text()}`));
  page1.on('pageerror', err => errors1.push(err.message));

  await page1.goto(`http://localhost:${PORT}/circuitjs.html?ctz=${CTZ}`, { waitUntil: 'domcontentloaded' });
  await new Promise(r => setTimeout(r, 5000));  // let simulation run 5 sec

  console.log('Console messages:', logs1.length);
  logs1.forEach(l => console.log(' ', l));
  console.log('Page errors:', errors1.length);
  errors1.forEach(e => console.log('  ERROR:', e));

  // ── Test 2: Through bridge ──
  console.log('\n=== TEST 2: bridge.html → circuitjs.html ===');
  const page2 = await browser.newPage();
  const logs2 = [], errors2 = [];
  page2.on('console', msg => logs2.push(`[${msg.type()}] ${msg.text()}`));
  page2.on('pageerror', err => errors2.push(err.message));

  await page2.goto(`http://localhost:${PORT}/bridge.html?ctz=${CTZ}&nodes=AC,filt,out&rate=4`, { waitUntil: 'domcontentloaded' });
  await new Promise(r => setTimeout(r, 5000));

  console.log('Console messages:', logs2.length);
  logs2.forEach(l => console.log(' ', l));
  console.log('Page errors:', errors2.length);
  errors2.forEach(e => console.log('  ERROR:', e));

  // ── Test 3: bridge.html with NO extra params (just ctz) ──
  console.log('\n=== TEST 3: bridge.html with minimal params ===');
  const page3 = await browser.newPage();
  const logs3 = [], errors3 = [];
  page3.on('console', msg => logs3.push(`[${msg.type()}] ${msg.text()}`));
  page3.on('pageerror', err => errors3.push(err.message));

  // Modify bridge to pass ONLY ctz, no extra params
  await page3.goto(`http://localhost:${PORT}/circuitjs.html?running=true&whiteBackground=false&editable=true&hideSidebar=false&hideMenu=false&ctz=${CTZ}`, { waitUntil: 'domcontentloaded' });
  await new Promise(r => setTimeout(r, 5000));

  console.log('Console messages:', logs3.length);
  logs3.forEach(l => console.log(' ', l));
  console.log('Page errors:', errors3.length);
  errors3.forEach(e => console.log('  ERROR:', e));

  // ── Test 4: Check iframe dimensions and $wnd ──
  console.log('\n=== TEST 4: bridge iframe internals ===');
  const page4 = await browser.newPage();
  page4.on('console', msg => console.log('  [console]', msg.text()));
  page4.on('pageerror', err => console.log('  [error]', err.message));

  await page4.goto(`http://localhost:${PORT}/bridge.html?ctz=${CTZ}&nodes=AC,filt,out&rate=4`, { waitUntil: 'domcontentloaded' });
  await new Promise(r => setTimeout(r, 4000));

  // Inspect the sim-frame iframe
  const frameInfo = await page4.evaluate(() => {
    const frame = document.getElementById('sim-frame');
    if (!frame) return { error: 'no sim-frame found' };
    const win = frame.contentWindow;
    return {
      frameSrc: frame.src,
      frameWidth: frame.offsetWidth,
      frameHeight: frame.offsetHeight,
      innerWidth: win ? win.innerWidth : 'N/A',
      innerHeight: win ? win.innerHeight : 'N/A',
      hasCircuitJS1: win ? !!win.CircuitJS1 : false,
      hasLZString: win ? !!win.LZString : false,
      locationSearch: win ? win.location.search : 'N/A',
    };
  });
  console.log('Frame info:', JSON.stringify(frameInfo, null, 2));

  await browser.close();
  server.close();
  console.log('\nDone.');
});
