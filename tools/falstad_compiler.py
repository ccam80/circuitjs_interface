"""Falstad-specific YAML dict -> stack_compiler dict compiler.

Translates a Falstad question definition (circuit, measurements, integrity)
into the generic dict format consumed by stack_compiler.compile_question().

Usage:
    from falstad_compiler import compile as falstad_compile
    from stack_compiler import compile_question

    stack_dict = falstad_compile(yaml_dict)
    xml = compile_question(stack_dict)
"""

from __future__ import annotations

import json
import re


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SIM_BASE_URL = "https://ccam80.github.io/circuitjs-moodle/circuitjs.html"
RATE = 2

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


def _fmt(v):
    """Format number for Maxima: drop unnecessary trailing zeros."""
    return f'{v:g}'


# ---------------------------------------------------------------------------
# Measurement processing
# ---------------------------------------------------------------------------

def _parse_measurements(raw_measurements):
    """Parse raw measurement dicts and compute derived fields.

    Returns list of dicts with computed input_name, display_name,
    data_key, unit, plus all original fields.
    """
    result = []
    for idx, m in enumerate(raw_measurements):
        source = m['source']
        identifier = str(m['identifier'])
        prop = m.get('property',
                     'nodeVoltage' if source == 'node' else 'current')

        input_name = f'ans{idx + 1}'

        if source == 'expression':
            safe = re.sub(r'[^A-Za-z0-9_]', '_', identifier)
            display_name = f'expr_{safe}'
            data_key = None
            unit = ''
        elif source == 'node':
            prefix = PROPERTY_PREFIX.get(prop, 'V')
            display_name = f'{prefix}_{identifier}'
            data_key = identifier
            unit = PROPERTY_UNITS.get(prop, 'V')
        else:  # element
            prefix = PROPERTY_PREFIX.get(prop, 'V')
            display_name = f'{prefix}_{identifier}'
            elem_idx = m.get('element_index', -1)
            if elem_idx >= 0:
                data_key = f'{elem_idx}:{prop}'
            else:
                data_key = f'{identifier}:{prop}'
            unit = PROPERTY_UNITS.get(prop, 'V')

        result.append({
            'input_name': input_name,
            'display_name': display_name,
            'data_key': data_key,
            'unit': unit,
            'source': source,
            'identifier': identifier,
            'element_index': m.get('element_index', -1),
            'property': prop,
            'target': m.get('target', 0.0),
            'tolerance': m.get('tolerance', 0.1),
            'tolerance_type': m.get('tolerance_type', 'absolute'),
            'graded': m.get('graded', True),
            'target_expr': m.get('target_expr', ''),
            'feedback_correct': m.get('feedback_correct', ''),
            'feedback_incorrect': m.get('feedback_incorrect', ''),
        })
    return result


def _derive_subscribe_params(measurements):
    """Split parsed measurements into subscribe nodes and elements lists."""
    nodes = []
    elements = []
    for m in measurements:
        if m['source'] == 'node':
            if m['data_key'] not in nodes:
                nodes.append(m['data_key'])
        elif m['source'] == 'element':
            if m['data_key'] and m['data_key'] not in elements:
                elements.append(m['data_key'])
    return nodes, elements


# ---------------------------------------------------------------------------
# HTML / JS builders
# ---------------------------------------------------------------------------

def _build_sim_url(ctz, base_url=SIM_BASE_URL, white_bg=False):
    """Build circuitjs URL for STACK iframe."""
    parts = ['running=true']
    if ctz:
        parts.append(f'ctz={ctz}')
    parts.append('editable=true')
    if white_bg:
        parts.append('whiteBackground=true')
    return base_url + '?' + '&'.join(parts)


def _build_readout_html(measurements, has_integrity):
    """Build HTML readout lines for all non-expression measurements."""
    lines = []
    for m in measurements:
        if m['source'] == 'expression':
            continue
        bold = ' style="font-weight:bold;"' if m['graded'] else ''
        tag = (' <span style="color:#090;">(graded)</span>'
               if m['graded'] else '')
        prefix = PROPERTY_PREFIX.get(m['property'], 'V')
        lines.append(
            f'    {prefix}<sub>{m["identifier"]}</sub> = '
            f'<span id="val-{m["input_name"]}"{bold}>&mdash;</span> '
            f'{m["unit"]}{tag}')
    if has_integrity:
        lines.append(
            '    <span id="integrity-status" '
            'style="color:#999;">Integrity: waiting...</span>')
    return '<br/>\n'.join(lines) if lines else '    (no measurements configured)'


def _build_js_block(measurements, nodes, elements, rate,
                    permissions, has_integrity, sim_url):
    """Build the [[script]] JS block content."""
    graded = [m for m in measurements
              if m['graded'] and m['source'] != 'expression']

    js = "import {stack_js} from '[[cors src=\"stackjsiframe.js\"/]]';\n\n"

    # Request access to each graded STACK input
    for m in graded:
        iname = m['input_name']
        js += (f'const {iname}Id = await '
               f'stack_js.request_access_to_input("{iname}", true);\n')
        js += f'const {iname}Input = document.getElementById({iname}Id);\n'

    # Integrity input
    if has_integrity:
        js += ('const intId = await '
               'stack_js.request_access_to_input("ans_integrity", true);\n')
        js += 'const intInput = document.getElementById(intId);\n'

    # Circuit state input
    js += ('const circId = await '
           'stack_js.request_access_to_input("ans_circuit", true);\n')
    js += 'const circInput = document.getElementById(circId);\n'
    js += "\n"

    # Set iframe src
    js += f'var origUrl = "{sim_url}";\n'
    js += "var savedCtz = circInput.value;\n"
    js += "var simFrame = document.getElementById('sim-frame');\n"
    js += "simFrame.src = origUrl;\n\n"

    # Subscribe message on load
    nodes_js = json.dumps(nodes)
    elements_js = json.dumps(elements)
    permissions_js = json.dumps(permissions)
    js += "simFrame.addEventListener('load', function() {\n"
    js += "  simFrame.contentWindow.postMessage({\n"
    js += "    type: 'circuitjs-subscribe',\n"
    js += f"    nodes: {nodes_js},\n"
    js += f"    elements: {elements_js},\n"
    js += f"    rate: {rate},\n"
    js += f"    permissions: {permissions_js},\n"
    js += "    studentCtz: savedCtz || null\n"
    js += "  }, '*');\n"
    js += "});\n\n"

    # Message listener
    js += "window.addEventListener('message', function(event) {\n"
    js += "  if (!event.data) return;\n\n"

    # Save circuit state
    js += ("  if (event.data.type === 'circuitjs-elements'"
           " && event.data.ctz) {\n")
    js += "    circInput.value = event.data.ctz;\n"
    js += "    circInput.dispatchEvent(new Event('change'));\n"
    js += "  }\n\n"

    # Route integrity result
    if has_integrity:
        js += "  if (event.data.type === 'circuitjs-integrity') {\n"
        js += "    intInput.value = event.data.integrity.toString();\n"
        js += "    intInput.dispatchEvent(new Event('change'));\n"
        js += "  }\n\n"

    js += "  if (event.data.type !== 'circuitjs-data') return;\n"
    js += "  var v;\n\n"

    # Display update for all non-expression measurements
    for m in measurements:
        if m['source'] == 'expression':
            continue
        key = m['data_key']
        js += f"  v = event.data.values['{key}'];\n"
        js += (f"  if (v !== null && v !== undefined) "
               f"document.getElementById('val-{m['input_name']}').textContent "
               f"= v.toFixed(4);\n")
    js += "\n"

    # Write graded values to STACK inputs
    for m in graded:
        key = m['data_key']
        iname = m['input_name']
        js += f"  v = event.data.values['{key}'];\n"
        js += "  if (v !== null && v !== undefined) {\n"
        js += f"    {iname}Input.value = v.toFixed(6);\n"
        js += f"    {iname}Input.dispatchEvent(new Event('change'));\n"
        js += "  }\n"

    # Route integrity value from data message
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
        js += ("        el.textContent = 'Integrity: FAILED"
               " \u2014 restricted component modified';\n")
        js += "        el.style.color = '#c00';\n"
        js += "      }\n"
        js += "    }\n"
        js += "  }\n"

    js += "  document.getElementById('status').textContent = '(live)';\n"
    js += "});"

    return js


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compile(d: dict) -> dict:
    """Compile a Falstad question dict to a stack_compiler input dict.

    Args:
        d: Dict with keys: name, description, ctz, measurements,
           and optionally: category, integrity, question_variables,
           tags, base_url, rate, white_bg

    Returns:
        Dict suitable for stack_compiler.compile_question()
    """
    name = d['name']
    category = d.get('category', '')
    description = d['description']
    ctz = d.get('ctz', '')
    custom_qvars = d.get('question_variables', '')
    base_url = d.get('base_url', SIM_BASE_URL)
    rate = d.get('rate', RATE)
    white_bg = d.get('white_bg', False)
    tags = d.get('tags', ['circuitjs'])

    # Parse measurements
    measurements = _parse_measurements(d.get('measurements', []))

    # Determine integrity
    integrity = d.get('integrity', {})
    editable_indices = integrity.get('editable_indices', [])
    removable_indices = integrity.get('removable_indices', [])
    type_rules = integrity.get('type_rules', [])
    has_integrity = bool(integrity) and (
        len(editable_indices) > 0
        or len(removable_indices) > 0
        or len(type_rules) > 0
    )
    if 'has_integrity' in d:
        has_integrity = d['has_integrity']

    # Derived params
    nodes, elements = _derive_subscribe_params(measurements)
    sim_url = _build_sim_url(ctz, base_url, white_bg)
    readout_html = _build_readout_html(measurements, has_integrity)

    permissions = {
        'editableIndices': sorted(editable_indices),
        'removableIndices': sorted(removable_indices),
        'typeRules': type_rules,
    }
    js_block = _build_js_block(measurements, nodes, elements, rate,
                               permissions, has_integrity, sim_url)

    graded = [m for m in measurements if m['graded']]
    n_graded = len(graded) or 1

    # --- question_text ---
    qt = []
    qt.append(f'<p>{description}</p>\n\n')
    qt.append('<p><em>Edit the simulated circuit, the result will be read '
              'when you click &quot;Check&quot;.</em></p>\n\n')
    qt.append('[[iframe height="640px" width="830px"]]\n')
    qt.append('<div style="font-family:sans-serif;">\n\n')
    qt.append('  <iframe id="sim-frame"\n')
    qt.append('    width="800" height="550" style="border:1px solid #ccc;">\n')
    qt.append('  </iframe>\n\n')
    qt.append('  <div id="readout" style="display:none; font-family:monospace; '
              'padding:8px; font-size:14px;\n')
    qt.append('    background:#f4f4f4; border:1px solid #ddd; margin-top:4px;">\n')
    qt.append(readout_html + '\n')
    qt.append('    <div id="status" style="color:#999; margin-top:4px;">'
              '(waiting for simulation...)</div>\n')
    qt.append('  </div>\n\n</div>\n\n')
    qt.append('[[script type="module"]]\n')
    qt.append(js_block + '\n')
    qt.append('[[/script]]\n[[/iframe]]\n\n')

    for m in graded:
        qt.append('<div style="display:none;">\n')
        qt.append(f'  <p>{m["display_name"]}: '
                  f'[[input:{m["input_name"]}]] {m["unit"]} '
                  f'[[validation:{m["input_name"]}]]</p>\n')
        qt.append('</div>\n')
    if has_integrity:
        qt.append('<div style="display:none;">\n')
        qt.append('  <p>[[input:ans_integrity]] '
                  '[[validation:ans_integrity]]</p>\n')
        qt.append('</div>\n')
    qt.append('<div style="display:none;">\n')
    qt.append('  <p>[[input:ans_circuit]] [[validation:ans_circuit]]</p>\n')
    qt.append('</div>\n')

    question_text = ''.join(qt)

    # --- question_variables ---
    qvar_lines = []
    if custom_qvars.strip():
        qvar_lines.append(custom_qvars.strip())
    for m in graded:
        iname = m['input_name']
        if m['target_expr']:
            qvar_lines.append(f'target_{iname}: {m["target_expr"]};')
        else:
            qvar_lines.append(f'target_{iname}: {_fmt(m["target"])};')
        qvar_lines.append(f'tol_{iname}: {_fmt(m["tolerance"])};')
    if has_integrity:
        qvar_lines.append('expected_integrity: 1;')
    question_variables = '\n'.join(qvar_lines) + '\n' if qvar_lines else ''

    # --- general_feedback ---
    gf_parts = []
    for m in graded:
        iname = m['input_name']
        gf_parts.append(
            f'<p>{m["display_name"]}: target = '
            f'{{@target_{iname}@}} {m["unit"]} '
            f'(&plusmn; {{@tol_{iname}@}} {m["unit"]}), '
            f'measured = {{@{iname}@}} {m["unit"]}</p>\n')
    general_feedback = ''.join(gf_parts)

    # --- specific_feedback ---
    specific_feedback = ''.join(
        f'[[feedback:prt{j+1}]]' for j in range(n_graded))

    # --- question_note ---
    note_parts = []
    for m in graded:
        iname = m['input_name']
        note_parts.append(f'{m["display_name"]}={{@target_{iname}@}}')
    question_note = (', '.join(note_parts)
                     if note_parts else 'no graded measurements')

    # --- inputs ---
    inputs = []
    for m in graded:
        inputs.append({
            'name': m['input_name'],
            'type': 'numerical',
            'model_answer': f'target_{m["input_name"]}',
            'box_size': 10,
            'strict_syntax': True,
            'insert_stars': 0,
            'forbid_float': False,
            'must_verify': False,
            'show_validation': 0,
        })
    if has_integrity:
        inputs.append({
            'name': 'ans_integrity',
            'type': 'numerical',
            'model_answer': 'expected_integrity',
            'box_size': 5,
            'strict_syntax': True,
            'insert_stars': 0,
            'forbid_float': False,
            'must_verify': False,
            'show_validation': 0,
        })
    inputs.append({
        'name': 'ans_circuit',
        'type': 'string',
        'model_answer': '""',
        'box_size': 1,
        'strict_syntax': False,
        'insert_stars': 0,
        'forbid_float': False,
        'must_verify': False,
        'show_validation': 0,
    })

    # --- PRTs ---
    prts = []
    for j, m in enumerate(graded):
        iname = m['input_name']
        prt_name = f'prt{j + 1}'
        value_node_name = '1' if has_integrity else '0'

        nodes_list = []

        # Node 0: Integrity gate
        if has_integrity:
            nodes_list.append({
                'name': '0',
                'description': 'Integrity gate',
                'answer_test': 'AlgEquiv',
                'student_answer': 'ans_integrity',
                'teacher_answer': 'expected_integrity',
                'quiet': True,
                'true_branch': {
                    'score_mode': '=',
                    'score': 0.0,
                    'next_node': 1,
                    'answer_note': f'{prt_name}-0-T',
                },
                'false_branch': {
                    'score_mode': '=',
                    'score': 0.0,
                    'next_node': -1,
                    'answer_note': f'{prt_name}-0-F',
                    'feedback': (
                        '<p style="color:#c00;">One or more restricted '
                        'circuit components were modified. Your answer '
                        'cannot be graded.</p>'
                    ),
                },
            })

        # Value check node
        test_type = ('NumRelative' if m['tolerance_type'] == 'relative'
                     else 'NumAbsolute')
        sans_val = ('computed_sans'
                    if m['source'] == 'expression' else iname)

        # Feedback text
        if m.get('feedback_correct'):
            true_fb = m['feedback_correct']
        else:
            true_fb = (
                f'<p>Correct! {m["display_name"]} = {{@{iname}@}} {m["unit"]}'
                f' is within {{@tol_{iname}@}} {m["unit"]} of the target '
                f'{{@target_{iname}@}} {m["unit"]}.</p>')

        if m.get('feedback_incorrect'):
            false_fb = m['feedback_incorrect']
        else:
            false_fb = (
                f'<p>Not quite. {m["display_name"]} = {{@{iname}@}} '
                f'{m["unit"]}, but the target is {{@target_{iname}@}} '
                f'{m["unit"]} (&plusmn; {{@tol_{iname}@}} {m["unit"]}).</p>')

        nodes_list.append({
            'name': value_node_name,
            'description': f'Check {m["display_name"]} against target',
            'answer_test': test_type,
            'student_answer': sans_val,
            'teacher_answer': f'target_{iname}',
            'test_options': f'tol_{iname}',
            'quiet': False,
            'true_branch': {
                'score_mode': '=',
                'score': 1.0,
                'next_node': -1,
                'answer_note': f'{prt_name}-{value_node_name}-T',
                'feedback': true_fb,
            },
            'false_branch': {
                'score_mode': '=',
                'score': 0.0,
                'next_node': -1,
                'answer_note': f'{prt_name}-{value_node_name}-F',
                'feedback': false_fb,
            },
        })

        # Feedback variables for expression measurements
        fb_vars = ''
        if m['source'] == 'expression':
            fb_lines = []
            for mm in measurements:
                if mm['source'] != 'expression':
                    fb_lines.append(
                        f'{mm["display_name"]}: {mm["input_name"]};')
            fb_lines.append(f'computed_sans: {m["identifier"]};')
            fb_vars = ' '.join(fb_lines)

        prts.append({
            'name': prt_name,
            'value': _fmt(1.0 / n_graded),
            'auto_simplify': True,
            'feedback_style': 'STANDARD',
            'feedback_variables': fb_vars,
            'nodes': nodes_list,
        })

    # --- tests ---
    value_node_name = '1' if has_integrity else '0'
    tests = []

    # Test 1: All correct
    t1_inputs = []
    for m in graded:
        t1_inputs.append({'name': m['input_name'],
                          'value': f'target_{m["input_name"]}'})
    if has_integrity:
        t1_inputs.append({'name': 'ans_integrity', 'value': '1'})
    t1_inputs.append({'name': 'ans_circuit', 'value': '""\n'})

    t1_expected = []
    for j in range(n_graded):
        pn = f'prt{j + 1}'
        t1_expected.append({
            'name': pn, 'score': 1.0, 'penalty': 0.0,
            'answer_note': f'{pn}-{value_node_name}-T',
        })
    tests.append({
        'testcase': 1, 'description': 'All correct',
        'inputs': t1_inputs, 'expected': t1_expected,
    })

    # Test 2: All wrong
    t2_inputs = []
    for m in graded:
        iname = m['input_name']
        t2_inputs.append({'name': iname,
                          'value': f'target_{iname} + tol_{iname} + 1'})
    if has_integrity:
        t2_inputs.append({'name': 'ans_integrity', 'value': '1'})
    t2_inputs.append({'name': 'ans_circuit', 'value': '""\n'})

    t2_expected = []
    for j in range(n_graded):
        pn = f'prt{j + 1}'
        t2_expected.append({
            'name': pn, 'score': 0.0, 'penalty': 0.1,
            'answer_note': f'{pn}-{value_node_name}-F',
        })
    tests.append({
        'testcase': 2, 'description': 'All wrong',
        'inputs': t2_inputs, 'expected': t2_expected,
    })

    # Test 3: Integrity failure
    if has_integrity:
        t3_inputs = []
        for m in graded:
            t3_inputs.append({'name': m['input_name'],
                              'value': f'target_{m["input_name"]}'})
        t3_inputs.append({'name': 'ans_integrity', 'value': '0'})
        t3_inputs.append({'name': 'ans_circuit', 'value': '""\n'})

        t3_expected = []
        for j in range(n_graded):
            pn = f'prt{j + 1}'
            t3_expected.append({
                'name': pn, 'score': 0.0, 'penalty': 0.1,
                'answer_note': f'{pn}-0-F',
            })
        tests.append({
            'testcase': 3, 'description': 'Integrity failure',
            'inputs': t3_inputs, 'expected': t3_expected,
        })

    return {
        'name': name,
        'category': category,
        'question_text': question_text,
        'general_feedback': general_feedback,
        'question_variables': question_variables,
        'specific_feedback': specific_feedback,
        'question_note': question_note,
        'inputs': inputs,
        'prts': prts,
        'tests': tests,
        'tags': tags,
        'default_grade': d.get('default_grade', 1),
        'penalty': d.get('penalty', 0.1),
    }
