#!/usr/bin/env python3
"""Run a controlled fault-injection experiment, not an AI productivity benchmark."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
os.chdir(ROOT)
spec=importlib.util.spec_from_file_location('acceptance',ROOT/'tests/acceptance.py')
oracle=importlib.util.module_from_spec(spec);spec.loader.exec_module(oracle)

def execute(route,mutation,out):
    subprocess.run(['node','scripts/db.mjs','reset',route],check=True)
    command=([str(ROOT/'bin/server'),'--mode',route] if route!='zenstack' else ['node','--import','tsx','src/server.ts'])
    env={**os.environ,'POC_MUTATION':mutation,'POC_ALLOW_MUTATIONS':'1'}
    log=out/f'{mutation or "control"}.log'
    with log.open('w') as stream:
        process=subprocess.Popen(command,env=env,cwd=ROOT/'apps/zenstack' if route=='zenstack' else ROOT,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            for _ in range(100):
                if process.poll() is not None: raise RuntimeError(f'server exited: {log.read_text()}')
                try:
                    with urllib.request.urlopen(oracle.BASE+'/health',timeout=1) as response:
                        if response.status==200: break
                except OSError: time.sleep(.1)
            else: raise RuntimeError('server readiness timeout')
            results=oracle.run(oracle.FAULTS[mutation] if mutation else None)
            # A 500 is an implementation failure, not an enforcement success.
            return {'mutation':mutation or 'control','cases':results,'passed':all(r['passed'] for r in results)}
        finally:
            import signal
            try: os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError: pass
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()

parser=argparse.ArgumentParser();parser.add_argument('--route',choices=['baseline','guarded','zenstack'],required=True);parser.add_argument('--out',default='reports');args=parser.parse_args()
out=Path(args.out).resolve()/args.route;out.mkdir(parents=True,exist_ok=True)
for stale in ['results.json','failure.json','db-probe.json','startup-probe.json']:
    (out/stale).unlink(missing_ok=True)
experiments=[]
try:
    for mutation in ['',*list(oracle.FAULTS)[:5],*(['disable-policy'] if args.route=='zenstack' else [])]:
        result=execute(args.route,mutation,out);experiments.append(result);print(json.dumps(result),flush=True)
    if args.route=='guarded':
        startup=[]
        subprocess.run(['node','scripts/db.mjs','reset','baseline'],check=True)
        for name,url,expected_error in [
            ('unprotected database',os.environ['DATABASE_URL'],'guarded runtime requires configured RLS'),
            ('privileged database role',os.environ['ADMIN_DATABASE_URL'],'runtime must use a non-owner')]:
            p=subprocess.run([str(ROOT/'bin/server'),'--mode','guarded'],env={**os.environ,'DATABASE_URL':url},capture_output=True,text=True,timeout=5)
            assert p.returncode!=0 and expected_error in p.stderr,(name,p.stdout,p.stderr)
            startup.append({'probe':name,'passed':True})
        (out/'startup-probe.json').write_text(json.dumps(startup,indent=2)+'\n')
        subprocess.run(['node','scripts/db.mjs','reset','guarded'],check=True)
        probe=subprocess.run(['node','scripts/db.mjs','probe'],check=True,capture_output=True,text=True)
        (out/'db-probe.json').write_text(probe.stdout)
        print(probe.stdout,flush=True)
    report={'route':args.route,'experiment':'controlled-fault-injection-v1','results':experiments}
    (out/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    assert experiments[0]['passed'],'control must pass every acceptance case'
    for result in experiments[1:]:
        if any(case.get('kind')=='harness_error' for case in result['cases']):raise AssertionError('harness error')
        # All ordinary injected faults should escape the baseline, and be blocked by the two policy paths.
        expected=args.route!='baseline' and result['mutation']!='disable-policy'
        assert result['passed']==expected, f'unexpected fault outcome: {result}'
except Exception as error:
    (out/'failure.json').write_text(json.dumps({'error':repr(error),'results':experiments},indent=2)+'\n')
    raise
