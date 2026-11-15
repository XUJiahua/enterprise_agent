#!/usr/bin/env python3
"""Version 6a two-level approval (countersign) cohort, v5b design on a new task.

Generation (phase 3 worker) and private evaluation are separate.
"""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
WORKER='benchmark/phase3/generation_worker.py'
BASE=('go.mod','go.sum','internal/app/server.go','internal/app/demo_identity.go')
SQL=('db/migrations/001_purchase.sql','generated/guard.sql','poc/transactional-action/schema.sql')
COMMON={'cmd/server/main.go':'benchmark/phase3/source/main.go.txt',
        'internal/protocol/protocol.go':'benchmark/phase6a/source/protocol.go.txt',
        'internal/ledger/ledger.go':'benchmark/phase6a/source/ledger.go.txt',
        'internal/ledger/reconcile.go':'benchmark/phase5/source/reconcile.go.txt',
        'internal/notify/notify.go':'benchmark/phase6a/source/notify.go.txt'}
# Start state = v5b completed: reject (v5b template) and reopen exist; bulk-approve is tiered.
CELL={'go-manual':{'internal/candidate/approval.go':'benchmark/phase6a/source/manual-approval.go.txt',
                   'internal/candidate/reject.go':'benchmark/phase5b/source/manual-reject.go.txt',
                   'internal/candidate/reopen.go':'benchmark/phase6a/source/manual-reopen.go.txt'},
      'go-action':{'internal/candidate/approval.go':'benchmark/phase6a/source/action-candidate.go.txt',
                   'internal/candidate/operations.go':'benchmark/phase6a/source/action-operations.go.txt',
                   'internal/action/action.go':'benchmark/phase6a/source/action.go.txt'}}
EXTRA_SQL=('benchmark/phase4/schema.sql','benchmark/phase5/schema.sql','benchmark/phase6a/schema.sql')
TEMPLATES=tuple(sorted(set(COMMON.values())|{p for c in CELL.values() for p in c.values()}))
FROZEN=BASE+TEMPLATES+EXTRA_SQL+('benchmark/phase6a/ctl.py','benchmark/phase6a/cohort.json','benchmark/phase6a/oracle.py',
 'benchmark/phase6a/db.mjs','benchmark/phase5/oracle.py','benchmark/phase4/oracle.py',WORKER,'benchmark/phase3/cohort_oracle.py',
 'benchmark/phase3/oracle.py','tests/acceptance.py','db/bootstrap.sql','db/seed.sql')+SQL
OBSERVED={'passed','acceptance-failed','candidate-error','budget-exceeded','scope-violation'}
UNMEASURED={'runtime-blocked','provider-error','protocol-error','infrastructure-blocked'}

def sha(data): return hashlib.sha256(data).hexdigest()
def digest(path): return sha(Path(path).read_bytes())
def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as stream: json.dump(value,stream,indent=2,ensure_ascii=False);stream.write('\n')
def git(root,*args): return subprocess.check_output(['git','-C',str(root),*map(str,args)],text=True).strip()
def process(args,**kw): return subprocess.run(list(map(str,args)),capture_output=True,text=True,check=True,**kw)
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
def contract(): return json.loads((HERE/'cohort.json').read_text())
def counts(): return {g:v['count'] for g,v in contract()['groups'].items()}
def source(base,path): return subprocess.check_output(['git','-C',str(ROOT),'show',base+':'+path])
def trials(plan,ident=None):
    selected=[t for t in plan['trials'] if ident is None or t['id']==ident]
    if not selected: raise ValueError('trial not in plan')
    return selected

def prompt(cell):
    return '\n'.join([contract()['requirements'],
     'Add the countersign Action described above to the existing service.',
     'Read the existing code first and follow its conventions.',
     'Both conditions use guarded RLS. The shared HTTP adapter in internal/protocol validates input and binds demo identity; register the new path with protocol.Confirm.',
     'Only edit/add internal/candidate/ Go code. All other exports, dependencies, Git settings and tests are protected.',
     'Preserve the existing APIs, including bulk-approve, bulk-reject and reopen.',
     'Do not commit/push, read credentials, other checkouts or evaluator materials; do not use other agents.',
     'Use go build ./... and go test ./... as needed. Private database credentials are unavailable during generation. The public SQL schema is in SCHEMA.sql.',
     ('Capability: protected internal/action is the platform Action kernel: New(ctx,pool), the Operation and Request types and '
      'Executor.Run(ctx,identity,operation,key,request). The existing operations are declared as action.Operation values in '
      'internal/candidate/operations.go and served through the kernel; declare new operations in internal/candidate as well. '
      'action.Rejection is distinct from protocol.Rejection; adapt at your boundary.'
      if cell['capability'] else 'Capability: no platform Action kernel. The existing batch approval, batch rejection and reopen are hand-written in internal/candidate/.'),
     'This is a trusted-code pilot; directory export alone does not enforce host or network isolation.'])

def files(plan,cell):
    data={p:source(plan['base_sha'],p) for p in BASE}
    for path,template in {**COMMON,**CELL[cell['id']]}.items(): data[path]=(ROOT/template).read_bytes()
    data['TASK.md']=(contract()['requirements']+'\n').encode()
    data['SCHEMA.sql']=b'\n'.join([*(source(plan['base_sha'],p) for p in SQL),*((ROOT/p).read_bytes() for p in EXTRA_SQL)])
    data['.gitignore']=b'bin/\n/server\n*.log\n__pycache__/\n'  # v3 cohort 003: stray go build output was a scope violation
    return data

def load_plan(path):
    value=json.loads(Path(path).read_text())
    if value.get('version')!=6 or value.get('kind') not in ('model-trial','synthetic-smoke'):
        raise ValueError('v6a cohort plan required')
    if set(value['frozen'])!=set(FROZEN): raise ValueError('incomplete harness manifest')
    for p,h in value['frozen'].items():
        if digest(ROOT/p)!=h: raise ValueError('evaluator drift: '+p)
    if set(value['source_files'])!=set(BASE+SQL): raise ValueError('invalid source manifest')
    for p,h in value['source_files'].items():
        if sha(source(value['base_sha'],p))!=h: raise ValueError('base source drift: '+p)
    if type(value['generation_budget_seconds']) is not int or value['generation_budget_seconds']<1:
        raise ValueError('invalid generation budget')
    if value['reasoning_effort'] not in ('low','medium','high') or not value['model']:
        raise ValueError('invalid model settings')
    if value.get('engine') not in ('codex','kiro'): raise ValueError('unknown generation engine')
    cells={c['id']:c for c in contract()['cells']};blocks={};seen=set()
    for t in value['trials']:
        if t['cell'] not in cells or type(t['instance']) is not int or t['instance']<1: raise ValueError('invalid trial')
        expected=t['cell']+'--'+str(t['instance']).zfill(3)
        if t['id']!=expected or expected in seen: raise ValueError('duplicate/invalid trial identity')
        seen.add(expected);blocks.setdefault(t['instance'],set()).add(t['cell'])
        if t['prompt']!=prompt(cells[t['cell']]) or sha(t['prompt'].encode())!=t['prompt_sha256']: raise ValueError('prompt tamper')
    if not blocks or sorted(blocks)!=list(range(1,len(blocks)+1)) or any(b!=set(cells) for b in blocks.values()):
        raise ValueError('incomplete two-cell block')
    if value['exports']!={c['id']:{p:sha(d) for p,d in files(value,c).items()} for c in cells.values()}: raise ValueError('export drift')
    return value

def fixtures(args): save(args.output,{'version':1,'seed':secrets.token_hex(32)})

def plan(args):
    if args.allow_dirty_harness and not args.synthetic: raise ValueError('dirty real harness forbidden')
    if git(ROOT,'status','--porcelain') and not args.allow_dirty_harness: raise ValueError('commit harness before registering cohort')
    private=json.loads(Path(args.fixtures).read_text())
    if private.get('version')!=1 or len(private.get('seed',''))<32: raise ValueError('private seed required')
    if args.repetitions not in range(1,101) or args.time_budget<1: raise ValueError('invalid repetitions/budget')
    base=git(ROOT,'rev-parse','--verify',args.base+'^{commit}')
    value={'version':6,'kind':'synthetic-smoke' if args.synthetic else 'model-trial','task':contract()['task'],
     'base_sha':base,'harness_sha':git(ROOT,'rev-parse','HEAD'),'frozen':{p:digest(ROOT/p) for p in FROZEN},
     'source_files':{p:sha(source(base,p)) for p in BASE+SQL},'fixtures_sha256':digest(args.fixtures),'seed':args.seed,
     'model':args.model,'reasoning_effort':args.reasoning_effort,'engine':args.engine,
     'generation_budget_seconds':args.time_budget,'trials':[],
     'isolation':'operator must isolate generation from evaluator; commands alone do not enforce it'}
    value['exports']={c['id']:{p:sha(d) for p,d in files(value,c).items()} for c in contract()['cells']}
    for instance in range(1,args.repetitions+1):
        block=[]
        for c in contract()['cells']:
            text=prompt(c)
            block.append({'id':c['id']+'--'+str(instance).zfill(3),'cell':c['id'],'instance':instance,
                          'prompt':text,'prompt_sha256':sha(text.encode())})
        random.Random(args.seed+instance).shuffle(block);value['trials']+=block
    save(args.output,value)
    print(json.dumps({'planned':len(value['trials']),'kind':value['kind']}))

def export_candidate(args):
    p=load_plan(args.plan);t=trials(p,args.trial)[0]
    candidate=Path(args.candidate).resolve();manifest=Path(args.manifest).resolve()
    if candidate==ROOT or candidate.is_relative_to(ROOT) or manifest.is_relative_to(candidate):
        raise ValueError('candidate must be outside harness and attestation outside candidate')
    if candidate.exists() or manifest.exists(): raise ValueError('export output exists')
    candidate.mkdir(parents=True)
    c=next(c for c in contract()['cells'] if c['id']==t['cell'])
    for name,data in files(p,c).items():
        path=candidate/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    process(['git','init','-b','candidate'],cwd=candidate)
    process(['git','add','.'],cwd=candidate)
    process(['git','-c','user.name=POC Export','-c','user.email=poc@example.invalid','commit','-m','v6a shared base'],cwd=candidate)
    save(manifest,{'version':6,'trial_id':t['id'],'plan_sha256':digest(args.plan),'initial_sha':git(candidate,'rev-parse','HEAD'),
                   'source_base_sha':p['base_sha'],'files':p['exports'][t['cell']]})

def prepare(args):
    """A per-trial kit: no private seed, Oracle source, controller or other cell; worker is the phase 3 worker."""
    p=load_plan(args.plan);t=trials(p,args.trial)[0];out=Path(args.output).resolve()
    if out==ROOT or out.is_relative_to(ROOT): raise ValueError('generation kit must be outside evaluator checkout')
    out.mkdir(parents=True,exist_ok=False)
    export_candidate(argparse.Namespace(plan=args.plan,trial=args.trial,candidate=out/'candidate',manifest=out/'manifest.json'))
    (out/'plan.json').write_bytes(Path(args.plan).read_bytes())
    (out/'worker.py').write_bytes((ROOT/WORKER).read_bytes())
    save(out/'job.json',{'version':6,'kind':p['kind'],'plan_sha256':digest(args.plan),'trial':t,
      'manifest_sha256':digest(out/'manifest.json'),'worker_sha256':digest(out/'worker.py'),
      'model':p['model'],'reasoning_effort':p['reasoning_effort'],'generation_budget_seconds':p['generation_budget_seconds'],
      'engine':p['engine']})

def validate_candidate(args,p):
    candidate=Path(args.candidate).resolve();manifest=Path(args.manifest).resolve();t=trials(p,args.trial)[0]
    if candidate==ROOT or candidate.is_relative_to(ROOT) or manifest.is_relative_to(candidate):
        raise ValueError('invalid candidate/attestation boundary')
    m=json.loads(manifest.read_text());initial=m['initial_sha']
    if (m.get('version')!=6 or m['trial_id']!=t['id'] or m['plan_sha256']!=digest(args.plan)
       or m['source_base_sha']!=p['base_sha'] or m['files']!=p['exports'][t['cell']]):
        raise ValueError('export attestation mismatch')
    if git(candidate,'rev-list','--count',initial)!='1' or git(candidate,'remote'): raise ValueError('export history/remote violation')
    if set(git(candidate,'ls-tree','-r','--name-only',initial).splitlines())!=set(m['files']): raise ValueError('unexpected base files')
    for name,h in m['files'].items():
        if sha(subprocess.check_output(['git','-C',str(candidate),'show',initial+':'+name]))!=h: raise ValueError('base snapshot tamper')
    process(['git','-C',candidate,'merge-base','--is-ancestor',initial,'HEAD'])
    if git(candidate,'status','--porcelain'): raise ValueError('candidate must be committed/clean')
    changed=git(candidate,'diff','--name-only',initial,'HEAD').splitlines()
    if any(not n.startswith('internal/candidate/') or not n.endswith('.go') for n in changed):
        raise ValueError('protected capability/export modification')
    for line in git(candidate,'ls-tree','-r','HEAD').splitlines():
        if line.split()[0]!='100644': raise ValueError('only regular non-executable source files allowed')
    return candidate

def freeze_candidate(args,p,t,generated,directory):
    """Human controls only: commit a hand-written candidate and emit a worker-format receipt."""
    candidate=Path(args.candidate).resolve()
    initial=json.loads(Path(args.manifest).read_text())['initial_sha']
    process(['git','add','--all'],cwd=candidate)
    process(['git','-c','user.name=POC v6a','-c','user.email=poc@example.invalid','commit','--allow-empty','-m','v6a control'],cwd=candidate)
    validate_candidate(args,p)
    (directory/'candidate.patch').write_bytes(subprocess.check_output(['git','-C',str(candidate),'diff','--binary',initial,'HEAD']))
    process(['git','bundle','create',directory/'candidate.bundle','HEAD'],cwd=candidate)
    value={'version':3,'kind':p['kind'],'plan_sha256':digest(args.plan),'trial':t,'candidate_sha':git(candidate,'rev-parse','HEAD'),
           'manifest_sha256':digest(args.manifest),'bundle_sha256':digest(directory/'candidate.bundle'),'generation':generated}
    save(directory/'receipt.json',value);return value

def receipt(args,p):
    # Version 3 is the shared standalone worker's receipt format.
    r=json.loads(Path(args.receipt).read_text());t=trials(p,args.trial)[0]
    if (r.get('version')!=3 or r['kind']!=p['kind'] or r['plan_sha256']!=digest(args.plan) or r['trial']!=t
        or r['manifest_sha256']!=digest(args.manifest) or r['generation']['status']!='completed'):
        raise ValueError('generation receipt identity mismatch')
    return r

def restore(args):
    p=load_plan(args.plan);r=receipt(args,p)
    if digest(args.bundle)!=r['bundle_sha256']: raise ValueError('bundle checksum mismatch')
    candidate=Path(args.candidate).resolve()
    if candidate.exists() or candidate==ROOT or candidate.is_relative_to(ROOT): raise ValueError('invalid restore directory')
    process(['git','clone',args.bundle,candidate]);process(['git','remote','remove','origin'],cwd=candidate)
    if git(candidate,'rev-parse','HEAD')!=r['candidate_sha']: raise ValueError('restored head mismatch')
    validate_candidate(args,p)

def score(cases):
    """Per-group, explicit/implicit and per-convention scores; raises on an incomplete case manifest."""
    groups=contract()['groups']
    if Counter(c['group'] for c in cases)!=counts() or len({(c['group'],c['case']) for c in cases})!=len(cases):
        raise ValueError('incomplete/duplicate case manifest')
    by={g:{'kind':v['kind'],'passed':sum(c['passed'] for c in cases if c['group']==g),'total':v['count']} for g,v in groups.items()}
    kinds={k:{'passed':sum(v['passed'] for v in by.values() if v['kind']==k),'total':sum(v['total'] for v in by.values() if v['kind']==k)}
           for k in ('explicit','implicit')}
    return {'groups':by,'explicit':kinds['explicit'],'implicit':kinds['implicit'],
            'explicit_passed':kinds['explicit']['passed']==kinds['explicit']['total'],
            'conventions':{g:v['passed']==v['total'] for g,v in by.items() if v['kind']=='implicit'},
            'passed':all(c['passed'] for c in cases)}

def stop(server):
    if server.poll() is None: os.killpg(server.pid,signal.SIGTERM)
    try: server.wait(timeout=5)
    except subprocess.TimeoutExpired: os.killpg(server.pid,signal.SIGKILL);server.wait()

def evaluate(args):
    p=load_plan(args.plan);t=trials(p,args.trial)[0];r=receipt(args,p)
    candidate=validate_candidate(args,p)
    if git(candidate,'rev-parse','HEAD')!=r['candidate_sha']: raise ValueError('candidate differs from generation receipt')
    if digest(args.fixtures)!=p['fixtures_sha256']: raise ValueError('private seed differs from plan')
    private=json.loads(Path(args.fixtures).read_text())
    out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=False)
    port=os.environ.get('POC_HTTP_PORT','8080')
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1',int(port)))==0: raise ValueError('evaluation port occupied')
    result={'version':6,**{k:r[k] for k in ('kind','plan_sha256','trial','candidate_sha','manifest_sha256','generation')},
            'cases':[],'passed':False}
    oracle=module('v6a_oracle',HERE/'oracle.py')
    server=None;db=None;binary=out/'server';start=time.monotonic()
    try:
        process(['go','version'],timeout=10)
        db=oracle.Database();db.call('init')
    except (OSError,subprocess.SubprocessError,oracle.InfrastructureError) as error:
        result.update({'outcome':'infrastructure-blocked','error':str(error)})
    else:
        with (out/'candidate.log').open('x') as log:
            try:
                subprocess.run(['go','build','-buildvcs=false','-o',str(binary),'./cmd/server'],cwd=candidate,
                               stdout=log,stderr=subprocess.STDOUT,check=True,timeout=120)
                env={k:os.environ[k] for k in ('PATH','HOME','LANG','TMPDIR','DATABASE_URL','POC_HTTP_PORT') if k in os.environ}
                server=subprocess.Popen([str(binary)],cwd=candidate,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                for _ in range(100):
                    if server.poll() is not None: raise RuntimeError('candidate exited before readiness')
                    try:
                        with urllib.request.urlopen('http://127.0.0.1:'+port+'/health',timeout=1) as response:
                            if response.status==200: break
                    except OSError: time.sleep(.1)
                else: raise RuntimeError('candidate readiness timeout')
                cases=oracle.run(db,private['seed']+':'+str(t['instance']))
                try: scored=score(cases)
                except ValueError as error: raise oracle.InfrastructureError(str(error)) from error
                result.update({'cases':cases,**scored,'outcome':'passed' if scored['passed'] else 'acceptance-failed'})
            except oracle.InfrastructureError as error:
                # v3 cohort 003: a handler panic closes the connection and looked like infrastructure.
                log.flush();crashed=server is not None and (server.poll() is not None or 'panic' in (out/'candidate.log').read_text())
                result.update({'outcome':'candidate-error' if crashed else 'infrastructure-blocked','error':str(error)})
            except (subprocess.SubprocessError,RuntimeError,OSError) as error:
                result.update({'outcome':'candidate-error','error':str(error)})
            finally:
                if server: stop(server)
    if db: db.close()
    binary.unlink(missing_ok=True)
    load_plan(args.plan);validate_candidate(args,p)
    result['evaluation_seconds']=round(time.monotonic()-start,3)
    save(out/'result.json',result)
    print(json.dumps({'trial':t['id'],'outcome':result['outcome'],'cases':len(result['cases']),
                      'explicit':result.get('explicit'),'implicit':result.get('implicit')}))
    return 0 if result['passed'] else (3 if result['outcome'] in UNMEASURED else 2)

def summarize(args):
    p=load_plan(args.plan);known={t['id']:t for t in p['trials']};records={}
    for directory in args.inputs:
        if not Path(directory).is_dir(): raise ValueError('result directory does not exist: '+str(directory))
        for path in Path(directory).rglob('result.json'):
            r=json.loads(path.read_text());ident=r['trial']['id']
            # Version 3 records are worker generation outcomes; version 6 are evaluations.
            if r.get('version') not in (3,6) or r['kind']!=p['kind'] or r['plan_sha256']!=digest(args.plan) or r['trial']!=known.get(ident):
                raise ValueError('foreign trial/cohort')
            if ident in records: raise ValueError('duplicate/conflicting trial: '+ident)
            if r['outcome'] not in OBSERVED|UNMEASURED: raise ValueError('unknown outcome')
            if (r.get('passed') is True)!=(r['outcome']=='passed'): raise ValueError('inconsistent pass flag')
            if r['outcome'] in ('passed','acceptance-failed'):
                if r['version']!=6: raise ValueError('acceptance outcome without evaluation')
                scored=score(r.get('cases',[]))
                if any(r.get(k)!=v for k,v in scored.items()): raise ValueError('score mismatch')
            records[ident]=r
    rows=[]
    for c in contract()['cells']:
        selected=[t for t in p['trials'] if t['cell']==c['id']]
        values=[records[t['id']] for t in selected if t['id'] in records]
        observed=sum(r['outcome'] in OBSERVED for r in values);complete=observed==len(selected)
        passed=sum(r['outcome']=='passed' for r in values);explicit=sum(bool(r.get('explicit_passed')) for r in values)
        rows.append({'cell':c['id'],'planned':len(selected),'observed':observed,'unmeasured':len(selected)-observed,
            'passed_all':passed,'passed_explicit':explicit,
            'conventions_followed':{g:sum(bool(r.get('conventions',{}).get(g)) for r in values)
                                    for g,v in contract()['groups'].items() if v['kind']=='implicit'},
            'first_pass_rate':passed/len(selected) if complete else None,
            'explicit_pass_rate':explicit/len(selected) if complete else None,
            'outcomes':dict(Counter(r['outcome'] for r in values)),
            'usage':[{'trial':r['trial']['id'],'usage':r.get('generation',{}).get('usage')} for r in values]})
    value={'version':6,'kind':p['kind'],'plan_sha256':digest(args.plan),'rows':rows,
           'note':'Explicit contract and implicit conventions are reported separately. Synthetic controls are not model evidence. '
                  'Missing/infrastructure trials remain unmeasured; absent usage is unknown.'}
    if args.output: save(args.output,value)
    print(json.dumps(value,indent=2))

def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='command',required=True)
    s=sub.add_parser('fixtures');s.add_argument('--output',required=True)
    s=sub.add_parser('plan')
    for n in ('base','model','fixtures','output'): s.add_argument('--'+n,required=True)
    s.add_argument('--repetitions',type=int,default=1);s.add_argument('--time-budget',type=int,default=600)
    s.add_argument('--reasoning-effort',choices=('low','medium','high'),default='medium');s.add_argument('--seed',type=int,default=20261206)
    s.add_argument('--engine',choices=('codex','kiro'),default='kiro')
    s.add_argument('--synthetic',action='store_true');s.add_argument('--allow-dirty-harness',action='store_true')
    s=sub.add_parser('prepare')
    for n in ('plan','trial','output'): s.add_argument('--'+n,required=True)
    for name in ('export','restore','evaluate'):
        s=sub.add_parser(name)
        for n in ('plan','trial','candidate','manifest'): s.add_argument('--'+n,required=True)
        if name in ('restore','evaluate'): s.add_argument('--receipt',required=True)
        if name=='restore': s.add_argument('--bundle',required=True)
        if name=='evaluate':
            for n in ('fixtures','output'): s.add_argument('--'+n,required=True)
    s=sub.add_parser('summarize');s.add_argument('--plan',required=True);s.add_argument('--inputs',action='append',required=True);s.add_argument('--output')
    args=parser.parse_args()
    try: return {'fixtures':fixtures,'plan':plan,'prepare':prepare,'export':export_candidate,'restore':restore,
                 'evaluate':evaluate,'summarize':summarize}[args.command](args) or 0
    except Exception as error:
        print('phase 6a error: '+str(error),file=sys.stderr);return 1

if __name__=='__main__': sys.exit(main())
