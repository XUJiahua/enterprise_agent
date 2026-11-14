#!/usr/bin/env python3
"""Plan reproducible trials; independently evaluate committed candidates.
No model is called by this CLI. Model execution happens in its own checkout.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import signal
import subprocess
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
ROUTES=('baseline','zenstack','guarded')
FROZEN=('benchmark/codex_runner.py','benchmark/benchctl.py','benchmark/tasks.json','benchmark/oracle.py','tests/acceptance.py','scripts/db.mjs','db/bootstrap.sql','db/baseline-grants.sql','db/seed.sql','db/migrations/001_purchase.sql','generated/guard.sql')

def git(root,*args):return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as stream:json.dump(value,stream,indent=2,ensure_ascii=False);stream.write('\n')
def load_module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def plan(args):
    dirty=bool(git(ROOT,'status','--porcelain'))
    if dirty and not args.allow_dirty_harness:raise ValueError('commit harness changes before freezing an experiment')
    if args.repetitions<1 or args.repetitions>100:raise ValueError('repetitions must be between 1 and 100')
    if args.time_budget<1:raise ValueError('time budget must be positive')
    base=git(ROOT,'rev-parse','--verify',args.base+'^{commit}')
    tasks=json.loads((ROOT/'benchmark/tasks.json').read_text())['tasks']
    selected=[t for t in tasks if not args.task or t['id']==args.task]
    if not selected:raise ValueError('unknown task')
    trials=[]
    for task in selected:
        for route in ROUTES:
            for repetition in range(1,args.repetitions+1):
                trials.append({'id':f"{task['id']}--{route}--{repetition:03d}",'task':task['id'],'route':route,'repetition':repetition,'prompt':task['prompt']})
    random.Random(args.seed).shuffle(trials)
    value={'version':1,'kind':'synthetic-smoke' if args.synthetic else 'model-trial','base_sha':base,'harness_sha':git(ROOT,'rev-parse','HEAD'),'dirty_harness':dirty,'frozen':{p:digest(ROOT/p) for p in FROZEN},'seed':args.seed,'model':args.model,'generation_reasoning_effort':args.reasoning_effort,'generation_budget_seconds':args.time_budget,'build_timeout_seconds':120,'trials':trials}
    save(args.output,value);print(json.dumps({'trials':len(trials),'base_sha':base,'kind':value['kind']}))

def validate(plan):
    if plan.get('version')!=1 or plan.get('kind') not in ('synthetic-smoke','model-trial'):raise ValueError('invalid plan')
    if set(plan['frozen'])!=set(FROZEN):raise ValueError('incomplete frozen evaluator manifest')
    for path,checksum in plan['frozen'].items():
        if digest(ROOT/path)!=checksum:raise ValueError('trusted evaluator drift: '+path)

def evaluate(args):
    experiment=json.loads(Path(args.plan).read_text());validate(experiment)
    matches=[t for t in experiment['trials'] if t['id']==args.trial]
    if len(matches)!=1:raise ValueError('trial not found or duplicate')
    trial=matches[0];candidate=Path(args.candidate).resolve()
    if candidate==ROOT:raise ValueError('candidate must be a separate checkout from evaluator')
    if git(candidate,'status','--porcelain'):raise ValueError('candidate must be committed and clean')
    subprocess.run(['git','-C',str(candidate),'merge-base','--is-ancestor',experiment['base_sha'],'HEAD'],check=True)
    if args.attempt<1:raise ValueError('attempt must be positive')
    out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
    result_path=out/f"{trial['id']}--attempt-{args.attempt:03d}.json"
    if result_path.exists():raise ValueError('result already exists; attempts are append-only')
    import socket
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1',8080))==0:raise ValueError('port 8080 already in use; refuse ambiguous evaluation')
    # Exactly one source controls fixture and Oracle, outside the candidate checkout.
    subprocess.run(['node',str(ROOT/'scripts/db.mjs'),'reset',trial['route']],cwd=ROOT,check=True)
    started=time.monotonic();server=None
    result={'version':1,'plan_sha256':digest(Path(args.plan)),'trial':trial,'attempt':args.attempt,'kind':experiment['kind'],'model':experiment['model'],'candidate_sha':git(candidate,'rev-parse','HEAD'),'base_sha':experiment['base_sha'],'harness_sha':experiment['harness_sha'],'evaluated_at':datetime.now(timezone.utc).isoformat(),'cases':[],'passed':False}
    budget=experiment['build_timeout_seconds'];log_path=out/f"{trial['id']}--attempt-{args.attempt:03d}.log"
    try:
        with log_path.open('x') as stream:
            if trial['route']=='zenstack':
                # Dependencies must be installed by the candidate runner before evaluation.
                cli=candidate/'apps/zenstack/node_modules/.bin/zen'
                subprocess.run([str(cli),'generate'],cwd=candidate/'apps/zenstack',check=True,timeout=budget,stdout=stream,stderr=subprocess.STDOUT)
                command=['node','--import','tsx','src/server.ts'];cwd=candidate/'apps/zenstack'
            else:
                binary=out/f"server-{trial['id']}-{args.attempt}"
                subprocess.run(['go','build','-buildvcs=false','-o',str(binary),'./cmd/server'],cwd=candidate,check=True,timeout=budget,stdout=stream,stderr=subprocess.STDOUT)
                command=[str(binary),'--mode',trial['route']];cwd=candidate
            env={k:os.environ[k] for k in ('PATH','HOME','LANG','LC_ALL','TMPDIR','DATABASE_URL','NODE_EXTRA_CA_CERTS') if k in os.environ}
            server=subprocess.Popen(command,cwd=cwd,env=env,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
            for _ in range(100):
                if server.poll() is not None:raise RuntimeError('candidate exited before readiness')
                try:
                    with urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=1) as response:
                        if response.status==200:break
                except OSError:time.sleep(.1)
            else:raise RuntimeError('candidate readiness timeout')
            task_oracle=load_module('task_oracle',ROOT/'benchmark/oracle.py')
            common=load_module('common_oracle',ROOT/'tests/acceptance.py')
            result['cases']=[{'group':'task',**case} for case in task_oracle.check(trial['task'])]+[{'group':'regression',**case} for case in common.run()]
            result['passed']=bool(result['cases']) and all(c['passed'] for c in result['cases'])
            result['outcome']='passed' if result['passed'] else 'acceptance-failed'
    except (subprocess.CalledProcessError,subprocess.TimeoutExpired,RuntimeError) as error:
        result['outcome']='candidate-error';result['error']=str(error)
    finally:
        if server:
            try:os.killpg(server.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:server.wait(timeout=5)
            except subprocess.TimeoutExpired:os.killpg(server.pid,signal.SIGKILL);server.wait()
        result['evaluation_seconds']=round(time.monotonic()-started,3)
        # Generation time/cost are deliberately not inferred from evaluator duration.
        save(result_path,result)
    print(json.dumps({'result':str(result_path),'passed':result['passed'],'outcome':result['outcome']}))
    return 0 if result['passed'] else 2

def summarize(args):
    experiment=json.loads(Path(args.plan).read_text());validate(experiment)
    expected={t['id']:t for t in experiment['trials']};grouped={key:[] for key in expected}
    for path in Path(args.results).glob('*--attempt-*.json'):
        result=json.loads(path.read_text());key=result['trial']['id']
        if result['plan_sha256']!=digest(Path(args.plan)) or key not in expected:raise ValueError('result does not belong to frozen plan')
        if result['trial']!=expected[key] or result['kind']!=experiment['kind']:raise ValueError('result trial metadata differs from plan')
        if any(r['attempt']==result['attempt'] for r in grouped[key]):raise ValueError('duplicate attempt number')
        grouped[key].append(result)
    rows=[]
    for route in ROUTES:
        trials=[sorted(grouped[key],key=lambda r:r['attempt']) for key,t in expected.items() if t['route']==route]
        for attempts in trials:
            if attempts and [r['attempt'] for r in attempts]!=list(range(1,len(attempts)+1)):raise ValueError('attempt history contains gaps')
        completed=sum(bool(a) for a in trials);first=sum(bool(a) and a[0]['passed'] for a in trials);final=sum(bool(a) and a[-1]['passed'] for a in trials)
        rows.append({'route':route,'planned':len(trials),'evaluated':completed,'missing':len(trials)-completed,'first_pass':first,'latest_pass':final,'repair_attempts':sum(max(0,len(a)-1) for a in trials)})
    value={'kind':experiment['kind'],'model':experiment['model'],'rows':rows,'note':'Missing trials are retained in denominators; evaluator duration is not model generation time or cost.'}
    if args.output:save(args.output,value)
    print(json.dumps(value,indent=2))


def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('plan');p.add_argument('--base',required=True);p.add_argument('--model',required=True);p.add_argument('--repetitions',type=int,default=10);p.add_argument('--seed',type=int,default=20261114);p.add_argument('--time-budget',type=int,default=300);p.add_argument('--reasoning-effort',choices=['low','medium','high'],default='medium');p.add_argument('--task');p.add_argument('--synthetic',action='store_true');p.add_argument('--allow-dirty-harness',action='store_true');p.add_argument('--output',required=True)
    e=sub.add_parser('evaluate');e.add_argument('--plan',required=True);e.add_argument('--trial',required=True);e.add_argument('--candidate',required=True);e.add_argument('--attempt',type=int,required=True);e.add_argument('--output',required=True)
    s=sub.add_parser('summarize');s.add_argument('--plan',required=True);s.add_argument('--results',required=True);s.add_argument('--output')
    args=parser.parse_args()
    try:return {'plan':plan,'evaluate':evaluate,'summarize':summarize}[args.command](args) or 0
    except Exception as error:print(f'benchmark error: {error}',file=sys.stderr);return 1
if __name__=='__main__':sys.exit(main())
