#!/usr/bin/env python3
"""Fail closed when artifacts are missing, incomplete, or disagree with expectations."""
import json
from pathlib import Path
import sys
root=Path(sys.argv[1] if len(sys.argv)>1 else 'reports')
rows=[]
for route in ['baseline','zenstack','guarded']:
    data=json.loads((root/route/'results.json').read_text())
    results=data['results'];control=results[0]
    assert control['mutation']=='control' and control['passed'] and len(control['cases'])==16 and all(c['passed'] for c in control['cases'])
    ordinary=[r for r in results if r['mutation'] not in ('control','disable-policy')]
    assert len(ordinary)==5
    for result in results:
        assert all(c.get('kind')!='harness_error' for c in result['cases'])
    blocked=sum(r['passed'] for r in ordinary)
    assert blocked==(0 if route=='baseline' else 5)
    if route=='zenstack':assert any(r['mutation']=='disable-policy' and not r['passed'] for r in results)
    rows.append((route,f"{sum(c['passed'] for c in control['cases'])}/16",f'{blocked}/5'))
startup=json.loads((root/'guarded/startup-probe.json').read_text());assert len(startup)==2 and all(p['passed'] for p in startup)
probe=json.loads((root/'guarded/db-probe.json').read_text());assert probe['ok'] and probe['checks']==6
for route in ['baseline','zenstack','guarded']:
    browser=json.loads((root/'browser'/route/'results.json').read_text())
    assert browser['stats']['expected']==6 and not any(browser['stats'].get(k,0) for k in ['unexpected','flaky','skipped'])
smoke=json.loads((root/'benchmark-smoke.json').read_text());assert smoke['ok'] and smoke['kind']=='synthetic-smoke' and smoke['evaluations']==4
benchmark=json.loads((root/'benchmark/summary.json').read_text());assert benchmark['kind']=='synthetic-smoke' and len(benchmark['rows'])==3
assert all(row['missing']==0 and row['evaluated']==1 and row['first_pass']==0 for row in benchmark['rows'])
verifier=json.loads((root/'verifier.json').read_text());assert verifier['ok'] and len(verifier['results'])==7
text='# Controlled POC results\n\n| Path | Healthy acceptance | Faults blocked |\n|---|---:|---:|\n'
for route,acceptance,blocked in rows:text+=f'| {route} | {acceptance} | {blocked} |\n'
text+='\nVerifier adversarial probes: 7/7. Direct SQL probes: 6/6. Startup configuration probes: 2/2. Shared browser acceptance: 18/18. Synthetic evaluator controls: 4 committed-candidate evaluations (not AI measurements).\n\nThese are hand-authored fault injections, not measured AI defect rates or first-pass productivity. A guarded field over-fetch fails closed with 403; ZenStack nullifies the denied field. Disabling the ZenStack policy client is an expected escaping boundary control.\n'
(root/'comparison.md').write_text(text)
print(text)
