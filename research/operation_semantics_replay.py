#!/usr/bin/env python3
"""Retrospective (NOT out-of-sample) run of the catalog-bound Verifier on the 228 archived v6 candidates.

The v6 data was already seen (docs/v6-confirmatory-results.md), so this measures fit, not
generalisation; the out-of-sample evaluation is left to v7. No model calls, no database; frozen
scores are read and never rewritten. Candidates are restored exactly like the v6 out-of-sample
run (research/verifier_extension_rules.py restore/truth: bundle, or export + scope diff).

Two configurations:
  full      the Verifier as shipped (knows the v6 declaration types action.Operation and action.Update);
  ablation  --legacy Operation: the v5b-era knowledge that missed action.Update in v6. It shows the
            unknown type now blocks as UNVERIFIABLE instead of passing silently.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
EV = ROOT / 'docs/evidence/v6-confirmatory-2026-11-16'
PH = {'T1': 'benchmark/phase5b/ctl.py', 'T2': 'benchmark/phase6a/ctl.py', 'T3': 'benchmark/phase6b/ctl.py'}
spec = importlib.util.spec_from_file_location('vrf', ROOT / 'research/verifier_extension_rules.py')
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)
CONFIGS = {'full': [], 'ablation': ['--legacy', 'Operation']}
VERIFIER_SOURCES = ('internal/extcheck/extcheck.go', 'internal/extcheck/operations.go', 'cmd/appctl/main.go')
RULES = ('extension-scope', 'catalog', 'catalog-event', 'catalog-fingerprint', 'catalog-record', 'unverifiable')


def target(rule, r):
    """The frozen result a rule is judged against: True = failed, None = not scored."""
    if rule == 'extension-scope':
        return r['outcome'] == 'scope-violation'
    conv = r['conventions']
    goal = {'catalog-event': ['implicit-outbox'], 'catalog-fingerprint': ['implicit-request-hash'],
            'catalog-record': ['implicit-audit', 'implicit-history']}.get(rule)
    if goal is None:  # catalog / unverifiable: judged against any frozen failure
        return None if r['outcome'] in ('candidate-error',) and not conv else not r['passed']
    values = [conv[g] for g in goal if g in conv]
    return None if not values else not all(values)


def confusion(rows, rule):
    c = {'TP': 0, 'FP': 0, 'FN': 0, 'TN': 0, 'excluded': 0}
    for r in rows:
        a = target(rule, r)
        if a is None:
            c['excluded'] += 1
            continue
        flagged = rule in r['rules']
        c[('T' if flagged == a else 'F') + ('P' if flagged else 'N')] += 1
    return c


def summarize(rows):
    silent = lambda r: bool(r['explicit_passed'] and not r['passed'])
    semantic = lambda r: any(x != 'unverifiable' for x in r['rules'])
    blocked = lambda r: bool(r['rules'])
    overall = {'TP': 0, 'FP': 0, 'FN': 0, 'TN': 0}
    for r in rows:
        overall[('T' if semantic(r) == (not r['passed']) else 'F') + ('P' if semantic(r) else 'N')] += 1
    groups = {}
    for task in PH:
        for cell in ('go-manual', 'go-action'):
            sub = [r for r in rows if r['task'] == task and r['cell'] == cell]
            groups[task + '/' + cell] = {'trials': len(sub), 'failed': sum(not r['passed'] for r in sub),
                'silent': sum(map(silent, sub)), 'silent_caught': sum(semantic(r) for r in sub if silent(r)),
                'flagged_passed': sum(semantic(r) for r in sub if r['passed']),
                'unverifiable_passed': sum('unverifiable' in r['rules'] for r in sub if r['passed']),
                'unverifiable_failed': sum('unverifiable' in r['rules'] for r in sub if not r['passed'])}
    return {'trials': len(rows),
            'semantic_findings_vs_any_frozen_failure': overall,
            'blocked_vs_any_frozen_failure': {
                'TP': sum(blocked(r) and not r['passed'] for r in rows), 'FP': sum(blocked(r) and r['passed'] for r in rows),
                'FN': sum(not blocked(r) and not r['passed'] for r in rows), 'TN': sum(not blocked(r) and r['passed'] for r in rows)},
            'silent_failures': sum(map(silent, rows)), 'silent_caught': sum(semantic(r) for r in rows if silent(r)),
            'unverifiable': {'passed': sum('unverifiable' in r['rules'] for r in rows if r['passed']),
                             'failed': sum('unverifiable' in r['rules'] for r in rows if not r['passed'])},
            'rules': {rule: confusion(rows, rule) for rule in RULES}, 'groups': groups,
            'false_positives': [{k: r[k] for k in ('task', 'model', 'trial', 'rules')} for r in rows if r['passed'] and semantic(r)],
            'codes': {code: sum(code in {f['code'] for f in r['findings']} for r in rows)
                      for code in sorted({f['code'] for r in rows for f in r['findings']})}}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', default='docs/evidence/operation-semantics-2026-11-16')
    p.add_argument('--appctl', default=str(ROOT / 'bin/appctl'))
    a = p.parse_args()
    rows = {name: [] for name in CONFIGS}
    catalog = None
    with tempfile.TemporaryDirectory(prefix='opsem-') as temp:
        temp = Path(temp)
        for task, ctl_path in PH.items():
            ctl = v.load_ctl(ctl_path, 'ctl_' + task)
            for model in sorted(d for d in (EV / task).iterdir() if d.is_dir()):
                plan = json.loads((model / 'plan.json').read_text())
                for trial in sorted(model.glob('go-*--*')):
                    outcome = json.loads((trial / 'outcome.json').read_text())['outcome'] if (trial / 'outcome.json').exists() else None
                    if not (trial / 'evaluation.json').exists() and outcome != 'scope-violation':
                        continue  # unmeasured / over budget: no candidate (same selection as the v6 run)
                    checkout, base, note = v.restore(trial, plan, ctl, temp / task)
                    t = v.truth(trial)
                    for name, args in CONFIGS.items():
                        res = subprocess.run([a.appctl, 'operations', '--root', str(checkout), '--base', base, *args],
                                             capture_output=True, text=True, timeout=120)
                        rep = json.loads(res.stdout)
                        if res.returncode not in (0, 1, 3) or 'error' in rep:
                            raise RuntimeError((trial, res.stdout, res.stderr))
                        catalog = rep['catalog']
                        rows[name].append({'task': task, 'model': model.name, 'trial': trial.name, 'cell': trial.name.split('--')[0],
                                           'reconstruction': note, **t, 'verdict': rep['verdict'], 'exit': res.returncode,
                                           'rules': sorted({f['rule'] for f in rep['findings']}), 'findings': rep['findings']})
    source = hashlib.sha256(b''.join((ROOT / p).read_bytes() for p in VERIFIER_SOURCES)).hexdigest()
    summary = {'kind': 'retrospective-catalog-verifier-replay', 'out_of_sample': False,
               'note': 'v6 data was seen before this Verifier was written; this is fit on seen data, not an out-of-sample estimate',
               'catalog': catalog, 'verifier_sources': list(VERIFIER_SOURCES), 'verifier_sources_sha256': source, 'new_model_generations': 0, 'frozen_scores_modified': False,
               'configs': {name: summarize(r) for name, r in rows.items()}}
    out = ROOT / a.out if not Path(a.out).is_absolute() else Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'trials.json').write_text(json.dumps(rows, indent=1, ensure_ascii=False) + '\n')
    (out / 'summary.json').write_text(json.dumps(summary, indent=1) + '\n')
    for name, s in summary['configs'].items():
        print(name, json.dumps({k: s[k] for k in ('trials', 'semantic_findings_vs_any_frozen_failure', 'blocked_vs_any_frozen_failure',
                                                    'silent_failures', 'silent_caught', 'unverifiable', 'codes')}))
        for rule, c in s['rules'].items():
            print('  ', rule, json.dumps(c))
        for g, c in s['groups'].items():
            print('  ', g, json.dumps(c))


if __name__ == '__main__':
    main()
