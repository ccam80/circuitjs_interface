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
import webbrowser
from pathlib import Path
from dataclasses import dataclass, asdict

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QGroupBox, QLabel, QLineEdit, QTextEdit, QPlainTextEdit,
    QSpinBox, QDoubleSpinBox, QCheckBox, QComboBox, QPushButton,
    QFileDialog, QMessageBox, QTabWidget, QTableWidget, QHeaderView,
)
from PyQt6.QtCore import Qt, QSettings, QTimer, QUrl
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

PROPERTIES = ['nodeVoltage', 'current', 'voltageDiff', 'power']

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


@dataclass
class Measurement:
    label: str
    property: str       # one of PROPERTIES
    target: float
    tolerance: float
    graded: bool

    @classmethod
    def default(cls, label='', prop='nodeVoltage'):
        return cls(label=label, property=prop,
                   target=0.0, tolerance=0.1, graded=True)

    def unit(self):
        return PROPERTY_UNITS.get(self.property, 'V')

    def data_key(self):
        """Key used in event.data.values from the bridge."""
        if self.property == 'nodeVoltage':
            return self.label
        return f'{self.label}:{self.property}'

    def display_name(self):
        prefix = PROPERTY_PREFIX.get(self.property, 'V')
        return f'{prefix}_{self.label}'

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


def _build_bridge_url(ctz, nodes, measures, editable, rate, white_bg,
                      base_url, html_escape=True):
    sep = '&amp;' if html_escape else '&'
    parts = [f'ctz={ctz}']
    if nodes:
        parts.append(f'nodes={",".join(nodes)}')
    if measures:
        parts.append(f'measures={",".join(measures)}')
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
    """Split measurements into bridge URL params: nodes and measures lists."""
    nodes = []
    measures = []
    for m in measurements:
        if m.property == 'nodeVoltage':
            if m.label not in nodes:
                nodes.append(m.label)
        else:
            key = f'{m.label}:{m.property}'
            if key not in measures:
                measures.append(key)
    return nodes, measures


def _build_readout_html(measurements):
    """Build HTML readout lines for all measurements."""
    lines = []
    for i, m in enumerate(measurements):
        bold = ' style="font-weight:bold;"' if m.graded else ''
        tag = (' <span style="color:#090;">(graded)</span>'
               if m.graded else '')
        iname = m.input_name(i)
        prefix = PROPERTY_PREFIX.get(m.property, 'V')
        lines.append(
            f'    {prefix}<sub>{m.label}</sub> = '
            f'<span id="val-{iname}"{bold}>&mdash;</span> '
            f'{m.unit()}{tag}')
    return '<br/>\n'.join(lines) if lines else '    (no measurements configured)'


def _build_js_block(measurements):
    """Build the [[script]] JS block that reads values and writes to STACK inputs."""
    graded = [(i, m) for i, m in enumerate(measurements) if m.graded]

    js = "import {stack_js} from '[[cors src=\"stackjsiframe.js\"/]]';\n\n"

    # Request access to each graded STACK input
    for i, m in graded:
        iname = m.input_name(i)
        js += (f'const {iname}Id = await '
               f'stack_js.request_access_to_input("{iname}", true);\n')
        js += f'const {iname}Input = document.getElementById({iname}Id);\n'
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

    js += "  document.getElementById('status').textContent = '(live)';\n"
    js += "});"
    return js


def generate_xml(name, description, ctz, measurements,
                 editable=True, white_bg=True, rate=2,
                 hide_input=False, base_url=BRIDGE_BASE_URL,
                 category=''):
    """Generate complete Moodle XML for a STACK + CircuitJS1 question."""

    nodes, measures = _derive_bridge_params(measurements)
    bridge_url = _build_bridge_url(
        ctz, nodes, measures, editable, rate, white_bg, base_url,
        html_escape=True)
    readout_html = _build_readout_html(measurements)
    js_block = _build_js_block(measurements)

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

    # --- PRTs (one per graded measurement) ---
    prt_weight = _fmt(1.0 / n_graded)
    for j, (i, m) in enumerate(graded):
        iname = m.input_name(i)
        prt_name = f'prt{j + 1}'
        p.append('      <prt>\n')
        p.append(f'        <name>{prt_name}</name>\n')
        p.append(f'        <value>{prt_weight}</value>\n')
        p.append('        <autosimplify>1</autosimplify>\n')
        p.append('        <feedbackstyle>1</feedbackstyle>\n')
        p.append('        <feedbackvariables>\n          <text/>\n'
                 '        </feedbackvariables>\n')
        p.append('        <node>\n')
        p.append('          <name>0</name>\n')
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
        p.append(f'          <trueanswernote>{prt_name}-1-T</trueanswernote>\n')
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
        p.append(f'          <falseanswernote>{prt_name}-1-F</falseanswernote>\n')
        p.append('          <falsefeedback format="html">\n')
        p.append(f'            <text><![CDATA[<p>Not quite. '
                 f'{m.display_name()} = {{@{iname}@}} {m.unit()}, '
                 f'but the target is {{@target_{iname}@}} {m.unit()} '
                 f'(&plusmn; {{@tol_{iname}@}} {m.unit()}).</p>]]></text>\n')
        p.append('          </falsefeedback>\n')
        p.append('        </node>\n')
        p.append('      </prt>\n')

    # --- test cases ---
    # Test 1: all correct
    p.append('      <qtest>\n')
    p.append('        <testcase>1</testcase>\n')
    p.append('        <description>All correct</description>\n')
    for j, (i, m) in enumerate(graded):
        iname = m.input_name(i)
        p.append(f'        <testinput>\n          <name>{iname}</name>\n'
                 f'          <value>target_{iname}</value>\n'
                 f'        </testinput>\n')
    for j in range(n_graded):
        prt_name = f'prt{j + 1}'
        p.append(f'        <expected>\n          <name>{prt_name}</name>\n'
                 f'          <expectedscore>1.0000000</expectedscore>\n'
                 f'          <expectedpenalty>0.0000000</expectedpenalty>\n'
                 f'          <expectedanswernote>{prt_name}-1-T</expectedanswernote>\n'
                 f'        </expected>\n')
    p.append('      </qtest>\n')

    # Test 2: all wrong
    p.append('      <qtest>\n')
    p.append('        <testcase>2</testcase>\n')
    p.append('        <description>All wrong</description>\n')
    for j, (i, m) in enumerate(graded):
        iname = m.input_name(i)
        p.append(f'        <testinput>\n          <name>{iname}</name>\n'
                 f'          <value>target_{iname} + tol_{iname} + 1</value>\n'
                 f'        </testinput>\n')
    for j in range(n_graded):
        prt_name = f'prt{j + 1}'
        p.append(f'        <expected>\n          <name>{prt_name}</name>\n'
                 f'          <expectedscore>0.0000000</expectedscore>\n'
                 f'          <expectedpenalty>0.1000000</expectedpenalty>\n'
                 f'          <expectedanswernote>{prt_name}-1-F</expectedanswernote>\n'
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
COL_LABEL  = 0
COL_TYPE   = 1
COL_TARGET = 2
COL_TOL    = 3
COL_GRADE  = 4
COL_REMOVE = 5
MEAS_COLUMNS = ['Label', 'Type', 'Target', 'Tolerance', 'Grade', '']


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
        self.test_btn = QPushButton('Test in Browser')
        row2.addWidget(self.test_btn)
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
            'Each row reads a value from the simulator. '
            '"Node Voltage" uses labeled nodes; other types use labeled elements.'))

        self.meas_table = QTableWidget(0, len(MEAS_COLUMNS))
        self.meas_table.setHorizontalHeaderLabels(MEAS_COLUMNS)
        header = self.meas_table.horizontalHeader()
        header.setSectionResizeMode(COL_LABEL, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_TYPE, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(COL_TYPE, 130)
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

        # --- Action buttons ---
        btn_row = QHBoxLayout()
        self.example_btn = QPushButton('Load Example')
        self.save_btn = QPushButton('Save XML...')
        self.save_btn.setDefault(True)
        self.copy_btn = QPushButton('Copy to Clipboard')
        btn_row.addWidget(self.example_btn)
        btn_row.addStretch()
        btn_row.addWidget(self.save_btn)
        btn_row.addWidget(self.copy_btn)
        layout.addLayout(btn_row)

        # --- Tabbed preview / simulator ---
        mono = QFont('Consolas', 9)
        mono.setStyleHint(QFont.StyleHint.Monospace)

        self.tabs = QTabWidget()

        # Tab 1: XML Preview
        xml_tab = QWidget()
        xml_lay = QVBoxLayout(xml_tab)
        xml_lay.setContentsMargins(4, 4, 4, 4)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(mono)
        xml_lay.addWidget(self.preview)
        self.tabs.addTab(xml_tab, 'XML Preview')

        # Tab 2: Live Simulator
        sim_tab = QWidget()
        sim_lay = QVBoxLayout(sim_tab)
        sim_lay.setContentsMargins(4, 4, 4, 4)

        sim_ctrl = QHBoxLayout()
        self.start_sim_btn = QPushButton('Start Simulator')
        self.use_value_btn = QPushButton('Use Value as Target')
        self.use_value_btn.setEnabled(False)
        sim_ctrl.addWidget(self.start_sim_btn)
        sim_ctrl.addWidget(self.use_value_btn)
        sim_ctrl.addStretch()
        sim_lay.addLayout(sim_ctrl)

        if HAS_WEBENGINE:
            self.web_view = QWebEngineView()
            sim_lay.addWidget(self.web_view, stretch=3)
        else:
            no_web = QLabel(
                'PyQt6-WebEngine not installed.\n'
                'Install with: pip install PyQt6-WebEngine\n'
                'Use "Test in Browser" button instead.')
            no_web.setAlignment(Qt.AlignmentFlag.AlignCenter)
            sim_lay.addWidget(no_web, stretch=3)
            self.web_view = None

        self.sim_readout = QPlainTextEdit()
        self.sim_readout.setReadOnly(True)
        self.sim_readout.setFont(mono)
        self.sim_readout.setMaximumHeight(120)
        self.sim_readout.setPlaceholderText(
            'Measurement values will appear here when the simulator is running...')
        sim_lay.addWidget(self.sim_readout)

        self.tabs.addTab(sim_tab, 'Live Simulator')
        layout.addWidget(self.tabs, stretch=1)

        # Simulator polling state
        self._sim_poll_timer = QTimer()
        self._sim_poll_timer.setInterval(400)
        self._latest_values = {}

        self.statusBar().showMessage('Ready')

    # ---- Measurement table helpers ----

    def _add_measurement_row(self, label='', prop='nodeVoltage',
                             target=0.0, tolerance=0.1, graded=True):
        """Add a new row to the measurement table."""
        row = self.meas_table.rowCount()
        self.meas_table.insertRow(row)

        # Label
        label_edit = QLineEdit(label)
        label_edit.setPlaceholderText('vout, R1, ...')
        label_edit.textChanged.connect(self._update_preview)
        self.meas_table.setCellWidget(row, COL_LABEL, label_edit)

        # Type
        type_combo = QComboBox()
        for p_key in PROPERTIES:
            type_combo.addItem(PROPERTY_DISPLAY[p_key], p_key)
        idx = PROPERTIES.index(prop) if prop in PROPERTIES else 0
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

        self._update_preview()

    def _on_remove_row(self):
        """Remove the measurement row whose 'x' button was clicked."""
        btn = self.sender()
        for row in range(self.meas_table.rowCount()):
            if self.meas_table.cellWidget(row, COL_REMOVE) is btn:
                self.meas_table.removeRow(row)
                break
        self._update_preview()

    def _on_type_changed(self):
        """Update unit suffixes when the Type dropdown changes."""
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
            label = self.meas_table.cellWidget(row, COL_LABEL).text().strip()
            prop = self.meas_table.cellWidget(row, COL_TYPE).currentData()
            target = self.meas_table.cellWidget(row, COL_TARGET).value()
            tol = self.meas_table.cellWidget(row, COL_TOL).value()
            # The checkbox is inside a container widget
            container = self.meas_table.cellWidget(row, COL_GRADE)
            grade_chk = container.findChild(QCheckBox)
            graded = grade_chk.isChecked() if grade_chk else True
            if label:  # skip rows with empty labels
                measurements.append(Measurement(
                    label=label, property=prop,
                    target=target, tolerance=tol, graded=graded))
        return measurements

    def _clear_measurements(self):
        """Remove all rows from the measurement table."""
        while self.meas_table.rowCount() > 0:
            self.meas_table.removeRow(0)

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
        self.test_btn.clicked.connect(self._on_test_browser)
        self.example_btn.clicked.connect(self._on_load_example)

        # Simulator
        self.start_sim_btn.clicked.connect(self._on_start_sim)
        self.use_value_btn.clicked.connect(self._on_use_value)
        self._sim_poll_timer.timeout.connect(self._poll_sim)
        if self.web_view:
            self.web_view.loadFinished.connect(self._on_sim_loaded)

    # ---- Helpers ----

    def _get_ctz(self):
        return extract_ctz(self.ctz_edit.toPlainText())

    def _get_bridge_url(self, html_escape=False):
        """Build bridge URL from current measurements and settings."""
        measurements = self._get_measurements()
        nodes, measures = _derive_bridge_params(measurements)
        return _build_bridge_url(
            self._get_ctz(), nodes, measures,
            self.editable_chk.isChecked(),
            self.rate_spin.value(),
            self.white_bg_chk.isChecked(),
            self.url_edit.text().strip() or BRIDGE_BASE_URL,
            html_escape=html_escape)

    def _generate(self):
        measurements = self._get_measurements()
        return generate_xml(
            name=self.name_edit.text().strip()
                 or 'Untitled CircuitJS1 Question',
            description=(self.desc_edit.toPlainText().strip()
                         or 'Adjust the circuit as instructed.'),
            ctz=self._get_ctz(),
            measurements=measurements,
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
            if not m.label:
                warnings.append(f'- Empty label in measurement row')
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

    def _on_test_browser(self):
        ctz = self._get_ctz()
        if not ctz:
            QMessageBox.warning(self, 'No CTZ', 'Enter a CTZ value first.')
            return
        webbrowser.open(self._get_bridge_url(html_escape=False))
        self.statusBar().showMessage('Opened in browser')

    def _on_load_example(self):
        self.name_edit.setText('CircuitJS1 Integration Test')
        self.desc_edit.setPlainText(
            'Observe the filter circuit output voltage. The simulator '
            'writes to the answer field automatically.')
        self.ctz_edit.setPlainText(EXAMPLE_CTZ)
        self._clear_measurements()
        self._add_measurement_row('AC', 'nodeVoltage', 2.5, 0.5, False)
        self._add_measurement_row('filt', 'nodeVoltage', 1.5, 0.5, False)
        self._add_measurement_row('out', 'nodeVoltage', 1.5, 0.5, True)
        self.statusBar().showMessage('Example loaded')

    # ---- Simulator ----

    def _on_start_sim(self):
        if not self.web_view:
            QMessageBox.warning(
                self, 'Not Available',
                'Install PyQt6-WebEngine to use the embedded simulator.')
            return
        ctz = self._get_ctz()
        if not ctz:
            QMessageBox.warning(self, 'No CTZ', 'Enter a CTZ value first.')
            return
        self._sim_poll_timer.stop()
        self._latest_values = {}
        self.use_value_btn.setEnabled(False)
        self.sim_readout.setPlainText('Loading simulator...')
        self.start_sim_btn.setText('Reload')

        self.web_view.setUrl(QUrl(self._get_bridge_url(html_escape=False)))

    def _on_sim_loaded(self, ok):
        if not ok:
            self.sim_readout.setPlainText('Failed to load simulator page.')
            return
        self.web_view.page().runJavaScript(
            "window._qgen_values = null;"
            "window.addEventListener('message', function(e) {"
            "  if (e.data && e.data.type === 'circuitjs-data')"
            "    window._qgen_values = e.data.values;"
            "});")
        self.sim_readout.setPlainText(
            'Simulator loaded. Waiting for first data...')
        self._sim_poll_timer.start()

    def _poll_sim(self):
        if not self.web_view:
            return
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

        # Build key -> metadata map from current measurements
        measurements = self._get_measurements()
        key_info = {}
        for m in measurements:
            key_info[m.data_key()] = {
                'unit': m.unit(),
                'graded': m.graded,
                'display': m.display_name(),
            }

        lines = []
        for key in sorted(values.keys()):
            val = values[key]
            info = key_info.get(key, {'unit': 'V', 'graded': False,
                                      'display': key})
            if val is None:
                lines.append(f'  {info["display"]} ({key}) = null')
            else:
                marker = '  <<< GRADED' if info['graded'] else ''
                lines.append(
                    f'  {info["display"]} ({key}) = '
                    f'{val:.6f} {info["unit"]}{marker}')
        self.sim_readout.setPlainText('\n'.join(lines))

        # Enable "Use Value" if there's a selected row with a matching value
        self.use_value_btn.setEnabled(bool(values))

    def _on_use_value(self):
        """Set the target of the currently selected table row from the
        simulator's latest value."""
        row = self.meas_table.currentRow()
        if row < 0:
            QMessageBox.information(
                self, 'Select Row',
                'Click a row in the Measurements table first.')
            return
        label_w = self.meas_table.cellWidget(row, COL_LABEL)
        type_w = self.meas_table.cellWidget(row, COL_TYPE)
        if not label_w or not type_w:
            return
        label = label_w.text().strip()
        prop = type_w.currentData()
        key = label if prop == 'nodeVoltage' else f'{label}:{prop}'
        val = self._latest_values.get(key)
        if val is not None:
            target_w = self.meas_table.cellWidget(row, COL_TARGET)
            target_w.setValue(round(val, 6))
            self.statusBar().showMessage(
                f'Target set to {val:.6f} from "{key}"')
        else:
            QMessageBox.warning(
                self, 'No Value',
                f'No simulator value found for key "{key}".\n'
                f'Check that the label matches a circuit element.')

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

        # Restore measurements (new format or migrate from old)
        if s.contains('measurements_json'):
            try:
                data = json.loads(s.value('measurements_json', '[]'))
                for d in data:
                    self._add_measurement_row(
                        label=d.get('label', ''),
                        prop=d.get('property', 'nodeVoltage'),
                        target=d.get('target', 0.0),
                        tolerance=d.get('tolerance', 0.1),
                        graded=d.get('graded', True))
            except (json.JSONDecodeError, TypeError):
                pass
        elif s.contains('nodes'):
            # Migrate from old single-value format
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
                    label=n, prop='nodeVoltage',
                    target=old_target if is_graded else 0.0,
                    tolerance=old_tol if is_graded else 0.1,
                    graded=is_graded)

    def closeEvent(self, event):
        self._sim_poll_timer.stop()
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
