#!/usr/bin/env python3
"""Real PostgreSQL mechanism controls, not a model trial or new cohort runner."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time
import urllib.request

from oracle import Database, run

ROOT=Path(__file__).resolve().parents[2]
MODES=['reference','omit-audit','commit-before-audit','skip-replay','skip-completeness','reject-all']
EXPECTED={'functional':6,'authorization':5,'atomicity':4,'input':24,'idempotency':6,'concurrency':3,'failure-rollback':2,'original-regression':16}


def stop(server):
    if server.poll() is None:os.killpg(server.pid,signal.SIGTERM)
    try:server.wait(timeout=5)
    except subprocess.TimeoutExpired:os.killpg(server.pid,signal.SIGKILL);server.wait()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',default='reports/action-controls')
    args=parser.parse_args();os.chdir(ROOT)
    contract=json.loads((ROOT/'benchmark/phase3/contract.json').read_text())
    assert contract['scoring_counts']==EXPECTED and contract['candidate_controls']==MODES and contract['model_cohort_registered'] is False
    out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=False)
    port=int(os.environ.get('POC_HTTP_PORT','8080'))
    with socket.socket() as probe:assert probe.connect_ex(('127.0.0.1',port))!=0,'HTTP port occupied'
    spec=importlib.util.spec_from_file_location('action_original_acceptance',ROOT/'tests/acceptance.py')
    original=importlib.util.module_from_spec(spec);spec.loader.exec_module(original)
    db=Database();reports=[];startup=[];direct=None
    try:
        with tempfile.TemporaryDirectory(prefix='action-controls-') as temporary:
            binary=Path(temporary)/'action-server'
            subprocess.run(['go','build','-buildvcs=false','-o',str(binary),'./cmd/action-server'],check=True,timeout=120)
            env={k:os.environ[k] for k in ['PATH','HOME','LANG','TMPDIR','DATABASE_URL','POC_HTTP_PORT'] if k in os.environ}
            for mode in MODES:
                db.call('init');directory=out/mode;directory.mkdir()
                command=[str(binary)]
                runtime=dict(env)
                if mode!='reference':command+=['--mutation',mode];runtime['POC_ALLOW_MUTATIONS']='1'
                with (directory/'server.log').open('x') as log:
                    server=subprocess.Popen(command,env=runtime,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                    try:
                        for _ in range(100):
                            assert server.poll() is None,'server exited before readiness'
                            try:
                                with urllib.request.urlopen('http://127.0.0.1:'+str(port)+'/health',timeout=1) as response:
                                    if response.status==200:break
                            except OSError:time.sleep(.1)
                        else:raise RuntimeError('readiness timeout')
                        cases=run(db)
                        if mode=='reference':
                            populated=db.call('snapshot');assert len(populated['audits'])==2 and len(populated['requests'])==1
                            direct=db.call('probe');assert direct=={'checks':12}
                        cases += [{'group':'original-regression',**c} for c in original.run()]
                        counts={group:sum(c['group']==group for c in cases) for group in EXPECTED}
                        assert counts==EXPECTED,(counts,EXPECTED)
                        groups={g:{'passed':sum(c['passed'] for c in cases if c['group']==g),'total':n} for g,n in counts.items()}
                        passed=all(c['passed'] for c in cases)
                        report={'kind':'synthetic-mechanism-control','mode':mode,'passed':passed,'groups':groups,'cases':cases}
                        (directory/'result.json').write_text(json.dumps(report,indent=2)+'\n')
                        assert groups['original-regression']=={'passed':16,'total':16}
                        if mode=='reference':assert passed,report
                        else:
                            assert not passed,report
                            if mode in ['commit-before-audit','skip-replay','skip-completeness']:assert groups['functional']=={'passed':6,'total':6},report
                            target={'omit-audit':'functional','commit-before-audit':'failure-rollback','skip-replay':'idempotency','skip-completeness':'atomicity','reject-all':'functional'}[mode]
                            assert groups[target]['passed']<groups[target]['total'],report
                        reports.append({'mode':mode,'passed':passed,'groups':groups})
                        print(json.dumps({'mode':mode,'passed':passed,'cases':len(cases)}),flush=True)
                    finally:stop(server)
            # Fail closed on missing RLS, mutable completed requests, or ungated probes.
            for name,target,mutation,allow in [('unguarded-purchase','purchase',None,False),
                ('unguarded-audit','audit',None,False),('mutable-request','writable',None,False),('column-mutable-request','column',None,False),('truncatable-request','truncate',None,False),
                ('mutation-gate',None,'omit-audit',False),('unknown-mutation',None,'unknown',True)]:
                db.call('init')
                if target:db.call('misconfigure',target=target)
                command=[str(binary)]+(['--mutation',mutation] if mutation else [])
                runtime=dict(env)
                if allow:runtime['POC_ALLOW_MUTATIONS']='1'
                with (out/(name+'.log')).open('x') as log:
                    server=subprocess.Popen(command,env=runtime,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                    try:
                        try:code=server.wait(timeout=3)
                        except subprocess.TimeoutExpired:raise AssertionError('misconfigured service started: '+name)
                        assert code!=0,name
                        startup.append({'probe':name,'passed':True})
                    finally:stop(server)
    finally:db.close()
    paths=['internal/action/approval.go','internal/action/http.go','cmd/action-server/main.go','internal/app/demo_identity.go',
           'poc/transactional-action/schema.sql','benchmark/phase3/contract.json','benchmark/phase3/oracle.py','benchmark/phase3/db.mjs','benchmark/phase3/smoke.py',
           'tests/acceptance.py','db/bootstrap.sql','db/migrations/001_purchase.sql','db/seed.sql','generated/guard.sql','internal/app/server.go','go.mod','go.sum']
    summary={'kind':'synthetic-mechanism-control','new_model_generations':0,'evaluations':len(reports),
             'cases_per_evaluation':sum(EXPECTED.values()),'reference_passed':reports[0]['passed'],
             'negative_controls_detected':sum(not r['passed'] for r in reports[1:]),'controls':reports,
             'direct_metadata_checks':direct['checks'],'startup_probes':startup,
             'source_parent_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
             'source_checkout_dirty':bool(subprocess.check_output(['git','status','--porcelain'],text=True).strip()),
             'source_sha256':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths},
             'note':'Typed Action mechanism controls only. Not a model comparison, general workflow engine, or production exactly-once guarantee.'}
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps({k:summary[k] for k in ['evaluations','cases_per_evaluation','reference_passed','negative_controls_detected','direct_metadata_checks']}))


if __name__=='__main__':main()
