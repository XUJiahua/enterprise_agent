#!/usr/bin/env python3
"""Post-hoc diagnostic for v4 action-cell scope violations. NOT a re-score.

Every scope-violating go-action candidate in the first v4 Kiro cohorts edited the
protected kernel only to add `var Reject = Transition{...}` next to action.Approve.
This moves that declaration into internal/candidate (mechanically, after checking the
kernel diff is exactly that), leaves the model's candidate code otherwise untouched,
and runs the frozen 108-case evaluation under a separate synthetic diagnostic plan.
Frozen first results stay scope-violation.

Usage: v4_relocate_diagnostic.py <lane-root> <model> <out-dir>   (needs DB env)
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'benchmark/phase4'))
import ctl  # noqa: E402

DECL = re.compile(r'^var Reject = Transition\{(?P<body>[^}]*)\}$')


def kernel_addition(candidate):
    diff = subprocess.check_output(['git', '-C', str(candidate), 'diff', '--', 'internal/action/'], text=True)
    changed = [l for l in diff.splitlines() if l[:1] in '+-' and not l.startswith(('+++', '---'))]
    removed = [l for l in changed if l.startswith('-')]
    added = [l[1:] for l in changed if l.startswith('+') and l[1:].strip() and not l[1:].lstrip().startswith('//')]
    if removed or len(added) != 1 or not DECL.match(added[0]):
        raise ValueError('kernel edit is not a single Reject declaration: %r' % changed)
    return DECL.match(added[0]).group('body')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('lane'); p.add_argument('model'); p.add_argument('out')
    a = p.parse_args()
    lane, out = Path(a.lane) / a.model, Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    seed, plan = out / 'diagnostic-seed.json', out / 'plan.json'
    ctl.fixtures(argparse.Namespace(output=seed))
    ctl.plan(argparse.Namespace(base='d8a5c576cc3c293ee2da884f5c618958df407b51', model='diagnostic-' + a.model, engine='kiro',
                                fixtures=seed, output=plan, repetitions=10, time_budget=600, reasoning_effort='medium',
                                seed=1, synthetic=True, allow_dirty_harness=True))
    experiment = ctl.load_plan(plan)
    rows = []
    for t in [x for x in experiment['trials'] if x['cell'] == 'go-action']:
        outcome = json.loads((lane / 'results' / t['id'] / 'outcome.json').read_text())['outcome']
        if outcome != 'scope-violation':
            continue
        source = lane / 'kits' / t['id'] / 'candidate'
        body = kernel_addition(source)
        target, manifest = out / 'candidates' / t['id'], out / 'exports' / (t['id'] + '.json')
        ctl.export_candidate(argparse.Namespace(plan=plan, trial=t['id'], candidate=target, manifest=manifest))
        shutil.rmtree(target / 'internal/candidate')
        shutil.copytree(source / 'internal/candidate', target / 'internal/candidate')
        for f in (target / 'internal/candidate').rglob('*.go'):
            f.write_text(re.sub(r'\baction\.Reject\b', 'relocatedReject', f.read_text()))
        (target / 'internal/candidate/relocated_reject.go').write_text(
            '// Diagnostic relocation of the model\'s kernel edit; not model-authored.\npackage candidate\n\n'
            'import "github.com/XUJiahua/enterprise_agent/internal/action"\n\n'
            'var relocatedReject = action.Transition{' + body + '}\n')
        snap = out / 'snapshots' / t['id']; snap.mkdir(parents=True)
        args = argparse.Namespace(plan=plan, trial=t['id'], candidate=target, manifest=manifest)
        ctl.freeze_candidate(args, experiment, t, {'status': 'completed', 'usage': None, 'diagnostic_relocation': True}, snap)
        proc = subprocess.run([sys.executable, str(ROOT / 'benchmark/phase4/ctl.py'), 'evaluate', '--plan', str(plan),
                               '--trial', t['id'], '--candidate', str(target), '--manifest', str(manifest),
                               '--receipt', str(snap / 'receipt.json'), '--fixtures', str(seed),
                               '--output', str(out / 'evaluations' / t['id'])], capture_output=True, text=True)
        r = json.loads((out / 'evaluations' / t['id'] / 'result.json').read_text())
        rows.append({'trial': t['id'], 'rc': proc.returncode, 'outcome': r['outcome'], 'explicit': r.get('explicit'),
                     'implicit': r.get('implicit'), 'conventions': r.get('conventions')})
        print(json.dumps(rows[-1]), flush=True)
    report = {'kind': 'v4-relocation-diagnostic', 'model': a.model, 'frozen_results_unchanged': True,
              'relocated': len(rows), 'passed_after_relocation': sum(r['outcome'] == 'passed' for r in rows), 'trials': rows}
    (out / 'diagnostic.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'trials'}))


if __name__ == '__main__':
    main()
