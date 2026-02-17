"""CircuitJS1 STACK Question Generator

Qt GUI for generating Moodle-importable STACK question XML
with embedded CircuitJS1 circuit simulations.

Supports multiple measurement types: node voltage, element current,
voltage across element, and power.

Usage:
    .venv/Scripts/python.exe tools/question_generator.py
"""

import sys
import re
import json
from pathlib import Path
from dataclasses import dataclass, asdict

from lzstring import LZString

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QGroupBox, QLabel, QLineEdit, QTextEdit, QPlainTextEdit,
    QDoubleSpinBox, QCheckBox, QComboBox, QPushButton,
    QFileDialog, QMessageBox, QTableWidget, QHeaderView,
    QAbstractItemView, QSplitter,
)
from PyQt6.QtCore import Qt, QSettings, QTimer, QUrl
from PyQt6.QtGui import QFont

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    HAS_WEBENGINE = True
except ImportError:
    HAS_WEBENGINE = False

SIM_BASE_URL = "https://ccam80.github.io/circuitjs-moodle/circuitjs.html"
RATE_DEFAULT = 2

# ---------------------------------------------------------------------------
# Measurement types
# ---------------------------------------------------------------------------

NODE_PROPERTIES = ['nodeVoltage']
ELEMENT_PROPERTIES = ['current', 'voltageDiff', 'power']
PROPERTIES = NODE_PROPERTIES + ELEMENT_PROPERTIES

PROPERTY_DISPLAY = {
    'nodeVoltage': 'Node Voltage',
    'current': 'Current',
    'voltageDiff': 'Voltage Across',
    'power': 'Power',
}

PROPERTY_UNITS = {
    'nodeVoltage': 'V',
    'current': 'A',
    'voltageDiff': 'V',
    'power': 'W',
}

PROPERTY_PREFIX = {
    'nodeVoltage': 'V',
    'current': 'I',
    'voltageDiff': 'V',
    'power': 'P',
}

SOURCE_NODE = 'node'
SOURCE_ELEMENT = 'element'
SOURCE_EXPRESSION = 'expression'

# Units for common Falstad element types (first parameter value)
ELEMENT_TYPE_UNITS = {
    'ResistorElm': 'Ω', 'CapacitorElm': 'F', 'InductorElm': 'H',
    'VoltageElm': 'V', 'CurrentElm': 'A', 'DiodeElm': '',
    'PotElm': 'Ω', 'VarRailElm': 'V', 'RailElm': 'V',
}

# Non-element line prefixes in Falstad export format
_EXPORT_NON_ELEMENT = {'$', 'w', 'o', '38', 'h', '&'}

# Meta-only prefixes (keeps wire lines to maintain index alignment with
# the API element list, which includes wires)
_EXPORT_META_ONLY = {'$', 'o', '38', 'h', '&'}

ELEMENT_LABEL_PREFIX = {
    'ResistorElm': 'R', 'CapacitorElm': 'C', 'InductorElm': 'L',
    'VoltageElm': 'V', 'CurrentElm': 'I', 'DiodeElm': 'D',
    'PotElm': 'P', 'RailElm': 'Vr', 'VarRailElm': 'Vr',
    'OpAmpElm': 'U', 'TransistorElm': 'Q', 'MosfetElm': 'M',
    'SwitchElm': 'S', 'Switch2Elm': 'S', 'ZenerElm': 'Dz',
    'LEDElm': 'LED', 'TransformerElm': 'T',
}


def _si_format(value, unit=''):
    """Format a numeric value with SI prefix and unit."""
    try:
        val = float(value)
    except (ValueError, TypeError):
        return str(value)
    if val == 0:
        return f'0 {unit}'.strip()
    abs_val = abs(val)
    for threshold, prefix in [(1e12, 'T'), (1e9, 'G'), (1e6, 'M'),
                               (1e3, 'k'), (1, ''), (1e-3, 'm'),
                               (1e-6, '\u03bc'), (1e-9, 'n'),
                               (1e-12, 'p')]:
        if abs_val >= threshold:
            return f'{val / threshold:.4g} {prefix}{unit}'.strip()
    return f'{val:.4g} {unit}'.strip()


# For some element types, the "main value" isn't the first parameter.
# This maps type code -> offset from firstParamIndex to the main value.
_VALUE_PARAM_OFFSET = {
    'v': 1,   # VoltageElm: waveform maxVoltage freq ... → skip waveform
    'R': 1,   # RailElm: waveform maxVoltage freq ... → skip waveform
    '174': 1, # PotElm: resistance position → show resistance (first is fine)
}


def _parse_element_values(export_text, elements):
    """Extract the primary parameter value for each element from export text.

    Returns a list parallel to elements with human-readable value strings.
    Uses _EXPORT_META_ONLY (not _EXPORT_NON_ELEMENT) to keep wire lines,
    maintaining 1:1 alignment with the element index list from the API.
    """
    lines = []
    for line in export_text.split('\n'):
        line = line.strip()
        if not line:
            continue
        prefix = line.split(' ', 1)[0]
        if prefix not in _EXPORT_META_ONLY:
            lines.append(line)

    values = []
    for i, elem in enumerate(elements):
        if i >= len(lines):
            values.append('')
            continue
        fields = lines[i].split(' ')
        type_code = fields[0]
        posts = elem.get('posts', 2)
        # Element format: type (x y)*posts flags param1 param2 ...
        first_param_idx = 2 * posts + 2  # +1 type, +1 flags
        value_offset = _VALUE_PARAM_OFFSET.get(type_code, 0)
        param_idx = first_param_idx + value_offset
        if param_idx < len(fields):
            raw = fields[param_idx]
            unit = ELEMENT_TYPE_UNITS.get(elem.get('type', ''), '')
            values.append(_si_format(raw, unit))
        else:
            values.append('')
    return values


def _assign_element_labels(elements):
    """Assign labels to non-wire elements.

    Prefers user-defined labels from CircuitJS (via getLabelName API).
    Falls back to auto-generated labels (R1, R2, C1, ...) for unlabeled
    elements, avoiding collisions with user-defined labels.

    Returns (label_map, index_to_label):
      label_map:     {'R1': 0, 'R_load': 3, ...}  label -> element index
      index_to_label: {0: 'R1', 3: 'R_load', ...}  element index -> label
    """
    # First pass: collect user-defined labels
    user_labels = {}   # index -> label
    used_labels = set()
    for elem in elements:
        if elem.get('type', '') == 'WireElm':
            continue
        lbl = elem.get('label', '')
        if lbl:
            user_labels[elem['index']] = lbl
            used_labels.add(lbl)

    # Second pass: auto-generate for elements without user labels
    by_type = {}
    for elem in elements:
        if elem.get('type', '') == 'WireElm':
            continue
        if elem['index'] in user_labels:
            continue
        by_type.setdefault(elem.get('type', ''), []).append(elem['index'])

    label_map = {}
    index_to_label = {}

    # Register user-defined labels first
    for idx, lbl in user_labels.items():
        label_map[lbl] = idx
        index_to_label[idx] = lbl

    # Auto-generate remaining, skipping labels already taken
    for etype in sorted(by_type):
        prefix = ELEMENT_LABEL_PREFIX.get(etype, etype[:2])
        indices = sorted(by_type[etype])
        seq = 1
        for idx in indices:
            label = f'{prefix}{seq}'
            while label in used_labels:
                seq += 1
                label = f'{prefix}{seq}'
            label_map[label] = idx
            index_to_label[idx] = label
            used_labels.add(label)
            seq += 1
    return label_map, index_to_label


def _get_element_lines(export_text):
    """Extract element lines (including wires) from export text.

    Uses _EXPORT_META_ONLY to maintain 1:1 alignment with the API element list.
    """
    lines = []
    for line in export_text.split('\n'):
        line = line.strip()
        if not line:
            continue
        prefix = line.split(' ', 1)[0]
        if prefix not in _EXPORT_META_ONLY:
            lines.append(line)
    return lines


def _build_node_connectivity(export_text, elements, index_to_label):
    """Build node connectivity using union-find on wire-merged coordinates.

    Returns (node_list, element_nodes):
      node_list:     {1: {'labels': ['VA'], 'elements': ['R1','R2']}, ...}
      element_nodes: {0: [1, 2], 3: [2, 3], ...}  element index -> node IDs per post
    """
    # Union-find on (x,y) coordinate tuples
    parent = {}

    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    elem_lines = _get_element_lines(export_text)

    # Parse wire lines and merge their endpoints
    for line in export_text.split('\n'):
        line = line.strip()
        parts = line.split()
        if parts and parts[0] == 'w' and len(parts) >= 5:
            try:
                union((int(parts[1]), int(parts[2])),
                      (int(parts[3]), int(parts[4])))
            except ValueError:
                pass

    # Extract post coordinates for each element
    element_posts = {}
    for i, elem in enumerate(elements):
        if i >= len(elem_lines):
            continue
        fields = elem_lines[i].split()
        posts = elem.get('posts', 2)
        coords = []
        for p in range(posts):
            x_idx = 1 + 2 * p
            y_idx = 2 + 2 * p
            if y_idx < len(fields):
                try:
                    coords.append((int(fields[x_idx]), int(fields[y_idx])))
                except ValueError:
                    pass
        element_posts[i] = coords
        for c in coords:
            find(c)  # ensure coordinate exists in union-find

    # Collect all unique coordinates and group by root
    all_coords = set()
    for coords in element_posts.values():
        all_coords.update(coords)
    groups = {}
    for c in all_coords:
        root = find(c)
        groups.setdefault(root, set()).add(c)

    # Assign node numbers deterministically (sorted by y then x of root)
    sorted_roots = sorted(groups.keys(), key=lambda c: (c[1], c[0]))
    node_map = {}
    for num, root in enumerate(sorted_roots, 1):
        node_map[root] = num

    # Build node_list and element_nodes
    node_list = {n: {'labels': [], 'elements': []} for n in node_map.values()}
    element_nodes = {}

    for idx, coords in element_posts.items():
        nodes = []
        for c in coords:
            root = find(c)
            n = node_map.get(root)
            if n is not None:
                nodes.append(n)
                label = index_to_label.get(idx)
                if label and label not in node_list[n]['elements']:
                    node_list[n]['elements'].append(label)
        element_nodes[idx] = nodes

    # Extract user labels from labeled node elements using API label data
    for i, elem in enumerate(elements):
        lbl = elem.get('label', '')
        if not lbl:
            continue
        if i in element_posts and element_posts[i]:
            root = find(element_posts[i][0])
            n = node_map.get(root)
            if n is not None and lbl not in node_list[n]['labels']:
                node_list[n]['labels'].append(lbl)

    return node_list, element_nodes


@dataclass
class Measurement:
    source_type: str    # 'node', 'element', or 'expression'
    identifier: str     # node label, element label (R1), or Maxima expression
    property: str       # one of PROPERTIES
    target: float
    tolerance: float
    graded: bool
    tolerance_type: str = 'absolute'   # 'absolute' or 'relative'
    target_expr: str = ''              # when non-empty, used instead of float target
    element_index: int = -1            # element index for index-based subscribe params

    @classmethod
    def node(cls, label='', target=0.0, tolerance=0.1, graded=True):
        return cls(source_type=SOURCE_NODE, identifier=label,
                   property='nodeVoltage', target=target,
                   tolerance=tolerance, graded=graded)

    @classmethod
    def element(cls, index='', prop='current', target=0.0,
                tolerance=0.1, graded=True):
        return cls(source_type=SOURCE_ELEMENT, identifier=str(index),
                   property=prop, target=target,
                   tolerance=tolerance, graded=graded)

    def unit(self):
        if self.source_type == SOURCE_EXPRESSION:
            return ''
        return PROPERTY_UNITS.get(self.property, 'V')

    def data_key(self):
        """Key used in event.data.values from the simulator."""
        if self.source_type == SOURCE_NODE:
            return self.identifier
        if self.source_type == SOURCE_EXPRESSION:
            return None  # expressions are computed, not read from simulator
        if self.element_index >= 0:
            return f'{self.element_index}:{self.property}'
        return f'{self.identifier}:{self.property}'

    def display_name(self):
        if self.source_type == SOURCE_EXPRESSION:
            safe = re.sub(r'[^A-Za-z0-9_]', '_', self.identifier)
            return f'expr_{safe}'
        prefix = PROPERTY_PREFIX.get(self.property, 'V')
        return f'{prefix}_{self.identifier}'

    def input_name(self, index):
        """STACK input name: ans1, ans2, ..."""
        return f'ans{index + 1}'


# ---------------------------------------------------------------------------
# XML generation
# ---------------------------------------------------------------------------

def extract_ctz(text):
    """Extract ctz param from a Falstad URL, or return raw value."""
    text = text.strip()
    m = re.search(r'[?&]ctz=([^&\s]+)', text)
    return m.group(1) if m else text



def _build_sim_url(ctz, editable, white_bg, base_url, html_escape=True):
    """Build direct circuitjs.html URL (no bridge) for STACK [[iframe]] sandbox."""
    sep = '&amp;' if html_escape else '&'
    parts = ['running=true']
    if ctz:
        parts.append(f'ctz={ctz}')
    parts.append(f'editable={"true" if editable else "false"}')
    if white_bg:
        parts.append('whiteBackground=true')
    return base_url + '?' + sep.join(parts)


def _esc(text):
    """XML-escape for text nodes."""
    return (text.replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;'))


def _esc_cdata(text):
    """Escape for CDATA (only ]]> needs escaping)."""
    return text.replace(']]>', ']]]]><![CDATA[>')


def _fmt(v):
    """Format number for Maxima: drop unnecessary trailing zeros."""
    return f'{v:g}'


def _derive_subscribe_params(measurements):
    """Split measurements into subscribe message params: nodes and elements lists.

    Uses index-based keys for element measurements (e.g. 0:current).
    Expression measurements are skipped (computed from other measurements).
    """
    nodes = []
    elements = []
    for m in measurements:
        if m.source_type == SOURCE_NODE:
            if m.identifier not in nodes:
                nodes.append(m.identifier)
        elif m.source_type == SOURCE_ELEMENT:
            key = m.data_key()
            if key and key not in elements:
                elements.append(key)
        # SOURCE_EXPRESSION measurements are computed, not from simulator
    return nodes, elements


def _build_readout_html(measurements, has_integrity=False):
    """Build HTML readout lines for all measurements."""
    lines = []
    for i, m in enumerate(measurements):
        if m.source_type == SOURCE_EXPRESSION:
            continue  # expressions are computed server-side, not displayed live
        bold = ' style="font-weight:bold;"' if m.graded else ''
        tag = (' <span style="color:#090;">(graded)</span>'
               if m.graded else '')
        iname = m.input_name(i)
        prefix = PROPERTY_PREFIX.get(m.property, 'V')
        lines.append(
            f'    {prefix}<sub>{m.identifier}</sub> = '
            f'<span id="val-{iname}"{bold}>&mdash;</span> '
            f'{m.unit()}{tag}')
    if has_integrity:
        lines.append(
            '    <span id="integrity-status" '
            'style="color:#999;">Integrity: waiting...</span>')
    return '<br/>\n'.join(lines) if lines else '    (no measurements configured)'


def _build_js_block(measurements, nodes=None, elements=None,
                    rate=2, editable_indices=None, has_integrity=False,
                    sim_url=''):
    """Build the [[script]] JS block that reads values and writes to STACK inputs.

    The script sends a 'circuitjs-subscribe' config message to the simulator
    iframe via postMessage (works cross-origin inside STACK's sandbox), then
    listens for 'circuitjs-data' responses to update STACK inputs.

    Circuit state is preserved across form submissions via a hidden
    'ans_circuit' input that stores the compressed ctz.
    """
    if nodes is None:
        nodes = []
    if elements is None:
        elements = []
    if editable_indices is None:
        editable_indices = []

    # Only non-expression graded measurements get JS handling
    graded = [(i, m) for i, m in enumerate(measurements)
              if m.graded and m.source_type != SOURCE_EXPRESSION]

    js = "import {stack_js} from '[[cors src=\"stackjsiframe.js\"/]]';\n\n"

    # Request access to each graded STACK input
    for i, m in graded:
        iname = m.input_name(i)
        js += (f'const {iname}Id = await '
               f'stack_js.request_access_to_input("{iname}", true);\n')
        js += f'const {iname}Input = document.getElementById({iname}Id);\n'

    # Request access to integrity input if active
    if has_integrity:
        js += ('const intId = await '
               'stack_js.request_access_to_input("ans_integrity", true);\n')
        js += 'const intInput = document.getElementById(intId);\n'

    # Request access to circuit state input (preserves edits across Check)
    js += ('const circId = await '
           'stack_js.request_access_to_input("ans_circuit", true);\n')
    js += 'const circInput = document.getElementById(circId);\n'
    js += "\n"

    # Determine iframe URL: use saved circuit state if available
    js += f'var origUrl = "{sim_url}";\n'
    js += "var savedCtz = circInput.value;\n"
    js += "var simFrame = document.getElementById('sim-frame');\n"
    js += "if (savedCtz) {\n"
    js += "  simFrame.src = origUrl.replace(/ctz=[^&]*/, 'ctz=' + savedCtz);\n"
    js += "} else {\n"
    js += "  simFrame.src = origUrl;\n"
    js += "}\n\n"

    # Send subscribe config to circuitjs iframe after it loads
    nodes_js = json.dumps(nodes)
    elements_js = json.dumps(elements)
    indices_js = json.dumps(sorted(editable_indices))
    js += "simFrame.addEventListener('load', function() {\n"
    js += "  simFrame.contentWindow.postMessage({\n"
    js += "    type: 'circuitjs-subscribe',\n"
    js += f"    nodes: {nodes_js},\n"
    js += f"    elements: {elements_js},\n"
    js += f"    rate: {rate},\n"
    js += f"    editableIndices: {indices_js}\n"
    js += "  }, '*');\n"
    js += "});\n\n"

    js += "window.addEventListener('message', function(event) {\n"
    js += "  if (!event.data) return;\n\n"

    # Save circuit state when elements message arrives with ctz
    js += "  if (event.data.type === 'circuitjs-elements' && event.data.ctz) {\n"
    js += "    circInput.value = event.data.ctz;\n"
    js += "    circInput.dispatchEvent(new Event('change'));\n"
    js += "  }\n\n"

    js += "  if (event.data.type !== 'circuitjs-data') return;\n"
    js += "  var v;\n\n"

    # Update display for non-expression measurements
    for i, m in enumerate(measurements):
        if m.source_type == SOURCE_EXPRESSION:
            continue
        iname = m.input_name(i)
        key = m.data_key()
        js += f"  v = event.data.values['{key}'];\n"
        js += (f"  if (v !== null && v !== undefined) "
               f"document.getElementById('val-{iname}').textContent "
               f"= v.toFixed(4);\n")
    js += "\n"

    # Write graded values to STACK inputs
    for i, m in graded:
        iname = m.input_name(i)
        key = m.data_key()
        js += f"  v = event.data.values['{key}'];\n"
        js += "  if (v !== null && v !== undefined) {\n"
        js += f"    {iname}Input.value = v.toFixed(6);\n"
        js += f"    {iname}Input.dispatchEvent(new Event('change'));\n"
        js += "  }\n"

    # Route integrity value to STACK input
    if has_integrity:
        js += "\n  v = event.data.values['integrity'];\n"
        js += "  if (v !== null && v !== undefined) {\n"
        js += "    intInput.value = v.toString();\n"
        js += "    intInput.dispatchEvent(new Event('change'));\n"
        js += "    var el = document.getElementById('integrity-status');\n"
        js += "    if (el) {\n"
        js += "      if (v === 1) {\n"
        js += "        el.textContent = 'Integrity: OK';\n"
        js += "        el.style.color = '#090';\n"
        js += "      } else {\n"
        js += "        el.textContent = 'Integrity: FAILED — restricted component modified';\n"
        js += "        el.style.color = '#c00';\n"
        js += "      }\n"
        js += "    }\n"
        js += "  }\n"

    js += "  document.getElementById('status').textContent = '(live)';\n"
    js += "});"
    return js


def generate_xml(name, description, ctz, measurements,
                 editable_indices=None, editable=True, white_bg=True,
                 rate=2, hide_input=False, base_url=SIM_BASE_URL,
                 category='', custom_qvars=''):
    """Generate complete Moodle XML for a STACK + CircuitJS1 question."""

    if editable_indices is None:
        editable_indices = []
    has_integrity = len(editable_indices) > 0

    nodes, elements = _derive_subscribe_params(measurements)
    sim_url = _build_sim_url(ctz, editable, white_bg, base_url,
                             html_escape=False)
    readout_html = _build_readout_html(measurements, has_integrity)
    js_block = _build_js_block(measurements, nodes=nodes, elements=elements,
                               rate=rate, editable_indices=editable_indices,
                               has_integrity=has_integrity,
                               sim_url=sim_url)

    graded = [(i, m) for i, m in enumerate(measurements) if m.graded]
    n_graded = len(graded) or 1

    input_style = ' style="display:none;"' if hide_input else ''
    must_verify = '0' if hide_input else '1'
    show_validation = '0' if hide_input else '1'

    cat_block = ''
    if category.strip():
        cat_block = (
            '  <question type="category">\n'
            '    <category>\n'
            f'      <text>{_esc(category)}</text>\n'
            '    </category>\n'
            '    <info format="html">\n'
            '      <text/>\n'
            '    </info>\n'
            '  </question>\n')

    # --- Build question variables ---
    qvar_lines = []
    for i, m in graded:
        iname = m.input_name(i)
        if m.target_expr:
            qvar_lines.append(f'target_{iname}: {m.target_expr};')
        else:
            qvar_lines.append(f'target_{iname}: {_fmt(m.target)};')
        qvar_lines.append(f'tol_{iname}: {_fmt(m.tolerance)};')
    if has_integrity:
        qvar_lines.append('expected_integrity: 1;')
    qvars = '\n'.join(qvar_lines) if qvar_lines else '/* no graded measurements */'

    # Prepend custom question variables if provided
    if custom_qvars.strip():
        qvars = custom_qvars.strip() + '\n' + qvars

    # --- Build target summary for question text ---
    target_lines = []
    for i, m in graded:
        dname = m.display_name()
        iname = m.input_name(i)
        target_lines.append(
            f'<strong>{dname}</strong>: {{@target_{iname}@}} {m.unit()} '
            f'(&plusmn; {{@tol_{iname}@}} {m.unit()})')
    target_html = ''
    if not hide_input and target_lines:
        target_html = '<p>Targets: ' + ', '.join(target_lines) + '</p>\n\n'

    # --- Assemble XML ---
    p = []
    p.append('<?xml version="1.0" encoding="UTF-8"?>\n<quiz>\n')
    p.append(cat_block)
    p.append('  <question type="stack">\n')
    p.append(f'    <name>\n      <text>{_esc(name)}</text>\n    </name>\n')

    # --- questiontext (CDATA) ---
    p.append('    <questiontext format="html">\n      <text><![CDATA[')
    p.append(f'<p>{_esc_cdata(description)}</p>\n\n')
    p.append(target_html)
    p.append('<p><em>Edit the simulated circuit, the result will be read '
             'when you click &quot;Check&quot;.</em></p>\n\n')
    p.append('[[iframe height="640px" width="830px"]]\n')
    p.append('<div style="font-family:sans-serif;">\n\n')
    p.append('  <iframe id="sim-frame"\n')
    p.append('    width="800" height="550" style="border:1px solid #ccc;">\n')
    p.append('  </iframe>\n\n')
    p.append('  <div id="readout" style="display:none; font-family:monospace; '
             'padding:8px; font-size:14px;\n')
    p.append('    background:#f4f4f4; border:1px solid #ddd; margin-top:4px;">\n')
    p.append(readout_html + '\n')
    p.append('    <div id="status" style="color:#999; margin-top:4px;">'
             '(waiting for simulation...)</div>\n')
    p.append('  </div>\n\n</div>\n\n')
    p.append('[[script type="module"]]\n')
    p.append(js_block + '\n')
    p.append('[[/script]]\n[[/iframe]]\n\n')

    # Input display divs
    for i, m in graded:
        iname = m.input_name(i)
        p.append(f'<div{input_style}>\n')
        p.append(f'  <p>{m.display_name()}: '
                 f'[[input:{iname}]] {m.unit()} '
                 f'[[validation:{iname}]]</p>\n')
        p.append('</div>\n')
    if has_integrity:
        p.append('<div style="display:none;">\n')
        p.append('  <p>[[input:ans_integrity]] '
                 '[[validation:ans_integrity]]</p>\n')
        p.append('</div>\n')
    # Hidden input to preserve circuit state across Check submissions
    p.append('<div style="display:none;">\n')
    p.append('  <p>[[input:ans_circuit]] [[validation:ans_circuit]]</p>\n')
    p.append('</div>\n')
    p.append(']]></text>\n    </questiontext>\n')

    # --- general feedback ---
    p.append('    <generalfeedback format="html">\n')
    p.append('      <text><![CDATA[')
    for i, m in graded:
        iname = m.input_name(i)
        p.append(f'<p>{m.display_name()}: target = '
                 f'{{@target_{iname}@}} {m.unit()} '
                 f'(&plusmn; {{@tol_{iname}@}} {m.unit()}), '
                 f'measured = {{@{iname}@}} {m.unit()}</p>\n')
    p.append(']]></text>\n    </generalfeedback>\n')

    # --- standard fields ---
    p.append('    <defaultgrade>1</defaultgrade>\n')
    p.append('    <penalty>0.1</penalty>\n')
    p.append('    <hidden>0</hidden>\n')
    p.append('    <idnumber/>\n')

    # --- STACK fields (direct children of <question>, no <plugin> wrapper) ---
    p.append('      <stackversion>\n        <text/>\n      </stackversion>\n')
    p.append(f'      <questionvariables>\n        <text><![CDATA['
             f'{qvars}\n]]></text>\n'
             f'      </questionvariables>\n')

    # specific feedback references all PRTs
    fb_refs = ''.join(f'[[feedback:prt{j+1}]]' for j in range(n_graded))
    p.append('      <specificfeedback format="html">\n'
             f'        <text><![CDATA[{fb_refs}]]></text>\n'
             '      </specificfeedback>\n')

    # question note
    note_parts = []
    for i, m in graded:
        iname = m.input_name(i)
        note_parts.append(f'{m.display_name()}={{@target_{iname}@}}')
    note_str = ', '.join(note_parts) if note_parts else 'no graded measurements'
    p.append('      <questionnote format="html">\n'
             f'        <text><![CDATA[{note_str}]]></text>\n'
             '      </questionnote>\n')
    p.append('      <questiondescription format="html">\n'
             '        <text/>\n      </questiondescription>\n')

    # boolean options
    p.append('      <questionsimplify>1</questionsimplify>\n')
    p.append('      <assumepositive>0</assumepositive>\n')
    p.append('      <assumereal>0</assumereal>\n')
    p.append('      <isbroken>0</isbroken>\n')

    # PRT messages
    p.append('      <prtcorrect format="html">\n'
             '        <text><![CDATA[<span style="font-size: 1.5em; '
             'color:green;"><i class="fa fa-check"></i></span> '
             'Correct answer, well done.]]></text>\n'
             '      </prtcorrect>\n')
    p.append('      <prtpartiallycorrect format="html">\n'
             '        <text><![CDATA[<span style="font-size: 1.5em; '
             'color:orange;"><i class="fa fa-adjust"></i></span> '
             'Your answer is partially correct.]]></text>\n'
             '      </prtpartiallycorrect>\n')
    p.append('      <prtincorrect format="html">\n'
             '        <text><![CDATA[<span style="font-size: 1.5em; '
             'color:red;"><i class="fa fa-times"></i></span> '
             'Incorrect answer.]]></text>\n'
             '      </prtincorrect>\n')

    # display options
    p.append('      <decimals>.</decimals>\n')
    p.append('      <scientificnotation>*10</scientificnotation>\n')
    p.append('      <multiplicationsign>dot</multiplicationsign>\n')
    p.append('      <sqrtsign>1</sqrtsign>\n')
    p.append('      <complexno>j</complexno>\n')
    p.append('      <inversetrig>cos-1</inversetrig>\n')
    p.append('      <logicsymbol>lang</logicsymbol>\n')
    p.append('      <matrixparens>[</matrixparens>\n')
    p.append('      <variantsselectionseed/>\n')

    # --- inputs (one per graded measurement) ---
    for i, m in graded:
        iname = m.input_name(i)
        p.append('      <input>\n')
        p.append(f'        <name>{iname}</name>\n')
        p.append('        <type>numerical</type>\n')
        p.append(f'        <tans>target_{iname}</tans>\n')
        p.append('        <boxsize>10</boxsize>\n')
        p.append('        <strictsyntax>1</strictsyntax>\n')
        p.append('        <insertstars>0</insertstars>\n')
        p.append('        <syntaxhint/>\n')
        p.append('        <syntaxattribute>0</syntaxattribute>\n')
        p.append('        <forbidwords/>\n')
        p.append('        <allowwords/>\n')
        p.append('        <forbidfloat>0</forbidfloat>\n')
        p.append('        <requirelowestterms>0</requirelowestterms>\n')
        p.append('        <checkanswertype>0</checkanswertype>\n')
        p.append(f'        <mustverify>{must_verify}</mustverify>\n')
        p.append(f'        <showvalidation>{show_validation}</showvalidation>\n')
        p.append('        <options/>\n')
        p.append('      </input>\n')

    # Integrity hidden input
    if has_integrity:
        p.append('      <input>\n')
        p.append('        <name>ans_integrity</name>\n')
        p.append('        <type>numerical</type>\n')
        p.append('        <tans>expected_integrity</tans>\n')
        p.append('        <boxsize>5</boxsize>\n')
        p.append('        <strictsyntax>1</strictsyntax>\n')
        p.append('        <insertstars>0</insertstars>\n')
        p.append('        <syntaxhint/>\n')
        p.append('        <syntaxattribute>0</syntaxattribute>\n')
        p.append('        <forbidwords/>\n')
        p.append('        <allowwords/>\n')
        p.append('        <forbidfloat>0</forbidfloat>\n')
        p.append('        <requirelowestterms>0</requirelowestterms>\n')
        p.append('        <checkanswertype>0</checkanswertype>\n')
        p.append('        <mustverify>0</mustverify>\n')
        p.append('        <showvalidation>0</showvalidation>\n')
        p.append('        <options/>\n')
        p.append('      </input>\n')

    # Circuit state hidden input (preserves edits across Check)
    p.append('      <input>\n')
    p.append('        <name>ans_circuit</name>\n')
    p.append('        <type>string</type>\n')
    p.append('        <tans>""</tans>\n')
    p.append('        <boxsize>1</boxsize>\n')
    p.append('        <strictsyntax>0</strictsyntax>\n')
    p.append('        <insertstars>0</insertstars>\n')
    p.append('        <syntaxhint/>\n')
    p.append('        <syntaxattribute>0</syntaxattribute>\n')
    p.append('        <forbidwords/>\n')
    p.append('        <allowwords/>\n')
    p.append('        <forbidfloat>0</forbidfloat>\n')
    p.append('        <requirelowestterms>0</requirelowestterms>\n')
    p.append('        <checkanswertype>0</checkanswertype>\n')
    p.append('        <mustverify>0</mustverify>\n')
    p.append('        <showvalidation>0</showvalidation>\n')
    p.append('        <options/>\n')
    p.append('      </input>\n')

    # --- PRTs (one per graded measurement) ---
    prt_weight = _fmt(1.0 / n_graded)
    for j, (i, m) in enumerate(graded):
        iname = m.input_name(i)
        prt_name = f'prt{j + 1}'
        value_node = '1' if has_integrity else '0'
        p.append('      <prt>\n')
        p.append(f'        <name>{prt_name}</name>\n')
        p.append(f'        <value>{prt_weight}</value>\n')
        p.append('        <autosimplify>1</autosimplify>\n')
        p.append('        <feedbackstyle>1</feedbackstyle>\n')

        # Feedback variables: for expression measurements, map display
        # names of other measurements to their STACK inputs
        if m.source_type == SOURCE_EXPRESSION:
            fb_lines = []
            for ii, mm in enumerate(measurements):
                if mm.source_type != SOURCE_EXPRESSION:
                    fb_lines.append(
                        f'{mm.display_name()}: {mm.input_name(ii)};')
            fb_lines.append(
                f'computed_sans: {m.identifier};')
            fb_text = ' '.join(fb_lines)
            p.append('        <feedbackvariables>\n'
                     f'          <text><![CDATA[{fb_text}]]></text>\n'
                     '        </feedbackvariables>\n')
        else:
            p.append('        <feedbackvariables>\n          <text/>\n'
                     '        </feedbackvariables>\n')

        # Node 0: Integrity gate (only if integrity checking is active)
        if has_integrity:
            p.append('        <node>\n')
            p.append('          <name>0</name>\n')
            p.append('          <description>Integrity gate</description>\n')
            p.append('          <answertest>AlgEquiv</answertest>\n')
            p.append('          <sans>ans_integrity</sans>\n')
            p.append('          <tans>expected_integrity</tans>\n')
            p.append('          <testoptions/>\n')
            p.append('          <quiet>1</quiet>\n')
            # true: proceed to value check
            p.append('          <truescoremode>=</truescoremode>\n')
            p.append('          <truescore>0.0</truescore>\n')
            p.append('          <truepenalty/>\n')
            p.append('          <truenextnode>1</truenextnode>\n')
            p.append(f'          <trueanswernote>'
                     f'{prt_name}-0-T</trueanswernote>\n')
            p.append('          <truefeedback format="html">\n')
            p.append('            <text/>\n')
            p.append('          </truefeedback>\n')
            # false: zero marks, integrity failure message
            p.append('          <falsescoremode>=</falsescoremode>\n')
            p.append('          <falsescore>0.0</falsescore>\n')
            p.append('          <falsepenalty/>\n')
            p.append('          <falsenextnode>-1</falsenextnode>\n')
            p.append(f'          <falseanswernote>'
                     f'{prt_name}-0-F</falseanswernote>\n')
            p.append('          <falsefeedback format="html">\n')
            p.append('            <text><![CDATA[<p style="color:#c00;">'
                     'One or more restricted circuit components were '
                     'modified. Your answer cannot be graded.</p>]]>'
                     '</text>\n')
            p.append('          </falsefeedback>\n')
            p.append('        </node>\n')

        # Value check node
        test_type = ('NumRelative' if m.tolerance_type == 'relative'
                     else 'NumAbsolute')
        sans_val = ('computed_sans'
                    if m.source_type == SOURCE_EXPRESSION else iname)
        p.append('        <node>\n')
        p.append(f'          <name>{value_node}</name>\n')
        p.append(f'          <description>Check {_esc(m.display_name())} '
                 f'against target</description>\n')
        p.append(f'          <answertest>{test_type}</answertest>\n')
        p.append(f'          <sans>{sans_val}</sans>\n')
        p.append(f'          <tans>target_{iname}</tans>\n')
        p.append(f'          <testoptions>tol_{iname}</testoptions>\n')
        p.append('          <quiet>0</quiet>\n')
        # true branch
        p.append('          <truescoremode>=</truescoremode>\n')
        p.append('          <truescore>1.0</truescore>\n')
        p.append('          <truepenalty/>\n')
        p.append('          <truenextnode>-1</truenextnode>\n')
        p.append(f'          <trueanswernote>'
                 f'{prt_name}-{value_node}-T</trueanswernote>\n')
        p.append('          <truefeedback format="html">\n')
        p.append(f'            <text><![CDATA[<p>Correct! '
                 f'{m.display_name()} = {{@{iname}@}} {m.unit()} is within '
                 f'{{@tol_{iname}@}} {m.unit()} of the target '
                 f'{{@target_{iname}@}} {m.unit()}.</p>]]></text>\n')
        p.append('          </truefeedback>\n')
        # false branch
        p.append('          <falsescoremode>=</falsescoremode>\n')
        p.append('          <falsescore>0.0</falsescore>\n')
        p.append('          <falsepenalty/>\n')
        p.append('          <falsenextnode>-1</falsenextnode>\n')
        p.append(f'          <falseanswernote>'
                 f'{prt_name}-{value_node}-F</falseanswernote>\n')
        p.append('          <falsefeedback format="html">\n')
        p.append(f'            <text><![CDATA[<p>Not quite. '
                 f'{m.display_name()} = {{@{iname}@}} {m.unit()}, '
                 f'but the target is {{@target_{iname}@}} {m.unit()} '
                 f'(&plusmn; {{@tol_{iname}@}} {m.unit()}).</p>]]></text>\n')
        p.append('          </falsefeedback>\n')
        p.append('        </node>\n')
        p.append('      </prt>\n')

    # --- test cases ---
    value_node = '1' if has_integrity else '0'

    # Helper: add ans_circuit test input (empty string, not graded)
    def _circuit_test_input():
        return ('        <testinput>\n          <name>ans_circuit</name>\n'
                '          <value>""\n</value>\n'
                '        </testinput>\n')

    # Test 1: all correct
    p.append('      <qtest>\n')
    p.append('        <testcase>1</testcase>\n')
    p.append('        <description>All correct</description>\n')
    for j, (i, m) in enumerate(graded):
        iname = m.input_name(i)
        p.append(f'        <testinput>\n          <name>{iname}</name>\n'
                 f'          <value>target_{iname}</value>\n'
                 f'        </testinput>\n')
    if has_integrity:
        p.append('        <testinput>\n          <name>ans_integrity</name>\n'
                 '          <value>1</value>\n'
                 '        </testinput>\n')
    p.append(_circuit_test_input())
    for j in range(n_graded):
        prt_name = f'prt{j + 1}'
        p.append(f'        <expected>\n          <name>{prt_name}</name>\n'
                 f'          <expectedscore>1.0000000</expectedscore>\n'
                 f'          <expectedpenalty>0.0000000</expectedpenalty>\n'
                 f'          <expectedanswernote>'
                 f'{prt_name}-{value_node}-T</expectedanswernote>\n'
                 f'        </expected>\n')
    p.append('      </qtest>\n')

    # Test 2: all wrong (values wrong, integrity ok)
    p.append('      <qtest>\n')
    p.append('        <testcase>2</testcase>\n')
    p.append('        <description>All wrong</description>\n')
    for j, (i, m) in enumerate(graded):
        iname = m.input_name(i)
        p.append(f'        <testinput>\n          <name>{iname}</name>\n'
                 f'          <value>target_{iname} + tol_{iname} + 1</value>\n'
                 f'        </testinput>\n')
    if has_integrity:
        p.append('        <testinput>\n          <name>ans_integrity</name>\n'
                 '          <value>1</value>\n'
                 '        </testinput>\n')
    p.append(_circuit_test_input())
    for j in range(n_graded):
        prt_name = f'prt{j + 1}'
        p.append(f'        <expected>\n          <name>{prt_name}</name>\n'
                 f'          <expectedscore>0.0000000</expectedscore>\n'
                 f'          <expectedpenalty>0.1000000</expectedpenalty>\n'
                 f'          <expectedanswernote>'
                 f'{prt_name}-{value_node}-F</expectedanswernote>\n'
                 f'        </expected>\n')
    p.append('      </qtest>\n')

    # Test 3: integrity failure (values correct but integrity=0)
    if has_integrity:
        p.append('      <qtest>\n')
        p.append('        <testcase>3</testcase>\n')
        p.append('        <description>Integrity failure</description>\n')
        for j, (i, m) in enumerate(graded):
            iname = m.input_name(i)
            p.append(f'        <testinput>\n          <name>{iname}</name>\n'
                     f'          <value>target_{iname}</value>\n'
                     f'        </testinput>\n')
        p.append('        <testinput>\n          <name>ans_integrity</name>\n'
                 '          <value>0</value>\n'
                 '        </testinput>\n')
        p.append(_circuit_test_input())
        for j in range(n_graded):
            prt_name = f'prt{j + 1}'
            p.append(f'        <expected>\n          <name>{prt_name}</name>\n'
                     f'          <expectedscore>0.0000000</expectedscore>\n'
                     f'          <expectedpenalty>0.1000000</expectedpenalty>\n'
                     f'          <expectedanswernote>'
                     f'{prt_name}-0-F</expectedanswernote>\n'
                     f'        </expected>\n')
        p.append('      </qtest>\n')

    # tags
    p.append('    <tags>\n      <tag>\n        <text>circuitjs</text>\n'
             '      </tag>\n    </tags>\n')

    p.append('  </question>\n</quiz>\n')

    return ''.join(p)


# ---------------------------------------------------------------------------
# Qt GUI
# ---------------------------------------------------------------------------

# Measurement table columns
COL_SOURCE  = 0
COL_IDENT   = 1
COL_TYPE    = 2
COL_TARGET  = 3
COL_TOL     = 4
COL_TOLTYPE = 5
COL_GRADE   = 6
COL_REMOVE  = 7
MEAS_COLUMNS = ['Source', 'Identifier', 'Property', 'Target', 'Tolerance',
                'Tol Type', 'Grade', '']


class SimulatorPanel(QWidget):
    """Embeddable panel showing the live CircuitJS1 simulator."""

    def __init__(self, main_window):
        super().__init__()
        self.main = main_window

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        if not HAS_WEBENGINE:
            placeholder = QLabel(
                'PyQt6-WebEngine is not installed.\n'
                'Install with: pip install PyQt6-WebEngine')
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(placeholder)
            self.web_view = None
            return

        # Controls
        ctrl = QHBoxLayout()
        self.reload_btn = QPushButton('Reload')
        self.save_circuit_btn = QPushButton('Save Circuit to CTZ')
        self.save_circuit_btn.setEnabled(False)
        self.save_circuit_btn.setToolTip(
            'Export the current (possibly edited) circuit back to the '
            'CTZ field in the main window')
        self.use_btn = QPushButton('Use Value as Target')
        self.use_btn.setEnabled(False)
        self.use_btn.setToolTip(
            'Select a row in the Measurements table, then click here')
        ctrl.addWidget(self.reload_btn)
        ctrl.addWidget(self.save_circuit_btn)
        ctrl.addWidget(self.use_btn)
        ctrl.addStretch()
        layout.addLayout(ctrl)

        # Web view — ClickFocus prevents the web view from stealing
        # keyboard focus away from QLineEdit / QDoubleSpinBox widgets
        # in the left pane (grading table, question variables, etc.).
        self.web_view = QWebEngineView()
        self.web_view.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        layout.addWidget(self.web_view, stretch=1)

        # Readout
        mono = QFont('Consolas', 10)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.readout = QPlainTextEdit()
        self.readout.setReadOnly(True)
        self.readout.setFont(mono)
        self.readout.setMaximumHeight(140)
        self.readout.setPlaceholderText('Waiting for simulator data...')
        layout.addWidget(self.readout)

        # Polling timer
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(400)
        self._latest_values = {}
        self._lz = LZString()
        self._sim_connected = False

        # Signals
        self.reload_btn.clicked.connect(self._on_reload)
        self.save_circuit_btn.clicked.connect(self._on_save_circuit)
        self.use_btn.clicked.connect(self._on_use_value)
        self._poll_timer.timeout.connect(self._poll)
        self.web_view.loadFinished.connect(self._on_loaded)

    def start(self, url):
        """Load (or reload) the simulator URL."""
        if not self.web_view:
            return
        self._poll_timer.stop()
        self._latest_values = {}
        self._sim_connected = False
        self.use_btn.setEnabled(False)
        self.save_circuit_btn.setEnabled(False)
        self.readout.setPlainText('Loading simulator...')
        self.web_view.setUrl(QUrl(url))

    def _on_reload(self):
        self.start(self.main._get_sim_url())

    def _build_monitor_js(self):
        """Build JS that directly uses the CircuitJS1 API on the loaded page.

        Since QWebEngineView loads circuitjs.html as the top-level page
        (no sandbox), we can access window.CircuitJS1 directly.
        """
        measurements = self.main._get_measurements()
        nodes, elements = _derive_subscribe_params(measurements)
        mode = self.main._get_editable_mode()
        editable_indices = (sorted(self.main._get_editable_indices())
                            if mode == 'values' else [])
        nodes_js = json.dumps(nodes)
        elements_js = json.dumps(elements)
        indices_js = json.dumps(editable_indices)
        has_integrity = len(editable_indices) > 0

        js = (
            "(function() {"
            f"var nodes = {nodes_js};"
            f"var elements = {elements_js};"
            f"var editableIndices = new Set({indices_js});"
            "var rate = 2;"
            "var skipEvery = Math.max(1, Math.round(60 / rate));"
            "var updateCount = 0;"
            "var NON_ELEM = ['$','w','o','38','h','&'];"
            "var baseSigs = null;"
            "var integrityOk = 1;"
            "window._qgen_values = null;"
            "window._qgen_elements = null;"
            "window._qgen_connected = false;"
            "window._qgen_exportCircuit = function() {"
            "  try { return window.CircuitJS1.exportCircuit(); }"
            "  catch(e) { return null; }"
            "};"
            "function extractSigs(txt, elems) {"
            "  var lines = txt.split('\\n').filter(function(l) {"
            "    l = l.trim(); if (!l) return false;"
            "    for (var p = 0; p < NON_ELEM.length; p++) {"
            "      var px = NON_ELEM[p];"
            "      if (l === px || l.indexOf(px + ' ') === 0) return false;"
            "    } return true;"
            "  });"
            "  if (lines.length !== elems.length) return null;"
            "  var s = [];"
            "  for (var i = 0; i < lines.length; i++) {"
            "    var f = lines[i].split(' ');"
            "    var pc; try { pc = elems[i].getPostCount(); } catch(e) { pc = 2; }"
            "    s.push(f[0] + ' ' + f.slice(2*pc+2).join(' '));"
            "  } return s;"
            "}"
            "function connect() {"
            "  if (!window.CircuitJS1) { setTimeout(connect, 300); return; }"
            "  var sim = window.CircuitJS1;"
            "  sim.onupdate = function() {"
            "    updateCount++;"
            "    if (updateCount % skipEvery !== 0) return;"
            "    var v = {};"
            "    for (var i = 0; i < nodes.length; i++) {"
            "      try { v[nodes[i]] = sim.getNodeVoltage(nodes[i]); }"
            "      catch(e) { v[nodes[i]] = null; }"
            "    }"
            "    if (elements.length > 0) {"
            "      var ae = sim.getElements();"
            "      for (var j = 0; j < elements.length; j++) {"
            "        var p = elements[j].split(':');"
            "        var ix = parseInt(p[0], 10);"
            "        var pr = p[1] || 'current';"
            "        if (ix < ae.length) {"
            "          try {"
            "            if (pr === 'current') v[elements[j]] = ae[ix].getCurrent();"
            "            else if (pr === 'voltageDiff' || pr === 'voltage')"
            "              v[elements[j]] = ae[ix].getVoltageDiff();"
            "            else if (pr === 'power')"
            "              v[elements[j]] = ae[ix].getVoltageDiff() * ae[ix].getCurrent();"
            "          } catch(e) { v[elements[j]] = null; }"
            "        }"
            "      }"
            "    }"
        )
        if has_integrity:
            js += "    v['integrity'] = integrityOk;"
        js += (
            "    window._qgen_values = v;"
            "    window._qgen_connected = true;"
            "  };"
            "  sim.onanalyze = function() {"
            "    var elems = sim.getElements();"
            "    var info = [];"
            "    for (var k = 0; k < elems.length; k++) {"
            "      var e = elems[k];"
            "      var lbl = '';"
            "      try { lbl = e.getLabelName() || ''; } catch(x) {}"
            "      info.push({ index: k, type: e.getType(), label: lbl });"
            "    }"
            "    window._qgen_elements = info;"
        )
        if has_integrity:
            js += (
                "    if (editableIndices.size > 0) {"
                "      var exp = sim.exportCircuit();"
                "      var sigs = extractSigs(exp, elems);"
                "      if (sigs) {"
                "        if (!baseSigs) { baseSigs = sigs; }"
                "        else {"
                "          integrityOk = 1;"
                "          for (var ci = 0; ci < baseSigs.length; ci++) {"
                "            if (editableIndices.has(ci)) continue;"
                "            if (ci >= sigs.length || sigs[ci] !== baseSigs[ci])"
                "              { integrityOk = 0; break; }"
                "          }"
                "          if (sigs.length !== baseSigs.length) integrityOk = 0;"
                "        }"
                "      }"
                "    }"
            )
        js += (
            "  };"
            "}"
            "connect();"
            "})();"
        )
        return js

    def _on_loaded(self, ok):
        if not ok:
            self.readout.setPlainText('Failed to load simulator page.')
            return
        # Inject JS that uses the CircuitJS1 API directly on this page
        self.web_view.page().runJavaScript(self._build_monitor_js())
        self.readout.setPlainText('Simulator loaded. Waiting for first data...')
        # Prevent the internal Chromium widget from grabbing focus on load
        proxy = self.web_view.focusProxy()
        if proxy is not None:
            proxy.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._poll_timer.start()

    def _poll(self):
        self.web_view.page().runJavaScript(
            'JSON.stringify(window._qgen_values)', 0,
            self._on_poll_result)

    def _on_poll_result(self, result):
        if not result or result == 'null':
            return
        try:
            values = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return
        self._latest_values = values

        # Enable save button once we've received data (sim is connected)
        if not self._sim_connected:
            self._sim_connected = True
            self.save_circuit_btn.setEnabled(True)

        # Build display info from main window's measurements
        measurements = self.main._get_measurements()
        key_info = {}
        for m in measurements:
            dk = m.data_key()
            if dk is not None:  # skip expression measurements
                key_info[dk] = {
                    'unit': m.unit(), 'graded': m.graded,
                    'display': m.display_name(),
                    'target': m.target, 'tolerance': m.tolerance}

        lines = []
        for key in sorted(values.keys()):
            val = values[key]
            if key == 'integrity':
                status = 'PASS' if val == 1 else 'FAIL'
                lines.append(f'  integrity = {status}')
                continue
            info = key_info.get(key, {'unit': '?', 'graded': False,
                                      'display': key,
                                      'target': 0, 'tolerance': 0})
            if val is None:
                lines.append(f'  {info["display"]} ({key}) = null')
            elif info['graded']:
                error = abs(val - info['target'])
                if error <= info['tolerance']:
                    marker = f'  <<< CORRECT (target: {info["target"]:.4g} +/- {info["tolerance"]:.4g})'
                else:
                    marker = f'  <<< INCORRECT (target: {info["target"]:.4g} +/- {info["tolerance"]:.4g})'
                lines.append(
                    f'  {info["display"]} ({key}) = '
                    f'{val:.6f} {info["unit"]}{marker}')
            else:
                lines.append(
                    f'  {info["display"]} ({key}) = '
                    f'{val:.6f} {info["unit"]}')
        self.readout.setPlainText('\n'.join(lines))
        self.use_btn.setEnabled(bool(values))

    def _on_save_circuit(self):
        """Export the current circuit from the simulator and update the CTZ field."""
        self.web_view.page().runJavaScript(
            'window._qgen_exportCircuit()', 0,
            self._on_export_result)

    def _on_export_result(self, result):
        if not result:
            QMessageBox.warning(
                self, 'Export Failed',
                'Could not export the circuit.\n'
                'The simulator may not be fully loaded yet.')
            return
        ctz = self._lz.compressToEncodedURIComponent(result)
        self.main.ctz_edit.setPlainText(ctz)
        self.main.statusBar().showMessage(
            f'Circuit saved — CTZ updated ({len(ctz)} chars)')
        # Re-run element labeling with updated circuit
        self.main._on_refresh_components()
        self.main._update_preview()

    def _on_use_value(self):
        """Write the simulator value into the selected measurement row."""
        tbl = self.main.meas_table
        sel = tbl.selectionModel().selectedRows()
        if not sel:
            QMessageBox.information(
                self, 'Select Row',
                'Click a row in the Measurements table first,\n'
                'then click this button.')
            return
        row = sel[0].row()
        source_w = tbl.cellWidget(row, COL_SOURCE)
        ident_w = tbl.cellWidget(row, COL_IDENT)
        type_w = tbl.cellWidget(row, COL_TYPE)
        if not source_w or not ident_w or not type_w:
            return
        # Get identifier from QComboBox or QLineEdit
        if isinstance(ident_w, QComboBox):
            identifier = ident_w.currentText().strip()
        else:
            identifier = ident_w.text().strip()
        if not identifier:
            QMessageBox.warning(
                self, 'Empty Identifier',
                'The selected measurement row has no identifier.\n'
                'Select or enter a node label or element label first.')
            return
        source = source_w.currentData()
        if source == SOURCE_EXPRESSION:
            QMessageBox.information(
                self, 'Expression',
                'Expression measurements are computed from other\n'
                'measurements and cannot be set from simulator values.')
            return
        prop = type_w.currentData()
        if source == SOURCE_NODE:
            # Use currentData for labeled nodes (contains the label text)
            if isinstance(ident_w, QComboBox):
                data = ident_w.currentData()
                key = str(data) if data else identifier
            else:
                key = identifier
        elif source == SOURCE_ELEMENT:
            idx = self.main._label_map.get(identifier)
            if idx is not None:
                key = f'{idx}:{prop}'
            else:
                key = f'{identifier}:{prop}'
        else:
            key = f'{identifier}:{prop}'
        val = self._latest_values.get(key)
        if val is not None:
            target_w = tbl.cellWidget(row, COL_TARGET)
            target_w.setText(f'{val:.6g}')
            self.main.statusBar().showMessage(
                f'Target set to {val:.6f} from "{key}"')
        else:
            QMessageBox.warning(
                self, 'No Value',
                f'No simulator value for key "{key}".\n'
                f'Check the label matches a circuit node/element.')


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('CircuitJS1 STACK Question Generator')
        self.setMinimumSize(1200, 900)
        self.settings = QSettings('FalstadSTACK', 'QuestionGenerator')
        self._in_focus_handler = False
        self._build_ui()
        self._connect_signals()
        self._restore_settings()
        self._update_preview()

        # Auto-start simulator (loads with or without a circuit URL)
        QTimer.singleShot(0, lambda: self._sim_panel.start(
            self._get_sim_url()))

    # ---- UI construction ----

    def _build_ui(self):
        # Instance state for element labeling / node connectivity
        self._label_map = {}       # label -> element index
        self._index_to_label = {}  # element index -> label
        self._node_list = {}       # node_num -> {labels, elements}
        self._element_nodes = {}   # element index -> [node IDs]
        self._elements = []        # raw element dicts from simulator

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(6)

        # --- Load from XML button (top) ---
        load_row = QHBoxLayout()
        self.load_xml_btn = QPushButton('Load from XML...')
        load_row.addWidget(self.load_xml_btn)
        load_row.addStretch()
        layout.addLayout(load_row)

        # --- Question group (full width top) ---
        q_grp = QGroupBox('Question')
        q_lay = QFormLayout(q_grp)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText('e.g. Voltage Divider - Adjust Vout')
        q_lay.addRow('Name:', self.name_edit)

        self.category_edit = QLineEdit()
        self.category_edit.setPlaceholderText(
            'e.g. $course$/Circuit Analysis  (empty = no category)')
        q_lay.addRow('Category:', self.category_edit)

        self.desc_edit = QTextEdit()
        self.desc_edit.setPlaceholderText(
            'Question description shown to students...')
        self.desc_edit.setMaximumHeight(70)
        q_lay.addRow('Description:', self.desc_edit)

        self.ctz_edit = QPlainTextEdit()
        self.ctz_edit.setMaximumHeight(55)
        self.ctz_edit.setPlaceholderText(
            'https://falstad.com/circuit/circuitjs.html?ctz=CQAg... or raw')
        q_lay.addRow('Circuit URL:', self.ctz_edit)

        self.editable_combo = QComboBox()
        self.editable_combo.addItem('All', 'all')
        self.editable_combo.addItem('Values', 'values')
        self.editable_combo.addItem('None', 'none')
        self.editable_combo.setCurrentIndex(0)
        q_lay.addRow('Editable:', self.editable_combo)

        layout.addWidget(q_grp)

        # --- Left/Right splitter ---
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ---- LEFT PANE: Components + Grading + Settings ----
        left_widget = QWidget()
        left_lay = QVBoxLayout(left_widget)
        left_lay.setContentsMargins(0, 0, 0, 0)

        # -- Components group --
        comp_grp = QGroupBox('Circuit Components')
        comp_lay = QVBoxLayout(comp_grp)

        # Component table starts with base columns; node columns added dynamically
        self._comp_base_columns = ['Label', 'Type', 'Value']
        self._comp_node_count = 0
        self.comp_table = QTableWidget(0, len(self._comp_base_columns) + 1)
        self.comp_table.setHorizontalHeaderLabels(
            self._comp_base_columns + ['Editable'])
        comp_header = self.comp_table.horizontalHeader()
        comp_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        comp_header.resizeSection(0, 50)
        comp_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        comp_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        comp_header.resizeSection(2, 90)
        # Last col = Editable
        last = self.comp_table.columnCount() - 1
        comp_header.setSectionResizeMode(last, QHeaderView.ResizeMode.Fixed)
        comp_header.resizeSection(last, 60)
        self.comp_table.verticalHeader().setVisible(False)
        comp_lay.addWidget(self.comp_table)

        comp_btn_row = QHBoxLayout()
        self.refresh_comp_btn = QPushButton('Refresh from Simulator')
        self.refresh_comp_btn.setToolTip(
            'Open the Live Simulator first, then click to populate '
            'the component list')
        comp_btn_row.addWidget(self.refresh_comp_btn)
        self.comp_status_label = QLabel('No components loaded')
        self.comp_status_label.setStyleSheet('color: #666;')
        comp_btn_row.addWidget(self.comp_status_label)
        comp_btn_row.addStretch()
        comp_lay.addLayout(comp_btn_row)

        left_lay.addWidget(comp_grp)

        # -- Grading group --
        m_grp = QGroupBox('Grading')
        m_lay = QVBoxLayout(m_grp)

        self.meas_table = QTableWidget(0, len(MEAS_COLUMNS))
        self.meas_table.setHorizontalHeaderLabels(MEAS_COLUMNS)
        header = self.meas_table.horizontalHeader()
        header.setSectionResizeMode(COL_SOURCE, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(COL_SOURCE, 85)
        header.setSectionResizeMode(COL_IDENT, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_TYPE, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(COL_TYPE, 110)
        header.setSectionResizeMode(COL_TARGET, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(COL_TARGET, 110)
        header.setSectionResizeMode(COL_TOL, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(COL_TOL, 80)
        header.setSectionResizeMode(COL_TOLTYPE, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(COL_TOLTYPE, 55)
        header.setSectionResizeMode(COL_GRADE, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(COL_GRADE, 45)
        header.setSectionResizeMode(COL_REMOVE, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(COL_REMOVE, 28)
        self.meas_table.verticalHeader().setVisible(False)
        self.meas_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.meas_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        m_lay.addWidget(self.meas_table)

        meas_btn_row = QHBoxLayout()
        self.add_meas_btn = QPushButton('+ Add Measurement')
        meas_btn_row.addWidget(self.add_meas_btn)
        meas_btn_row.addStretch()
        m_lay.addLayout(meas_btn_row)

        left_lay.addWidget(m_grp, stretch=1)

        # -- Question Variables table (under Grading) --
        qv_grp = QGroupBox('Question Variables')
        qv_lay = QVBoxLayout(qv_grp)

        self.qvars_table = QTableWidget(0, 3)
        self.qvars_table.setHorizontalHeaderLabels(['Label', 'Expression', ''])
        qv_header = self.qvars_table.horizontalHeader()
        qv_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        qv_header.resizeSection(0, 120)
        qv_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        qv_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        qv_header.resizeSection(2, 28)
        self.qvars_table.verticalHeader().setVisible(False)
        self.qvars_table.setMaximumHeight(120)
        qv_lay.addWidget(self.qvars_table)

        qv_btn_row = QHBoxLayout()
        self.add_qvar_btn = QPushButton('+ Add Variable')
        qv_btn_row.addWidget(self.add_qvar_btn)
        qv_btn_row.addStretch()
        qv_lay.addLayout(qv_btn_row)

        left_lay.addWidget(qv_grp)

        left_lay.addStretch()
        splitter.addWidget(left_widget)

        # ---- RIGHT PANE: Simulator Panel ----
        self._sim_panel = SimulatorPanel(self)
        splitter.addWidget(self._sim_panel)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, stretch=1)

        # -- Save XML button (bottom right) --
        bottom_row = QHBoxLayout()
        bottom_row.addStretch()
        self.save_btn = QPushButton('Save XML...')
        self.save_btn.setDefault(True)
        bottom_row.addWidget(self.save_btn)
        layout.addLayout(bottom_row)

        self.statusBar().showMessage('Ready')

    # ---- Focus-based row selection ----

    def _on_focus_changed(self, old, new):
        if new is None or self._in_focus_handler:
            return
        self._in_focus_handler = True
        try:
            for row in range(self.meas_table.rowCount()):
                for col in range(self.meas_table.columnCount()):
                    w = self.meas_table.cellWidget(row, col)
                    if w is not None and (w is new or w.isAncestorOf(new)):
                        self.meas_table.selectRow(row)
                        return
        finally:
            self._in_focus_handler = False

    # ---- Measurement table helpers ----

    def _add_measurement_row(self, source='node', identifier='',
                             prop='nodeVoltage', target=0.0,
                             tolerance=0.1, graded=True,
                             tolerance_type='absolute', target_expr=''):
        """Add a new row to the measurement table."""
        row = self.meas_table.rowCount()
        self.meas_table.insertRow(row)

        # Source (Node / Element / Expression)
        source_combo = QComboBox()
        source_combo.addItem('Node', SOURCE_NODE)
        source_combo.addItem('Element', SOURCE_ELEMENT)
        source_combo.addItem('Expression', SOURCE_EXPRESSION)
        idx_map = {SOURCE_NODE: 0, SOURCE_ELEMENT: 1, SOURCE_EXPRESSION: 2}
        source_combo.setCurrentIndex(idx_map.get(source, 0))
        source_combo.currentIndexChanged.connect(self._on_source_changed)
        self.meas_table.setCellWidget(row, COL_SOURCE, source_combo)

        # Identifier — QComboBox (editable) for Node/Element, QLineEdit for Expression
        if source == SOURCE_EXPRESSION:
            ident_w = QLineEdit(identifier)
            ident_w.setPlaceholderText('Maxima expr, e.g. V_R1 * I_R1')
            ident_w.textChanged.connect(self._update_preview)
        else:
            ident_w = QComboBox()
            ident_w.setEditable(True)
            self._populate_ident_combo(ident_w, source)
            if identifier:
                ident_w.setCurrentText(identifier)
            ident_w.currentTextChanged.connect(self._update_preview)
        self.meas_table.setCellWidget(row, COL_IDENT, ident_w)

        # Property
        type_combo = QComboBox()
        if source == SOURCE_EXPRESSION:
            type_combo.addItem('(expression)', 'expression')
            type_combo.setEnabled(False)
        elif source == SOURCE_NODE:
            type_combo.addItem(PROPERTY_DISPLAY['nodeVoltage'], 'nodeVoltage')
        else:
            for p_key in ELEMENT_PROPERTIES:
                type_combo.addItem(PROPERTY_DISPLAY[p_key], p_key)
            if prop in ELEMENT_PROPERTIES:
                type_combo.setCurrentIndex(ELEMENT_PROPERTIES.index(prop))
        type_combo.currentIndexChanged.connect(self._on_type_changed)
        self.meas_table.setCellWidget(row, COL_TYPE, type_combo)

        # Target — QLineEdit (accepts number or Maxima expression)
        target_edit = QLineEdit()
        if target_expr:
            target_edit.setText(target_expr)
        elif target != 0.0:
            target_edit.setText(f'{target:g}')
        target_edit.setPlaceholderText('number or expression')
        target_edit.textChanged.connect(self._update_preview)
        self.meas_table.setCellWidget(row, COL_TARGET, target_edit)

        # Tolerance
        tol_spin = QDoubleSpinBox()
        tol_spin.setRange(0, 1e6)
        tol_spin.setDecimals(6)
        tol_spin.setSingleStep(0.01)
        tol_spin.setValue(tolerance)
        tol_spin.valueChanged.connect(self._update_preview)
        self.meas_table.setCellWidget(row, COL_TOL, tol_spin)

        # Tolerance type (Abs / Rel)
        toltype_combo = QComboBox()
        toltype_combo.addItem('Abs', 'absolute')
        toltype_combo.addItem('Rel', 'relative')
        toltype_combo.setCurrentIndex(
            1 if tolerance_type == 'relative' else 0)
        toltype_combo.currentIndexChanged.connect(self._update_preview)
        self.meas_table.setCellWidget(row, COL_TOLTYPE, toltype_combo)

        # Grade checkbox (centered in a container widget)
        grade_container = QWidget()
        grade_layout = QHBoxLayout(grade_container)
        grade_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grade_layout.setContentsMargins(0, 0, 0, 0)
        grade_chk = QCheckBox()
        grade_chk.setChecked(graded)
        grade_chk.stateChanged.connect(self._update_preview)
        grade_layout.addWidget(grade_chk)
        self.meas_table.setCellWidget(row, COL_GRADE, grade_container)

        # Remove button
        rm_btn = QPushButton('x')
        rm_btn.setFixedWidth(28)
        rm_btn.clicked.connect(self._on_remove_row)
        self.meas_table.setCellWidget(row, COL_REMOVE, rm_btn)

        self._update_preview()

    def _populate_ident_combo(self, combo, source):
        """Fill identifier combo with available items from loaded components."""
        combo.blockSignals(True)
        combo.clear()
        if source == SOURCE_ELEMENT:
            for label in sorted(self._index_to_label.values()):
                combo.addItem(label)
        elif source == SOURCE_NODE:
            for n in sorted(self._node_list.keys()):
                info = self._node_list[n]
                for lbl in info['labels']:
                    desc = f"{lbl} (node {n}, {', '.join(info['elements'])})"
                    combo.addItem(desc, lbl)  # data=label for lookup
                if not info['labels']:
                    desc_parts = info['elements']
                    desc = f"node {n} ({', '.join(desc_parts)})" if desc_parts else f"node {n}"
                    combo.addItem(desc, str(n))
        combo.blockSignals(False)

    def _get_ident_text(self, row):
        """Get identifier text from either QComboBox or QLineEdit."""
        w = self.meas_table.cellWidget(row, COL_IDENT)
        if isinstance(w, QComboBox):
            data = w.currentData()
            if data:
                return str(data).strip()
            return w.currentText().strip()
        return w.text().strip()

    def _on_remove_row(self):
        """Remove the measurement row whose 'x' button was clicked."""
        btn = self.sender()
        for row in range(self.meas_table.rowCount()):
            if self.meas_table.cellWidget(row, COL_REMOVE) is btn:
                self.meas_table.removeRow(row)
                break
        self._update_preview()

    def _on_source_changed(self):
        """Rebuild identifier and property widgets when Source changes."""
        combo = self.sender()
        for row in range(self.meas_table.rowCount()):
            if self.meas_table.cellWidget(row, COL_SOURCE) is combo:
                source = combo.currentData()
                type_w = self.meas_table.cellWidget(row, COL_TYPE)

                # Replace identifier widget
                if source == SOURCE_EXPRESSION:
                    new_ident = QLineEdit()
                    new_ident.setPlaceholderText(
                        'Maxima expr, e.g. V_R1 * I_R1')
                    new_ident.textChanged.connect(self._update_preview)
                else:
                    new_ident = QComboBox()
                    new_ident.setEditable(True)
                    self._populate_ident_combo(new_ident, source)
                    new_ident.currentTextChanged.connect(self._update_preview)
                self.meas_table.setCellWidget(row, COL_IDENT, new_ident)

                # Rebuild property dropdown
                type_w.blockSignals(True)
                type_w.clear()
                type_w.setEnabled(True)
                if source == SOURCE_EXPRESSION:
                    type_w.addItem('(expression)', 'expression')
                    type_w.setEnabled(False)
                elif source == SOURCE_NODE:
                    type_w.addItem(
                        PROPERTY_DISPLAY['nodeVoltage'], 'nodeVoltage')
                else:
                    for p_key in ELEMENT_PROPERTIES:
                        type_w.addItem(PROPERTY_DISPLAY[p_key], p_key)
                type_w.blockSignals(False)
                break
        self._update_preview()

    def _on_type_changed(self):
        """Update preview when the Property dropdown changes."""
        self._update_preview()

    def _get_measurements(self):
        """Read all measurements from the table."""
        measurements = []
        for row in range(self.meas_table.rowCount()):
            source = self.meas_table.cellWidget(
                row, COL_SOURCE).currentData()
            identifier = self._get_ident_text(row)
            prop = self.meas_table.cellWidget(row, COL_TYPE).currentData()

            # Parse target: try float first, else treat as expression
            target_text = self.meas_table.cellWidget(
                row, COL_TARGET).text().strip()
            target_val = 0.0
            target_expr = ''
            try:
                target_val = float(target_text)
            except (ValueError, TypeError):
                target_expr = target_text

            tol = self.meas_table.cellWidget(row, COL_TOL).value()
            tol_type = self.meas_table.cellWidget(
                row, COL_TOLTYPE).currentData()
            container = self.meas_table.cellWidget(row, COL_GRADE)
            grade_chk = container.findChild(QCheckBox)
            graded = grade_chk.isChecked() if grade_chk else True

            # Resolve element_index from label_map
            elem_idx = self._label_map.get(identifier, -1) if source == SOURCE_ELEMENT else -1

            if identifier:
                measurements.append(Measurement(
                    source_type=source, identifier=identifier,
                    property=prop, target=target_val,
                    tolerance=tol, graded=graded,
                    tolerance_type=tol_type,
                    target_expr=target_expr,
                    element_index=elem_idx))
        return measurements

    def _clear_measurements(self):
        """Remove all rows from the measurement table."""
        while self.meas_table.rowCount() > 0:
            self.meas_table.removeRow(0)

    # ---- Question Variables table helpers ----

    def _add_qvar_row(self, label='', expression=''):
        """Add a row to the question variables table."""
        row = self.qvars_table.rowCount()
        self.qvars_table.insertRow(row)

        label_edit = QLineEdit(label)
        label_edit.setPlaceholderText('e.g. P_expected')
        label_edit.textChanged.connect(self._update_preview)
        self.qvars_table.setCellWidget(row, 0, label_edit)

        expr_edit = QLineEdit(expression)
        expr_edit.setPlaceholderText('e.g. 0.015')
        expr_edit.textChanged.connect(self._update_preview)
        self.qvars_table.setCellWidget(row, 1, expr_edit)

        rm_btn = QPushButton('x')
        rm_btn.setFixedWidth(28)
        rm_btn.clicked.connect(self._on_remove_qvar_row)
        self.qvars_table.setCellWidget(row, 2, rm_btn)

        self._update_preview()

    def _on_remove_qvar_row(self):
        """Remove the qvar row whose 'x' button was clicked."""
        btn = self.sender()
        for row in range(self.qvars_table.rowCount()):
            if self.qvars_table.cellWidget(row, 2) is btn:
                self.qvars_table.removeRow(row)
                break
        self._update_preview()

    def _get_qvars_text(self):
        """Build Maxima variable definitions from the qvars table."""
        lines = []
        for row in range(self.qvars_table.rowCount()):
            label = self.qvars_table.cellWidget(row, 0).text().strip()
            expr = self.qvars_table.cellWidget(row, 1).text().strip()
            if label and expr:
                lines.append(f'{label}: {expr};')
        return '\n'.join(lines)

    # ---- Component table helpers ----

    def _populate_components(self, elements, export_text=''):
        """Populate the enhanced component table from element info list."""
        # Preserve existing editable state, falling back to saved indices
        old_editable = self._get_editable_indices()
        if not old_editable and hasattr(self, '_saved_editable_indices'):
            old_editable = self._saved_editable_indices

        # Build labeling and connectivity
        self._elements = elements
        self._label_map, self._index_to_label = _assign_element_labels(
            elements)
        if export_text:
            self._node_list, self._element_nodes = (
                _build_node_connectivity(
                    export_text, elements, self._index_to_label))
        else:
            self._node_list = {}
            self._element_nodes = {}

        # Determine max post count for node columns (excluding wires)
        non_wire = [e for e in elements if e.get('type') != 'WireElm']
        max_posts = max((e.get('posts', 2) for e in non_wire), default=2)
        self._comp_node_count = max_posts

        # Rebuild table columns: Label, Type, Value, Node1..NodeN, Editable
        col_count = 3 + max_posts + 1  # base 3 + nodes + editable
        while self.comp_table.rowCount() > 0:
            self.comp_table.removeRow(0)
        self.comp_table.setColumnCount(col_count)
        headers = ['Label', 'Type', 'Value']
        for p in range(max_posts):
            headers.append(f'Node {p + 1}')
        headers.append('Editable')
        self.comp_table.setHorizontalHeaderLabels(headers)

        comp_header = self.comp_table.horizontalHeader()
        comp_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        comp_header.resizeSection(0, 50)
        comp_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        comp_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        comp_header.resizeSection(2, 90)
        for p in range(max_posts):
            col = 3 + p
            comp_header.setSectionResizeMode(
                col, QHeaderView.ResizeMode.Fixed)
            comp_header.resizeSection(col, 110)
        edit_col = col_count - 1
        comp_header.setSectionResizeMode(
            edit_col, QHeaderView.ResizeMode.Fixed)
        comp_header.resizeSection(edit_col, 60)

        # Populate rows (non-wire only)
        for elem in non_wire:
            idx = elem['index']
            label = self._index_to_label.get(idx, str(idx))
            row = self.comp_table.rowCount()
            self.comp_table.insertRow(row)

            # Label
            lbl_w = QLabel(label)
            lbl_w.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.comp_table.setCellWidget(row, 0, lbl_w)

            # Type (without value appended)
            self.comp_table.setCellWidget(row, 1, QLabel(elem['type']))

            # Value
            self.comp_table.setCellWidget(
                row, 2, QLabel(elem.get('value', '')))

            # Node columns
            nodes = self._element_nodes.get(idx, [])
            for p in range(max_posts):
                col = 3 + p
                if p < len(nodes):
                    n = nodes[p]
                    info = self._node_list.get(n, {})
                    desc_parts = (info.get('elements', [])
                                  + info.get('labels', []))
                    if desc_parts:
                        node_text = f"{n} ({', '.join(desc_parts)})"
                    else:
                        node_text = str(n)
                    self.comp_table.setCellWidget(
                        row, col, QLabel(node_text))
                else:
                    self.comp_table.setCellWidget(row, col, QLabel(''))

            # Editable checkbox
            chk_container = QWidget()
            chk_layout = QHBoxLayout(chk_container)
            chk_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chk_layout.setContentsMargins(0, 0, 0, 0)
            chk = QCheckBox()
            chk.setChecked(idx in old_editable)
            chk.stateChanged.connect(self._on_comp_editable_changed)
            chk_layout.addWidget(chk)
            self.comp_table.setCellWidget(row, edit_col, chk_container)

        self._update_comp_status()

    def _get_editable_indices(self):
        """Get set of element indices marked as editable."""
        if not self._label_map:
            return set()
        indices = set()
        edit_col = self.comp_table.columnCount() - 1
        for row in range(self.comp_table.rowCount()):
            container = self.comp_table.cellWidget(row, edit_col)
            chk = container.findChild(QCheckBox) if container else None
            lbl_w = self.comp_table.cellWidget(row, 0)
            if chk and chk.isChecked() and lbl_w:
                label = lbl_w.text()
                idx = self._label_map.get(label)
                if idx is not None:
                    indices.add(idx)
        return indices

    def _on_comp_editable_changed(self):
        """Update status and preview when component editability changes."""
        self._update_comp_status()
        self._update_preview()

    def _update_comp_status(self):
        """Update the component status label."""
        total = self.comp_table.rowCount()
        if total == 0:
            self.comp_status_label.setText('No components loaded')
            return
        editable = self._get_editable_indices()
        locked = total - len(editable)
        if editable:
            self.comp_status_label.setText(
                f'{locked} locked, {len(editable)} editable '
                f'(integrity checking ON)')
            self.comp_status_label.setStyleSheet('color: #090;')
        else:
            self.comp_status_label.setText(
                f'{total} components (integrity checking OFF — '
                f'check at least one to enable)')
            self.comp_status_label.setStyleSheet('color: #666;')

    def _on_refresh_components(self):
        """Refresh the component list from the simulator."""
        if (self._sim_panel.web_view is None):
            QMessageBox.information(
                self, 'Open Simulator First',
                'Open the Live Simulator and wait for it to load,\n'
                'then click Refresh.')
            return
        # Query elements directly from CircuitJS1 API rather than relying
        # on the cached circuitjs-elements message (which may have fired
        # before our listener was injected).
        self._sim_panel.web_view.page().runJavaScript(
            "(function() {"
            "  try {"
            "    var sim = window.CircuitJS1;"
            "    if (!sim) return null;"
            "    var elems = sim.getElements();"
            "    var info = [];"
            "    for (var i = 0; i < elems.length; i++) {"
            "      var e = elems[i];"
            "      var posts = 2;"
            "      try { posts = e.getPostCount(); } catch(x) {}"
            "      var lbl = '';"
            "      try { lbl = e.getLabelName() || ''; } catch(x) {}"
            "      info.push({ index: i, type: e.getType(), posts: posts, label: lbl });"
            "    }"
            "    var exported = sim.exportCircuit();"
            "    return JSON.stringify({ elements: info, export: exported });"
            "  } catch(e) { return null; }"
            "})()", 0,
            self._on_elements_result)

    def _on_elements_result(self, result):
        """Handle element list from simulator."""
        if not result or result == 'null':
            QMessageBox.warning(
                self, 'No Elements',
                'No element data available. Make sure the simulator\n'
                'has loaded and the circuit is running.')
            return
        try:
            data = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return

        elements = data.get('elements', [])
        export_text = data.get('export', '')

        # Parse element values from the export text
        values = _parse_element_values(export_text, elements)
        for i, elem in enumerate(elements):
            elem['value'] = values[i] if i < len(values) else ''

        self._populate_components(elements, export_text)
        non_wire = sum(1 for e in elements if e.get('type') != 'WireElm')
        self.statusBar().showMessage(
            f'Loaded {non_wire} components ({len(self._node_list)} nodes)')

    # ---- Signal wiring ----

    def _connect_signals(self):
        # Focus-based row selection
        QApplication.instance().focusChanged.connect(self._on_focus_changed)

        # Field -> preview update
        for w in (self.name_edit, self.category_edit):
            w.textChanged.connect(self._update_preview)
        for w in (self.desc_edit, self.ctz_edit):
            w.textChanged.connect(self._update_preview)
        self.editable_combo.currentIndexChanged.connect(self._update_preview)

        # Buttons
        self.add_meas_btn.clicked.connect(
            lambda: self._add_measurement_row())
        self.add_qvar_btn.clicked.connect(
            lambda: self._add_qvar_row())
        self.save_btn.clicked.connect(self._on_save)
        self.load_xml_btn.clicked.connect(self._on_load_xml)

        # Editable components
        self.refresh_comp_btn.clicked.connect(self._on_refresh_components)

    # ---- Helpers ----

    def _get_ctz(self):
        return extract_ctz(self.ctz_edit.toPlainText())

    def _get_editable_mode(self):
        """Return the current editable mode: 'all', 'values', or 'none'."""
        return self.editable_combo.currentData() or 'all'

    def _is_editable(self):
        """Whether the circuit is editable at all."""
        return self._get_editable_mode() != 'none'

    def _get_sim_url(self):
        """Build circuitjs.html URL for the GUI preview."""
        return _build_sim_url(
            self._get_ctz(), self._is_editable(),
            False, SIM_BASE_URL, html_escape=False)

    def _generate(self):
        measurements = self._get_measurements()
        mode = self._get_editable_mode()
        editable_indices = (sorted(self._get_editable_indices())
                            if mode == 'values' else [])
        return generate_xml(
            name=self.name_edit.text().strip()
                 or 'Untitled CircuitJS1 Question',
            description=(self.desc_edit.toPlainText().strip()
                         or 'Adjust the circuit as instructed.'),
            ctz=self._get_ctz(),
            measurements=measurements,
            editable_indices=editable_indices,
            editable=self._is_editable(),
            white_bg=False,
            rate=RATE_DEFAULT,
            hide_input=True,
            base_url=SIM_BASE_URL,
            category=self.category_edit.text().strip(),
            custom_qvars=self._get_qvars_text(),
        )

    def _validate(self):
        warnings = []
        if not self._get_ctz():
            warnings.append('- No CTZ value: question will not load a circuit')
        measurements = self._get_measurements()
        if not measurements:
            warnings.append('- No measurements: no values will be read')
        graded = [m for m in measurements if m.graded]
        if not graded:
            warnings.append('- No graded measurements: question has no score')
        for m in measurements:
            if not m.identifier:
                warnings.append('- Empty identifier in measurement row')
        return warnings

    def _safe_filename(self):
        name = self.name_edit.text().strip() or 'question'
        safe = re.sub(r'[^\w\s-]', '', name).strip().replace(' ', '_')[:50]
        return f'{safe}.xml'

    def _last_dir(self):
        return self.settings.value(
            'last_save_dir',
            str(Path(__file__).resolve().parent.parent / 'deploy'))

    # ---- Slots ----

    def _update_preview(self):
        try:
            self._generate()
            self.statusBar().showMessage('Ready')
        except Exception as e:
            self.statusBar().showMessage(f'Error: {e}')

    def _on_save(self):
        warnings = self._validate()
        if warnings:
            reply = QMessageBox.warning(
                self, 'Warnings',
                '\n'.join(warnings) + '\n\nSave anyway?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
        xml = self._generate()
        path, _ = QFileDialog.getSaveFileName(
            self, 'Save Question XML',
            str(Path(self._last_dir()) / self._safe_filename()),
            'XML Files (*.xml);;All Files (*)')
        if path:
            Path(path).write_text(xml, encoding='utf-8')
            self.settings.setValue('last_save_dir', str(Path(path).parent))
            self._save_settings()
            self.statusBar().showMessage(f'Saved: {path}')

    def _on_load_xml(self):
        path, _ = QFileDialog.getOpenFileName(
            self, 'Load Question XML',
            str(Path(self._last_dir())),
            'XML Files (*.xml);;All Files (*)')
        if not path:
            return
        try:
            self._load_from_xml(Path(path).read_text(encoding='utf-8'))
            self.settings.setValue('last_save_dir', str(Path(path).parent))
            self.statusBar().showMessage(f'Loaded: {path}')
        except Exception as e:
            QMessageBox.warning(self, 'Load Error', str(e))

    def _load_from_xml(self, xml_text):
        """Parse a previously-saved Moodle STACK XML and populate the GUI."""
        import xml.etree.ElementTree as ET

        root = ET.fromstring(xml_text)
        q = root.find('.//question[@type="stack"]')
        if q is None:
            raise ValueError('No <question type="stack"> found in XML')

        # Name
        name_el = q.find('name/text')
        if name_el is not None and name_el.text:
            self.name_edit.setText(name_el.text)

        # Category
        cat_q = root.find('.//question[@type="category"]/category/text')
        if cat_q is not None and cat_q.text:
            self.category_edit.setText(cat_q.text)

        # Description & CTZ from questiontext CDATA
        qt = q.find('questiontext/text')
        if qt is not None and qt.text:
            cdata = qt.text
            # Extract description from first <p>
            desc_m = re.search(r'<p>(.*?)</p>', cdata, re.DOTALL)
            if desc_m:
                self.desc_edit.setPlainText(
                    desc_m.group(1).replace('&amp;', '&')
                                   .replace('&lt;', '<')
                                   .replace('&gt;', '>'))
            # Extract CTZ from iframe src
            ctz_m = re.search(r'[?&]ctz=([^&"\'<>\s]+)', cdata)
            if ctz_m:
                self.ctz_edit.setPlainText(ctz_m.group(1))

        # Reload simulator with the loaded circuit
        self._sim_panel.start(self._get_sim_url())

    # ---- Settings persistence ----

    def _save_settings(self):
        s = self.settings
        s.setValue('name', self.name_edit.text())
        s.setValue('category', self.category_edit.text())
        s.setValue('ctz', self.ctz_edit.toPlainText())
        s.setValue('editable_mode', self._get_editable_mode())
        # Save qvars table as JSON list of [label, expression] pairs
        qvars_data = []
        for row in range(self.qvars_table.rowCount()):
            label = self.qvars_table.cellWidget(row, 0).text()
            expr = self.qvars_table.cellWidget(row, 1).text()
            qvars_data.append([label, expr])
        s.setValue('qvars_json', json.dumps(qvars_data))
        # Save measurements as JSON (includes new fields)
        measurements = self._get_measurements()
        s.setValue('measurements_json', json.dumps(
            [asdict(m) for m in measurements]))
        # Save editable indices
        s.setValue('editable_indices', json.dumps(
            sorted(self._get_editable_indices())))
        # Save index_to_label for populating dropdowns before simulator loads
        s.setValue('index_to_label', json.dumps(self._index_to_label))

    def _restore_settings(self):
        s = self.settings
        if s.contains('name'):
            self.name_edit.setText(s.value('name', ''))
        if s.contains('category'):
            self.category_edit.setText(s.value('category', ''))
        if s.contains('ctz'):
            self.ctz_edit.setPlainText(s.value('ctz', ''))

        if s.contains('editable_mode'):
            mode = s.value('editable_mode', 'all')
            mode_map = {'all': 0, 'values': 1, 'none': 2}
            self.editable_combo.setCurrentIndex(mode_map.get(mode, 0))

        if s.contains('qvars_json'):
            try:
                for label, expr in json.loads(s.value('qvars_json', '[]')):
                    self._add_qvar_row(label, expr)
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        # Restore index_to_label for dropdown population
        if s.contains('index_to_label'):
            try:
                raw = json.loads(s.value('index_to_label', '{}'))
                self._index_to_label = {int(k): v for k, v in raw.items()}
                self._label_map = {v: k for k, v in
                                   self._index_to_label.items()}
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        # Restore editable indices (applied when component table is populated)
        self._saved_editable_indices = set()
        if s.contains('editable_indices'):
            try:
                self._saved_editable_indices = set(
                    json.loads(s.value('editable_indices', '[]')))
            except (json.JSONDecodeError, TypeError):
                pass

        if s.contains('measurements_json'):
            try:
                data = json.loads(s.value('measurements_json', '[]'))
                for d in data:
                    self._add_measurement_row(
                        source=d.get('source_type', SOURCE_NODE),
                        identifier=d.get('identifier', ''),
                        prop=d.get('property', 'nodeVoltage'),
                        target=d.get('target', 0.0),
                        tolerance=d.get('tolerance', 0.1),
                        graded=d.get('graded', True),
                        tolerance_type=d.get('tolerance_type', 'absolute'),
                        target_expr=d.get('target_expr', ''))
            except (json.JSONDecodeError, TypeError):
                pass

    def closeEvent(self, event):
        if hasattr(self._sim_panel, '_poll_timer'):
            self._sim_panel._poll_timer.stop()
        self._save_settings()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
