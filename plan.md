# Falstad CircuitJS1 + STACK Graded Integration

## Live URLs

| Resource | URL |
|----------|-----|
| GitHub repo | https://github.com/ccam80/circuitjs-moodle |
| Bridge test page | https://ccam80.github.io/circuitjs-moodle/test-bridge.html |
| Bridge (for embedding) | https://ccam80.github.io/circuitjs-moodle/bridge.html |
| Simulator standalone | https://ccam80.github.io/circuitjs-moodle/circuitjs.html |
| STACK template | `deploy/stack-question-template.txt` (local reference) |

## Status

### Done
- [x] Built CircuitJS1 from pfalstad/circuitjs1 source (Java 21 + Gradle 8.7 + GWT 2.8.2)
- [x] Created `bridge.html` — loads simulator same-origin, reads values via JS API, relays via postMessage
- [x] Created `test-bridge.html` — standalone test page using bundled jsinterface.txt demo circuit
- [x] Created `stack-question-template.txt` — copy-paste reference for STACK question fields
- [x] Deployed to GitHub Pages at ccam80.github.io/circuitjs-moodle
- [x] Cleaned up build artifacts (Gradle, SEVA77 extract, source repo)

### Bug fix applied
- bridge.html v1 had a race condition: `oncircuitjsloaded` callback set on `frame.contentWindow` was lost when iframe navigation replaced the window context
- Fix: poll + re-set callback every 300ms until CircuitJS1 object detected
- Also: controls (sidebar, menu) now shown by default instead of hidden; test page uses real bundled circuit instead of fabricated ctz value

### Bridge test: PASSED (2025-02-13)
- [x] Simulator loads, vsense voltage values stream to readout div
- Note: jsinterface.txt demo circuit runs slowly (8-bit counter, dozens of gates — expected for a heavy circuit)
- Note: extsin element crashes on double-click (it's an API-driven external voltage, not editable via UI — CircuitJS1 quirk, not our bug)
- Note: only vsense appeared because that's all the URL requested; D0-D7 exist but weren't in `nodes=` param

### Version mismatch fix (2025-02-13) — WRONG DIRECTORY
- Building from GitHub source produced GWT output that was older than falstad.com's live version
- First fix attempt: downloaded GWT files from `falstad.com/circuit/circuitjs1/` — this is an OLD build (GWT 2.7.0, 5 browser permutations including IE8/9/10, ~360 KB per permutation)
- **Root cause**: the live site loads from `falstad.com/circuit/circuitjs101/` (GWT 2.11.0, 2 modern-browser permutations, ~770 KB per permutation)
- Old GWT build lacks support for newer circuit element types → stampCircuit() on any circuit created with the current Falstad site
- Also found: bridge.html used `URLSearchParams` which converts `+` to space — corrupts lz-string ctz values that contain `+` as a valid data character

### Fix applied (2025-02-13)
- [x] Downloaded correct GWT files from `circuitjs101/` on falstad.com (GWT 2.11.0)
- [x] Updated circuitjs.html to reference `circuitjs101/circuitjs1.nocache.js`
- [x] Fixed bridge.html: replaced `URLSearchParams` with raw query string parsing (`decodeURIComponent` preserves `+`)
- [x] Removed old `circuitjs1/` directory (GWT 2.7.0)
- **Lesson**: falstad.com has multiple GWT build directories — `circuitjs101/` is current, `circuitjs1/` is legacy

### Multi-measurement support added (2026-02-15)
- [x] `bridge.html`: added `measures=` URL parameter for label-based element extraction
  - Supports properties: `current`, `voltageDiff`, `voltage0`, `voltage1`, `power`
  - `labelMap` built from `onanalyze` callback, maps element labels to element objects
  - Power computed client-side as `getVoltageDiff() * getCurrent()`
  - Backward compatible: existing `nodes=` and `elements=` params unchanged
- [x] `test-bridge.html`: updated to demonstrate `measures=R1:current,R1:voltageDiff,R1:power` alongside `nodes=`
  - Readout now shows unit-appropriate suffixes (V, A, W) based on key
- [x] `stack-question-template.txt`: expanded with 4 examples
  - Example 1: voltage measurement (original)
  - Example 2: current measurement via labeled element
  - Example 3: multi-value grading (voltage + current, 2 STACK inputs, 2 PRTs)
  - Example 4: impedance calculation (V/I computed in Maxima feedback variables)
  - Added "Supported Measurement Properties" reference table
- [x] `tools/question_generator.py`: fully rewritten for multi-measurement support
  - `Measurement` dataclass: label, property, target, tolerance, graded
  - GUI: QTableWidget replaces old single-value grading (Label, Type, Target, Tol, Grade columns)
  - XML generation: N inputs + N PRTs, one per graded measurement, weighted equally
  - Settings: JSON serialization with migration from old single-node format
  - "Use Value as Target" works on currently selected table row

### Current: deploy and test
- [ ] Push changes to GitHub Pages
- [ ] Test `measures=` parameter with a circuit that has labeled elements
- [ ] Test question generator GUI launches and produces valid XML
- [ ] Create first current-graded STACK question

## Architecture

```
test-bridge.html (or STACK-JS iframe)
  │  listens for postMessage events
  │
  └── bridge.html  (GitHub Pages origin)
       │  reads URL params: ctz/cct/startCircuit, nodes, elements, rate
       │  polls for CircuitJS1 JS API object
       │  reads node voltages / element currents on each sim update
       │  posts {type: 'circuitjs-data', values: {...}} to parent
       │
       └── circuitjs.html  (same origin — JS API accessible)
            └── GWT-compiled CircuitJS1 simulator
```

### Why the nesting matters

The CircuitJS1 JS API requires **same-origin** between the calling page and the simulator iframe. bridge.html and circuitjs.html share the same GitHub Pages origin, so the API works. Communication out to the STACK-JS sandbox (which has a blob: origin) uses postMessage, which works cross-origin by design.

## bridge.html Parameters

| Param | Default | Purpose |
|-------|---------|---------|
| `ctz` | (none) | Compressed circuit data from Falstad "Export As Link" |
| `cct` | (none) | Raw circuit text |
| `startCircuit` | (none) | Filename from bundled circuits/ directory |
| `nodes` | (none) | Comma-separated labeled node names to read (e.g. `vout,vmid`) |
| `elements` | (none) | Element index:property pairs (e.g. `3:current,5:voltageDiff`) — legacy |
| `measures` | (none) | Label-based element measures (e.g. `R1:current,R1:power`) — preferred |
| `rate` | `4` | postMessage events per second |
| `editable` | `true` | Whether student can edit the circuit |
| `whiteBackground` | `false` | White background |
| `hideSidebar` | `false` | Hide the right sidebar |
| `hideMenu` | `false` | Hide the top menu bar |

## Next Steps (in order)

### 1. Verify the bridge works (you, in browser)
Open the test page. If voltage values stream into the readout, the bridge layer is proven. If not, check browser DevTools Console — the error will indicate whether it's a loading issue, same-origin issue, or API issue.

### 2. Test with your own circuit
1. Open https://falstad.com/circuit/circuitjs.html
2. Build or load a circuit
3. Right-click the wire/node you want to grade → Edit → set a label (e.g. `vout`)
4. File → Export As Link
5. Copy the `ctz=` value from the generated URL
6. Visit: `https://ccam80.github.io/circuitjs-moodle/test-bridge.html` — but modify the URL in the source to point at your circuit (or just edit the iframe src in DevTools)

### 3. Create first STACK question in Moodle
Follow `deploy/stack-question-template.txt`. Key fields:
- Question variables: `target_voltage: 3.3; tol: 0.1;`
- Question text: the `[[iframe]]` block with bridge URL + your ctz
- Input ans1: hidden algebraic input
- PRT: NumAbsolute test

### 4. Test STACK-JS ↔ bridge communication
This is the riskiest step. The STACK-JS sandbox iframe has a blob: origin. The bridge.html iframe inside it has the GitHub Pages origin. postMessage should cross this boundary, but test it:
- Does the STACK-JS iframe receive the postMessage events?
- Does `stack_js.request_access_to_input("ans1")` resolve?
- Does the hidden ans1 input receive the voltage value?

Use STACK's question preview/debug mode to inspect ans1.

### 5. If STACK-JS nesting doesn't work
Fallback options (in order of preference):
1. **Load bridge.html directly as the STACK `[[iframe]]` src** — the `[[iframe]]` block might support an explicit src attribute
2. **Upload CircuitJS1 as a Moodle File resource** — makes it same-origin with Moodle, might avoid framing issues
3. **Use `[[javascript]]` block instead of `[[iframe]]`** — runs JS in the Moodle page context, but more limited

### 6. Deploy to both live courses
Build one pilot question per course. Test with a colleague or test student account before going live.

## Directory Layout

```
C:/local_working_projects/falstad_stack_integration/
├── plan.md                    ← this file
└── deploy/                    ← git repo → github.com/ccam80/circuitjs-moodle
    ├── bridge.html            ← custom: postMessage relay
    ├── test-bridge.html       ← custom: standalone test page
    ├── stack-question-template.txt  ← STACK question copy-paste reference
    ├── circuitjs.html         ← CircuitJS1 entry point
    ├── circuitjs1/            ← GWT-compiled simulator + bundled circuits
    ├── font/                  ← UI icon fonts
    ├── doc/                   ← CircuitJS1 documentation (incl. js-interface)
    └── (other CircuitJS1 support files)
```

## Key References

- [CircuitJS1 JS API](https://www.falstad.com/circuit/doc/js-interface.html)
- [STACK-JS documentation](https://docs.stack-assessment.org/en/Developer/STACK-JS/)
- [STACK JSXGraph binding patterns](https://docs.stack-assessment.org/en/Specialist_tools/JSXGraph/Binding/)
- [CircuitJS1 source (pfalstad)](https://github.com/pfalstad/circuitjs1)
