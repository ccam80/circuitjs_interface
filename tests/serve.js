/**
 * Minimal static file server for the deploy/ directory.
 * Used by Playwright integration tests.
 *
 * Usage: node tests/serve.js [port]
 */
'use strict';

const http = require('http');
const fs = require('fs');
const path = require('path');

const PORT = parseInt(process.argv[2], 10) || 8091;
const ROOT = path.resolve(__dirname, '..', 'deploy');

const MIME = {
  '.html': 'text/html',
  '.js':   'application/javascript',
  '.css':  'text/css',
  '.png':  'image/png',
  '.gif':  'image/gif',
  '.json': 'application/json',
  '.txt':  'text/plain',
  '.svg':  'image/svg+xml',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
};

const server = http.createServer(function(req, res) {
  var url;
  try { url = new URL(req.url, 'http://localhost'); }
  catch(e) { res.writeHead(400); res.end(); return; }

  var pathname = decodeURIComponent(url.pathname);
  if (pathname === '/') pathname = '/circuitjs.html';

  var filePath = path.join(ROOT, pathname);

  // Prevent directory traversal
  if (!filePath.startsWith(ROOT)) {
    res.writeHead(403); res.end(); return;
  }

  fs.readFile(filePath, function(err, data) {
    if (err) {
      res.writeHead(404);
      res.end('Not found: ' + pathname);
      return;
    }
    var ext = path.extname(filePath);
    res.writeHead(200, {
      'Content-Type': MIME[ext] || 'application/octet-stream',
      'Cache-Control': 'no-cache',
    });
    res.end(data);
  });
});

server.listen(PORT, function() {
  console.log('Serving ' + ROOT + ' on http://localhost:' + PORT);
});
