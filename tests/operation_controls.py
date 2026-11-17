#!/usr/bin/env python3
"""Positive/negative controls for `appctl operations` (catalog-bound Verifier); no model, no database.

Two kinds of starting points:
  * v6 cohort exports (T1 = phase5b, T2 = phase6a, T3 = phase6b) at the cohort base commit, with the
    human candidates of each phase's controls module (the v6 kernel: extension code declares
    action.Operation / action.Update, bound field by field to the catalog);
  * a platform export: the T3 action export with internal/action replaced by the protected catalog
    (internal/opdef) and catalog kernel (internal/opkernel); extension code references entries by name.
Each control asserts the exact set of finding codes and the exit code (0 pass, 1 fail, 3 unverifiable),
and that the candidate builds.
"""
import importlib.util
import json
from pathlib import Path
import shutil
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


CTL = {'T1': load('ctl5b', 'benchmark/phase5b/ctl.py').ctl, 'T2': load('ctl6a', 'benchmark/phase6a/ctl.py'),
       'T3': load('ctl6b', 'benchmark/phase6b/ctl.py').ctl}
MOD = {'T1': load('controls5', 'benchmark/phase5/controls.py'), 'T2': load('controls6a', 'benchmark/phase6a/controls.py'),
       'T3': load('controls6b', 'benchmark/phase6b/controls.py')}
OPS = 'internal/candidate/operations.go'


def human(task, cell, mode, old=None, new=None, path=OPS):
    def apply(c):
        MOD[task].apply(c, cell, mode)
        if old is not None:
            replace(c / path, old, new)
    return apply


PLATFORM_APPROVAL = '''// Batch Actions of the purchase service through the catalog kernel.
package candidate

import (
\t"context"
\t"errors"
\t"net/http"

\t"github.com/XUJiahua/enterprise_agent/internal/app"
\t"github.com/XUJiahua/enterprise_agent/internal/opdef"
\t"github.com/XUJiahua/enterprise_agent/internal/opkernel"
\t"github.com/XUJiahua/enterprise_agent/internal/protocol"
\t"github.com/jackc/pgx/v5/pgxpool"
)

func adapt(err error) error {
\tvar denied *opkernel.Rejection
\tif errors.As(err, &denied) {
\t\treturn &protocol.Rejection{Status: denied.Status}
\t}
\treturn err
}

func batch(executor *opkernel.Executor, name opdef.Name) protocol.Handler {
\top, _ := opdef.Lookup(name)
\treturn func(ctx context.Context, u app.Identity, key string, ids []string) (protocol.Result, bool, error) {
\t\tdone, replayed, err := executor.Run(ctx, u, name, key, opkernel.Request{IDs: ids})
\t\tif err != nil {
\t\t\treturn protocol.Result{}, false, adapt(err)
\t\t}
\t\treturn protocol.Result{Field: op.Response, IDs: done}, replayed, nil
\t}
}

func New(ctx context.Context, pool *pgxpool.Pool, fallback http.Handler) (http.Handler, error) {
\texecutor, err := opkernel.New(ctx, pool)
\tif err != nil {
\t\treturn nil, err
\t}
\treopen := func(ctx context.Context, u app.Identity, key, id, reason string) (protocol.ItemResult, bool, error) {
\t\tdone, replayed, err := executor.Run(ctx, u, opdef.Reopen, key, opkernel.Request{IDs: []string{id}, Reason: reason})
\t\tif err != nil {
\t\t\treturn protocol.ItemResult{}, false, adapt(err)
\t\t}
\t\treturn protocol.ItemResult{ID: done[0], Status: "submitted"}, replayed, nil
\t}
@REASSIGN@\tbatches := protocol.HTTP(protocol.Routes{"/purchases/bulk-approve": batch(executor, opdef.BulkApprove), "/purchases/bulk-reject": batch(executor, opdef.BulkReject)}, fallback)
\treturn @ROUTE@, nil
}
'''
REASSIGN = '''\treassign := func(ctx context.Context, u app.Identity, key string, ids []string, assignee string) (protocol.AssignResult, bool, error) {
\t\tdone, replayed, err := executor.Run(ctx, u, @NAME@, key, opkernel.Request{IDs: ids, Value: assignee})
\t\tif err != nil {
\t\t\treturn protocol.AssignResult{}, false, adapt(err)
\t\t}
\t\treturn protocol.AssignResult{Field: "reassignedIds", IDs: done, Assignee: assignee}, replayed, nil
\t}
'''
ROUTE = 'protocol.Item("reopen", "requester", reopen, @INNER@)'
DECLARED = '''
var reassignOp = opdef.Operation{Name: opdef.BulkReassign, Kind: opdef.FieldUpdate, Role: "approver", Subject: opdef.Others,
\tStatus: "submitted", Column: "assigneeId", Param: "assignee", Response: "reassignedIds",
\tInputs: []string{opdef.IDs, "assignee"}, Fingerprint: []string{opdef.IDs, "assignee"}, Record: opdef.AssignmentHistory,
\t@EVENT@, Denial: opdef.UniformDenial}
'''


def platform_source(name=None, declared=None, extra=''):
    reassign = REASSIGN.replace('@NAME@', name) if name else ''
    inner = 'protocol.Assign("/purchases/bulk-reassign", reassign, batches)' if name else 'batches'
    text = PLATFORM_APPROVAL.replace('@REASSIGN@', reassign).replace('@ROUTE@', ROUTE.replace('@INNER@', inner))
    if declared:
        text += DECLARED.replace('@EVENT@', declared)
    return text.replace('import (\n', 'import (\n' + extra) if extra else text


def platform_export(target, extra_kernel=''):
    """T3 action export with the v6 kernel replaced by the catalog and catalog kernel."""
    export('T3', 'go-action', target, do_commit=False)
    shutil.rmtree(target / 'internal/action')
    (target / OPS).unlink()
    for rel in ('internal/opdef/opdef.go', 'internal/opdef/catalog.go', 'internal/opdef/registry.go', 'internal/opkernel/opkernel.go'):
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, target / rel)
    if extra_kernel:
        with open(target / 'internal/opkernel/opkernel.go', 'a') as f:
            f.write(extra_kernel)
    (target / 'internal/candidate/approval.go').write_text(platform_source())
    return commit(target)


def platform(name=None, declared=None, extra='', after=None):
    def apply(c):
        text = platform_source(name, declared, extra)
        (c / 'internal/candidate/approval.go').write_text(text)
        if after:
            after(c)
    return apply


def exempt_in_catalog(c):  # the extension author edits the protected catalog to exempt reassign
    replace(c / 'internal/opdef/catalog.go', 'Event: "purchase.reassigned", Denial', 'EventExemption: "not needed for reassign", Denial')


NEW_KERNEL_TYPE = '\n// Batch is a kernel type added after this Verifier was built.\ntype Batch struct{ Names []opdef.Name }\n'
LEGACY_OP_ONLY = ['--legacy', 'Operation']  # the v5b-era Verifier knowledge: action.Update unknown

# (task, cell, control) -> (apply, expected codes, extra appctl args, platform kernel addition)
CONTROLS = {
    # T1 reopen (phase5b kernel + human candidates)
    ('T1', 'go-manual', 'correct'): (human('T1', 'go-manual', 'correct'), set()),
    ('T1', 'go-manual', 'omit-audit'): (human('T1', 'go-manual', 'omit-audit'), {'RECORD_NOT_WRITTEN'}),
    ('T1', 'go-manual', 'reason-hash'): (human('T1', 'go-manual', 'reason-hash'), {'FINGERPRINT_EXTRA_INPUT'}),
    ('T1', 'go-manual', 'omit-event'): (human('T1', 'go-manual', 'omit-event'), {'EVENT_NOT_WRITTEN'}),
    ('T1', 'go-manual', 'distinguish'): (human('T1', 'go-manual', 'distinguish'), set()),
    ('T1', 'go-manual', 'unchanged'): (lambda c: None, set()),
    ('T1', 'go-action', 'correct'): (human('T1', 'go-action', 'correct'), set()),
    ('T1', 'go-action', 'noevent'): (human('T1', 'go-action', 'correct', 'Event: "purchase.reopened"', 'Event: action.NoEvent'), {'EVENT_EXEMPTION_NOT_IN_CATALOG'}),
    ('T1', 'go-action', 'omit-event'): (human('T1', 'go-action', 'omit-event'), {'EVENT_MISMATCH'}),
    ('T1', 'go-action', 'own-false'): (human('T1', 'go-action', 'own-false'), {'DECLARATION_MISMATCH'}),
    ('T1', 'go-action', 'unknown-name'): (human('T1', 'go-action', 'correct', 'Name: "reopen.v5"', 'Name: "reopen.v6"'), {'OPERATION_NOT_IN_CATALOG'}),
    ('T1', 'go-action', 'bypass-omit-audit'): (human('T1', 'go-action', 'omit-audit'), {'RECORD_NOT_WRITTEN'}),
    ('T1', 'go-action', 'bypass-reason-hash'): (human('T1', 'go-action', 'reason-hash'), {'FINGERPRINT_EXTRA_INPUT'}),
    ('T1', 'go-action', 'bypass-distinguish'): (human('T1', 'go-action', 'distinguish'), set()),
    # T2 countersign (phase6a)
    ('T2', 'go-manual', 'correct'): (human('T2', 'go-manual', 'correct'), set()),
    ('T2', 'go-manual', 'omit-audit'): (human('T2', 'go-manual', 'omit-audit'), {'RECORD_NOT_WRITTEN'}),
    ('T2', 'go-manual', 'amount-hash'): (human('T2', 'go-manual', 'amount-hash'), {'FINGERPRINT_EXTRA_INPUT'}),
    ('T2', 'go-manual', 'omit-event'): (human('T2', 'go-manual', 'omit-event'), {'EVENT_NOT_WRITTEN'}),
    ('T2', 'go-manual', 'distinguish'): (human('T2', 'go-manual', 'distinguish'), set()),
    ('T2', 'go-manual', 'unchanged'): (lambda c: None, set()),
    ('T2', 'go-action', 'correct'): (human('T2', 'go-action', 'correct'), set()),
    ('T2', 'go-action', 'no-event'): (human('T2', 'go-action', 'no-event'), {'EVENT_EXEMPTION_NOT_IN_CATALOG'}),
    ('T2', 'go-action', 'empty-event'): (human('T2', 'go-action', 'empty-event'), {'EVENT_MISMATCH'}),
    ('T2', 'go-action', 'no-separate'): (human('T2', 'go-action', 'no-separate'), {'DECLARATION_MISMATCH'}),
    ('T2', 'go-action', 'bypass-amount-hash'): (human('T2', 'go-action', 'amount-hash'), {'FINGERPRINT_EXTRA_INPUT'}),
    ('T2', 'go-action', 'bypass-distinguish'): (human('T2', 'go-action', 'distinguish'), set()),
    # T3 bulk reassign (phase6b): a field update, assignee is part of the fingerprint
    ('T3', 'go-manual', 'correct'): (human('T3', 'go-manual', 'correct'), set()),
    ('T3', 'go-manual', 'now-timestamp'): (human('T3', 'go-manual', 'now-timestamp'), set()),
    ('T3', 'go-manual', 'omit-history'): (human('T3', 'go-manual', 'omit-history'), {'RECORD_NOT_WRITTEN'}),
    ('T3', 'go-manual', 'param-hash'): (human('T3', 'go-manual', 'param-hash'), {'FINGERPRINT_SHAPE'}),
    ('T3', 'go-manual', 'drop-assignee-hash'): (human('T3', 'go-manual', 'correct', 'ledger.Fingerprint(opReassign+"\\n"+assignee, ids)',
                                                      'ledger.Fingerprint(opReassign, ids)', 'internal/candidate/reassign.go'), {'FINGERPRINT_INPUT_MISSING'}),
    ('T3', 'go-manual', 'omit-event'): (human('T3', 'go-manual', 'omit-event'), {'EVENT_NOT_WRITTEN'}),
    ('T3', 'go-manual', 'distinguish'): (human('T3', 'go-manual', 'distinguish'), set()),
    ('T3', 'go-manual', 'unchanged'): (lambda c: None, set()),
    ('T3', 'go-action', 'correct'): (human('T3', 'go-action', 'correct'), set()),
    ('T3', 'go-action', 'no-event'): (human('T3', 'go-action', 'no-event'), {'EVENT_EXEMPTION_NOT_IN_CATALOG'}),
    ('T3', 'go-action', 'omit-event'): (human('T3', 'go-action', 'omit-event'), {'EVENT_MISMATCH'}),
    ('T3', 'go-action', 'own-true'): (human('T3', 'go-action', 'own-true'), {'DECLARATION_MISMATCH'}),
    ('T3', 'go-action', 'bypass-omit-history'): (human('T3', 'go-action', 'omit-history'), {'RECORD_NOT_WRITTEN'}),
    ('T3', 'go-action', 'bypass-param-hash'): (human('T3', 'go-action', 'param-hash'), {'FINGERPRINT_SHAPE'}),
    ('T3', 'go-action', 'bypass-distinguish'): (human('T3', 'go-action', 'distinguish'), set()),
    # The v6 gap: a Verifier that knows only action.Operation must not pass action.Update silently.
    ('T3', 'go-action', 'unknown-kind-correct'): (human('T3', 'go-action', 'correct'), {'UNVERIFIABLE'}, LEGACY_OP_ONLY),
    ('T3', 'go-action', 'unknown-kind-no-event'): (human('T3', 'go-action', 'no-event'), {'UNVERIFIABLE'}, LEGACY_OP_ONLY),
    # Platform: catalog + catalog kernel, extension references entries by name.
    ('P', 'platform', 'unchanged'): (lambda c: None, set()),
    ('P', 'platform', 'reference-reassign'): (platform('opdef.BulkReassign'), set()),
    ('P', 'platform', 'reference-lookup'): (platform('opdef.Name("bulk-reassign.v6")'), set()),
    ('P', 'platform', 'self-declared'): (platform('reassignOp.Name', 'Event: "purchase.reassigned"'), {'DECLARATION_LITERAL'}),
    ('P', 'platform', 'self-exempted'): (platform('reassignOp.Name', 'EventExemption: "not needed for reassign"'),
                                         {'DECLARATION_LITERAL', 'EVENT_EXEMPTION_NOT_IN_CATALOG'}),
    ('P', 'platform', 'catalog-exempted'): (platform('opdef.BulkReassign', after=exempt_in_catalog), {'PROTECTED_PATH_CHANGED', 'CATALOG_DRIFT'}),
    ('P', 'platform', 'unknown-name-literal'): (platform('"bulk-reassign.v7"'), {'OPERATION_NOT_IN_CATALOG'}),
    ('P', 'platform', 'unknown-name-conversion'): (platform('opdef.Name("bulk-transfer.v1")'), {'OPERATION_NOT_IN_CATALOG'}),
    ('P', 'platform', 'non-constant-name'): (platform('opdef.Name(os.Getenv("OPERATION"))', extra='\t"os"\n'), {'UNVERIFIABLE'}),
    ('P', 'platform', 'unknown-kernel-type'): (platform('opdef.BulkReassign'), {'UNVERIFIABLE'}, [], NEW_KERNEL_TYPE),
}
UNCOVERED = {'distinguish', 'bypass-distinguish', 'now-timestamp', 'unchanged'}


def commit(target):
    for args in (['init', '-q', '-b', 'candidate'], ['add', '-A'],
                 ['-c', 'user.name=POC', '-c', 'user.email=poc@example.invalid', 'commit', '-qm', 'export']):
        subprocess.run(['git', *args], cwd=target, check=True)
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=target, text=True).strip()


def export(task, cell, target, do_commit=True):
    c = next(x for x in CTL[task].contract()['cells'] if x['id'] == cell)
    for name, data in CTL[task].files({'base_sha': BASE}, c).items():
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        (target / name).write_bytes(data)
    return commit(target) if do_commit else None


def main():
    results = []
    with tempfile.TemporaryDirectory(prefix='op-controls-') as temp:
        for key, spec in CONTROLS.items():
            task, cell, name = key
            apply, expected = spec[0], spec[1]
            args = spec[2] if len(spec) > 2 else []
            kernel_extra = spec[3] if len(spec) > 3 else ''
            target = Path(temp) / '-'.join(key)
            target.mkdir()
            base = platform_export(target, kernel_extra) if task == 'P' else export(task, cell, target)
            apply(target)
            build = subprocess.run(['go', 'build', './...'], cwd=target, capture_output=True, text=True)
            p = subprocess.run([str(BINARY), 'operations', '--root', str(target), '--base', base, *args], capture_output=True, text=True)
            report = json.loads(p.stdout)
            codes = {f['code'] for f in report['findings']}
            exit_code = 0 if not expected else 3 if 'UNVERIFIABLE' in expected else 1
            ok = codes == expected and p.returncode == exit_code and build.returncode == 0
            kind = 'negative' if expected else 'uncovered' if name in UNCOVERED and name != 'unchanged' else 'positive'
            results.append({'task': task, 'cell': cell, 'control': name, 'kind': kind, 'expected': sorted(expected),
                            'codes': sorted(codes), 'verdict': report.get('verdict'), 'exit': p.returncode,
                            'builds': build.returncode == 0, 'passed': ok, 'args': args})
            assert ok, (results[-1], report, build.stderr[-1500:])
            catalog = report['catalog']
    count = lambda k: sum(r['kind'] == k for r in results)
    print(json.dumps({'ok': True, 'kind': 'operation-verifier-controls', 'model_calls': 0, 'catalog': catalog,
                      'controls': len(results), 'negative_detected': count('negative'), 'positive_clean': count('positive'),
                      'uncovered_clean': count('uncovered'),
                      'unverifiable_detected': sum('UNVERIFIABLE' in r['expected'] for r in results), 'results': results}, indent=1))


if __name__ == '__main__':
    main()
