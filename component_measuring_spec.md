# Multi-Measurement Support Specification

## Overview

Extends the CircuitJS1 + STACK integration from voltage-only grading to support
current, voltage-across, power, and impedance measurements via label-based
element lookup.

## Measurement Types

| Property     | Bridge Key              | Unit | API Method                            |
|--------------|-------------------------|------|---------------------------------------|
| nodeVoltage  | `"labelName"`           | V    | `sim.getNodeVoltage(name)`            |
| current      | `"label:current"`       | A    | `element.getCurrent()`                |
| voltageDiff  | `"label:voltageDiff"`   | V    | `element.getVoltageDiff()`            |
| voltage0     | `"label:voltage0"`      | V    | `element.getVoltage(0)`               |
| voltage1     | `"label:voltage1"`      | V    | `element.getVoltage(1)`               |
| power        | `"label:power"`         | W    | `getVoltageDiff() * getCurrent()`     |

Node voltages use the `nodes=` URL parameter and `getNodeVoltage()`.
Element measures use the `measures=` URL parameter and label-based element lookup.
Both can be combined in one bridge URL.

Impedance is computed in the STACK Maxima layer as `Z = V / I` from two
separate measurements — no dedicated bridge property needed.

---

## File Changes

### 1. `deploy/bridge.html`

**New URL parameter:** `measures=<label>:<property>,<label>:<property>,...`

Example: `bridge.html?ctz=...&nodes=vout&measures=R1:current,R1:power&rate=2`

**Changes:**

- Parse `measures` parameter (alongside existing `nodes` and `elements`).
- Declare `labelMap = {}` in the IIFE scope — maps element label strings to
  CircuitJS1 element objects.
- In `sim.onanalyze`: rebuild `labelMap` by iterating all elements and calling
  `getLabelName()` on each. This runs on circuit load and whenever the circuit
  is edited.
- In `sim.onupdate`: after the existing `nodes` and `elements` extraction
  blocks, add a new loop over `measures`. For each entry, split on `:` to get
  `(label, property)`, look up the element in `labelMap`, and call the
  appropriate API method. Power is computed as `getVoltageDiff() * getCurrent()`.
  Results are added to the same `data.values` object that gets postMessaged.
- All existing parameters (`nodes=`, `elements=`, `rate`, `editable`, etc.)
  continue to work unchanged.

### 2. `deploy/test-bridge.html`

- Bridge iframe `src` updated to include `measures=R1:current,R1:voltageDiff,R1:power`
  alongside the existing `nodes=AC,filt,out`.
- Added `unitForKey(key)` helper that returns `'A'` for `:current` keys, `'W'`
  for `:power` keys, and `'V'` otherwise.
- Readout display uses unit-appropriate suffixes.
- Note in page text explains that `measures=` values will be null if the test
  circuit lacks matching labeled elements.

### 3. `deploy/stack-question-template.txt`

Expanded from a single example to four, plus a reference table:

- **Example 1** — Voltage measurement (original, unchanged)
- **Example 2** — Current measurement via `measures=R1:current`
- **Example 3** — Multi-value grading (voltage + current): two STACK inputs
  (`ans1`, `ans2`), two PRTs (`prt1`, `prt2`), each weighted 0.5
- **Example 4** — Impedance/resistance: reads `R1:voltageDiff` and `R1:current`
  into `ans1`/`ans2`, computes `measured_Z: ans1 / ans2` in PRT feedback
  variables, tests against target
- **Reference table** — all supported measurement properties with keys, units,
  and descriptions
- **Notes 7–9** — how to label elements for current grading, impedance pattern,
  why `measures=` is preferred over `elements=`

### 4. `tools/question_generator.py`

Full rewrite of the XML generation and GUI to support N measurements of any type.

#### Data model

```python
@dataclass
class Measurement:
    label: str          # Falstad label (node name or element label)
    property: str       # 'nodeVoltage', 'current', 'voltageDiff', 'power'
    target: float       # Expected value
    tolerance: float    # Absolute tolerance
    graded: bool        # Whether this measurement has a PRT

    def unit(self) -> str        # 'V', 'A', 'W'
    def data_key(self) -> str    # 'vout' or 'R1:current'
    def display_name(self) -> str  # 'V_vout' or 'I_R1'
    def input_name(self, index) -> str  # 'ans1', 'ans2', ...
```

#### XML generation (`generate_xml`)

Signature changed from:

```python
generate_xml(name, description, ctz, nodes, grade_node,
             target_voltage, tolerance, ...)
```

To:

```python
generate_xml(name, description, ctz, measurements, ...)
```

Where `measurements` is a `list[Measurement]`.

Key behaviors:

- **Bridge URL** — `nodes=` and `measures=` derived from measurements
  automatically. `nodeVoltage` entries go into `nodes=`; all others go into
  `measures=`.
- **Question variables** — one `target_ansN` and `tol_ansN` per graded
  measurement.
- **STACK inputs** — one `<input>` block per graded measurement (`ans1`,
  `ans2`, ...).
- **PRTs** — one `<prt>` per graded measurement, each weighted `1/N`. Each PRT
  uses `NumAbsolute` to compare `ansN` against `target_ansN` with
  tolerance `tol_ansN`.
- **Specific feedback** — references all PRTs: `[[feedback:prt1]][[feedback:prt2]]...`
- **Test cases** — "all correct" and "all wrong" cases covering all graded inputs.
- **JS block** — requests `stack_js` access to each graded input, reads values
  from `event.data.values` by data key, writes to corresponding STACK inputs.
- **Readout HTML** — displays all measurements with appropriate unit and
  "(graded)" marker.

#### GUI (`MainWindow`)

**Removed:**
- `nodes_edit` (QLineEdit for comma-separated node labels)
- `grade_combo` (QComboBox for selecting which node to grade)
- `target_spin` / `tol_spin` (single target voltage and tolerance)

**Added: Measurements table** (QTableWidget with 6 columns):

| Column    | Widget         | Purpose                                 |
|-----------|----------------|-----------------------------------------|
| Label     | QLineEdit      | Falstad label (e.g., `vout`, `R1`)      |
| Type      | QComboBox      | Node Voltage / Current / Voltage Across / Power |
| Target    | QDoubleSpinBox | Expected value (unit suffix auto-updates)|
| Tolerance | QDoubleSpinBox | Absolute tolerance                      |
| Grade     | QCheckBox      | Whether to grade this measurement       |
| (remove)  | QPushButton    | Delete this row                         |

- **[+ Add Measurement]** button adds a new row with defaults.
- **Type dropdown change** updates the unit suffix on Target and Tolerance
  spinboxes (V, A, W).
- **"Use Value as Target"** (simulator tab) sets the target of the currently
  selected table row from the latest simulator value.

#### Settings persistence

- New format: `measurements_json` — JSON array of measurement dicts via
  `dataclasses.asdict()`.
- **Migration from old format**: if `measurements_json` is absent but `nodes`
  exists, reconstructs measurements from old `nodes`, `grade_node`, `target`,
  `tolerance` settings. Old node-voltage measurements are created with
  `graded=True` only for the previously selected grade node.

#### Helper functions

- `_derive_bridge_params(measurements)` — splits measurements into `(nodes_list, measures_list)` for the bridge URL.
- `_build_bridge_url(ctz, nodes, measures, ...)` — now accepts both `nodes` and `measures` lists.
- `_build_readout_html(measurements)` — generates HTML readout with subscripted labels and units.
- `_build_js_block(measurements)` — generates the `[[script]]` block with per-input access requests and value routing.

---

## Usage Patterns

### Grading current through a component

1. In Falstad, right-click the component -> Edit -> set Label (e.g., `R1`)
2. In the question generator, add a measurement: Label=`R1`, Type=`Current`
3. Bridge URL gets `measures=R1:current`
4. Value arrives as `event.data.values['R1:current']` in amps

### Grading impedance / resistance

1. Add two measurements for the same element: `R1:voltageDiff` and `R1:current`
2. Both are graded, writing to `ans1` and `ans2`
3. In the STACK PRT feedback variables: `measured_Z: ans1 / ans2;`
4. Test `measured_Z` against `target_Z` with `NumAbsolute`

### Multi-value grading

Add multiple graded measurements. Each gets its own STACK input and PRT node.
PRT weights are `1/N` so the total grade sums to 1. Partial credit is automatic
(e.g., correct voltage but wrong current = 50%).

---

## Limitations

- **AC impedance (magnitude + phase)** requires time-series FFT or zero-crossing
  analysis — not supported. The `Z = V/I` pattern works for DC resistance and
  steady-state AC magnitude only.
- **`getLabelName()` availability** — documented for `LabeledNodeElm` but
  appears to work on most element types. If an element type doesn't support it,
  the `try/catch` in `onanalyze` handles it gracefully (label stays empty).
- **Label uniqueness** — if two elements share the same label, `labelMap`
  stores only the last one. Labels should be unique per circuit.
- **`elements=` (index-based)** is retained for backward compatibility but is
  fragile — indices change when the circuit is edited. Prefer `measures=`.
