/**
 * Tier 2: Playwright integration tests for circuitjs-reporting.js.
 *
 * Run: npx playwright test tests/integrity.spec.js
 *
 * Loads the real CircuitJS simulator in a browser, sends subscribe
 * messages with permissions, and verifies integrity checking works
 * end-to-end against the actual GWT-compiled simulator.
 */
'use strict';

const { test, expect } = require('@playwright/test');

// Voltage divider circuit (same as tests/Voltage_Divider.xml)
const CIRCUIT_CTZ = 'CQAgjCAMB0l3BWcMBMcUHYMGZIA4UA2ATmIxEO2QpRAQFMBaMMAKADdwxbtC8vaAFn6QoIQaLCjpUaAlYB3AeP4oJKqIuW9V6nZqVrRw8diEitRkPsFnrfTQCdT5l+BQjk8Vs9u1u-H4gaJ5S3r52IW4engjh0XiBdvqS8JCsaORBAeLEhO6BIABqAIIZkFmR+CqiUYLFALLllbQxuflt9UUAQqxAA';
const BASE_URL = 'http://localhost:8091';

/**
 * Wait for CircuitJS1 to be available on the page.
 * GWT apps take a while to compile and initialize.
 */
async function waitForCircuitJS(page) {
  await page.waitForFunction(
    () => typeof window.CircuitJS1 !== 'undefined' && window.CircuitJS1 !== null,
    null,
    { timeout: 30000 }
  );
}

/**
 * Set up a message listener on the page to capture postMessage events.
 */
async function setupMessageCapture(page) {
  await page.evaluate(() => {
    window.__testMessages = [];
    window.addEventListener('message', function(e) {
      if (e.data && typeof e.data === 'object' && e.data.type) {
        // Deep clone to avoid reference issues
        window.__testMessages.push(JSON.parse(JSON.stringify(e.data)));
      }
    });
  });
}

/**
 * Send a circuitjs-subscribe message to the page.
 */
async function subscribe(page, config) {
  await page.evaluate(function(cfg) {
    window.postMessage(cfg, '*');
  }, Object.assign({ type: 'circuitjs-subscribe' }, config));
}

/**
 * Wait for a circuitjs-data message containing a specific key.
 */
async function waitForDataWithKey(page, key, timeout) {
  timeout = timeout || 15000;
  await page.waitForFunction(
    function(k) {
      return window.__testMessages.some(function(m) {
        return m.type === 'circuitjs-data' && k in m.values;
      });
    },
    key,
    { timeout: timeout }
  );
}

/**
 * Get all captured messages of a given type.
 */
async function getMessages(page, type) {
  return page.evaluate(function(t) {
    return window.__testMessages.filter(function(m) { return m.type === t; });
  }, type);
}


// ═══════════════════════════════════════════════════════════════
// Tests
// ═══════════════════════════════════════════════════════════════

test.describe('CircuitJS reporting integration', function() {

  test('CircuitJS loads successfully', async function({ page }) {
    await page.goto(BASE_URL + '/circuitjs.html?ctz=' + CIRCUIT_CTZ + '&running=true');
    await waitForCircuitJS(page);

    var hasAPI = await page.evaluate(function() {
      var sim = window.CircuitJS1;
      return typeof sim.getElements === 'function'
          && typeof sim.exportCircuit === 'function';
    });
    expect(hasAPI).toBe(true);
  });

  test('reporting script exposes __cjsReporting on window', async function({ page }) {
    await page.goto(BASE_URL + '/circuitjs.html?ctz=' + CIRCUIT_CTZ + '&running=true');
    await waitForCircuitJS(page);

    var hasFunctions = await page.evaluate(function() {
      var r = window.__cjsReporting;
      return r
        && typeof r.buildElementInfo === 'function'
        && typeof r.checkIntegrity === 'function'
        && typeof r.buildTypeCounts === 'function';
    });
    expect(hasFunctions).toBe(true);
  });

  test('subscribe WITHOUT permissions key → no integrity in data', async function({ page }) {
    await page.goto(BASE_URL + '/circuitjs.html?ctz=' + CIRCUIT_CTZ + '&running=true');
    await waitForCircuitJS(page);
    await setupMessageCapture(page);

    // Subscribe with NO permissions key at all
    await subscribe(page, {
      nodes: [],
      elements: ['0:voltageDiff'],
      rate: 60,
      // no permissions key
    });

    // Wait for some data messages
    await page.waitForFunction(
      () => window.__testMessages.filter(m => m.type === 'circuitjs-data').length >= 2,
      null, { timeout: 15000 }
    );

    var dataMessages = await getMessages(page, 'circuitjs-data');
    expect(dataMessages.length).toBeGreaterThan(0);

    // None should have 'integrity' key
    for (var msg of dataMessages) {
      expect('integrity' in msg.values).toBe(false);
    }
  });

  test('subscribe WITH empty permissions → integrity=1 in data', async function({ page }) {
    await page.goto(BASE_URL + '/circuitjs.html?ctz=' + CIRCUIT_CTZ + '&running=true');
    await waitForCircuitJS(page);
    await setupMessageCapture(page);

    // Subscribe with permissions (empty = everything locked)
    await subscribe(page, {
      nodes: [],
      elements: ['0:voltageDiff'],
      rate: 60,
      permissions: { editableIndices: [], removableIndices: [], typeRules: [] },
    });

    // Wait for data message with integrity
    await waitForDataWithKey(page, 'integrity');

    var dataMessages = await getMessages(page, 'circuitjs-data');
    var withIntegrity = dataMessages.filter(function(m) { return 'integrity' in m.values; });
    expect(withIntegrity.length).toBeGreaterThan(0);
    // Unmodified circuit → integrity = 1
    expect(withIntegrity[0].values.integrity).toBe(1);
  });

  test('baseline is captured after subscribe with permissions', async function({ page }) {
    await page.goto(BASE_URL + '/circuitjs.html?ctz=' + CIRCUIT_CTZ + '&running=true');
    await waitForCircuitJS(page);
    await setupMessageCapture(page);

    await subscribe(page, {
      nodes: [],
      elements: [],
      rate: 60,
      permissions: { editableIndices: [], removableIndices: [], typeRules: [] },
    });

    // Wait for baseline to be captured
    await page.waitForFunction(
      () => window.__cjsReporting._baseline && window.__cjsReporting._baseline.info,
      null, { timeout: 15000 }
    );

    var baseline = await page.evaluate(function() {
      var b = window.__cjsReporting._baseline;
      return {
        infoLength: b.info.length,
        hasTypeCounts: !!b.typeCounts,
        firstTypeCode: b.info[0] ? b.info[0].typeCode : null,
      };
    });

    expect(baseline.infoLength).toBeGreaterThan(0);
    expect(baseline.hasTypeCounts).toBe(true);
    expect(baseline.firstTypeCode).toBeTruthy();
  });

  test('checkIntegrity detects param change against real baseline', async function({ page }) {
    await page.goto(BASE_URL + '/circuitjs.html?ctz=' + CIRCUIT_CTZ + '&running=true');
    await waitForCircuitJS(page);
    await setupMessageCapture(page);

    await subscribe(page, {
      nodes: [],
      elements: [],
      rate: 60,
      permissions: { editableIndices: [], removableIndices: [], typeRules: [] },
    });

    await page.waitForFunction(
      () => window.__cjsReporting._baseline && window.__cjsReporting._baseline.info,
      null, { timeout: 15000 }
    );

    var result = await page.evaluate(function() {
      var rep = window.__cjsReporting;
      var baseline = rep._baseline;
      // Clone current info from baseline (identical to current real circuit)
      var current = baseline.info.map(function(e) {
        return { typeCode: e.typeCode, coords: e.coords, paramSig: e.paramSig, apiType: e.apiType };
      });

      // Verify unchanged passes
      var passResult = rep.checkIntegrity(current, baseline);

      // Now modify a component's paramSig
      current[0].paramSig = 'TAMPERED';
      var failResult = rep.checkIntegrity(current, baseline);

      return { pass: passResult, fail: failResult };
    });

    expect(result.pass).toBe(1);
    expect(result.fail).toBe(0);
  });

  test('checkIntegrity detects element deletion against real baseline', async function({ page }) {
    await page.goto(BASE_URL + '/circuitjs.html?ctz=' + CIRCUIT_CTZ + '&running=true');
    await waitForCircuitJS(page);
    await setupMessageCapture(page);

    await subscribe(page, {
      nodes: [],
      elements: [],
      rate: 60,
      permissions: { editableIndices: [], removableIndices: [], typeRules: [] },
    });

    await page.waitForFunction(
      () => window.__cjsReporting._baseline && window.__cjsReporting._baseline.info,
      null, { timeout: 15000 }
    );

    var result = await page.evaluate(function() {
      var rep = window.__cjsReporting;
      var baseline = rep._baseline;
      // Remove first element
      var current = baseline.info.slice(1).map(function(e) {
        return { typeCode: e.typeCode, coords: e.coords, paramSig: e.paramSig, apiType: e.apiType };
      });
      return rep.checkIntegrity(current, baseline);
    });

    expect(result).toBe(0);
  });

  test('checkIntegrity detects element addition against real baseline', async function({ page }) {
    await page.goto(BASE_URL + '/circuitjs.html?ctz=' + CIRCUIT_CTZ + '&running=true');
    await waitForCircuitJS(page);
    await setupMessageCapture(page);

    await subscribe(page, {
      nodes: [],
      elements: [],
      rate: 60,
      permissions: { editableIndices: [], removableIndices: [], typeRules: [] },
    });

    await page.waitForFunction(
      () => window.__cjsReporting._baseline && window.__cjsReporting._baseline.info,
      null, { timeout: 15000 }
    );

    var result = await page.evaluate(function() {
      var rep = window.__cjsReporting;
      var baseline = rep._baseline;
      var current = baseline.info.map(function(e) {
        return { typeCode: e.typeCode, coords: e.coords, paramSig: e.paramSig, apiType: e.apiType };
      });
      // Add a fake extra element
      current.push({
        typeCode: 'r', coords: '999 999 888 888',
        paramSig: '9999', apiType: 'ResistorElm',
      });
      return rep.checkIntegrity(current, baseline);
    });

    expect(result).toBe(0);
  });

  test('editable element can be changed against real baseline', async function({ page }) {
    await page.goto(BASE_URL + '/circuitjs.html?ctz=' + CIRCUIT_CTZ + '&running=true');
    await waitForCircuitJS(page);
    await setupMessageCapture(page);

    // Make element 0 editable
    await subscribe(page, {
      nodes: [],
      elements: [],
      rate: 60,
      permissions: { editableIndices: [0], removableIndices: [], typeRules: [] },
    });

    await page.waitForFunction(
      () => window.__cjsReporting._baseline && window.__cjsReporting._baseline.info,
      null, { timeout: 15000 }
    );

    var result = await page.evaluate(function() {
      var rep = window.__cjsReporting;
      var baseline = rep._baseline;
      var current = baseline.info.map(function(e) {
        return { typeCode: e.typeCode, coords: e.coords, paramSig: e.paramSig, apiType: e.apiType };
      });
      // Modify element 0 (editable)
      current[0].paramSig = 'CHANGED_VALUE';
      return rep.checkIntegrity(current, baseline);
    });

    expect(result).toBe(1);
  });

  test('buildElementInfo matches real circuit export to API elements', async function({ page }) {
    await page.goto(BASE_URL + '/circuitjs.html?ctz=' + CIRCUIT_CTZ + '&running=true');
    await waitForCircuitJS(page);

    var result = await page.evaluate(function() {
      var sim = window.CircuitJS1;
      var rep = window.__cjsReporting;
      var elems = sim.getElements();
      var exported = sim.exportCircuit();
      var info = rep.buildElementInfo(exported, elems);

      if (!info) return { success: false, reason: 'buildElementInfo returned null' };
      if (info.length !== elems.length) {
        return {
          success: false,
          reason: 'length mismatch: info=' + info.length + ' elems=' + elems.length,
        };
      }

      // Verify each element has consistent type info
      var mismatches = [];
      for (var i = 0; i < info.length; i++) {
        if (info[i].apiType !== elems[i].getType()) {
          mismatches.push({
            index: i,
            infoType: info[i].apiType,
            elemType: elems[i].getType(),
          });
        }
      }

      return {
        success: mismatches.length === 0,
        count: info.length,
        mismatches: mismatches,
        types: info.map(function(e) { return e.apiType; }),
      };
    });

    expect(result.success).toBe(true);
    expect(result.count).toBeGreaterThan(0);
  });
});
