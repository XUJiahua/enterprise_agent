#!/usr/bin/env python3
"""Run the same production Next.js build and real browser suite on each backend."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--route',choices=['baseline','guarded','zenstack'],required=True);args=parser.parse_args()
os.chdir(ROOT)
out=ROOT/'reports/browser'/args.route;out.mkdir(parents=True,exist_ok=True)
(out/'results.json').unlink(missing_ok=True)
subprocess.run(['node','scripts/db.mjs','reset',args.route],check=True)
command=[str(ROOT/'bin/server'),'--mode',args.route] if args.route!='zenstack' else ['node','--import','tsx','src/server.ts']
env={**os.environ,'POC_ROUTE':args.route,'NEXT_TELEMETRY_DISABLED':'1','POC_BROWSER_REPORT_DIR':str(out),'POC_MUTATION':''}
with (out/'backend.log').open('w') as stream:
    server=subprocess.Popen(command,cwd=ROOT/'apps/zenstack' if args.route=='zenstack' else ROOT,env=env,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
    try:
        for _ in range(100):
            if server.poll() is not None:raise RuntimeError('backend exited before readiness')
            try:
                with urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('POC_HTTP_PORT','8080')+'/health',timeout=1) as response:
                    if response.status==200:break
            except OSError:time.sleep(.1)
        else:raise RuntimeError('backend readiness timeout')
        subprocess.run(['npm','run','test:e2e','--prefix','apps/web'],env=env,check=True)
        data=json.loads((out/'results.json').read_text())
        assert data['stats']['expected']==6 and not any(data['stats'].get(k,0) for k in ['unexpected','flaky','skipped']),data['stats']
    finally:
        try:os.killpg(server.pid,signal.SIGTERM)
        except ProcessLookupError:pass
        try:server.wait(timeout=5)
        except subprocess.TimeoutExpired:os.killpg(server.pid,signal.SIGKILL);server.wait()
