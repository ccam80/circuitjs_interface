"""Parity test: YAML dict -> falstad_compiler -> stack_compiler -> XML vs reference."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))

from falstad_compiler import compile as falstad_compile
from stack_compiler import compile_question

VOLTAGE_DIVIDER_YAML = {
    "name": "Voltage Divider",
    "category": "M1:",
    "description": "Adjust the circuit as instructed.",
    "ctz": (
        "CQAgjCAMB0l3BWcMBMcUHYMGZIA4UA2ATmIxEO2QpRAQFMBaMMAKADdwxbtC8va"
        "AFn6QoIQaLCjpUaAlYB3AeP4oJKqIuW9V6nZqVrRw8diEitRkPsFnrfTQCdT5l+B"
        "Qjk8Vs9u1u-H4gaJ5S3r52IW4engjh0XiBdvqicXCsaORBAeLEhO6BIABqAIIZkFm"
        "R+CqiUYLFALLllbQxuflt9UUAQqxAA"
    ),
    "question_variables": "P_expected: 0.0015;",
    "measurements": [
        {
            "source": "element",
            "identifier": "R1",
            "element_index": 5,
            "property": "power",
            "target": 2.5,
            "tolerance": 0.1,
            "graded": True,
        },
    ],
    "integrity": {
        "editable_indices": [5, 6],
        "removable_indices": [10],
        "type_rules": [
            {"type": "CapacitorElm", "maxAdd": 1, "maxRemove": 0},
        ],
    },
    "tags": ["circuitjs"],
}


def main():
    # Compile through both stages
    stack_dict = falstad_compile(VOLTAGE_DIVIDER_YAML)
    compiled = compile_question(stack_dict)

    # Load reference
    ref_path = os.path.join(os.path.dirname(__file__), 'Voltage_Divider.xml')
    with open(ref_path, 'r', encoding='utf-8') as f:
        reference = f.read()

    # Write output for debugging
    out_path = os.path.join(os.path.dirname(__file__),
                            'falstad_compiled_output.xml')
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
        print('PERFECT MATCH — falstad compiler output identical to reference')
    else:
        print(f'{len(diffs)} line(s) differ:\n')
        for lineno, ref, comp in diffs[:30]:
            print(f'  Line {lineno}:')
            print(f'    REF:  {ref!r}')
            print(f'    GOT:  {comp!r}')
            print()
        if len(diffs) > 30:
            print(f'  ... and {len(diffs) - 30} more')
        sys.exit(1)


if __name__ == '__main__':
    main()
