#!/usr/bin/env python3
"""Real PostgreSQL v6b controls: human candidates per cell plus a fake-Kiro worker transport.

No model or provider is contacted; nothing here is a model sample.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import importlib.util

HERE=Path(__file__).resolve().parent
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);sys.modules[name]=value;spec.loader.exec_module(value);return value
ctl=load('phase6b_ctl',HERE/'ctl.py').ctl
controls=load('phase6b_controls',HERE/'controls.py')

BASE='d8a5c576cc3c293ee2da884f5c618958df407b51'

def call(*args,expected=0):
    p=subprocess.run([sys.executable,str(HERE/'ctl.py'),*map(str,args)],capture_output=True,text=True,timeout=900)
    if p.returncode!=expected: raise AssertionError((args,p.returncode,p.stdout[-2000:],p.stderr[-2000:]))
    return p

def check(result,cell,mode):
    violated,explicit=controls.MODES[cell][mode]
    assert len(result['cases'])==sum(ctl.counts().values()),result.get('error')
    for g in ('reopen-regression','reject-regression','approve-regression','original-regression'):
        assert result['groups'][g]['passed']==result['groups'][g]['total'],(g,result['groups'][g])
    if mode=='correct': assert result['passed'],result['groups']
    elif violated:
        # Exactly the targeted convention fails; the explicit contract and the others hold.
        assert result['explicit_passed'],result['groups']
        assert {g for g,ok in result['conventions'].items() if not ok}=={violated},result['conventions']
    else: assert not explicit and not result['explicit_passed'],result['groups']

FAKE_KIRO='''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
if '--version' in sys.argv: print('synthetic-v6b-kiro');sys.exit(0)
assert 'DATABASE_URL' not in os.environ and 'ADMIN_DATABASE_URL' not in os.environ
sys.stdin.read();root=Path.cwd()
def emit(t,d): print(json.dumps({'type':t,'data':d}))
emit('runStarted',{'engine':'v3'})
emit('sessionUpdate',{'update':{'sessionUpdate':'session_info_update','_meta':{'kiro':{'kind':'turn_start'}}}})
if '--trust-tools=' in sys.argv: text='READY'
else:
 cell='go-action' if (root/'internal/action/action.go').exists() else 'go-manual'
 for name,value in REPLACEMENTS[cell].items(): (root/name).write_text(value)
 text='Synthetic v6b Kiro control only'
emit('sessionUpdate',{'update':{'sessionUpdate':'session_info_update','_meta':{'kiro':{'kind':'session_usage','credits':[{'usage':0.25,'unit':'credit'}]}}}})
emit('runFinished',{'status':'success','stopReason':'end_turn','finalText':text})
'''

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',default='reports/phase6b-controls');a=p.parse_args()
    os.chdir(ctl.ROOT);out=Path(a.out).resolve();out.mkdir(parents=True,exist_ok=False)
    private=out/'public-control-seed.json';plan=out/'plan.json'
    call('fixtures','--output',private)
    call('plan','--base',BASE,'--model','synthetic-v6b-control','--engine','kiro','--fixtures',private,'--output',plan,
         '--synthetic','--allow-dirty-harness')
    experiment=ctl.load_plan(plan);evaluations=[]
    with tempfile.TemporaryDirectory(prefix='v6b-controls-') as temp:
        temp=Path(temp)
        for cell,modes in controls.MODES.items():
            for mode in modes:
                ident=cell+'--001';name=cell+'-'+mode;candidate=temp/name;manifest=out/'exports'/(name+'.json')
                call('export','--plan',plan,'--trial',ident,'--candidate',candidate,'--manifest',manifest)
                controls.apply(candidate,cell,mode)
                directory=out/'snapshots'/name;directory.mkdir(parents=True)
                args=argparse.Namespace(plan=plan,trial=ident,candidate=candidate,manifest=manifest)
                ctl.freeze_candidate(args,experiment,ctl.trials(experiment,ident)[0],
                                     {'status':'completed','usage':None,'synthetic_control':True},directory)
                result_dir=out/'controls'/name
                call('evaluate','--plan',plan,'--trial',ident,'--candidate',candidate,'--manifest',manifest,
                     '--receipt',directory/'receipt.json','--fixtures',private,'--output',result_dir,
                     expected=0 if mode=='correct' else 2)
                result=json.loads((result_dir/'result.json').read_text());check(result,cell,mode)
                evaluations.append({'cell':cell,'mode':mode,'violates':controls.MODES[cell][mode][0],
                    'passed':result['passed'],'explicit':result['explicit'],'implicit':result['implicit'],
                    'conventions':result['conventions'],'groups':{g:v['passed'] for g,v in result['groups'].items()}})
                print(json.dumps(evaluations[-1]),flush=True)
        # Handler panic must be a candidate error (not infrastructure); a stray go-build `server` is ignored, not out of scope.
        candidate=temp/'go-manual-panic';manifest=out/'exports'/'go-manual-panic.json'
        call('export','--plan',plan,'--trial','go-manual--001','--candidate',candidate,'--manifest',manifest)
        approval=candidate/'internal/candidate/approval.go';text=approval.read_text();route=controls.ROUTES['go-manual']
        approval.write_text(text.replace(route,'\treassign := func(ctx context.Context, u app.Identity, key string, ids []string, assignee string) (protocol.AssignResult, bool, error) {\n'
            '\t\tvar m map[string]int\n\t\tm["boom"] = 1\n\t\treturn protocol.AssignResult{}, false, nil\n\t}\n'
            '\treturn protocol.Assign("/purchases/bulk-reassign", reassign, '+route.removeprefix('\treturn ').removesuffix(', nil')+'), nil'))
        (candidate/'server').write_bytes(b'stray build output')
        directory=out/'snapshots'/'go-manual-panic';directory.mkdir(parents=True)
        ctl.freeze_candidate(argparse.Namespace(plan=plan,trial='go-manual--001',candidate=candidate,manifest=manifest),experiment,
                             ctl.trials(experiment,'go-manual--001')[0],{'status':'completed','usage':None,'synthetic_control':True},directory)
        call('evaluate','--plan',plan,'--trial','go-manual--001','--candidate',candidate,'--manifest',manifest,
             '--receipt',directory/'receipt.json','--fixtures',private,'--output',out/'controls'/'go-manual-panic',expected=2)
        panic=json.loads((out/'controls/go-manual-panic/result.json').read_text())
        assert panic['outcome']=='candidate-error',panic
        crash_control={'cell':'go-manual','mode':'handler-panic','outcome':panic['outcome'],'stray_server_ignored':True}
        print(json.dumps(crash_control),flush=True)
        # Transport: prepare -> standalone phase 3 worker (fake kiro-cli) -> restore -> evaluate -> summarize.
        replacements={}
        for cell in controls.MODES:
            reference=temp/(cell+'-correct')
            replacements[cell]={str(p.relative_to(reference)):p.read_text() for p in (reference/'internal/candidate').glob('*.go')}
        fake=temp/'fake-kiro';fake.write_text(FAKE_KIRO.replace('REPLACEMENTS',repr(replacements)));fake.chmod(0o755)
        for cell in controls.MODES:
            ident=cell+'--001';kit=temp/('kit-'+cell);worker_out=out/'worker'/ident
            call('prepare','--plan',plan,'--trial',ident,'--output',kit)
            assert {p.name for p in kit.iterdir()}=={'candidate','plan.json','manifest.json','job.json','worker.py'}
            assert not (kit/'candidate/benchmark').exists() and json.loads(private.read_text())['seed'] not in (kit/'plan.json').read_text()
            proc=subprocess.run([sys.executable,'-I',str(kit/'worker.py'),'--output',str(worker_out),'--synthetic-test-driver',str(fake)],
                                cwd=kit,capture_output=True,text=True,timeout=300,
                                env={k:v for k,v in os.environ.items() if k not in ('DATABASE_URL','ADMIN_DATABASE_URL','POC_DB_RESET')})
            assert proc.returncode==0,(proc.stdout,proc.stderr)
            receipt=json.loads((worker_out/'receipt.json').read_text())
            assert receipt['generation']['engine']=='kiro' and receipt['generation']['usage']=={'credits':0.25},receipt
            restored=temp/('restored-'+cell)
            call('restore','--plan',plan,'--trial',ident,'--bundle',worker_out/'candidate.bundle','--receipt',worker_out/'receipt.json',
                 '--manifest',kit/'manifest.json','--candidate',restored)
            call('evaluate','--plan',plan,'--trial',ident,'--candidate',restored,'--manifest',kit/'manifest.json',
                 '--receipt',worker_out/'receipt.json','--fixtures',private,'--output',out/'evaluations'/ident)
        call('summarize','--plan',plan,'--inputs',out/'evaluations','--output',out/'summary.json')
        summary=json.loads((out/'summary.json').read_text())
        assert all(r['planned']==r['observed']==r['passed_all']==r['passed_explicit']==1 and r['unmeasured']==0
                   and set(r['conventions_followed'].values())=={1} for r in summary['rows']),summary
        call('summarize','--plan',plan,'--inputs',out/'evaluations/go-action--001','--output',out/'partial.json')
        partial=json.loads((out/'partial.json').read_text())
        manual=next(r for r in partial['rows'] if r['cell']=='go-manual')
        assert manual['unmeasured']==1 and manual['first_pass_rate'] is None and manual['explicit_pass_rate'] is None
        call('summarize','--plan',plan,'--inputs',out/'evaluations','--inputs',out/'evaluations',expected=1)
    report={'kind':'synthetic-v6b-bulk-reassign-control','new_model_generations':0,
            'human_evaluations':len(evaluations),'worker_transport_evaluations':2,
            'cases_per_evaluation':sum(ctl.counts().values()),
            'explicit_cases':sum(v['count'] for v in ctl.contract()['groups'].values() if v['kind']=='explicit'),
            'implicit_cases':sum(v['count'] for v in ctl.contract()['groups'].values() if v['kind']=='implicit'),
            'reference_passed':sum(e['mode']=='correct' and e['passed'] for e in evaluations),
            'implicit_violations_isolated':sum(bool(e['violates']) for e in evaluations),
            'explicit_violations_detected':sum(not e['violates'] and e['mode']!='correct' for e in evaluations),
            'crash_control':crash_control,'partial_denominator_checked':True,'duplicate_rejected':True,'controls':evaluations,
            'source_parent_commit':ctl.git(ctl.ROOT,'rev-parse','HEAD'),
            'source_checkout_dirty':bool(ctl.git(ctl.ROOT,'status','--porcelain')),
            'source_sha256':{p:ctl.digest(ctl.ROOT/p) for p in ctl.FROZEN+('benchmark/phase6b/controls.py','benchmark/phase6b/driver_smoke.py')}}
    ctl.save(out/'control.json',report);print(json.dumps({k:v for k,v in report.items() if k not in ('controls','source_sha256')}))

if __name__=='__main__':main()
