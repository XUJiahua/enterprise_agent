#!/usr/bin/env python3
"""Post-hoc interception study: run `appctl extensions` on every archived real v4/v4b/v5/v5b candidate.

No model calls, no database. Frozen scores are read, never rewritten. Candidates are restored from
candidate.bundle; scope-violation trials (no bundle) are rebuilt from the plan's export (checked
against the plan's per-file hashes) plus scope-violation.diff and archived untracked sources.
v4b evidence lives on the poc/v4b-extension-point commit below and is read via `git archive`.
"""
import argparse
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
V4B_COMMIT = 'd2f9a25fef970cf39fc125d53d4d981cb29deba4'
PHASES = {  # phase -> (evidence dir, ctl module)
    'v4': ('docs/evidence/phase4-kiro-cohort-001-models', 'benchmark/phase4/ctl.py'),
    'v4b': ('docs/evidence/phase4b-kiro-cohort-001-models', 'benchmark/phase4/ctl.py'),
    'v5': ('docs/evidence/phase5-kiro-cohort-001-models', 'benchmark/phase5/ctl.py'),
    'v5b': ('docs/evidence/phase5b-kiro-cohort-001-models', 'benchmark/phase5b/ctl.py'),
}
RULES = ('extension-scope', 'operation-declaration', 'event-consumer', 'manual-event-consumer', 'manual-fingerprint')
# Rule -> frozen convention it targets (None: the frozen scope gate).
TARGET = {'extension-scope': None, 'operation-declaration': 'implicit-outbox', 'event-consumer': 'implicit-outbox',
          'manual-event-consumer': 'implicit-outbox', 'manual-fingerprint': 'implicit-request-hash'}


def run(*args, cwd=None, check=True):
    return subprocess.run(list(map(str, args)), cwd=cwd, capture_output=True, text=True, check=check, timeout=120)


def load_ctl(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, 'ctl', mod)  # phase5b wraps phase5's ctl


def commit_all(path, message):
    run('git', 'init', '-q', '-b', 'candidate', cwd=path)
    run('git', 'add', '-A', cwd=path)
    run('git', '-c', 'user.name=POC', '-c', 'user.email=poc@example.invalid', 'commit', '-qm', message, cwd=path)
    return run('git', 'rev-parse', 'HEAD', cwd=path).stdout.strip()


def restore(trial, plan, ctl, work):
    """Return (checkout, base commit, reconstruction note)."""
    manifest = json.loads((trial / 'manifest.json').read_text())
    cell = next(c for c in ctl.contract()['cells'] if c['id'] == trial.name.split('--')[0])
    expected = plan['exports'][cell['id']]
    target = work / trial.parent.name / trial.name
    if (trial / 'candidate.bundle').exists():
        run('git', 'clone', '-q', trial / 'candidate.bundle', target)
        base = manifest['initial_sha']
        run('git', 'merge-base', '--is-ancestor', base, 'HEAD', cwd=target)
        note = 'bundle'
    else:
        target.mkdir(parents=True)
        for name, data in ctl.files(plan, cell).items():
            (target / name).parent.mkdir(parents=True, exist_ok=True)
            (target / name).write_bytes(data)
        base = commit_all(target, 'rebuilt export')
        note = 'export+diff'
    for name, digest in expected.items():  # the base tree must be exactly the frozen export
        if ctl.sha(subprocess.check_output(['git', '-C', str(target), 'show', base + ':' + name])) != digest:
            raise ValueError(f'{trial}: base drift {name}')
    if note == 'export+diff':
        run('git', 'apply', '--whitespace=nowarn', trial / 'scope-violation.diff', cwd=target)
        extra = trial / 'scope-untracked'
        for src in extra.rglob('*.go.txt') if extra.exists() else []:
            dst = target / str(src.relative_to(extra)).removesuffix('.txt')
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dst)
        status = (trial / 'scope-status.txt').read_text().splitlines()
        untracked = [l[3:] for l in status if l.startswith('?? ')]
        missing = [p for p in untracked if not (target / p).exists()]
        if missing:
            note += ' (untracked not archived: ' + ', '.join(missing) + ')'
    return target, base, note


def truth(trial):
    path = trial / 'evaluation.json'
    if path.exists():
        e = json.loads(path.read_text())
        return {'outcome': e['outcome'], 'passed': e['passed'], 'explicit_passed': e.get('explicit_passed'),
                'conventions': e.get('conventions') or {}}
    return {'outcome': json.loads((trial / 'outcome.json').read_text())['outcome'], 'passed': False,
            'explicit_passed': None, 'conventions': {}}


def failed_target(rule, t):
    """True/False for the rule's targeted frozen result; None when it was not scored."""
    goal = TARGET[rule]
    if goal is None:
        return t['outcome'] == 'scope-violation'
    value = t['conventions'].get(goal)
    return None if value is None else not value


def confusion(rows, rule, actual):
    c = {'TP': 0, 'FP': 0, 'FN': 0, 'TN': 0, 'excluded': 0}
    for r in rows:
        a = actual(r)
        if a is None:
            c['excluded'] += 1
            continue
        flagged = rule in r['rules']
        c[('T' if flagged == a else 'F') + ('P' if flagged else 'N')] += 1
    return c


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', default='docs/evidence/verifier-extension-rules-2026-11-15')
    p.add_argument('--appctl', default=str(ROOT / 'bin/appctl'))
    a = p.parse_args()
    out = Path(a.out)
    rows = []
    with tempfile.TemporaryDirectory(prefix='vrf-') as temp:
        temp = Path(temp)
        archive = subprocess.check_output(['git', '-C', str(ROOT), 'archive', V4B_COMMIT, PHASES['v4b'][0]])
        tarfile.open(fileobj=io.BytesIO(archive)).extractall(temp / 'v4b', filter='data')
        for phase, (evidence, ctl_path) in PHASES.items():
            ctl = load_ctl(ctl_path, 'ctl_' + phase)
            root = (temp / 'v4b' if phase == 'v4b' else ROOT) / evidence
            for model in sorted(d for d in root.iterdir() if d.is_dir()):
                plan = json.loads((model / 'plan.json').read_text())
                for trial in sorted(model.glob('go-*--*')):
                    checkout, base, note = restore(trial, plan, ctl, temp / phase)
                    res = run(a.appctl, 'extensions', '--root', checkout, '--base', base, check=False)
                    report = json.loads(res.stdout)
                    if res.returncode not in (0, 1) or 'error' in report:
                        raise RuntimeError((trial, res.stdout, res.stderr))
                    t = truth(trial)
                    rows.append({'phase': phase, 'model': model.name, 'trial': trial.name,
                                 'cell': trial.name.split('--')[0], 'reconstruction': note, **t,
                                 'rules': sorted({f['rule'] for f in report['findings']}),
                                 'findings': report['findings']})
    silent = lambda r: bool(r['explicit_passed'] and not r['passed'])  # failure the explicit contract missed
    union = lambda r: bool(r['rules'])
    cells = sorted({(r['phase'], r['cell']) for r in rows}, key=lambda k: (list(PHASES).index(k[0]), k[1]))
    by_rule = {}
    for rule in RULES:
        by_rule[rule] = {'vs_targeted_frozen_result': confusion(rows, rule, lambda r, rule=rule: failed_target(rule, r)),
                         'vs_any_frozen_failure': confusion(rows, rule, lambda r: not r['passed']),
                         'silent_failures_caught': sum(rule in r['rules'] for r in rows if silent(r)),
                         'by_cell': {}}
        for ph, cell in cells:
            sub = [r for r in rows if r['phase'] == ph and r['cell'] == cell]
            by_rule[rule]['by_cell'][ph + '/' + cell] = confusion(sub, rule, lambda r, rule=rule: failed_target(rule, r))
    groups = {}
    for ph, cell in cells:
        sub = [r for r in rows if r['phase'] == ph and r['cell'] == cell]
        groups[ph + '/' + cell] = {'trials': len(sub), 'failed': sum(not r['passed'] for r in sub),
            'scope_violation': sum(r['outcome'] == 'scope-violation' for r in sub),
            'silent': sum(map(silent, sub)), 'silent_caught': sum(union(r) for r in sub if silent(r)),
            'flagged_failed': sum(union(r) for r in sub if not r['passed']),
            'flagged_passed': sum(union(r) for r in sub if r['passed'])}
    c = {'TP': 0, 'FP': 0, 'FN': 0, 'TN': 0}
    for r in rows:
        c[('T' if union(r) == (not r['passed']) else 'F') + ('P' if union(r) else 'N')] += 1
    summary = {'kind': 'post-hoc-verifier-interception', 'new_model_generations': 0, 'frozen_scores_modified': False,
               'trials': len(rows), 'phases': {ph: sum(r['phase'] == ph for r in rows) for ph in PHASES},
               'v4b_evidence_commit': V4B_COMMIT,
               'reconstructions': {n: sum(r['reconstruction'] == n for r in rows) for n in sorted({r['reconstruction'] for r in rows})},
               'silent_failures': sum(map(silent, rows)), 'silent_failures_caught_by_any_rule': sum(union(r) for r in rows if silent(r)),
               'uncaught_silent_failures': [{k: r[k] for k in ('phase', 'model', 'trial', 'conventions')} for r in rows if silent(r) and not union(r)],
               'any_rule': {'vs_any_frozen_failure': c}, 'rules': by_rule, 'groups': groups}
    out.mkdir(parents=True, exist_ok=True)
    (out / 'trials.json').write_text(json.dumps(rows, indent=1, ensure_ascii=False) + '\n')
    (out / 'summary.json').write_text(json.dumps(summary, indent=1) + '\n')
    print(json.dumps({k: summary[k] for k in ('trials', 'phases', 'reconstructions', 'silent_failures',
                                              'silent_failures_caught_by_any_rule', 'any_rule')}))
    for rule, v in by_rule.items():
        print(rule, json.dumps(v['vs_targeted_frozen_result']), 'silent caught', v['silent_failures_caught'])
    for g, v in groups.items():
        print(g, json.dumps(v))


if __name__ == '__main__':
    main()
