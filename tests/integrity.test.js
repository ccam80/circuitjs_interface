/**
 * Tier 1: Unit tests for circuitjs-reporting.js integrity logic.
 *
 * Run: node --test tests/integrity.test.js
 *
 * Tests buildElementInfo (parsing) and checkIntegrity (violation detection)
 * with mock circuit data — no browser or CircuitJS needed.
 */
'use strict';

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const {
  buildElementInfo,
  checkIntegrity,
  buildTypeCounts,
  META_ONLY_PREFIXES,
} = require('../deploy/circuitjs-reporting.js');

// ── Helpers ──

/** Create a mock element with getPostCount() and getType(). */
function mockElem(apiType, postCount) {
  postCount = postCount || 2;
  return {
    getPostCount: function() { return postCount; },
    getType: function() { return apiType; },
  };
}

/**
 * Voltage divider circuit export text:
 *   $ ... (meta line)
 *   v 192 368 192 48 0 0 40 5 0 0 0.5   (voltage source)
 *   r 192 48 464 48 0 1000              (R1 = 1kΩ)
 *   r 464 48 464 368 0 2000             (R2 = 2kΩ)
 *   w 464 368 192 368 0                  (wire)
 */
const DIVIDER_EXPORT =
  '$ 1 0.000005 10.20027730826997 50 5 43 50\n' +
  'v 192 368 192 48 0 0 40 5 0 0 0.5\n' +
  'r 192 48 464 48 0 1000\n' +
  'r 464 48 464 368 0 2000\n' +
  'w 464 368 192 368 0\n';

const DIVIDER_ELEMS = [
  mockElem('VoltageElm'),   // v
  mockElem('ResistorElm'),  // r (1kΩ)
  mockElem('ResistorElm'),  // r (2kΩ)
  mockElem('WireElm'),      // w
];

/** Build a baseline object from element info. */
function makeBaseline(info, opts) {
  opts = opts || {};
  return {
    info: info,
    typeCounts: buildTypeCounts(info),
    editableIndices: opts.editableIndices || new Set(),
    removableIndices: opts.removableIndices || new Set(),
    typeRules: opts.typeRules || {},
  };
}

// ═══════════════════════════════════════════════════════════════
// buildElementInfo
// ═══════════════════════════════════════════════════════════════

describe('buildElementInfo', function() {

  it('parses a voltage divider circuit correctly', function() {
    var info = buildElementInfo(DIVIDER_EXPORT, DIVIDER_ELEMS);
    assert.ok(info, 'should return non-null');
    assert.equal(info.length, 4, 'should have 4 elements');

    // Voltage source: v 192 368 192 48 0 0 40 5 0 0 0.5
    // coords = fields[1..4], flags = fields[5], paramSig = fields[6:]
    assert.equal(info[0].typeCode, 'v');
    assert.equal(info[0].coords, '192 368 192 48');
    assert.equal(info[0].paramSig, '0 40 5 0 0 0.5');
    assert.equal(info[0].apiType, 'VoltageElm');

    // R1: r 192 48 464 48 0 1000
    assert.equal(info[1].typeCode, 'r');
    assert.equal(info[1].coords, '192 48 464 48');
    assert.equal(info[1].paramSig, '1000');
    assert.equal(info[1].apiType, 'ResistorElm');

    // R2: r 464 48 464 368 0 2000
    assert.equal(info[2].typeCode, 'r');
    assert.equal(info[2].coords, '464 48 464 368');
    assert.equal(info[2].paramSig, '2000');
    assert.equal(info[2].apiType, 'ResistorElm');

    // Wire: w 464 368 192 368 0
    assert.equal(info[3].typeCode, 'w');
    assert.equal(info[3].coords, '464 368 192 368');
    assert.equal(info[3].paramSig, '');
    assert.equal(info[3].apiType, 'WireElm');
  });

  it('filters meta lines ($ o 38 h &) but keeps wires', function() {
    var text =
      '$ 1 0.000005\n' +
      'o 0 1 2\n' +
      '38 some scope data\n' +
      'h some hint\n' +
      '& some adjustment\n' +
      'r 0 0 100 0 0 1000\n' +
      'w 100 0 200 0 0\n';
    var elems = [mockElem('ResistorElm'), mockElem('WireElm')];
    var info = buildElementInfo(text, elems);
    assert.ok(info, 'should return non-null');
    assert.equal(info.length, 2, 'only r and w lines should remain');
    assert.equal(info[0].typeCode, 'r');
    assert.equal(info[1].typeCode, 'w');
  });

  it('returns null on line/element count mismatch', function() {
    // 4 elements but only 3 non-meta lines
    var text =
      '$ 1 0.000005\n' +
      'r 0 0 100 0 0 1000\n' +
      'r 100 0 200 0 0 2000\n' +
      'w 200 0 300 0 0\n';
    var elems = [
      mockElem('ResistorElm'),
      mockElem('ResistorElm'),
      mockElem('WireElm'),
      mockElem('VoltageElm'),  // extra element
    ];
    var info = buildElementInfo(text, elems);
    assert.equal(info, null, 'mismatch should return null');
  });

  it('handles elements with >2 posts (e.g. transistor)', function() {
    // Transistor: t x1 y1 x2 y2 x3 y3 flags params...
    // 3 posts = 6 coordinate values
    var text = 't 100 200 300 400 500 600 0 1 -4.4 0.0 100\n';
    var elems = [mockElem('TransistorElm', 3)];
    var info = buildElementInfo(text, elems);
    assert.ok(info);
    assert.equal(info[0].typeCode, 't');
    // 3 posts × 2 coords = fields[1..6], flags = fields[7]
    assert.equal(info[0].coords, '100 200 300 400 500 600');
    // firstParamIndex = 2*3 + 2 = 8, fields[8..] = '1 -4.4 0.0 100'
    assert.equal(info[0].paramSig, '1 -4.4 0.0 100');
  });

  it('handles empty export text', function() {
    var info = buildElementInfo('', []);
    assert.ok(info, 'empty circuit should return empty array');
    assert.equal(info.length, 0);
  });

  it('handles export with only meta lines and no elements', function() {
    var info = buildElementInfo('$ 1 0.000005\no 0 1\n', []);
    assert.ok(info, 'only meta lines + 0 elems = valid empty match');
    assert.equal(info.length, 0);
  });
});

// ═══════════════════════════════════════════════════════════════
// buildTypeCounts
// ═══════════════════════════════════════════════════════════════

describe('buildTypeCounts', function() {
  it('counts element types correctly', function() {
    var info = buildElementInfo(DIVIDER_EXPORT, DIVIDER_ELEMS);
    var counts = buildTypeCounts(info);
    assert.equal(counts['VoltageElm'], 1);
    assert.equal(counts['ResistorElm'], 2);
    assert.equal(counts['WireElm'], 1);
  });
});

// ═══════════════════════════════════════════════════════════════
// checkIntegrity
// ═══════════════════════════════════════════════════════════════

describe('checkIntegrity', function() {

  it('returns 1 when baseline is null (no checking)', function() {
    var result = checkIntegrity([], null);
    assert.equal(result, 1);
  });

  it('returns 1 when baseline.info is null', function() {
    var result = checkIntegrity([], { info: null });
    assert.equal(result, 1);
  });

  // ── All locked (empty permissions = everything locked) ──

  it('ALL LOCKED: identical circuit passes', function() {
    var info = buildElementInfo(DIVIDER_EXPORT, DIVIDER_ELEMS);
    var baseline = makeBaseline(info);
    // Current is the same as baseline
    var current = info.map(function(e) { return Object.assign({}, e); });
    assert.equal(checkIntegrity(current, baseline), 1);
  });

  it('ALL LOCKED: changing a resistor value fails', function() {
    var info = buildElementInfo(DIVIDER_EXPORT, DIVIDER_ELEMS);
    var baseline = makeBaseline(info);
    var current = info.map(function(e) { return Object.assign({}, e); });
    // Change R1 (index 1) from 1000 to 5000
    current[1].paramSig = '5000';
    assert.equal(checkIntegrity(current, baseline), 0);
  });

  it('ALL LOCKED: changing voltage source params fails', function() {
    var info = buildElementInfo(DIVIDER_EXPORT, DIVIDER_ELEMS);
    var baseline = makeBaseline(info);
    var current = info.map(function(e) { return Object.assign({}, e); });
    current[0].paramSig = '40 10 0 0 0.5';  // changed amplitude
    assert.equal(checkIntegrity(current, baseline), 0);
  });

  it('ALL LOCKED: deleting an element fails', function() {
    var info = buildElementInfo(DIVIDER_EXPORT, DIVIDER_ELEMS);
    var baseline = makeBaseline(info);
    // Remove R2 (index 2)
    var current = info.filter(function(_, i) { return i !== 2; });
    assert.equal(checkIntegrity(current, baseline), 0);
  });

  it('ALL LOCKED: adding a resistor fails (no type rule)', function() {
    var info = buildElementInfo(DIVIDER_EXPORT, DIVIDER_ELEMS);
    var baseline = makeBaseline(info);
    var current = info.map(function(e) { return Object.assign({}, e); });
    // Add a new resistor at different coords
    current.push({
      typeCode: 'r', coords: '0 0 100 0',
      paramSig: '3000', apiType: 'ResistorElm',
    });
    assert.equal(checkIntegrity(current, baseline), 0);
  });

  it('ALL LOCKED: adding a new type fails', function() {
    var info = buildElementInfo(DIVIDER_EXPORT, DIVIDER_ELEMS);
    var baseline = makeBaseline(info);
    var current = info.map(function(e) { return Object.assign({}, e); });
    current.push({
      typeCode: 'c', coords: '0 0 100 0',
      paramSig: '0.000001', apiType: 'CapacitorElm',
    });
    assert.equal(checkIntegrity(current, baseline), 0);
  });

  // ── Editable elements ──

  it('EDITABLE: changing an editable element passes', function() {
    var info = buildElementInfo(DIVIDER_EXPORT, DIVIDER_ELEMS);
    var baseline = makeBaseline(info, { editableIndices: new Set([1]) });
    var current = info.map(function(e) { return Object.assign({}, e); });
    current[1].paramSig = '5000';  // R1 is editable, change it
    assert.equal(checkIntegrity(current, baseline), 1);
  });

  it('EDITABLE: changing a non-editable element still fails', function() {
    var info = buildElementInfo(DIVIDER_EXPORT, DIVIDER_ELEMS);
    var baseline = makeBaseline(info, { editableIndices: new Set([1]) });
    var current = info.map(function(e) { return Object.assign({}, e); });
    current[2].paramSig = '5000';  // R2 is NOT editable
    assert.equal(checkIntegrity(current, baseline), 0);
  });

  // ── Removable elements ──

  it('REMOVABLE: removing a removable element passes', function() {
    var info = buildElementInfo(DIVIDER_EXPORT, DIVIDER_ELEMS);
    var baseline = makeBaseline(info, { removableIndices: new Set([1]) });
    // Remove R1 (index 1)
    var current = info.filter(function(_, i) { return i !== 1; });
    assert.equal(checkIntegrity(current, baseline), 1);
  });

  it('REMOVABLE: removing a non-removable element fails', function() {
    var info = buildElementInfo(DIVIDER_EXPORT, DIVIDER_ELEMS);
    var baseline = makeBaseline(info, { removableIndices: new Set([1]) });
    // Remove R2 (index 2) — not removable
    var current = info.filter(function(_, i) { return i !== 2; });
    assert.equal(checkIntegrity(current, baseline), 0);
  });

  // ── Type rules ──

  it('TYPE RULE: adding within maxAdd passes', function() {
    var info = buildElementInfo(DIVIDER_EXPORT, DIVIDER_ELEMS);
    var baseline = makeBaseline(info, {
      typeRules: { ResistorElm: { maxAdd: 1, maxRemove: 0 } },
    });
    var current = info.map(function(e) { return Object.assign({}, e); });
    current.push({
      typeCode: 'r', coords: '0 0 100 0',
      paramSig: '3000', apiType: 'ResistorElm',
    });
    assert.equal(checkIntegrity(current, baseline), 1);
  });

  it('TYPE RULE: adding beyond maxAdd fails', function() {
    var info = buildElementInfo(DIVIDER_EXPORT, DIVIDER_ELEMS);
    var baseline = makeBaseline(info, {
      typeRules: { ResistorElm: { maxAdd: 1, maxRemove: 0 } },
    });
    var current = info.map(function(e) { return Object.assign({}, e); });
    // Add 2 resistors (exceeds maxAdd=1)
    current.push({
      typeCode: 'r', coords: '0 0 100 0',
      paramSig: '3000', apiType: 'ResistorElm',
    });
    current.push({
      typeCode: 'r', coords: '0 100 100 100',
      paramSig: '4000', apiType: 'ResistorElm',
    });
    assert.equal(checkIntegrity(current, baseline), 0);
  });

  it('TYPE RULE: removing beyond removable + maxRemove fails', function() {
    var info = buildElementInfo(DIVIDER_EXPORT, DIVIDER_ELEMS);
    // R1 (index 1) is removable, but maxRemove=0 means no extra removals
    var baseline = makeBaseline(info, {
      removableIndices: new Set([1]),
      typeRules: { ResistorElm: { maxAdd: 0, maxRemove: 0 } },
    });
    // Remove BOTH resistors (only 1 is removable, excess=1 > maxRemove=0)
    var current = info.filter(function(e) { return e.apiType !== 'ResistorElm'; });
    assert.equal(checkIntegrity(current, baseline), 0);
  });

  it('TYPE RULE: removing non-removable fails even with maxRemove (per-element check first)', function() {
    var info = buildElementInfo(DIVIDER_EXPORT, DIVIDER_ELEMS);
    var baseline = makeBaseline(info, {
      removableIndices: new Set([1]),
      typeRules: { ResistorElm: { maxAdd: 0, maxRemove: 1 } },
    });
    // Remove both resistors: R1 (index 1) is removable, R2 (index 2) is NOT
    // Per-element check catches R2 deletion before type rule is evaluated
    var current = info.filter(function(e) { return e.apiType !== 'ResistorElm'; });
    assert.equal(checkIntegrity(current, baseline), 0);
  });

  it('REMOVABLE: removing all removable elements of a type passes', function() {
    var info = buildElementInfo(DIVIDER_EXPORT, DIVIDER_ELEMS);
    // Both resistors are removable
    var baseline = makeBaseline(info, {
      removableIndices: new Set([1, 2]),
    });
    var current = info.filter(function(e) { return e.apiType !== 'ResistorElm'; });
    assert.equal(checkIntegrity(current, baseline), 1);
  });

  it('TYPE RULE: new type with rule passes within limit', function() {
    var info = buildElementInfo(DIVIDER_EXPORT, DIVIDER_ELEMS);
    var baseline = makeBaseline(info, {
      typeRules: { CapacitorElm: { maxAdd: 2, maxRemove: 0 } },
    });
    var current = info.map(function(e) { return Object.assign({}, e); });
    current.push({
      typeCode: 'c', coords: '0 0 100 0',
      paramSig: '0.000001', apiType: 'CapacitorElm',
    });
    assert.equal(checkIntegrity(current, baseline), 1);
  });

  it('TYPE RULE: new type exceeding maxAdd fails', function() {
    var info = buildElementInfo(DIVIDER_EXPORT, DIVIDER_ELEMS);
    var baseline = makeBaseline(info, {
      typeRules: { CapacitorElm: { maxAdd: 1, maxRemove: 0 } },
    });
    var current = info.map(function(e) { return Object.assign({}, e); });
    current.push({
      typeCode: 'c', coords: '0 0 100 0',
      paramSig: '0.000001', apiType: 'CapacitorElm',
    });
    current.push({
      typeCode: 'c', coords: '0 100 100 100',
      paramSig: '0.000002', apiType: 'CapacitorElm',
    });
    assert.equal(checkIntegrity(current, baseline), 0);
  });

  // ── Coords-based matching (not index-based) ──

  it('COORDS: element reordering does not break integrity', function() {
    var info = buildElementInfo(DIVIDER_EXPORT, DIVIDER_ELEMS);
    var baseline = makeBaseline(info);
    // Reverse the order — same elements, different indices
    var current = info.map(function(e) { return Object.assign({}, e); }).reverse();
    assert.equal(checkIntegrity(current, baseline), 1);
  });

  it('COORDS: same type at different coords is a different element', function() {
    var info = buildElementInfo(DIVIDER_EXPORT, DIVIDER_ELEMS);
    var baseline = makeBaseline(info);
    var current = info.map(function(e) { return Object.assign({}, e); });
    // Move R1 to different coords (= delete old + add new of same type)
    current[1].coords = '999 999 888 888';
    // This should fail: original coords not found, and new element adds 1
    // to ResistorElm count while removing 1 — net zero but original is gone
    assert.equal(checkIntegrity(current, baseline), 0);
  });

  // ── Combined scenarios ──

  it('COMBINED: edit an editable element + add via type rule passes', function() {
    var info = buildElementInfo(DIVIDER_EXPORT, DIVIDER_ELEMS);
    var baseline = makeBaseline(info, {
      editableIndices: new Set([1]),  // R1 editable
      typeRules: { ResistorElm: { maxAdd: 1, maxRemove: 0 } },
    });
    var current = info.map(function(e) { return Object.assign({}, e); });
    current[1].paramSig = '5000';  // edit R1
    current.push({
      typeCode: 'r', coords: '0 0 100 0',
      paramSig: '3000', apiType: 'ResistorElm',
    });  // add a resistor
    assert.equal(checkIntegrity(current, baseline), 1);
  });

  it('COMBINED: edit non-editable + add via rule still fails', function() {
    var info = buildElementInfo(DIVIDER_EXPORT, DIVIDER_ELEMS);
    var baseline = makeBaseline(info, {
      editableIndices: new Set([1]),  // only R1 editable
      typeRules: { ResistorElm: { maxAdd: 1, maxRemove: 0 } },
    });
    var current = info.map(function(e) { return Object.assign({}, e); });
    current[2].paramSig = '5000';  // edit R2 — NOT editable
    assert.equal(checkIntegrity(current, baseline), 0);
  });
});
