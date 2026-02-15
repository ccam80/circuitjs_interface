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
    QSpinBox, QDoubleSpinBox, QCheckBox, QComboBox, QPushButton,
    QFileDialog, QMessageBox, QTableWidget, QHeaderView,
    QAbstractItemView,
)
from PyQt6.QtCore import Qt, QSettings, QTimer, QUrl, QEvent
from PyQt6.QtGui import QFont

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    HAS_WEBENGINE = True
except ImportError:
    HAS_WEBENGINE = False

BRIDGE_BASE_URL = "https://ccam80.github.io/circuitjs-moodle/bridge.html"

EXAMPLE_CTZ = (
    "CQAgjCAMB0l3BWEA2aAWB8CcBmSy0AOBBAdkJE0sskoFMBaMMAKAGMQAmNWsZ"
    "Wnr34g0UWPAidomSFgSccpTmBwICyMXAiQWAJxFxwwwV0IVaqlgHkDQ2gjCcjt"
    "FywDmt0xRM5OLqCxgpLTcdlyQFHwW4eDw1DCsfqSeUSJ84JzeIABmAJYANgAuLE"
    "lcSs5lTo5ZAIIAwiwA9iJGUAZYFEgw8LKkyGqcXVwtOCC5AHZNw+kCshRSXeJw"
    "WH0DSBAQjQCuxQAWIKPaLEA"
)

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

# Units for common Falstad element types (first parameter value)
ELEMENT_TYPE_UNITS = {
    'ResistorElm': 'Ω', 'CapacitorElm': 'F', 'InductorElm': 'H',
    'VoltageElm': 'V', 'CurrentElm': 'A', 'DiodeElm': '',
    'PotElm': 'Ω', 'VarRailElm': 'V', 'RailElm': 'V',
}

# Non-element line prefixes in Falstad export format
_EXPORT_NON_ELEMENT = {'$', 'w', 'o', '38', 'h', '&'}


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
    """
    lines = []
    for line in export_text.split('\n'):
        line = line.strip()
        if not line:
            continue
        prefix = line.split(' ', 1)[0]
        if prefix not in _EXPORT_NON_ELEMENT:
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


@dataclass
class Measurement:
    source_type: str    # 'node' or 'element'
    identifier: str     # node label ('vout') or element index ('3')
    property: str       # one of PROPERTIES
    target: float
    tolerance: float
    graded: bool

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
        return PROPERTY_UNITS.get(self.property, 'V')

    def data_key(self):
        """Key used in event.data.values from the bridge."""
        if self.source_type == SOURCE_NODE:
            return self.identifier              # 'vout'
        return f'{self.identifier}:{self.property}'  # '3:current'

    def display_name(self):
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


def _build_bridge_url(ctz, nodes, elements, editable, rate, white_bg,
                      base_url, editable_indices=None, html_escape=True):
    sep = '&amp;' if html_escape else '&'
    parts = [f'ctz={ctz}']
    if nodes:
        parts.append(f'nodes={",".join(nodes)}')
    if elements:
        parts.append(f'elements={",".join(elements)}')
    if editable_indices:
        parts.append(f'editableIndices={",".join(str(i) for i in editable_indices)}')
    parts.append(f'editable={"true" if editable else "false"}')
    parts.append(f'rate={rate}')
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


def _derive_bridge_params(measurements):
    """Split measurements into bridge URL params: nodes and elements lists."""
    nodes = []
    elements = []
    for m in measurements:
        if m.source_type == SOURCE_NODE:
            if m.identifier not in nodes:
                nodes.append(m.identifier)
        else:
            key = f'{m.identifier}:{m.property}'
            if key not in elements:
                elements.append(key)
    return nodes, elements


def _build_readout_html(measurements, has_integrity=False):
    """Build HTML readout lines for all measurements."""
    lines = []
    for i, m in enumerate(measurements):
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


def _build_js_block(measurements, has_integrity=False):
    """Build the [[script]] JS block that reads values and writes to STACK inputs."""
    graded = [(i, m) for i, m in enumerate(measurements) if m.graded]

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
    js += "\n"

    js += "window.addEventListener('message', function(event) {\n"
    js += "  if (!event.data || event.data.type !== 'circuitjs-data') return;\n"
    js += "  var v;\n\n"

    # Update display for all measurements
    for i, m in enumerate(measurements):
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
                 rate=2, hide_input=False, base_url=BRIDGE_BASE_URL,
                 category=''):
    """Generate complete Moodle XML for a STACK + CircuitJS1 question."""

    if editable_indices is None:
        editable_indices = []
    has_integrity = len(editable_indices) > 0

    nodes, elements = _derive_bridge_params(measurements)
    bridge_url = _build_bridge_url(
        ctz, nodes, elements, editable, rate, white_bg, base_url,
        editable_indices=editable_indices, html_escape=True)
    readout_html = _build_readout_html(measurements, has_integrity)
    js_block = _build_js_block(measurements, has_integrity)

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
        qvar_lines.append(f'target_{iname}: {_fmt(m.target)};')
        qvar_lines.append(f'tol_{iname}: {_fmt(m.tolerance)};')
    if has_integrity:
        qvar_lines.append('expected_integrity: 1;')
    qvars = '\n'.join(qvar_lines) if qvar_lines else '/* no graded measurements */'

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
    p.append('<p><em>Right-click a component and choose "Edit..." to change '
             'its value. The readout updates live.</em></p>\n\n')
    p.append('[[iframe height="640px" width="830px"]]\n')
    p.append('<div style="font-family:sans-serif;">\n\n')
    p.append(f'  <iframe id="sim-bridge"\n    src="{bridge_url}"\n')
    p.append('    width="800" height="550" style="border:1px solid #ccc;">\n')
    p.append('  </iframe>\n\n')
    p.append('  <div id="readout" style="font-family:monospace; padding:8px; '
             'font-size:14px;\n')
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

    # --- STACK plugin ---
    p.append('    <plugin qtype="stack">\n')
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
    p.append('      <decimals>\n        <text>.</text>\n      </decimals>\n')
    p.append('      <scientificnotation>\n        <text>*10</text>\n'
             '      </scientificnotation>\n')
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

    # --- PRTs (one per graded measurement) ---
    prt_weight = _fmt(1.0 / n_graded)
    for j, (i, m) in enumerate(graded):
        iname = m.input_name(i)
        prt_name = f'prt{j + 1}'
        # Node numbering: with integrity gate, Node 0 = integrity check,
        # Node 1 = value check. Without integrity, Node 0 = value check.
        value_node = '1' if has_integrity else '0'
        p.append('      <prt>\n')
        p.append(f'        <name>{prt_name}</name>\n')
        p.append(f'        <value>{prt_weight}</value>\n')
        p.append('        <autosimplify>1</autosimplify>\n')
        p.append('        <feedbackstyle>1</feedbackstyle>\n')
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
        p.append('        <node>\n')
        p.append(f'          <name>{value_node}</name>\n')
        p.append(f'          <description>Check {m.display_name()} '
                 f'against target</description>\n')
        p.append('          <answertest>NumAbsolute</answertest>\n')
        p.append(f'          <sans>{iname}</sans>\n')
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
        for j in range(n_graded):
            prt_name = f'prt{j + 1}'
            p.append(f'        <expected>\n          <name>{prt_name}</name>\n'
                     f'          <expectedscore>0.0000000</expectedscore>\n'
                     f'          <expectedpenalty>0.1000000</expectedpenalty>\n'
                     f'          <expectedanswernote>'
                     f'{prt_name}-0-F</expectedanswernote>\n'
                     f'        </expected>\n')
        p.append('      </qtest>\n')

    # close plugin
    p.append('    </plugin>\n')

    # tags
    p.append('    <tags>\n      <tag>\n        <text>circuitjs</text>\n'
             '      </tag>\n    </tags>\n')

    p.append('  </question>\n</quiz>\n')

    return ''.join(p)


# ---------------------------------------------------------------------------
# Qt GUI
# ---------------------------------------------------------------------------

# Measurement table columns
COL_SOURCE = 0
COL_IDENT  = 1
COL_TYPE   = 2
COL_TARGET = 3
COL_TOL    = 4
COL_GRADE  = 5
COL_REMOVE = 6
MEAS_COLUMNS = ['Source', 'Identifier', 'Property', 'Target', 'Tolerance',
                'Grade', '']


class SimulatorWindow(QWidget):
    """Separate resizable window showing the live CircuitJS1 simulator."""

    def __init__(self, main_window):
        super().__init__()
        self.main = main_window
        self.setWindowTitle('CircuitJS1 Live Simulator')
        self.resize(900, 720)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

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

        # Web view
        self.web_view = QWebEngineView()
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
        """Load (or reload) the bridge URL."""
        self._poll_timer.stop()
        self._latest_values = {}
        self._sim_connected = False
        self.use_btn.setEnabled(False)
        self.save_circuit_btn.setEnabled(False)
        self.readout.setPlainText('Loading simulator...')
        self.web_view.setUrl(QUrl(url))

    def _on_reload(self):
        ctz = self.main._get_ctz()
        if not ctz:
            return
        self.start(self.main._get_bridge_url(html_escape=False))

    def _on_loaded(self, ok):
        if not ok:
            self.readout.setPlainText('Failed to load simulator page.')
            return
        # Inject listener: bridge.html posts to window.parent which is
        # itself when top-level. We capture values into a global.
        # Also inject an export helper that grabs circuit text from the
        # same-origin circuitjs.html iframe.
        self.web_view.page().runJavaScript(
            "window._qgen_values = null;"
            "window._qgen_elements = null;"
            "window.addEventListener('message', function(e) {"
            "  if (e.data && e.data.type === 'circuitjs-data') {"
            "    window._qgen_values = e.data.values;"
            "    window._qgen_connected = true;"
            "  }"
            "  if (e.data && e.data.type === 'circuitjs-elements') {"
            "    window._qgen_elements = e.data.elements;"
            "  }"
            "});"
            "window._qgen_connected = false;"
            "window._qgen_exportCircuit = function() {"
            "  try {"
            "    var f = document.getElementById('sim-frame');"
            "    return f.contentWindow.CircuitJS1.exportCircuit();"
            "  } catch(e) { return null; }"
            "};")
        self.readout.setPlainText('Simulator loaded. Waiting for first data...')
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
            key_info[m.data_key()] = {
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
        identifier = ident_w.text().strip()
        if not identifier:
            QMessageBox.warning(
                self, 'Empty Identifier',
                'The selected measurement row has no identifier.\n'
                'Enter a node label or element index first.')
            return
        source = source_w.currentData()
        prop = type_w.currentData()
        if source == SOURCE_NODE:
            key = identifier
        else:
            key = f'{identifier}:{prop}'
        val = self._latest_values.get(key)
        if val is not None:
            tbl.cellWidget(row, COL_TARGET).setValue(round(val, 6))
            self.main.statusBar().showMessage(
                f'Target set to {val:.6f} from "{key}"')
        else:
            QMessageBox.warning(
                self, 'No Value',
                f'No simulator value for key "{key}".\n'
                f'Check the label matches a circuit node/element.')

    def closeEvent(self, event):
        self._poll_timer.stop()
        super().closeEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('CircuitJS1 STACK Question Generator')
        self.setMinimumSize(780, 900)
        self.settings = QSettings('FalstadSTACK', 'QuestionGenerator')
        self._build_ui()
        self._connect_signals()
        self._restore_settings()
        self._update_preview()

    # ---- UI construction ----

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(6)

        # --- Question group ---
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

        layout.addWidget(q_grp)

        # --- Circuit group ---
        c_grp = QGroupBox('Circuit')
        c_lay = QVBoxLayout(c_grp)

        c_lay.addWidget(QLabel(
            'CTZ value (paste Falstad "Export As Link" URL or raw ctz):'))
        self.ctz_edit = QPlainTextEdit()
        self.ctz_edit.setMaximumHeight(55)
        self.ctz_edit.setPlaceholderText(
            'https://falstad.com/circuit/circuitjs.html?ctz=CQAg... or raw')
        c_lay.addWidget(self.ctz_edit)

        row2 = QHBoxLayout()
        self.editable_chk = QCheckBox('Editable')
        self.editable_chk.setChecked(True)
        self.white_bg_chk = QCheckBox('White background')
        self.white_bg_chk.setChecked(True)
        row2.addWidget(self.editable_chk)
        row2.addWidget(self.white_bg_chk)
        row2.addWidget(QLabel('Rate:'))
        self.rate_spin = QSpinBox()
        self.rate_spin.setRange(1, 10)
        self.rate_spin.setValue(2)
        self.rate_spin.setSuffix(' /sec')
        row2.addWidget(self.rate_spin)
        row2.addStretch()
        c_lay.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel('Bridge URL:'))
        self.url_edit = QLineEdit(BRIDGE_BASE_URL)
        row3.addWidget(self.url_edit)
        c_lay.addLayout(row3)

        layout.addWidget(c_grp)

        # --- Measurements group ---
        m_grp = QGroupBox('Measurements')
        m_lay = QVBoxLayout(m_grp)

        m_lay.addWidget(QLabel(
            'Node = labeled node voltage. Element = component by index '
            '(use Refresh from Simulator to see indices).'))

        self.meas_table = QTableWidget(0, len(MEAS_COLUMNS))
        self.meas_table.setHorizontalHeaderLabels(MEAS_COLUMNS)
        header = self.meas_table.horizontalHeader()
        header.setSectionResizeMode(COL_SOURCE, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(COL_SOURCE, 75)
        header.setSectionResizeMode(COL_IDENT, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_TYPE, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(COL_TYPE, 120)
        header.setSectionResizeMode(COL_TARGET, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(COL_TARGET, 120)
        header.setSectionResizeMode(COL_TOL, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(COL_TOL, 100)
        header.setSectionResizeMode(COL_GRADE, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(COL_GRADE, 50)
        header.setSectionResizeMode(COL_REMOVE, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(COL_REMOVE, 30)
        self.meas_table.verticalHeader().setVisible(False)
        self.meas_table.setMaximumHeight(200)
        self.meas_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.meas_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        m_lay.addWidget(self.meas_table)

        meas_btn_row = QHBoxLayout()
        self.add_meas_btn = QPushButton('+ Add Measurement')
        meas_btn_row.addWidget(self.add_meas_btn)
        meas_btn_row.addStretch()
        self.hide_chk = QCheckBox(
            'Hide input fields (for production; uncheck to test grading)')
        meas_btn_row.addWidget(self.hide_chk)
        m_lay.addLayout(meas_btn_row)

        layout.addWidget(m_grp)

        # --- Editable Components group (integrity checking) ---
        e_grp = QGroupBox('Editable Components (Integrity Checking)')
        e_lay = QVBoxLayout(e_grp)

        e_lay.addWidget(QLabel(
            'Check "Editable" for components students may modify. '
            'All other components are locked — editing them fails the '
            'integrity check (score = 0). Leave all unchecked to disable '
            'integrity checking.'))

        COMP_COLUMNS = ['Index', 'Type', 'Editable']
        self.comp_table = QTableWidget(0, len(COMP_COLUMNS))
        self.comp_table.setHorizontalHeaderLabels(COMP_COLUMNS)
        comp_header = self.comp_table.horizontalHeader()
        comp_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        comp_header.resizeSection(0, 50)
        comp_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        comp_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        comp_header.resizeSection(2, 65)
        self.comp_table.verticalHeader().setVisible(False)
        self.comp_table.setMaximumHeight(150)
        e_lay.addWidget(self.comp_table)

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
        e_lay.addLayout(comp_btn_row)

        layout.addWidget(e_grp)

        # --- Action buttons ---
        btn_row = QHBoxLayout()
        self.example_btn = QPushButton('Load Example')
        self.save_btn = QPushButton('Save XML...')
        self.save_btn.setDefault(True)
        self.copy_btn = QPushButton('Copy to Clipboard')
        self.sim_btn = QPushButton('Live Simulator...')
        btn_row.addWidget(self.example_btn)
        btn_row.addWidget(self.sim_btn)
        btn_row.addStretch()
        btn_row.addWidget(self.save_btn)
        btn_row.addWidget(self.copy_btn)
        layout.addLayout(btn_row)

        # --- XML Preview ---
        mono = QFont('Consolas', 9)
        mono.setStyleHint(QFont.StyleHint.Monospace)

        p_grp = QGroupBox('XML Preview')
        p_lay = QVBoxLayout(p_grp)
        p_lay.setContentsMargins(4, 4, 4, 4)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(mono)
        p_lay.addWidget(self.preview)
        layout.addWidget(p_grp, stretch=1)

        # Simulator window (created on demand)
        self._sim_window = None

        self.statusBar().showMessage('Ready')

    # ---- Event filter for table row selection ----

    def eventFilter(self, obj, event):
        """Select the measurement table row when any cell widget gets focus."""
        if event.type() == QEvent.Type.FocusIn:
            # Walk up to find which row this widget belongs to
            for row in range(self.meas_table.rowCount()):
                for col in range(self.meas_table.columnCount()):
                    w = self.meas_table.cellWidget(row, col)
                    if w is not None and (w is obj or w.isAncestorOf(obj)):
                        self.meas_table.selectRow(row)
                        return False
        return super().eventFilter(obj, event)

    def _install_row_select_filter(self, widget):
        """Install event filter on widget and all focusable children."""
        widget.installEventFilter(self)
        for child in widget.findChildren(QWidget):
            child.installEventFilter(self)

    # ---- Measurement table helpers ----

    def _add_measurement_row(self, source='node', identifier='',
                             prop='nodeVoltage', target=0.0,
                             tolerance=0.1, graded=True):
        """Add a new row to the measurement table."""
        row = self.meas_table.rowCount()
        self.meas_table.insertRow(row)

        # Source (Node / Element)
        source_combo = QComboBox()
        source_combo.addItem('Node', SOURCE_NODE)
        source_combo.addItem('Element', SOURCE_ELEMENT)
        source_combo.setCurrentIndex(0 if source == SOURCE_NODE else 1)
        source_combo.currentIndexChanged.connect(self._on_source_changed)
        self.meas_table.setCellWidget(row, COL_SOURCE, source_combo)

        # Identifier (node label or element index)
        ident_edit = QLineEdit(identifier)
        if source == SOURCE_NODE:
            ident_edit.setPlaceholderText('node label, e.g. vout')
        else:
            ident_edit.setPlaceholderText('element index, e.g. 3')
        ident_edit.textChanged.connect(self._update_preview)
        self.meas_table.setCellWidget(row, COL_IDENT, ident_edit)

        # Property
        type_combo = QComboBox()
        if source == SOURCE_NODE:
            type_combo.addItem(PROPERTY_DISPLAY['nodeVoltage'], 'nodeVoltage')
        else:
            for p_key in ELEMENT_PROPERTIES:
                type_combo.addItem(PROPERTY_DISPLAY[p_key], p_key)
            if prop in ELEMENT_PROPERTIES:
                idx = ELEMENT_PROPERTIES.index(prop)
                type_combo.setCurrentIndex(idx)
        type_combo.currentIndexChanged.connect(self._on_type_changed)
        self.meas_table.setCellWidget(row, COL_TYPE, type_combo)

        # Target
        unit = PROPERTY_UNITS.get(prop, 'V')
        target_spin = QDoubleSpinBox()
        target_spin.setRange(-1e6, 1e6)
        target_spin.setDecimals(6)
        target_spin.setSingleStep(0.1)
        target_spin.setValue(target)
        target_spin.setSuffix(f' {unit}')
        target_spin.valueChanged.connect(self._update_preview)
        self.meas_table.setCellWidget(row, COL_TARGET, target_spin)

        # Tolerance
        tol_spin = QDoubleSpinBox()
        tol_spin.setRange(0, 1e6)
        tol_spin.setDecimals(6)
        tol_spin.setSingleStep(0.01)
        tol_spin.setValue(tolerance)
        tol_spin.setSuffix(f' {unit}')
        tol_spin.valueChanged.connect(self._update_preview)
        self.meas_table.setCellWidget(row, COL_TOL, tol_spin)

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

        # Install event filters so clicking any widget selects the row
        for w in (source_combo, ident_edit, type_combo, target_spin,
                  tol_spin, grade_container, rm_btn):
            self._install_row_select_filter(w)

        self._update_preview()

    def _on_remove_row(self):
        """Remove the measurement row whose 'x' button was clicked."""
        btn = self.sender()
        for row in range(self.meas_table.rowCount()):
            if self.meas_table.cellWidget(row, COL_REMOVE) is btn:
                self.meas_table.removeRow(row)
                break
        self._update_preview()

    def _on_source_changed(self):
        """Rebuild the Property dropdown when Source changes."""
        combo = self.sender()
        for row in range(self.meas_table.rowCount()):
            if self.meas_table.cellWidget(row, COL_SOURCE) is combo:
                source = combo.currentData()
                ident_w = self.meas_table.cellWidget(row, COL_IDENT)
                type_w = self.meas_table.cellWidget(row, COL_TYPE)

                # Update placeholder text
                if source == SOURCE_NODE:
                    ident_w.setPlaceholderText('node label, e.g. vout')
                else:
                    ident_w.setPlaceholderText('element index, e.g. 3')

                # Rebuild property dropdown
                type_w.blockSignals(True)
                type_w.clear()
                if source == SOURCE_NODE:
                    type_w.addItem(
                        PROPERTY_DISPLAY['nodeVoltage'], 'nodeVoltage')
                else:
                    for p_key in ELEMENT_PROPERTIES:
                        type_w.addItem(PROPERTY_DISPLAY[p_key], p_key)
                type_w.blockSignals(False)

                # Update unit suffix
                prop = type_w.currentData()
                unit = PROPERTY_UNITS.get(prop, 'V')
                self.meas_table.cellWidget(row, COL_TARGET).setSuffix(
                    f' {unit}')
                self.meas_table.cellWidget(row, COL_TOL).setSuffix(
                    f' {unit}')
                break
        self._update_preview()

    def _on_type_changed(self):
        """Update unit suffixes when the Property dropdown changes."""
        combo = self.sender()
        for row in range(self.meas_table.rowCount()):
            if self.meas_table.cellWidget(row, COL_TYPE) is combo:
                prop = combo.currentData()
                unit = PROPERTY_UNITS.get(prop, 'V')
                self.meas_table.cellWidget(row, COL_TARGET).setSuffix(
                    f' {unit}')
                self.meas_table.cellWidget(row, COL_TOL).setSuffix(
                    f' {unit}')
                break
        self._update_preview()

    def _get_measurements(self):
        """Read all measurements from the table."""
        measurements = []
        for row in range(self.meas_table.rowCount()):
            source = self.meas_table.cellWidget(
                row, COL_SOURCE).currentData()
            identifier = self.meas_table.cellWidget(
                row, COL_IDENT).text().strip()
            prop = self.meas_table.cellWidget(row, COL_TYPE).currentData()
            target = self.meas_table.cellWidget(row, COL_TARGET).value()
            tol = self.meas_table.cellWidget(row, COL_TOL).value()
            container = self.meas_table.cellWidget(row, COL_GRADE)
            grade_chk = container.findChild(QCheckBox)
            graded = grade_chk.isChecked() if grade_chk else True
            if identifier:  # skip rows with empty identifiers
                measurements.append(Measurement(
                    source_type=source, identifier=identifier,
                    property=prop, target=target,
                    tolerance=tol, graded=graded))
        return measurements

    def _clear_measurements(self):
        """Remove all rows from the measurement table."""
        while self.meas_table.rowCount() > 0:
            self.meas_table.removeRow(0)

    # ---- Component table helpers ----

    def _populate_components(self, elements):
        """Populate the editable components table from element info list."""
        # Preserve existing editable state, falling back to saved indices
        old_editable = self._get_editable_indices()
        if not old_editable and hasattr(self, '_saved_editable_indices'):
            old_editable = self._saved_editable_indices

        while self.comp_table.rowCount() > 0:
            self.comp_table.removeRow(0)

        for elem in elements:
            row = self.comp_table.rowCount()
            self.comp_table.insertRow(row)

            idx_label = QLabel(str(elem['index']))
            idx_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.comp_table.setCellWidget(row, 0, idx_label)

            type_str = elem['type']
            value_str = elem.get('value', '')
            if value_str:
                type_str = f"{type_str}  ({value_str})"
            type_label = QLabel(type_str)
            self.comp_table.setCellWidget(row, 1, type_label)

            chk_container = QWidget()
            chk_layout = QHBoxLayout(chk_container)
            chk_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chk_layout.setContentsMargins(0, 0, 0, 0)
            chk = QCheckBox()
            chk.setChecked(elem['index'] in old_editable)
            chk.stateChanged.connect(self._on_comp_editable_changed)
            chk_layout.addWidget(chk)
            self.comp_table.setCellWidget(row, 2, chk_container)

        self._update_comp_status()

    def _get_editable_indices(self):
        """Get set of element indices marked as editable."""
        indices = set()
        for row in range(self.comp_table.rowCount()):
            container = self.comp_table.cellWidget(row, 2)
            chk = container.findChild(QCheckBox) if container else None
            idx_label = self.comp_table.cellWidget(row, 0)
            if chk and chk.isChecked() and idx_label:
                try:
                    indices.add(int(idx_label.text()))
                except ValueError:
                    pass
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
        if (self._sim_window is None or
                not hasattr(self._sim_window, 'web_view')):
            QMessageBox.information(
                self, 'Open Simulator First',
                'Open the Live Simulator and wait for it to load,\n'
                'then click Refresh.')
            return
        # Query elements directly from CircuitJS1 API rather than relying
        # on the cached circuitjs-elements message (which may have fired
        # before our listener was injected).
        self._sim_window.web_view.page().runJavaScript(
            "(function() {"
            "  try {"
            "    var f = document.getElementById('sim-frame');"
            "    var sim = f.contentWindow.CircuitJS1;"
            "    var elems = sim.getElements();"
            "    var info = [];"
            "    for (var i = 0; i < elems.length; i++) {"
            "      var e = elems[i];"
            "      var posts = 2;"
            "      try { posts = e.getPostCount(); } catch(x) {}"
            "      info.push({ index: i, type: e.getType(), posts: posts });"
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

        self._populate_components(elements)
        self.statusBar().showMessage(
            f'Loaded {len(elements)} components from simulator')

    # ---- Signal wiring ----

    def _connect_signals(self):
        # Field -> preview update
        for w in (self.name_edit, self.url_edit, self.category_edit):
            w.textChanged.connect(self._update_preview)
        for w in (self.desc_edit, self.ctz_edit):
            w.textChanged.connect(self._update_preview)
        self.rate_spin.valueChanged.connect(self._update_preview)
        for w in (self.editable_chk, self.white_bg_chk, self.hide_chk):
            w.stateChanged.connect(self._update_preview)

        # Buttons
        self.add_meas_btn.clicked.connect(
            lambda: self._add_measurement_row())
        self.save_btn.clicked.connect(self._on_save)
        self.copy_btn.clicked.connect(self._on_copy)
        self.example_btn.clicked.connect(self._on_load_example)

        # Editable components
        self.refresh_comp_btn.clicked.connect(self._on_refresh_components)

        # Simulator window
        self.sim_btn.clicked.connect(self._on_open_simulator)

    # ---- Helpers ----

    def _get_ctz(self):
        return extract_ctz(self.ctz_edit.toPlainText())

    def _get_bridge_url(self, html_escape=False):
        """Build bridge URL from current measurements and settings."""
        measurements = self._get_measurements()
        nodes, elements = _derive_bridge_params(measurements)
        editable_indices = sorted(self._get_editable_indices())
        return _build_bridge_url(
            self._get_ctz(), nodes, elements,
            self.editable_chk.isChecked(),
            self.rate_spin.value(),
            self.white_bg_chk.isChecked(),
            self.url_edit.text().strip() or BRIDGE_BASE_URL,
            editable_indices=editable_indices,
            html_escape=html_escape)

    def _generate(self):
        measurements = self._get_measurements()
        editable_indices = sorted(self._get_editable_indices())
        return generate_xml(
            name=self.name_edit.text().strip()
                 or 'Untitled CircuitJS1 Question',
            description=(self.desc_edit.toPlainText().strip()
                         or 'Adjust the circuit as instructed.'),
            ctz=self._get_ctz(),
            measurements=measurements,
            editable_indices=editable_indices,
            editable=self.editable_chk.isChecked(),
            white_bg=self.white_bg_chk.isChecked(),
            rate=self.rate_spin.value(),
            hide_input=self.hide_chk.isChecked(),
            base_url=self.url_edit.text().strip() or BRIDGE_BASE_URL,
            category=self.category_edit.text().strip(),
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
            xml = self._generate()
            self.preview.setPlainText(xml)
            self.statusBar().showMessage('Preview updated')
        except Exception as e:
            self.preview.setPlainText(f'Error generating XML:\n{e}')
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

    def _on_copy(self):
        xml = self._generate()
        QApplication.clipboard().setText(xml)
        self.statusBar().showMessage('XML copied to clipboard')

    def _on_load_example(self):
        self.name_edit.setText('CircuitJS1 Integration Test')
        self.desc_edit.setPlainText(
            'Observe the filter circuit output voltage. The simulator '
            'writes to the answer field automatically.')
        self.ctz_edit.setPlainText(EXAMPLE_CTZ)
        self._clear_measurements()
        self._add_measurement_row('node', 'AC', 'nodeVoltage',
                                  2.5, 0.5, False)
        self._add_measurement_row('node', 'filt', 'nodeVoltage',
                                  1.5, 0.5, False)
        self._add_measurement_row('node', 'out', 'nodeVoltage',
                                  1.5, 0.5, True)
        self.statusBar().showMessage('Example loaded')

    # ---- Simulator ----

    def _on_open_simulator(self):
        if not HAS_WEBENGINE:
            QMessageBox.warning(
                self, 'Not Available',
                'PyQt6-WebEngine is not installed.\n'
                'Install with: pip install PyQt6-WebEngine')
            return
        ctz = self._get_ctz()
        if not ctz:
            QMessageBox.warning(self, 'No CTZ', 'Enter a CTZ value first.')
            return
        if self._sim_window is None:
            self._sim_window = SimulatorWindow(self)
        self._sim_window.show()
        self._sim_window.raise_()
        self._sim_window.activateWindow()
        self._sim_window.start(self._get_bridge_url(html_escape=False))

    # ---- Settings persistence ----

    def _save_settings(self):
        s = self.settings
        s.setValue('name', self.name_edit.text())
        s.setValue('category', self.category_edit.text())
        s.setValue('ctz', self.ctz_edit.toPlainText())
        s.setValue('bridge_url', self.url_edit.text())
        s.setValue('editable', self.editable_chk.isChecked())
        s.setValue('white_bg', self.white_bg_chk.isChecked())
        s.setValue('rate', self.rate_spin.value())
        s.setValue('hide_input', self.hide_chk.isChecked())
        # Save measurements as JSON
        measurements = self._get_measurements()
        s.setValue('measurements_json', json.dumps(
            [asdict(m) for m in measurements]))
        # Save editable indices
        s.setValue('editable_indices', json.dumps(
            sorted(self._get_editable_indices())))

    def _restore_settings(self):
        s = self.settings
        if s.contains('name'):
            self.name_edit.setText(s.value('name', ''))
        if s.contains('category'):
            self.category_edit.setText(s.value('category', ''))
        if s.contains('ctz'):
            self.ctz_edit.setPlainText(s.value('ctz', ''))
        if s.contains('bridge_url'):
            self.url_edit.setText(s.value('bridge_url', BRIDGE_BASE_URL))
        if s.contains('editable'):
            self.editable_chk.setChecked(s.value('editable', True, type=bool))
        if s.contains('white_bg'):
            self.white_bg_chk.setChecked(s.value('white_bg', True, type=bool))
        if s.contains('rate'):
            self.rate_spin.setValue(s.value('rate', 2, type=int))
        if s.contains('hide_input'):
            self.hide_chk.setChecked(s.value('hide_input', False, type=bool))

        # Restore editable indices (applied when component table is populated)
        self._saved_editable_indices = set()
        if s.contains('editable_indices'):
            try:
                self._saved_editable_indices = set(
                    json.loads(s.value('editable_indices', '[]')))
            except (json.JSONDecodeError, TypeError):
                pass

        # Restore measurements (new format, old label format, or legacy)
        if s.contains('measurements_json'):
            try:
                data = json.loads(s.value('measurements_json', '[]'))
                for d in data:
                    # New format has source_type/identifier
                    if 'source_type' in d:
                        self._add_measurement_row(
                            source=d.get('source_type', SOURCE_NODE),
                            identifier=d.get('identifier', ''),
                            prop=d.get('property', 'nodeVoltage'),
                            target=d.get('target', 0.0),
                            tolerance=d.get('tolerance', 0.1),
                            graded=d.get('graded', True))
                    else:
                        # Migrate from old label-based format
                        prop = d.get('property', 'nodeVoltage')
                        source = (SOURCE_NODE if prop == 'nodeVoltage'
                                  else SOURCE_ELEMENT)
                        self._add_measurement_row(
                            source=source,
                            identifier=d.get('label', ''),
                            prop=prop,
                            target=d.get('target', 0.0),
                            tolerance=d.get('tolerance', 0.1),
                            graded=d.get('graded', True))
            except (json.JSONDecodeError, TypeError):
                pass
        elif s.contains('nodes'):
            # Migrate from oldest single-value format
            nodes_str = s.value('nodes', '')
            old_nodes = [n.strip() for n in nodes_str.split(',')
                         if n.strip()]
            old_grade = s.value('grade_node', '')
            old_target = float(s.value('target', 3.3))
            old_tol = float(s.value('tolerance', 0.1))
            for n in old_nodes:
                is_graded = (n == old_grade) if old_grade else (
                    n == old_nodes[0] if old_nodes else False)
                self._add_measurement_row(
                    source=SOURCE_NODE, identifier=n,
                    prop='nodeVoltage',
                    target=old_target if is_graded else 0.0,
                    tolerance=old_tol if is_graded else 0.1,
                    graded=is_graded)

    def closeEvent(self, event):
        if self._sim_window:
            self._sim_window.close()
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
