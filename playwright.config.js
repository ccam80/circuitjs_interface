// @ts-check
'use strict';

const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './tests',
  testMatch: '*.spec.js',
  timeout: 60000,
  retries: 1,
  use: {
    baseURL: 'http://localhost:8091',
    // CircuitJS is heavy; give pages time to load
    navigationTimeout: 30000,
    actionTimeout: 10000,
  },
  webServer: {
    command: 'node tests/serve.js 8091',
    port: 8091,
    reuseExistingServer: true,
    timeout: 10000,
  },
});
