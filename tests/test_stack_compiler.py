"""Parity test: stack_compiler output vs known-good Voltage_Divider.xml."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))

from stack_compiler import compile_question

# The question_text and general_feedback are copied verbatim from the
# known-good Voltage_Divider.xml to focus the diff on structural issues.

QUESTION_TEXT = (
    '<p>Adjust the circuit as instructed.</p>\n'
    '\n'
    '<p><em>Edit the simulated circuit, the result will be read '
    'when you click &quot;Check&quot;.</em></p>\n'
    '\n'
    '[[iframe height="640px" width="830px"]]\n'
    '<div style="font-family:sans-serif;">\n'
    '\n'
    '  <iframe id="sim-frame"\n'
    '    width="800" height="550" style="border:1px solid #ccc;">\n'
    '  </iframe>\n'
    '\n'
    '  <div id="readout" style="display:none; font-family:monospace; '
    'padding:8px; font-size:14px;\n'
    '    background:#f4f4f4; border:1px solid #ddd; margin-top:4px;">\n'
    '    P<sub>R1</sub> = <span id="val-ans1" style="font-weight:bold;">'
    '&mdash;</span> W <span style="color:#090;">(graded)</span><br/>\n'
    '    <span id="integrity-status" style="color:#999;">'
    'Integrity: waiting...</span>\n'
    '    <div id="status" style="color:#999; margin-top:4px;">'
    '(waiting for simulation...)</div>\n'
    '  </div>\n'
    '\n'
    '</div>\n'
    '\n'
    '[[script type="module"]]\n'
    'import {stack_js} from \'[[cors src="stackjsiframe.js"/]]\';\n'
    '\n'
    'const ans1Id = await stack_js.request_access_to_input("ans1", true);\n'
    'const ans1Input = document.getElementById(ans1Id);\n'
    'const intId = await stack_js.request_access_to_input("ans_integrity", true);\n'
    'const intInput = document.getElementById(intId);\n'
    'const circId = await stack_js.request_access_to_input("ans_circuit", true);\n'
    'const circInput = document.getElementById(circId);\n'
    '\n'
    'var origUrl = "https://ccam80.github.io/circuitjs-moodle/circuitjs.html'
    '?running=true&ctz=CQAgjCAMB0l3BWcMBMcUHYMGZIA4UA2ATmIxEO2QpRAQFMBaMMAKADdwxbtC8vaAFn6QoIQaLCjpUaAlYB3AeP4oJKqIuW9V6nZqVrRw8diEitRkPsFnrfTQCdT5l+BQjk8Vs9u1u-H4gaJ5S3r52IW4engjh0XiBdvqicXCsaORBAeLEhO6BIABqAIIZkFmR+CqiUYLFALLllbQxuflt9UUAQqxAA&editable=true";\n'
    'var savedCtz = circInput.value;\n'
    'var simFrame = document.getElementById(\'sim-frame\');\n'
    'simFrame.src = origUrl;\n'
    '\n'
    'simFrame.addEventListener(\'load\', function() {\n'
    '  simFrame.contentWindow.postMessage({\n'
    '    type: \'circuitjs-subscribe\',\n'
    '    nodes: [],\n'
    '    elements: ["5:power"],\n'
    '    rate: 2,\n'
    '    permissions: {"editableIndices": [5, 6], "removableIndices": [10], '
    '"typeRules": [{"type": "CapacitorElm", "maxAdd": 1, "maxRemove": 0}]},\n'
    '    studentCtz: savedCtz || null\n'
    '  }, \'*\');\n'
    '});\n'
    '\n'
    'window.addEventListener(\'message\', function(event) {\n'
    '  if (!event.data) return;\n'
    '\n'
    '  if (event.data.type === \'circuitjs-elements\' && event.data.ctz) {\n'
    '    circInput.value = event.data.ctz;\n'
    '    circInput.dispatchEvent(new Event(\'change\'));\n'
    '  }\n'
    '\n'
    '  if (event.data.type === \'circuitjs-integrity\') {\n'
    '    intInput.value = event.data.integrity.toString();\n'
    '    intInput.dispatchEvent(new Event(\'change\'));\n'
    '  }\n'
    '\n'
    '  if (event.data.type !== \'circuitjs-data\') return;\n'
    '  var v;\n'
    '\n'
    '  v = event.data.values[\'5:power\'];\n'
    '  if (v !== null && v !== undefined) '
    'document.getElementById(\'val-ans1\').textContent = v.toFixed(4);\n'
    '\n'
    '  v = event.data.values[\'5:power\'];\n'
    '  if (v !== null && v !== undefined) {\n'
    '    ans1Input.value = v.toFixed(6);\n'
    '    ans1Input.dispatchEvent(new Event(\'change\'));\n'
    '  }\n'
    '\n'
    '  v = event.data.values[\'integrity\'];\n'
    '  if (v !== null && v !== undefined) {\n'
    '    intInput.value = v.toString();\n'
    '    intInput.dispatchEvent(new Event(\'change\'));\n'
    '    var el = document.getElementById(\'integrity-status\');\n'
    '    if (el) {\n'
    '      if (v === 1) {\n'
    '        el.textContent = \'Integrity: OK\';\n'
    '        el.style.color = \'#090\';\n'
    '      } else {\n'
    '        el.textContent = \'Integrity: FAILED \u2014 restricted '
    'component modified\';\n'
    '        el.style.color = \'#c00\';\n'
    '      }\n'
    '    }\n'
    '  }\n'
    '  document.getElementById(\'status\').textContent = \'(live)\';\n'
    '});\n'
    '[[/script]]\n'
    '[[/iframe]]\n'
    '\n'
    '<div style="display:none;">\n'
    '  <p>P_R1: [[input:ans1]] W [[validation:ans1]]</p>\n'
    '</div>\n'
    '<div style="display:none;">\n'
    '  <p>[[input:ans_integrity]] [[validation:ans_integrity]]</p>\n'
    '</div>\n'
    '<div style="display:none;">\n'
    '  <p>[[input:ans_circuit]] [[validation:ans_circuit]]</p>\n'
    '</div>\n'
)

GENERAL_FEEDBACK = (
    '<p>P_R1: target = {@target_ans1@} W '
    '(&plusmn; {@tol_ans1@} W), measured = {@ans1@} W</p>\n'
)

VOLTAGE_DIVIDER = {
    "name": "Voltage Divider",
    "category": "M1:",
    "question_text": QUESTION_TEXT,
    "general_feedback": GENERAL_FEEDBACK,
    "default_grade": 1,
    "penalty": 0.1,
    "question_variables": (
        "P_expected: 0.0015;\n"
        "target_ans1: 2.5;\n"
        "tol_ans1: 0.1;\n"
        "expected_integrity: 1;\n"
    ),
    "specific_feedback": "[[feedback:prt1]]",
    "question_note": "P_R1={@target_ans1@}",
    "inputs": [
        {
            "name": "ans1",
            "type": "numerical",
            "model_answer": "target_ans1",
            "box_size": 10,
            "strict_syntax": True,
            "insert_stars": 0,
            "forbid_float": False,
            "must_verify": False,
            "show_validation": 0,
        },
        {
            "name": "ans_integrity",
            "type": "numerical",
            "model_answer": "expected_integrity",
            "box_size": 5,
            "strict_syntax": True,
            "insert_stars": 0,
            "forbid_float": False,
            "must_verify": False,
            "show_validation": 0,
        },
        {
            "name": "ans_circuit",
            "type": "string",
            "model_answer": '""',
            "box_size": 1,
            "strict_syntax": False,
            "insert_stars": 0,
            "forbid_float": False,
            "must_verify": False,
            "show_validation": 0,
        },
    ],
    "prts": [
        {
            "name": "prt1",
            "value": 1,
            "auto_simplify": True,
            "feedback_style": "STANDARD",
            "nodes": [
                {
                    "name": "0",
                    "description": "Integrity gate",
                    "answer_test": "AlgEquiv",
                    "student_answer": "ans_integrity",
                    "teacher_answer": "expected_integrity",
                    "quiet": True,
                    "true_branch": {
                        "score_mode": "=",
                        "score": 0.0,
                        "next_node": 1,
                        "answer_note": "prt1-0-T",
                    },
                    "false_branch": {
                        "score_mode": "=",
                        "score": 0.0,
                        "next_node": -1,
                        "answer_note": "prt1-0-F",
                        "feedback": (
                            '<p style="color:#c00;">One or more restricted '
                            'circuit components were modified. Your answer '
                            'cannot be graded.</p>'
                        ),
                    },
                },
                {
                    "name": "1",
                    "description": "Check P_R1 against target",
                    "answer_test": "NumAbsolute",
                    "student_answer": "ans1",
                    "teacher_answer": "target_ans1",
                    "test_options": "tol_ans1",
                    "quiet": False,
                    "true_branch": {
                        "score_mode": "=",
                        "score": 1.0,
                        "next_node": -1,
                        "answer_note": "prt1-1-T",
                        "feedback": (
                            '<p>Correct! P_R1 = {@ans1@} W is within '
                            '{@tol_ans1@} W of the target '
                            '{@target_ans1@} W.</p>'
                        ),
                    },
                    "false_branch": {
                        "score_mode": "=",
                        "score": 0.0,
                        "next_node": -1,
                        "answer_note": "prt1-1-F",
                        "feedback": (
                            '<p>Not quite. P_R1 = {@ans1@} W, but the '
                            'target is {@target_ans1@} W (&plusmn; '
                            '{@tol_ans1@} W).</p>'
                        ),
                    },
                },
            ],
        },
    ],
    "tests": [
        {
            "testcase": 1,
            "description": "All correct",
            "inputs": [
                {"name": "ans1", "value": "target_ans1"},
                {"name": "ans_integrity", "value": "1"},
                {"name": "ans_circuit", "value": '""'},
            ],
            "expected": [
                {"name": "prt1", "score": 1.0, "penalty": 0.0,
                 "answer_note": "prt1-1-T"},
            ],
        },
        {
            "testcase": 2,
            "description": "All wrong",
            "inputs": [
                {"name": "ans1", "value": "target_ans1 + tol_ans1 + 1"},
                {"name": "ans_integrity", "value": "1"},
                {"name": "ans_circuit", "value": '""'},
            ],
            "expected": [
                {"name": "prt1", "score": 0.0, "penalty": 0.1,
                 "answer_note": "prt1-1-F"},
            ],
        },
        {
            "testcase": 3,
            "description": "Integrity failure",
            "inputs": [
                {"name": "ans1", "value": "target_ans1"},
                {"name": "ans_integrity", "value": "0"},
                {"name": "ans_circuit", "value": '""'},
            ],
            "expected": [
                {"name": "prt1", "score": 0.0, "penalty": 0.1,
                 "answer_note": "prt1-0-F"},
            ],
        },
    ],
    "tags": ["circuitjs"],
}


def main():
    compiled = compile_question(VOLTAGE_DIVIDER)

    ref_path = os.path.join(os.path.dirname(__file__), 'Voltage_Divider.xml')
    with open(ref_path, 'r', encoding='utf-8') as f:
        reference = f.read()

    # Write compiled output for manual diffing
    out_path = os.path.join(os.path.dirname(__file__), 'compiled_output.xml')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(compiled)

    # Line-by-line diff
    ref_lines = reference.splitlines()
    comp_lines = compiled.splitlines()

    diffs = []
    max_lines = max(len(ref_lines), len(comp_lines))
    for i in range(max_lines):
        ref_line = ref_lines[i] if i < len(ref_lines) else '<EOF>'
        comp_line = comp_lines[i] if i < len(comp_lines) else '<EOF>'
        if ref_line != comp_line:
            diffs.append((i + 1, ref_line, comp_line))

    if not diffs:
        print('PERFECT MATCH — compiled output identical to reference')
    else:
        print(f'{len(diffs)} line(s) differ:\n')
        for lineno, ref, comp in diffs[:30]:
            print(f'  Line {lineno}:')
            print(f'    REF:  {ref}')
            print(f'    GOT:  {comp}')
            print()
        if len(diffs) > 30:
            print(f'  ... and {len(diffs) - 30} more')


if __name__ == '__main__':
    main()
