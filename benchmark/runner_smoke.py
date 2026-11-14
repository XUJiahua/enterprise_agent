#!/usr/bin/env python3
"""End-to-end driver controls with an explicitly synthetic CLI, no model calls."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
OUT = ROOT / 'reports/runner-smoke'
OUT.mkdir(parents=True, exist_ok=False)
base = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
plan_path = OUT / 'plan.json'
subprocess.run(['python3', 'benchmark/benchctl.py', 'plan', '--base', base,
                '--model', 'synthetic-driver-control', '--task', 'tenant-summary',
                '--repetitions', '1', '--synthetic', '--allow-dirty-harness',
                '--output', str(plan_path)], check=True, capture_output=True)
trial_id = 'tenant-summary--baseline--001'


def execute(*args, expected=0):
    process = subprocess.run(['python3', 'benchmark/codex_runner.py', *map(str, args)],
                             capture_output=True, text=True)
    assert process.returncode == expected, (process.stdout, process.stderr)
    return process


with tempfile.TemporaryDirectory(prefix='poc-synthetic-driver-') as temp:
    driver = Path(temp) / 'fake-codex'
    driver.write_text('''#!/usr/bin/env python3
import json,sys
from pathlib import Path
if '--version' in sys.argv:
 print('codex-cli synthetic-test-only');sys.exit(0)
prompt=sys.stdin.read()
cwd=Path(sys.argv[sys.argv.index('-C')+1])
print(json.dumps({'type':'turn.started'}))
if sys.argv[sys.argv.index('--sandbox')+1]=='read-only':
 text='READY'
else:
 source=cwd/'internal/app/server.go'
 needle='ctx := r.Context()'
 patch=''' + repr('''ctx := r.Context()
 if r.Method == "GET" && r.URL.Path == "/purchases/summary" {
  var count, total int
  err := tx.QueryRow(ctx, `SELECT count(*), COALESCE(sum(amount),0) FROM "Purchase" WHERE "tenantId"=$1`, u.Tenant).Scan(&count, &total)
  return map[string]int{"count":count,"totalAmount":total}, 200, err
 }''') + '''
 assert needle in source.read_text()
 source.write_text(source.read_text().replace(needle,patch,1))
 text='Synthetic fixture implementation only'
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':text}}))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':100,'cached_input_tokens':25,'output_tokens':50}}))
''')
    driver.chmod(0o755)
    output = OUT / 'positive'
    execute('run', '--plan', plan_path, '--trial', trial_id,
            '--synthetic-test-driver', driver, '--output', output)
    batch = json.loads((output / 'batch.json').read_text())
    assert batch['kind'] == 'synthetic-smoke' and batch['observed_trials'] == 1
    assert batch['passed_trials'] == 1 and batch['unmeasured_trials'] == 2
    assert batch['first_pass_rate'] is None
    row = batch['trials'][0]
    assert row['usage'] == {'input_tokens': 100, 'cached_input_tokens': 25, 'output_tokens': 50}
    evidence = output / 'generation' / trial_id
    assert (evidence / 'candidate.patch').stat().st_size > 0
    assert (evidence / 'candidate.bundle').stat().st_size > 0
    execute('run', '--plan', plan_path, '--trial', trial_id,
            '--synthetic-test-driver', driver, '--output', output, expected=1)
    candidate = output / 'candidates' / trial_id
    subprocess.run(['git', 'worktree', 'remove', '--force', str(candidate)], check=True, capture_output=True)
    # Runtime initialization fails before turn.started: no candidate/evaluation/rate.
    driver.write_text('''#!/usr/bin/env python3
import sys
if '--version' in sys.argv:
 print('codex-cli synthetic-test-only');sys.exit(0)
print('Read-only file system (os error 30)',file=sys.stderr)
sys.exit(1)
''')
    blocked = OUT / 'blocked'
    execute('run', '--plan', plan_path, '--synthetic-test-driver', driver,
            '--output', blocked, expected=3)
    batch = json.loads((blocked / 'batch.json').read_text())
    assert batch['status'] == 'blocked' and batch['blocked_stage'] == 'preflight'
    assert batch['observed_trials'] == 0 and batch['unmeasured_trials'] == 3
    assert batch['first_pass_rate'] is None and not (blocked / 'attempts').exists()
    # A fake driver cannot be passed off as a model run.
    model_plan = json.loads(plan_path.read_text())
    model_plan['kind'] = 'model-trial'
    (OUT / 'model-plan.json').write_text(json.dumps(model_plan))
    execute('run', '--plan', OUT / 'model-plan.json', '--synthetic-test-driver', driver,
            '--output', OUT / 'forbidden-mix', expected=1)
print(json.dumps({'ok': True, 'kind': 'synthetic-smoke', 'evaluations': 1,
                  'controls': ['driver-to-independent-oracle', 'token-provenance', 'patch-and-bundle',
                               'append-only-output', 'preflight-blocker-unmeasured', 'synthetic-model-separation']}))
