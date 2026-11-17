#!/usr/bin/env python3
"""Per-model facts for v4/v5 implicit-convention cohorts (conventions read from results) from archived evidence (no re-scoring).

Usage: analyze_v4_cohort.py <evidence-root>   (one subdirectory per model)
"""
import json
import math
import statistics
import sys
from pathlib import Path

UNMEASURED = {'runtime-blocked', 'provider-error', 'protocol-error', 'infrastructure-blocked'}
CONVENTIONS = ('implicit-audit', 'implicit-operation-scope', 'implicit-request-hash', 'implicit-uniform-409')


def sign_p(a, b):
    n = a + b
    return 1.0 if n == 0 else min(1.0, 2 * sum(math.comb(n, i) for i in range(min(a, b) + 1)) / 2 ** n)


def trial(d):
    out = json.loads((d / 'outcome.json').read_text())
    ev = json.loads((d / 'evaluation.json').read_text()) if (d / 'evaluation.json').exists() else None
    outcome = ev['outcome'] if ev else out['outcome']
    scored = ev is not None and outcome in ('passed', 'acceptance-failed')
    return {'outcome': outcome, 'passed': outcome == 'passed',
            'explicit_passed': bool(scored and ev['explicit_passed']),
            'conventions': ev['conventions'] if scored else None,
            'explicit': ev['explicit'] if scored else None, 'implicit': ev['implicit'] if scored else None,
            'seconds': out['generation']['generation_seconds'],
            'credits': (out['generation'].get('usage') or {}).get('credits')}


def model(root):
    plan = json.loads((root / 'plan.json').read_text())
    t = {x['id']: dict(trial(root / x['id']), cell=x['cell'], instance=x['instance']) for x in plan['trials']}
    cells = {}
    for c in ('go-manual', 'go-action'):
        r = [x for x in t.values() if x['cell'] == c]
        scored = [x for x in r if x['conventions'] is not None]
        names = sorted({k for x in t.values() if x['conventions'] for k in x['conventions']}) or CONVENTIONS
        clean = [x for x in scored if x['explicit_passed']]
        cells[c] = {
            'planned': len(r), 'observed': sum(x['outcome'] not in UNMEASURED for x in r),
            'passed_all': sum(x['passed'] for x in r), 'passed_explicit': sum(x['explicit_passed'] for x in r),
            'outcomes': {o: sum(x['outcome'] == o for x in r) for o in sorted({x['outcome'] for x in r})},
            'conventions_followed_of_scored': {k: [sum(x['conventions'][k] for x in scored), len(scored)] for k in names},
            'conventions_followed_given_explicit_pass': {k: [sum(x['conventions'][k] for x in clean), len(clean)] for k in names},
            'median_seconds': statistics.median(x['seconds'] for x in r),
            'median_credits': statistics.median(x['credits'] for x in r if x['credits'] is not None),
            'total_credits': round(sum(x['credits'] or 0 for x in r), 3)}
    ao = mo = 0
    blocks = []
    for i in range(1, max(x['instance'] for x in t.values()) + 1):
        m, a = t[f'go-manual--{i:03d}'], t[f'go-action--{i:03d}']
        if m['outcome'] in UNMEASURED or a['outcome'] in UNMEASURED:
            blocks.append('unmeasured')
            continue
        ao += a['passed'] and not m['passed']
        mo += m['passed'] and not a['passed']
        blocks.append(('P' if m['passed'] else 'F') + ('P' if a['passed'] else 'F'))
    return {'model': plan['model'], 'harness_sha': plan['harness_sha'], 'cells': cells,
            'blocks_manual_action': blocks, 'action_only_pass': ao, 'manual_only_pass': mo,
            'sign_test_p_exploratory': round(sign_p(ao, mo), 4)}


if __name__ == '__main__':
    root = Path(sys.argv[1])
    print(json.dumps([model(d) for d in sorted(root.iterdir()) if (d / 'plan.json').exists()], indent=2))
