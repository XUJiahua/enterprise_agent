#!/usr/bin/env python3
"""Version 3 capability cohort. Generation and private evaluation are separate."""
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
sys.path.insert(0,str(ROOT/'benchmark'))
import codex_runner as driver

BASE=('go.mod','go.sum','internal/app/server.go','internal/app/demo_identity.go')
TEMPLATES={'cmd/server/main.go':'main.go.txt','internal/protocol/protocol.go':'protocol.go.txt',
           'internal/candidate/approval.go':'candidate.go.txt'}
CAPABILITY='internal/action/approval.go'
FROZEN=BASE+(CAPABILITY,'benchmark/phase3/ctl.py','benchmark/phase3/cohort.json',
 'benchmark/phase3/cohort_oracle.py','benchmark/phase3/oracle.py','benchmark/phase3/db.mjs',
 'benchmark/phase3/generation_worker.py',
 'benchmark/phase3/contract.json','benchmark/phase3/visible_smoke.py',
 'benchmark/codex_runner.py','benchmark/benchctl.py','tests/acceptance.py',
 'db/bootstrap.sql','db/migrations/001_purchase.sql','db/seed.sql','generated/guard.sql',
 'poc/transactional-action/schema.sql')+tuple('benchmark/phase3/source/'+p for p in TEMPLATES.values())
OBSERVED={'passed','acceptance-failed','candidate-error','budget-exceeded','scope-violation'}
UNMEASURED={'runtime-blocked','provider-error','protocol-error','infrastructure-blocked'}

def sha(data): return hashlib.sha256(data).hexdigest()
def digest(path): return sha(Path(path).read_bytes())
def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as stream: json.dump(value,stream,indent=2,ensure_ascii=False);stream.write('\n')
def git(root,*args):
    return subprocess.check_output(['git','-C',str(root),*map(str,args)],text=True).strip()
def process(args,**kw): return subprocess.run(list(map(str,args)),capture_output=True,text=True,check=True,**kw)
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
def contract(): return json.loads((HERE/'cohort.json').read_text())
def source(base,path): return subprocess.check_output(['git','-C',str(ROOT),'show',base+':'+path])
def trials(plan,ident=None):
    selected=[t for t in plan['trials'] if ident is None or t['id']==ident]
    if not selected: raise ValueError('trial not in plan')
    return selected
def prompt(cell):
    return '\n'.join([contract()['requirements'],
     'Evolve the existing atomic batch approval to add audit and scoped idempotency.',
     'Both conditions use guarded RLS. The shared HTTP adapter already validates input and binds demo identity.',
     'Only edit/add internal/candidate/ Go code. All other exports, dependencies, Git settings and tests are protected.',
     'Preserve the original APIs. New returns: protocol.Result, replayed bool, error; protocol.Rejection controls HTTP rejection status.',
     'Do not commit/push, read credentials, other checkouts or evaluator materials; do not use other agents.',
     'Use go test ./... and python3 visible_smoke.py only if an independent public test service is supplied.',
     'Private database credentials are unavailable during generation. Public SQL schema is in SCHEMA.sql.',
     ('Capability: protected internal/action exposes New(ctx,pool,"") and Executor.BulkApprove(ctx,identity,key,ids). '
      'Its Result and Rejection types are distinct from protocol types; adapt values/errors at your boundary.'
      if cell['capability'] else 'Capability: no prebuilt Action kernel. Implement the transaction in internal/candidate/.'),
     'This is a trusted-code pilot; directory export alone does not enforce host or network isolation.'])

def files(plan,cell):
    data={p:source(plan['base_sha'],p) for p in BASE}
    data.update({p:(HERE/'source'/template).read_bytes() for p,template in TEMPLATES.items()})
    data['TASK.md']=(contract()['requirements']+'\n').encode()
    data['visible_smoke.py']=(HERE/'visible_smoke.py').read_bytes()
    data['SCHEMA.sql']=b'\n'.join(source(plan['base_sha'],p) for p in
       ('db/migrations/001_purchase.sql','generated/guard.sql','poc/transactional-action/schema.sql'))
    data['.gitignore']=b'bin/\n*.log\n__pycache__/\n'
    if cell['capability']: data[CAPABILITY]=source(plan['base_sha'],CAPABILITY)
    return data

def load_plan(path):
    value=json.loads(Path(path).read_text())
    if value.get('version')!=3 or value.get('kind') not in ('model-trial','synthetic-smoke'):
        raise ValueError('v3 cohort plan required')
    if set(value['frozen'])!=set(FROZEN): raise ValueError('incomplete harness manifest')
    for p,h in value['frozen'].items():
        if digest(ROOT/p)!=h: raise ValueError('evaluator drift: '+p)
    if set(value['source_files'])!=set(BASE+(CAPABILITY,)+('db/migrations/001_purchase.sql','generated/guard.sql','poc/transactional-action/schema.sql')):
        raise ValueError('invalid source manifest')
    for p,h in value['source_files'].items():
        if sha(source(value['base_sha'],p))!=h: raise ValueError('base source drift: '+p)
    if type(value['generation_budget_seconds']) is not int or value['generation_budget_seconds']<1:
        raise ValueError('invalid generation budget')
    if value['reasoning_effort'] not in ('low','medium','high') or not value['model']:
        raise ValueError('invalid model settings')
    if value.get('engine','codex') not in ('codex','kiro'): raise ValueError('unknown generation engine')
    cells={c['id']:c for c in contract()['cells']};blocks={};seen=set()
    for t in value['trials']:
        if t['cell'] not in cells or type(t['instance']) is not int or t['instance']<1:
            raise ValueError('invalid trial')
        expected=t['cell']+'--'+str(t['instance']).zfill(3)
        if t['id']!=expected or expected in seen: raise ValueError('duplicate/invalid trial identity')
        seen.add(expected);blocks.setdefault(t['instance'],set()).add(t['cell'])
        if t['prompt']!=prompt(cells[t['cell']]) or sha(t['prompt'].encode())!=t['prompt_sha256']:
            raise ValueError('prompt tamper')
    if not blocks or sorted(blocks)!=list(range(1,len(blocks)+1)) or any(b!=set(cells) for b in blocks.values()):
        raise ValueError('incomplete two-cell block')
    expected={c['id']:{p:sha(data) for p,data in files(value,c).items()} for c in cells.values()}
    if value['exports']!=expected: raise ValueError('export drift')
    return value

def fixtures(args): save(args.output,{'version':1,'seed':secrets.token_hex(32)})

def plan(args):
    if args.allow_dirty_harness and not args.synthetic: raise ValueError('dirty real harness forbidden')
    if git(ROOT,'status','--porcelain') and not args.allow_dirty_harness: raise ValueError('commit harness before registering cohort')
    private=json.loads(Path(args.fixtures).read_text())
    if private.get('version')!=1 or len(private.get('seed',''))<32: raise ValueError('private seed required')
    if args.repetitions not in range(1,101) or args.time_budget<1: raise ValueError('invalid repetitions/budget')
    base=git(ROOT,'rev-parse','--verify',args.base+'^{commit}')
    value={'version':3,'kind':'synthetic-smoke' if args.synthetic else 'model-trial',
     'task':contract()['task'],'base_sha':base,'harness_sha':git(ROOT,'rev-parse','HEAD'),
     'frozen':{p:digest(ROOT/p) for p in FROZEN},
     'source_files':{p:sha(source(base,p)) for p in BASE+(CAPABILITY,)+('db/migrations/001_purchase.sql','generated/guard.sql','poc/transactional-action/schema.sql')},
     'fixtures_sha256':digest(args.fixtures),'seed':args.seed,'model':args.model,'reasoning_effort':args.reasoning_effort,
     'engine':args.engine,'generation_budget_seconds':args.time_budget,'trials':[],
     'isolation':'operator must isolate generation from evaluator; commands alone do not enforce it'}
    value['exports']={c['id']:{p:sha(data) for p,data in files(value,c).items()} for c in contract()['cells']}
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
    process(['git','-c','user.name=POC Export','-c','user.email=poc@example.invalid','commit','-m','v3 shared base'],cwd=candidate)
    save(manifest,{'version':3,'trial_id':t['id'],'plan_sha256':digest(args.plan),'initial_sha':git(candidate,'rev-parse','HEAD'),
                   'source_base_sha':p['base_sha'],'files':p['exports'][t['cell']]})

def prepare(args):
    """A per-trial kit: no private seed, Oracle source, controller or other cell."""
    p=load_plan(args.plan);t=trials(p,args.trial)[0];out=Path(args.output).resolve()
    if out==ROOT or out.is_relative_to(ROOT):raise ValueError('generation kit must be outside evaluator checkout')
    out.mkdir(parents=True,exist_ok=False)
    export_candidate(argparse.Namespace(plan=args.plan,trial=args.trial,candidate=out/'candidate',manifest=out/'manifest.json'))
    (out/'plan.json').write_bytes(Path(args.plan).read_bytes())
    (out/'worker.py').write_bytes((HERE/'generation_worker.py').read_bytes())
    save(out/'job.json',{'version':3,'kind':p['kind'],'plan_sha256':digest(args.plan),'trial':t,
      'manifest_sha256':digest(out/'manifest.json'),'worker_sha256':digest(out/'worker.py'),
      'model':p['model'],'reasoning_effort':p['reasoning_effort'],'generation_budget_seconds':p['generation_budget_seconds'],
      'engine':p.get('engine','codex')})

def validate_candidate(args,p):
    candidate=Path(args.candidate).resolve();manifest=Path(args.manifest).resolve();t=trials(p,args.trial)[0]
    if candidate==ROOT or candidate.is_relative_to(ROOT) or manifest.is_relative_to(candidate):
        raise ValueError('invalid candidate/attestation boundary')
    m=json.loads(manifest.read_text());initial=m['initial_sha']
    if (m.get('version')!=3 or m['trial_id']!=t['id'] or m['plan_sha256']!=digest(args.plan)
       or m['source_base_sha']!=p['base_sha'] or m['files']!=p['exports'][t['cell']]):
        raise ValueError('export attestation mismatch')
    if git(candidate,'rev-list','--count',initial)!='1' or git(candidate,'remote'):
        raise ValueError('export history/remote violation')
    initial_files=git(candidate,'ls-tree','-r','--name-only',initial).splitlines()
    if set(initial_files)!=set(m['files']): raise ValueError('unexpected base files')
    for name,h in m['files'].items():
        if sha(subprocess.check_output(['git','-C',str(candidate),'show',initial+':'+name]))!=h:
            raise ValueError('base snapshot tamper')
    process(['git','-C',candidate,'merge-base','--is-ancestor',initial,'HEAD'])
    if git(candidate,'status','--porcelain'): raise ValueError('candidate must be committed/clean')
    changed=git(candidate,'diff','--name-only',initial,'HEAD').splitlines()
    if any(not n.startswith('internal/candidate/') or not n.endswith('.go') for n in changed):
        raise ValueError('protected capability/export modification')
    # Reject links/submodules even inside the editable subtree.
    for line in git(candidate,'ls-tree','-r','HEAD').splitlines():
        if line.split()[0]!='100644': raise ValueError('only regular non-executable source files allowed')
    return candidate

def stop(server):
    if server.poll() is None: os.killpg(server.pid,signal.SIGTERM)
    try: server.wait(timeout=5)
    except subprocess.TimeoutExpired: os.killpg(server.pid,signal.SIGKILL);server.wait()

def freeze_candidate(args,p,t,generated,directory):
    candidate=Path(args.candidate).resolve()
    initial=json.loads(Path(args.manifest).read_text())['initial_sha']
    # Include staged changes as well as worktree/untracked changes.
    changed=driver.changed_paths(candidate)
    if any(not n.startswith('internal/candidate/') or not n.endswith('.go') for n in changed):
        raise ValueError('scope-violation')
    for path in candidate.rglob('*'):
        if path.is_symlink(): raise ValueError('scope-violation')
    if changed: process(['git','add','--',*changed],cwd=candidate)
    process(['git','-c','user.name=POC v3','-c','user.email=poc@example.invalid','commit','--allow-empty','-m','v3 candidate'],cwd=candidate)
    validate_candidate(args,p)
    (directory/'candidate.patch').write_bytes(subprocess.check_output(['git','-C',str(candidate),'diff','--binary',initial,'HEAD']))
    process(['git','bundle','create',directory/'candidate.bundle','HEAD'],cwd=candidate)
    receipt={'version':3,'kind':p['kind'],'plan_sha256':digest(args.plan),'trial':t,
       'candidate_sha':git(candidate,'rev-parse','HEAD'),'manifest_sha256':digest(args.manifest),
       'bundle_sha256':digest(directory/'candidate.bundle'),'generation':generated}
    save(directory/'receipt.json',receipt)
    return receipt

def generate(args):
    p=load_plan(args.plan)
    if bool(args.synthetic_test_driver)!=(p['kind']=='synthetic-smoke'): raise ValueError('synthetic/real driver mismatch')
    if p.get('engine','codex')!='codex': raise ValueError('non-Codex plans run through prepare + worker.py')
    selected=trials(p,args.trial)
    if len(selected)>8: raise ValueError('select at most eight trials per generation batch')
    out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=False)
    candidates=Path(args.candidate_root).resolve()
    if candidates==ROOT or candidates.is_relative_to(ROOT) or out.is_relative_to(candidates):
        raise ValueError('candidate root must be outside harness and receipts')
    binary=args.synthetic_test_driver or args.codex_bin
    check=driver.preflight(binary,p['model'],p['reasoning_effort'],out,p['kind']=='synthetic-smoke')
    batch={'version':3,'kind':p['kind'],'plan_sha256':digest(args.plan),'cli_version':check['cli_version'],'trials':[]}
    if not check['ready']:
        batch['status']='preflight-blocked'
        save(out/'batch.json',batch)
        return 3
    for t in selected:
        a=argparse.Namespace(plan=args.plan,trial=t['id'],candidate=candidates/t['id'],manifest=out/'exports'/(t['id']+'.json'))
        export_candidate(a)
        directory=out/'generation'/t['id']
        result=driver.invoke(binary,p['model'],p['reasoning_effort'],a.candidate,t['prompt'],directory,
                             p['generation_budget_seconds'],'workspace-write')
        result['cli_version']=check['cli_version']
        load_plan(args.plan)
        row={'trial':t,'generation':result};batch['trials'].append(row)
        if result['status']=='completed':
            try:
                receipt=freeze_candidate(a,p,t,result,directory)
                row.update({'outcome':'generated','candidate_sha':receipt['candidate_sha']})
            except ValueError as error:
                row.update({'outcome':'scope-violation','error':str(error)})
        else: row['outcome']=result['status']
        if row['outcome']!='generated':
            save(out/'outcomes'/t['id']/'result.json',{'version':3,'kind':p['kind'],'plan_sha256':digest(args.plan),
              'trial':t,'outcome':row['outcome'],'passed':False,'generation':result})
        # Persist each trial before starting another, allowing explicit partial recovery.
        save(out/'progress'/(t['id']+'.json'),row)
        if row['outcome'] in UNMEASURED: batch['status']='generation-blocked';break
    else: batch['status']='selected-generated'
    save(out/'batch.json',batch)
    return 0 if batch['status']=='selected-generated' else 3

def receipt(args,p):
    r=json.loads(Path(args.receipt).read_text());t=trials(p,args.trial)[0]
    if (r.get('version')!=3 or r['kind']!=p['kind'] or r['plan_sha256']!=digest(args.plan)
        or r['trial']!=t or r['manifest_sha256']!=digest(args.manifest)
        or r['generation']['status']!='completed'):
        raise ValueError('generation receipt identity mismatch')
    return r

def restore(args):
    p=load_plan(args.plan);r=receipt(args,p)
    if digest(args.bundle)!=r['bundle_sha256']: raise ValueError('bundle checksum mismatch')
    candidate=Path(args.candidate).resolve()
    if candidate.exists() or candidate==ROOT or candidate.is_relative_to(ROOT): raise ValueError('invalid restore directory')
    process(['git','clone',args.bundle,candidate])
    process(['git','remote','remove','origin'],cwd=candidate)
    if git(candidate,'rev-parse','HEAD')!=r['candidate_sha']: raise ValueError('restored head mismatch')
    validate_candidate(args,p)

def evaluate(args):
    p=load_plan(args.plan);t=trials(p,args.trial)[0];r=receipt(args,p)
    candidate=validate_candidate(args,p)
    if git(candidate,'rev-parse','HEAD')!=r['candidate_sha']: raise ValueError('candidate differs from generation receipt')
    if digest(args.fixtures)!=p['fixtures_sha256']: raise ValueError('private seed differs from plan')
    private=json.loads(Path(args.fixtures).read_text())
    out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=False)
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1',int(os.environ.get('POC_HTTP_PORT','8080'))))==0:
            raise ValueError('evaluation port occupied')
    result={**{k:r[k] for k in ('version','kind','plan_sha256','trial','candidate_sha','manifest_sha256','generation')},
            'cases':[],'groups':{},'passed':False}
    oracle=module('v3_cohort_oracle',HERE/'cohort_oracle.py')
    mechanism=oracle
    server=None;db=None;binary=out/'server';start=time.monotonic()
    try:
        # Verify tool availability independently before attributing build errors.
        process(['go','version'],timeout=10)
        db=mechanism.Database();db.call('init')
    except (OSError,subprocess.SubprocessError,mechanism.InfrastructureError) as error:
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
                        with urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('POC_HTTP_PORT','8080')+'/health',timeout=1) as response:
                            if response.status==200: break
                    except OSError: time.sleep(.1)
                else: raise RuntimeError('candidate readiness timeout')
                cases=oracle.run(db,private['seed']+':'+str(t['instance']))
                if (Counter(c['group'] for c in cases)!=contract()['scoring_counts']
                    or len({(c['group'],c['case']) for c in cases})!=len(cases)):
                    raise mechanism.InfrastructureError('incomplete/duplicate case manifest')
                result['cases']=cases
                result['groups']={g:{'passed':sum(c['passed'] for c in cases if c['group']==g),'total':n}
                                  for g,n in contract()['scoring_counts'].items()}
                result['passed']=all(c['passed'] for c in cases)
                result['outcome']='passed' if result['passed'] else 'acceptance-failed'
            except mechanism.InfrastructureError as error:
                result.update({'outcome':'infrastructure-blocked','error':str(error)})
            except (subprocess.SubprocessError,RuntimeError,OSError) as error:
                result.update({'outcome':'candidate-error','error':str(error)})
            finally:
                if server: stop(server)
    if db: db.close()
    binary.unlink(missing_ok=True)
    # Trusted harness and protected exports must still match after candidate execution.
    load_plan(args.plan);validate_candidate(args,p)
    result['evaluation_seconds']=round(time.monotonic()-start,3)
    save(out/'result.json',result)
    print(json.dumps({'trial':t['id'],'outcome':result['outcome'],'cases':len(result['cases'])}))
    return 0 if result['passed'] else (3 if result['outcome'] in UNMEASURED else 2)

def summarize(args):
    p=load_plan(args.plan);known={t['id']:t for t in p['trials']};records={}
    for directory in args.inputs:
        if not Path(directory).is_dir():raise ValueError('result directory does not exist: '+str(directory))
        for path in Path(directory).rglob('result.json'):
            r=json.loads(path.read_text());ident=r['trial']['id']
            if (r.get('version')!=3 or r['kind']!=p['kind'] or r['plan_sha256']!=digest(args.plan) or r['trial']!=known.get(ident)):
                raise ValueError('foreign trial/cohort')
            if ident in records: raise ValueError('duplicate/conflicting trial: '+ident)
            if r['outcome'] not in OBSERVED|UNMEASURED: raise ValueError('unknown outcome')
            if (r.get('passed') is True)!=(r['outcome']=='passed'): raise ValueError('inconsistent pass flag')
            if r['outcome'] in ('passed','acceptance-failed'):
                cases=r.get('cases',[])
                if (Counter(c['group'] for c in cases)!=contract()['scoring_counts']
                    or len({(c['group'],c['case']) for c in cases})!=len(cases)
                    or bool(all(c['passed'] for c in cases))!=r['passed']):
                    raise ValueError('incomplete/inconsistent acceptance evidence')
                groups={g:{'passed':sum(c['passed'] for c in cases if c['group']==g),'total':n} for g,n in contract()['scoring_counts'].items()}
                if r['groups']!=groups: raise ValueError('group score mismatch')
            records[ident]=r
    rows=[]
    for c in contract()['cells']:
        selected=[t for t in p['trials'] if t['cell']==c['id']]
        values=[records[t['id']] for t in selected if t['id'] in records]
        observed=sum(r['outcome'] in OBSERVED for r in values);passed=sum(r['outcome']=='passed' for r in values)
        rows.append({'cell':c['id'],'planned':len(selected),'observed':observed,'passed':passed,
                     'unmeasured':len(selected)-observed,'first_pass_rate':passed/len(selected) if observed==len(selected) else None,
                     'outcomes':dict(Counter(r['outcome'] for r in values)),
                     'usage':[{'trial':r['trial']['id'],'usage':r.get('generation',{}).get('usage')} for r in values]})
    value={'version':3,'kind':p['kind'],'plan_sha256':digest(args.plan),'rows':rows,
           'note':'No inference of model benefit from synthetic controls. Missing/infrastructure trials remain unmeasured; absent usage is unknown.'}
    if args.output: save(args.output,value)
    print(json.dumps(value,indent=2))

def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='command',required=True)
    s=sub.add_parser('fixtures');s.add_argument('--output',required=True)
    s=sub.add_parser('plan')
    for n in ('base','model','fixtures','output'): s.add_argument('--'+n,required=True)
    s.add_argument('--repetitions',type=int,default=1);s.add_argument('--time-budget',type=int,default=180)
    s.add_argument('--reasoning-effort',choices=('low','medium','high'),default='medium');s.add_argument('--seed',type=int,default=20261114)
    s.add_argument('--engine',choices=('codex','kiro'),default='codex')
    s.add_argument('--synthetic',action='store_true');s.add_argument('--allow-dirty-harness',action='store_true')
    s=sub.add_parser('prepare')
    for n in ('plan','trial','output'):s.add_argument('--'+n,required=True)
    for name in ('export','restore','evaluate'):
        s=sub.add_parser(name)
        for n in ('plan','trial','candidate','manifest'): s.add_argument('--'+n,required=True)
        if name in ('restore','evaluate'): s.add_argument('--receipt',required=True)
        if name=='restore': s.add_argument('--bundle',required=True)
        if name=='evaluate':
            for n in ('fixtures','output'): s.add_argument('--'+n,required=True)
    s=sub.add_parser('generate')
    for n in ('plan','candidate-root','output'): s.add_argument('--'+n,required=True)
    s.add_argument('--trial');s.add_argument('--codex-bin',default='codex');s.add_argument('--synthetic-test-driver')
    s=sub.add_parser('summarize');s.add_argument('--plan',required=True);s.add_argument('--inputs',action='append',required=True);s.add_argument('--output')
    args=parser.parse_args()
    try: return {'fixtures':fixtures,'plan':plan,'prepare':prepare,'export':export_candidate,'generate':generate,'restore':restore,'evaluate':evaluate,'summarize':summarize}[args.command](args) or 0
    except Exception as error:
        print('phase 3 error: '+str(error),file=sys.stderr);return 1

if __name__=='__main__': sys.exit(main())
