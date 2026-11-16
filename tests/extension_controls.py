#!/usr/bin/env python3
"""Positive/negative controls for `appctl extensions` on real v4/v5/v5b exports (no model, no database).

Each control starts from the frozen cohort export (ctl.files at the cohort base commit), applies a
human-written candidate from the phase controls modules or a small extra mutation reproducing an
observed real failure, and asserts the exact set of rule codes. Codes not listed must not appear.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / 'bin/appctl'
BASE = 'd8a5c576cc3c293ee2da884f5c618958df407b51'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def replace(path, old, new):
    text = path.read_text()
    assert text.count(old) == 1, (path, old)
    path.write_text(text.replace(old, new))


V4_REJECT = 'var Reject = Transition{Operation: "bulk-reject.v4", From: "submitted", To: "rejected", Field: "rejectedIds"}\n'


def v4_kernel_reject(c):  # the real v4 haiku/qwen failure: Reject declared beside action.Approve
    replace(c / 'internal/action/action.go', 'type Rejection struct', V4_REJECT + '\ntype Rejection struct')


def v4_candidate_reject(c):  # its in-scope relocation (the v4 diagnostic)
    replace(c / 'internal/action/action.go', 'type Rejection struct', V4_REJECT + '\ntype Rejection struct')
    subprocess.run(['git', 'checkout', '--', 'internal/action/action.go'], cwd=c, check=True)
    (c / 'internal/candidate/reject.go').write_text('package candidate\n\nimport "github.com/XUJiahua/enterprise_agent/internal/action"\n\n'
                                                     + V4_REJECT.replace('Transition{', 'action.Transition{').replace('var Reject', 'var reject'))


def event(value):  # v5b reopen declared through the kernel with the given Event expression
    def apply(c):
        controls5.apply(c, 'go-action', 'correct')
        replace(c / 'internal/candidate/operations.go', 'Event: "purchase.reopened"', 'Event: ' + value)
    return apply


def field(old, new):
    def apply(c):
        controls5.apply(c, 'go-action', 'correct')
        replace(c / 'internal/candidate/operations.go', old, new)
    return apply


def kernel_reopen(c):  # declaration inside the protected kernel
    controls5.apply(c, 'go-action', 'correct')
    text = (c / 'internal/candidate/operations.go').read_text()
    decl = text[text.index('var reopenOp'):].strip()
    (c / 'internal/candidate/operations.go').write_text(text.replace(decl, ''))
    action = c / 'internal/action/action.go'
    action.write_text(action.read_text() + '\nvar Reopen = ' + decl.split(' = ', 1)[1].replace('action.', '') + '\n')
    approval = c / 'internal/candidate/approval.go'
    approval.write_text(approval.read_text().replace('reopenOp', 'action.Reopen'))


def manual(mode, old=None, new=None):
    def apply(c):
        controls5.apply(c, 'go-manual', mode)
        if old:
            replace(c / 'internal/candidate/reopen.go', old, new)
    return apply


ctl4 = load('ctl', 'benchmark/phase4/ctl.py')
controls4 = load('controls4', 'benchmark/phase4/controls.py')
ctl5 = load('ctl5', 'benchmark/phase5/ctl.py')
ctl5b = load('ctl5b', 'benchmark/phase5b/ctl.py').ctl
controls5 = load('controls5', 'benchmark/phase5/controls.py')

# (phase, cell, name) -> (apply, exact expected codes)
P5_ACTION = {
    'correct': (lambda c: controls5.apply(c, 'go-action', 'correct'), set()),
    'omit-event': (lambda c: controls5.apply(c, 'go-action', 'omit-event'), {'FIELD_MISSING', 'EVENT_EXPECTED_BY_CONSUMER'}),
    'own-false': (lambda c: controls5.apply(c, 'go-action', 'own-false'), set()),  # behavioural; not static
    'bypass-reason-hash': (lambda c: controls5.apply(c, 'go-action', 'reason-hash'), {'FINGERPRINT_EXTRA_INPUT'}),
    'bypass-omit-event': (lambda c: manual_bypass(c), {'EVENT_EXPECTED_BY_CONSUMER'}),
    'bypass-omit-audit': (lambda c: controls5.apply(c, 'go-action', 'omit-audit'), set()),  # not covered
    'bypass-distinguish': (lambda c: controls5.apply(c, 'go-action', 'distinguish'), set()),  # not covered
    'unchanged': (lambda c: None, set()),
}


def manual_bypass(c):  # hand-written reopen without an event inside the action cell
    controls5.apply(c, 'go-action', 'omit-audit')
    text = (c / 'internal/candidate/reopen.go').read_text()
    start = text.index('\t\tif _, err = tx.Exec(ctx, `INSERT INTO "PurchaseEvent"')
    end = text.index('\t\t}\n', start) + 4
    (c / 'internal/candidate/reopen.go').write_text(text[:start] + text[end:])


CONTROLS = {
    ('v4', 'go-action', 'correct'): (lambda c: controls4.apply(c, 'go-action', 'correct'), set()),
    ('v4', 'go-action', 'kernel-reject'): (v4_kernel_reject, {'PROTECTED_PATH_CHANGED', 'DECLARATION_OUTSIDE_EXTENSION'}),
    ('v4', 'go-action', 'candidate-reject'): (v4_candidate_reject, set()),
    ('v4', 'go-action', 'unversioned'): (lambda c: (v4_candidate_reject(c), replace(c / 'internal/candidate/reject.go', '"bulk-reject.v4"', '"bulk-reject"')), {'NAME_UNVERSIONED'}),
    ('v4', 'go-action', 'duplicate'): (lambda c: (v4_candidate_reject(c), replace(c / 'internal/candidate/reject.go', '"bulk-reject.v4"', '"bulk-approve.v3"')), {'OPERATION_NAME_DUPLICATE'}),
    ('v4', 'go-manual', 'correct'): (lambda c: controls4.apply(c, 'go-manual', 'correct'), set()),
    ('v4', 'go-manual', 'approve-hash'): (lambda c: controls4.apply(c, 'go-manual', 'approve-hash'), {'FINGERPRINT_OPERATION_MISSING'}),
    ('v4', 'go-manual', 'unchanged'): (lambda c: None, set()),
    **{('v5', 'go-action', k): v for k, v in P5_ACTION.items()},
    ('v5', 'go-manual', 'correct'): (manual('correct'), set()),
    ('v5', 'go-manual', 'reason-hash'): (manual('reason-hash'), {'FINGERPRINT_EXTRA_INPUT'}),
    ('v5', 'go-manual', 'omit-event'): (manual('omit-event'), {'EVENT_EXPECTED_BY_CONSUMER'}),
    ('v5', 'go-manual', 'wrong-event'): (manual('correct', "'purchase.reopened'", "'purchase.resubmitted'"), {'EVENT_NO_CONSUMER', 'EVENT_EXPECTED_BY_CONSUMER'}),
    ('v5', 'go-manual', 'literal-op-omit-event'): (lambda c: (manual('omit-event')(c), (c / 'internal/candidate/reopen.go').write_text(
        (c / 'internal/candidate/reopen.go').read_text().replace('const opReopen = "reopen.v5"\n', '').replace('opReopen', '"reopen.v5"'))), {'EVENT_EXPECTED_BY_CONSUMER'}),
    ('v5', 'go-manual', 'omit-audit'): (manual('omit-audit'), set()),  # not covered
    ('v5', 'go-manual', 'distinguish'): (manual('distinguish'), set()),  # not covered
    ('v5', 'go-manual', 'unchanged'): (lambda c: None, set()),
    **{('v5b', 'go-action', k): v for k, v in P5_ACTION.items()},
    ('v5b', 'go-action', 'noevent'): (event('action.NoEvent'), {'EVENT_EXPECTED_BY_CONSUMER'}),
    ('v5b', 'go-action', 'noevent-literal'): (event('"none"'), {'EVENT_EXPECTED_BY_CONSUMER'}),
    ('v5b', 'go-action', 'wrong-event'): (event('"purchase.resubmitted"'), {'EVENT_NO_CONSUMER'}),
    ('v5b', 'go-action', 'empty-event'): (event('""'), {'FIELD_EMPTY', 'EVENT_EXPECTED_BY_CONSUMER'}),
    ('v5b', 'go-action', 'non-constant'): (field('Event: "purchase.reopened"}', 'Event: reopenEvent}\n\nvar reopenEvent = "purchase.reopened"'), {'FIELD_NOT_CONSTANT'}),
    ('v5b', 'go-action', 'missing-role'): (field('Role: "requester", ', ''), {'FIELD_MISSING'}),
    ('v5b', 'go-action', 'kernel-reopen'): (kernel_reopen, {'PROTECTED_PATH_CHANGED', 'DECLARATION_OUTSIDE_EXTENSION'}),
    ('v5b', 'go-manual', 'correct'): (manual('correct'), set()),
    ('v5b', 'go-manual', 'reason-hash'): (manual('reason-hash'), {'FINGERPRINT_EXTRA_INPUT'}),
    ('v5b', 'go-manual', 'omit-event'): (manual('omit-event'), {'EVENT_EXPECTED_BY_CONSUMER'}),
    ('v5b', 'go-manual', 'protocol-edit'): (lambda c: (manual('correct')(c), (c / 'internal/protocol/protocol.go').write_text(
        (c / 'internal/protocol/protocol.go').read_text() + '\n// edited\n')), {'PROTECTED_PATH_CHANGED'}),
    ('v5b', 'go-manual', 'unchanged'): (lambda c: None, set()),
}
CTL = {'v4': ctl4, 'v5': ctl5, 'v5b': ctl5b}


def export(phase, cell, target):
    c = next(x for x in CTL[phase].contract()['cells'] if x['id'] == cell)
    for name, data in CTL[phase].files({'base_sha': BASE}, c).items():
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        (target / name).write_bytes(data)
    for args in (['init', '-q', '-b', 'candidate'], ['add', '-A'],
                 ['-c', 'user.name=POC', '-c', 'user.email=poc@example.invalid', 'commit', '-qm', 'export']):
        subprocess.run(['git', *args], cwd=target, check=True)
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=target, text=True).strip()


# Known-bad candidates no static rule targets (behavioural or out of scope): expected clean, reported separately.
UNCOVERED = {'own-false', 'bypass-omit-audit', 'bypass-distinguish', 'omit-audit', 'distinguish', 'unchanged'}


def main():
    results = []
    with tempfile.TemporaryDirectory(prefix='ext-controls-') as temp:
        for (phase, cell, name), (apply, expected) in CONTROLS.items():
            target = Path(temp) / f'{phase}-{cell}-{name}'
            target.mkdir()
            base = export(phase, cell, target)
            apply(target)
            build = subprocess.run(['go', 'build', './...'], cwd=target, capture_output=True, text=True)
            p = subprocess.run([str(BINARY), 'extensions', '--root', str(target), '--base', base], capture_output=True, text=True)
            report = json.loads(p.stdout)
            codes = {f['code'] for f in report['findings']}
            ok = codes == expected and p.returncode == (1 if expected else 0) and build.returncode == 0
            kind = 'negative' if expected else 'uncovered' if name in UNCOVERED else 'positive'
            results.append({'phase': phase, 'cell': cell, 'control': name, 'kind': kind, 'expected': sorted(expected), 'codes': sorted(codes),
                            'exit': p.returncode, 'builds': build.returncode == 0, 'passed': ok})
            assert ok, (results[-1], report, build.stderr[-1500:])
    count = lambda k: sum(r['kind'] == k for r in results)
    print(json.dumps({'ok': True, 'kind': 'extension-verifier-controls', 'model_calls': 0, 'controls': len(results),
                      'negative_detected': count('negative'), 'positive_clean': count('positive'),
                      'uncovered_clean': count('uncovered'), 'results': results}, indent=1))


if __name__ == '__main__':
    main()
