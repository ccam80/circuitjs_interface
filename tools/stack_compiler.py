"""Standalone STACK question compiler: dict → Moodle XML import format.

Accepts a dict matching the coursesmith stack_question schema
(see coursesmith/asset_types/stack_question/schema.md) and produces
a complete Moodle XML string suitable for question bank import.

This targets the *single-question XML import* format, NOT the MBZ
backup format.  The MBZ format wraps STACK fields in
<plugin qtype="stack">; the import format puts them as direct
children of <question>.  This distinction was discovered by testing
actual Moodle imports — the plugin wrapper causes silent failures.

Structural requirements verified against Moodle 4.x + STACK 4.6:
  - <idnumber/> required after <hidden>
  - <isbroken>0</isbroken> required after <assumereal>
  - <decimals> and <scientificnotation> use direct text (no <text> child)
  - Empty fields use self-closing tags: <syntaxhint/>, <options/>, etc.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------

def _esc(text: str) -> str:
    """XML-escape for text nodes (not attributes — quotes are fine)."""
    return (str(text)
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;'))


def _cdata(text: str) -> str:
    """Wrap text in CDATA, escaping any ]]> sequences."""
    escaped = str(text).replace(']]>', ']]]]><![CDATA[>')
    return f'<![CDATA[{escaped}]]>'


def _bool(val) -> str:
    """Convert truthy to '1'/'0'."""
    return '1' if val else '0'


def _tag(name: str, value, indent: int = 0) -> str:
    """Emit <name>value</name> or <name/> if value is empty string/None."""
    prefix = '  ' * indent
    v = str(value) if value is not None else ''
    if v == '':
        return f'{prefix}<{name}/>\n'
    return f'{prefix}<{name}>{_esc(v)}</{name}>\n'


def _tag_raw(name: str, raw_content: str, indent: int = 0,
             attrs: str = '') -> str:
    """Emit <name attrs>raw_content</name> (content NOT escaped)."""
    prefix = '  ' * indent
    attr_str = f' {attrs}' if attrs else ''
    return f'{prefix}<{name}{attr_str}>{raw_content}</{name}>\n'


def _tag_cdata(name: str, text: str, indent: int = 0,
               attrs: str = '') -> str:
    """Emit <name><text><![CDATA[...]]></text></name>."""
    prefix = '  ' * indent
    attr_str = f' {attrs}' if attrs else ''
    if not text:
        return f'{prefix}<{name}{attr_str}>\n{prefix}  <text/>\n{prefix}</{name}>\n'
    return (f'{prefix}<{name}{attr_str}>\n'
            f'{prefix}  <text>{_cdata(text)}</text>\n'
            f'{prefix}</{name}>\n')


# ---------------------------------------------------------------------------
# Defaults — match STACK/Moodle defaults, overridable via the dict
# ---------------------------------------------------------------------------

_PRT_CORRECT = (
    '<span style="font-size: 1.5em; color:green;">'
    '<i class="fa fa-check"></i></span> Correct answer, well done.'
)
_PRT_PARTIAL = (
    '<span style="font-size: 1.5em; color:orange;">'
    '<i class="fa fa-adjust"></i></span> Your answer is partially correct.'
)
_PRT_INCORRECT = (
    '<span style="font-size: 1.5em; color:red;">'
    '<i class="fa fa-times"></i></span> Incorrect answer.'
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compile_question(d: dict) -> str:
    """Compile a stack_question dict to a Moodle XML import string.

    Args:
        d: Dict matching the coursesmith stack_question YAML schema.

    Returns:
        Complete XML string with <?xml?> header, <quiz> wrapper,
        optional category pseudo-question, and the STACK question.
    """
    p: list[str] = []
    p.append('<?xml version="1.0" encoding="UTF-8"?>\n<quiz>\n')

    # Optional category
    cat = d.get('category', '')
    if cat:
        p.append('  <question type="category">\n')
        p.append('    <category>\n')
        p.append(f'      <text>{_esc(cat)}</text>\n')
        p.append('    </category>\n')
        p.append('    <info format="html">\n      <text/>\n    </info>\n')
        p.append('  </question>\n')

    p.append('  <question type="stack">\n')

    # --- name ---
    p.append(f'    <name>\n      <text>{_esc(d["name"])}</text>\n    </name>\n')

    # --- questiontext ---
    p.append('    <questiontext format="html">\n')
    p.append(f'      <text>{_cdata(d.get("question_text", ""))}</text>\n')
    p.append('    </questiontext>\n')

    # --- generalfeedback ---
    p.append('    <generalfeedback format="html">\n')
    p.append(f'      <text>{_cdata(d.get("general_feedback", ""))}</text>\n')
    p.append('    </generalfeedback>\n')

    # --- standard fields ---
    p.append(_tag('defaultgrade', d.get('default_grade', 1), 2))
    p.append(_tag('penalty', d.get('penalty', 0.1), 2))
    p.append(_tag('hidden', 0, 2))
    p.append('    <idnumber/>\n')

    # --- STACK fields (direct children of <question>) ---
    p.append(_tag_cdata('stackversion', '', 3))
    p.append(_tag_cdata('questionvariables',
                        d.get('question_variables', ''), 3))
    p.append(_tag_cdata('specificfeedback',
                        d.get('specific_feedback', '[[feedback:prt1]]'),
                        3, 'format="html"'))
    p.append(_tag_cdata('questionnote',
                        d.get('question_note', ''), 3, 'format="html"'))
    p.append(_tag_cdata('questiondescription',
                        d.get('question_description', ''), 3,
                        'format="html"'))

    # Boolean options
    p.append(_tag('questionsimplify',
                  _bool(d.get('question_simplify', True)), 3))
    p.append(_tag('assumepositive',
                  _bool(d.get('assume_positive', False)), 3))
    p.append(_tag('assumereal',
                  _bool(d.get('assume_real', False)), 3))
    p.append(_tag('isbroken', 0, 3))

    # PRT feedback messages
    p.append(_tag_cdata('prtcorrect',
                        d.get('prt_correct', _PRT_CORRECT),
                        3, 'format="html"'))
    p.append(_tag_cdata('prtpartiallycorrect',
                        d.get('prt_partially_correct', _PRT_PARTIAL),
                        3, 'format="html"'))
    p.append(_tag_cdata('prtincorrect',
                        d.get('prt_incorrect', _PRT_INCORRECT),
                        3, 'format="html"'))

    # Display options — these use direct text content, NOT <text> children
    p.append(_tag('decimals', d.get('decimals', '.'), 3))
    p.append(_tag('scientificnotation',
                  d.get('scientific_notation', '*10'), 3))
    p.append(_tag('multiplicationsign',
                  d.get('multiplication_sign', 'dot'), 3))
    p.append(_tag('sqrtsign',
                  _bool(d.get('sqrt_sign', True)), 3))
    p.append(_tag('complexno', d.get('complex_no', 'j'), 3))
    p.append(_tag('inversetrig', d.get('inverse_trig', 'cos-1'), 3))
    p.append(_tag('logicsymbol', d.get('logic_symbol', 'lang'), 3))
    p.append(_tag('matrixparens', d.get('matrix_parens', '['), 3))
    p.append(_tag('variantsselectionseed',
                  d.get('variants_selection_seed', ''), 3))

    # --- Inputs ---
    for inp in d.get('inputs', []):
        p.append(_emit_input(inp))

    # --- PRTs ---
    for prt in d.get('prts', []):
        p.append(_emit_prt(prt))

    # --- Deployed seeds ---
    for seed in d.get('deployed_seeds', []):
        p.append(_tag('deployedseed', seed, 3))

    # --- Test cases ---
    for test in d.get('tests', []):
        p.append(_emit_qtest(test))

    # --- Tags ---
    tags = d.get('tags', [])
    if tags:
        p.append('    <tags>\n')
        for t in tags:
            p.append(f'      <tag>\n        <text>{_esc(t)}</text>\n'
                     f'      </tag>\n')
        p.append('    </tags>\n')

    p.append('  </question>\n</quiz>\n')
    return ''.join(p)


# ---------------------------------------------------------------------------
# Input emission
# ---------------------------------------------------------------------------

_SHOW_VALIDATION = {'WITH_VARIABLES': 1, 'COMPACT': 2, 'NONE': 0}
_INPUT_TYPE_MAP = {
    'algebraic': 'algebraic', 'numerical': 'numerical',
    'units': 'units', 'matrix': 'matrix', 'textarea': 'textarea',
    'dropdown': 'dropdown', 'radio': 'radio', 'checkbox': 'checkbox',
    'boolean': 'boolean', 'string': 'string', 'equiv_reasoning': 'equiv',
}


def _emit_input(inp: dict) -> str:
    p: list[str] = []
    p.append('      <input>\n')
    p.append(_tag('name', inp['name'], 4))

    itype = inp.get('type', 'algebraic')
    p.append(_tag('type', _INPUT_TYPE_MAP.get(itype, itype), 4))
    p.append(_tag('tans', inp.get('model_answer', ''), 4))
    p.append(_tag('boxsize', inp.get('box_size', 15), 4))
    p.append(_tag('strictsyntax',
                  _bool(inp.get('strict_syntax', True)), 4))
    p.append(_tag('insertstars', inp.get('insert_stars', 0), 4))
    p.append(_tag('syntaxhint', inp.get('syntax_hint', ''), 4))
    p.append(_tag('syntaxattribute',
                  inp.get('syntax_attribute', 0), 4))
    p.append(_tag('forbidwords', inp.get('forbid_words', ''), 4))
    p.append(_tag('allowwords', inp.get('allow_words', ''), 4))
    p.append(_tag('forbidfloat',
                  _bool(inp.get('forbid_float', False)), 4))
    p.append(_tag('requirelowestterms',
                  _bool(inp.get('require_lowest_terms', False)), 4))
    p.append(_tag('checkanswertype',
                  _bool(inp.get('check_answer_type', False)), 4))
    p.append(_tag('mustverify',
                  _bool(inp.get('must_verify', True)), 4))

    sv = inp.get('show_validation', 1)
    if isinstance(sv, str):
        sv = _SHOW_VALIDATION.get(sv, 1)
    p.append(_tag('showvalidation', sv, 4))
    p.append(_tag('options', inp.get('options', ''), 4))
    p.append('      </input>\n')
    return ''.join(p)


# ---------------------------------------------------------------------------
# PRT emission
# ---------------------------------------------------------------------------

_FEEDBACK_STYLE = {'FORMATIVE': 0, 'STANDARD': 1, 'COMPACT': 2,
                   'SYMBOL_ONLY': 3}


def _emit_prt(prt: dict) -> str:
    p: list[str] = []
    p.append('      <prt>\n')
    p.append(_tag('name', prt['name'], 4))
    p.append(_tag('value', prt.get('value', 1), 4))
    p.append(_tag('autosimplify',
                  _bool(prt.get('auto_simplify', True)), 4))

    fs = prt.get('feedback_style', 'STANDARD')
    if isinstance(fs, str):
        fs = _FEEDBACK_STYLE.get(fs, 1)
    p.append(_tag('feedbackstyle', fs, 4))

    fbvars = prt.get('feedback_variables', '')
    if fbvars:
        p.append(_tag_cdata('feedbackvariables', fbvars, 4))
    else:
        p.append('        <feedbackvariables>\n'
                 '          <text/>\n'
                 '        </feedbackvariables>\n')

    for node in prt.get('nodes', []):
        p.append(_emit_node(node))

    p.append('      </prt>\n')
    return ''.join(p)


def _emit_node(node: dict) -> str:
    p: list[str] = []
    p.append('        <node>\n')
    p.append(_tag('name', node.get('name', '0'), 5))
    p.append(_tag('description', node.get('description', ''), 5))
    p.append(_tag('answertest', node.get('answer_test', 'AlgEquiv'), 5))
    p.append(_tag('sans', node.get('student_answer', ''), 5))
    p.append(_tag('tans', node.get('teacher_answer', ''), 5))
    p.append(_tag('testoptions', node.get('test_options', ''), 5))
    p.append(_tag('quiet', _bool(node.get('quiet', False)), 5))

    for prefix in ('true', 'false'):
        branch = node.get(f'{prefix}_branch', {})
        p.append(_tag(f'{prefix}scoremode',
                      branch.get('score_mode', '='), 5))
        p.append(_tag(f'{prefix}score', branch.get('score', 0.0), 5))
        penalty = branch.get('penalty')
        p.append(_tag(f'{prefix}penalty',
                      str(penalty) if penalty is not None else '', 5))
        next_node = branch.get('next_node')
        p.append(_tag(f'{prefix}nextnode',
                      next_node if next_node is not None else -1, 5))
        p.append(_tag(f'{prefix}answernote',
                      branch.get('answer_note', ''), 5))
        fb_text = branch.get('feedback', '')
        if fb_text:
            p.append(f'          <{prefix}feedback format="html">\n')
            p.append(f'            <text>{_cdata(fb_text)}</text>\n')
            p.append(f'          </{prefix}feedback>\n')
        else:
            p.append(f'          <{prefix}feedback format="html">\n')
            p.append(f'            <text/>\n')
            p.append(f'          </{prefix}feedback>\n')

    p.append('        </node>\n')
    return ''.join(p)


# ---------------------------------------------------------------------------
# Test case emission
# ---------------------------------------------------------------------------

def _emit_qtest(test: dict) -> str:
    p: list[str] = []
    p.append('      <qtest>\n')
    p.append(_tag('testcase', test.get('testcase', 1), 4))
    p.append(_tag('description', test.get('description', ''), 4))

    # Test inputs — accept list-of-dicts or dict-of-dicts
    inputs = test.get('inputs', {})
    if isinstance(inputs, dict):
        for name, value in inputs.items():
            p.append('        <testinput>\n')
            p.append(_tag('name', name, 5))
            p.append(_tag('value', value, 5))
            p.append('        </testinput>\n')
    else:
        for ti in inputs:
            p.append('        <testinput>\n')
            p.append(_tag('name', ti['name'], 5))
            p.append(_tag('value', ti['value'], 5))
            p.append('        </testinput>\n')

    # Expected results — accept list-of-dicts or dict-of-dicts
    expected = test.get('expected', {})
    if isinstance(expected, dict):
        for name, exp in expected.items():
            p.append(_emit_expected(name, exp))
    else:
        for exp in expected:
            p.append(_emit_expected(exp['name'], exp))

    p.append('      </qtest>\n')
    return ''.join(p)


def _emit_expected(name: str, exp) -> str:
    p: list[str] = []
    p.append('        <expected>\n')
    p.append(_tag('name', name, 5))
    if isinstance(exp, dict):
        p.append(_tag('expectedscore',
                      f"{exp.get('score', 0.0):.7f}", 5))
        p.append(_tag('expectedpenalty',
                      f"{exp.get('penalty', 0.0):.7f}", 5))
        p.append(_tag('expectedanswernote',
                      exp.get('answer_note', ''), 5))
    p.append('        </expected>\n')
    return ''.join(p)
