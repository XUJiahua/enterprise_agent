#!/usr/bin/env python3
"""Per-trial facts for a v3 Kiro cohort from archived evidence (no re-scoring)."""
import json
import statistics
import sys
from pathlib import Path


def tool_calls(events):
    calls = {}
    for line in events.read_text().splitlines():
        e = json.loads(line)
        if e['type'] != 'sessionUpdate':
            continue
        u = e['data']['update']
        if u.get('sessionUpdate') in ('tool_call', 'tool_call_update'):
            c = calls.setdefault(u['toolCallId'], {'text': ''})
            c['kind'] = u.get('kind', c.get('kind'))
            c['text'] += json.dumps(u.get('rawInput', '')) + json.dumps(u.get('content', ''))
    return list(calls.values())


def main(root):
    root = Path(root)
    plan = json.loads((root / 'plan.json').read_text())
    rows = []
    for t in plan['trials']:
        d = root / t['id']
        ev = json.loads((d / 'evaluation.json').read_text()) if (d / 'evaluation.json').exists() else None
        out = json.loads((d / 'outcome.json').read_text())
        patch = (d / 'candidate.patch').read_text() if (d / 'candidate.patch').exists() else ''
        calls = tool_calls(d / 'generation/events.jsonl')
        rows.append({
            'trial': t['id'], 'cell': t['cell'],
            'outcome': ev['outcome'] if ev else out['outcome'],
            'cases_passed': sum(c['passed'] for c in ev['cases']) if ev else None,
            'failed_cases': [c['group'] + ':' + c['case'] for c in ev['cases'] if not c['passed']] if ev else None,
            'generation_seconds': out['generation']['generation_seconds'],
            'credits': (out['generation'].get('usage') or {}).get('credits'),
            'model': out['generation'].get('reported_model'),
            'files': sorted({l[6:] for l in patch.splitlines() if l.startswith('+++ b/')}),
            'added_lines': sum(l.startswith('+') and not l.startswith('+++') for l in patch.splitlines()),
            'tool_calls': len(calls),
            'wrote_tests': any(f.endswith('_test.go') for f in {l[6:] for l in patch.splitlines() if l.startswith('+++ b/')}),
            'ran_go_test': any('go test' in c['text'] for c in calls),
            'sandbox_denials': sum('Operation not permitted' in c['text'] for c in calls),
        })
    cells = {}
    for c in ('go-manual', 'go-action'):
        r = [x for x in rows if x['cell'] == c]
        cells[c] = {'planned': len(r), 'passed': sum(x['outcome'] == 'passed' for x in r),
                    'outcomes': {o: sum(x['outcome'] == o for x in r) for o in sorted({x['outcome'] for x in r})},
                    'median_seconds': statistics.median(x['generation_seconds'] for x in r),
                    'median_credits': statistics.median(x['credits'] for x in r if x['credits'] is not None),
                    'median_added_lines': statistics.median(x['added_lines'] for x in r),
                    'wrote_tests': sum(x['wrote_tests'] for x in r), 'ran_go_test': sum(x['ran_go_test'] for x in r),
                    'sandbox_denials': sum(x['sandbox_denials'] for x in r)}
    print(json.dumps({'cells': cells, 'trials': rows}, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main(sys.argv[1])
