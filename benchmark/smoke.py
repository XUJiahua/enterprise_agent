#!/usr/bin/env python3
"""Actual candidate evaluations used to validate plumbing; never AI trial data."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
os.chdir(ROOT)
OUT=ROOT/'reports/benchmark'
OUT.mkdir(parents=True,exist_ok=True)
BASE=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
CLI=ROOT/'benchmark/benchctl.py'

def run(*args,expected=0):
    process=subprocess.run(['python3',str(CLI),*map(str,args)],capture_output=True,text=True)
    assert process.returncode==expected,(args,process.stdout,process.stderr)
    return process

for stale in OUT.glob('*'):
    if stale.is_dir():shutil.rmtree(stale)
    else:stale.unlink()
plan=OUT/'plan.json'
arguments=['plan','--base',BASE,'--model','synthetic-fixture','--task','tenant-summary','--repetitions','1','--synthetic','--allow-dirty-harness']
run(*arguments,'--output',plan)
run(*arguments,'--output',OUT/'plan-copy.json')
assert plan.read_bytes()==(OUT/'plan-copy.json').read_bytes(),'seeded plans must be reproducible'
trials=json.loads(plan.read_text())['trials']
empty=json.loads(run('summarize','--plan',plan,'--results',OUT/'attempts').stdout)
assert all(row['missing']==1 for row in empty['rows'])
with tempfile.TemporaryDirectory(prefix='enterprise-agent-candidate-') as temp:
    candidate=Path(temp)/'candidate'
    subprocess.run(['git','worktree','add','--detach',str(candidate),BASE],cwd=ROOT,check=True,capture_output=True)
    try:
        # The candidate has its own dependencies and generated schema. It never imports the trusted Oracle.
        shutil.copytree(ROOT/'apps/zenstack/node_modules',candidate/'apps/zenstack/node_modules',symlinks=True)
        for trial in trials:
            run('evaluate','--plan',plan,'--trial',trial['id'],'--candidate',candidate,'--attempt','1','--output',OUT/'attempts',expected=2)
            result=json.loads((OUT/'attempts'/f"{trial['id']}--attempt-001.json").read_text())
            assert result['outcome']=='acceptance-failed'
            regression=[c for c in result['cases'] if c['group']=='regression']
            assert len(regression)==16 and all(c['passed'] for c in regression)
            assert any(not c['passed'] for c in result['cases'] if c['group']=='task')
        baseline=next(t for t in trials if t['route']=='baseline')
        # Add a real correct implementation to one candidate, then independently re-evaluate attempt 2.
        source=candidate/'internal/app/server.go';text=source.read_text()
        needle='ctx := r.Context()'
        patch='''ctx := r.Context()
 if r.Method == "GET" && r.URL.Path == "/purchases/summary" {
  var count, total int
  err := tx.QueryRow(ctx, `SELECT count(*), COALESCE(sum(amount),0) FROM "Purchase" WHERE "tenantId"=$1`, u.Tenant).Scan(&count, &total)
  return map[string]int{"count":count,"totalAmount":total}, 200, err
 }'''
        assert needle in text
        source.write_text(text.replace(needle,patch,1))
        subprocess.run(['git','-C',str(candidate),'add','internal/app/server.go'],check=True)
        subprocess.run(['git','-C',str(candidate),'-c','user.name=POC Smoke','-c','user.email=poc@example.invalid','commit','-m','synthetic control: tenant summary'],check=True,capture_output=True)
        run('evaluate','--plan',plan,'--trial',baseline['id'],'--candidate',candidate,'--attempt','2','--output',OUT/'attempts')
        run('evaluate','--plan',plan,'--trial',baseline['id'],'--candidate',candidate,'--attempt','2','--output',OUT/'attempts',expected=1)
        value=json.loads(run('summarize','--plan',plan,'--results',OUT/'attempts','--output',OUT/'summary.json').stdout)
        assert value['kind']=='synthetic-smoke'
        assert all(row['evaluated']==1 and row['first_pass']==0 and row['missing']==0 for row in value['rows'])
        assert next(row for row in value['rows'] if row['route']=='baseline')['latest_pass']==1
        broken=json.loads(plan.read_text());broken['frozen']['benchmark/oracle.py']='0'*64
        (OUT/'tampered-plan.json').write_text(json.dumps(broken))
        rejection=run('evaluate','--plan',OUT/'tampered-plan.json','--trial',baseline['id'],'--candidate',candidate,'--attempt','3','--output',OUT/'attempts',expected=1)
        assert 'evaluator drift' in rejection.stderr
    finally:
        subprocess.run(['git','worktree','remove','--force',str(candidate)],cwd=ROOT,check=True)
print(json.dumps({'ok':True,'kind':'synthetic-smoke','evaluations':4,'controls':['seed reproducibility','missing denominators','real failing candidates','real repaired candidate','append-only attempts','evaluator drift refusal']}))
