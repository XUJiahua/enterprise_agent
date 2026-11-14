#!/usr/bin/env python3
"""Real PostgreSQL candidate/transfer controls with an explicit fake Codex CLI."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import ctl
import controls

def call(*args,expected=0):
    p=subprocess.run([sys.executable,str(ctl.HERE/'ctl.py'),*map(str,args)],capture_output=True,text=True)
    if p.returncode!=expected: raise AssertionError((args,p.returncode,p.stdout,p.stderr))
    return p

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',default='reports/phase3-cohort-controls');a=p.parse_args()
    os.chdir(ctl.ROOT);out=Path(a.out).resolve();out.mkdir(parents=True,exist_ok=False)
    private=out/'public-control-seed.json';plan=out/'plan.json'
    call('fixtures','--output',private)
    call('plan','--base','d8a5c576cc3c293ee2da884f5c618958df407b51','--model','synthetic-v3-control',
         '--fixtures',private,'--output',plan,'--synthetic','--allow-dirty-harness')
    experiment=ctl.load_plan(plan);evaluations=[]
    with tempfile.TemporaryDirectory(prefix='v3-synthetic-candidates-') as temp:
        temp=Path(temp)
        for cell in ('go-manual','go-action'):
            for mode in ('correct','unchanged','reject-all'):
                ident=cell+'--001';name=cell+'-'+mode;candidate=temp/name;manifest=out/'exports'/(name+'.json')
                call('export','--plan',plan,'--trial',ident,'--candidate',candidate,'--manifest',manifest)
                controls.apply(candidate,cell,mode)
                directory=out/'snapshots'/name;directory.mkdir(parents=True)
                args=argparse.Namespace(plan=plan,trial=ident,candidate=candidate,manifest=manifest)
                t=ctl.trials(experiment,ident)[0]
                ctl.freeze_candidate(args,experiment,t,{'status':'completed','usage':None,'synthetic_control':True},directory)
                result_dir=out/'controls'/name
                call('evaluate','--plan',plan,'--trial',ident,'--candidate',candidate,'--manifest',manifest,
                     '--receipt',directory/'receipt.json','--fixtures',private,'--output',result_dir,
                     expected=0 if mode=='correct' else 2)
                result=json.loads((result_dir/'result.json').read_text())
                assert len(result['cases'])==66 and result['groups']['original-regression']=={'passed':16,'total':16},result
                assert result['passed']==(mode=='correct'),result
                evaluations.append({'cell':cell,'mode':mode,'passed':result['passed'],'groups':result['groups']})
        # The fake CLI carries a synthetic answer; no model or provider is contacted.
        replacements={}
        for cell in ('go-manual','go-action'):
            reference=temp/(cell+'-correct')
            replacements[cell]={str(p.relative_to(reference)):p.read_text() for p in (reference/'internal/candidate').glob('*.go')}
        fake=temp/'fake-codex'
        fake.write_text('''#!/usr/bin/env python3
import json,sys
from pathlib import Path
if '--version' in sys.argv: print('synthetic-v3-codex');sys.exit(0)
prompt=sys.stdin.read()
print(json.dumps({'type':'turn.started'}))
if sys.argv[sys.argv.index('--sandbox')+1]=='read-only': text='READY'
else:
 root=Path(sys.argv[sys.argv.index('-C')+1])
 cell='go-action' if (root/'internal/action/approval.go').exists() else 'go-manual'
 replacements='''+repr(replacements)+'''
 for name,value in replacements[cell].items(): (root/name).write_text(value)
 text='Synthetic v3 control only'
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':text}}))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':101,'output_tokens':37}}))
''');fake.chmod(0o755)
        generation=out/'generation-batch'
        call('generate','--plan',plan,'--candidate-root',temp/'generated','--output',generation,'--synthetic-test-driver',fake)
        batch=json.loads((generation/'batch.json').read_text());assert batch['status']=='selected-generated'
        for cell in ('go-manual','go-action'):
            ident=cell+'--001';directory=generation/'generation'/ident;manifest=generation/'exports'/(ident+'.json')
            restored=temp/('restored-'+cell)
            call('restore','--plan',plan,'--trial',ident,'--bundle',directory/'candidate.bundle',
                 '--receipt',directory/'receipt.json','--manifest',manifest,'--candidate',restored)
            call('evaluate','--plan',plan,'--trial',ident,'--candidate',restored,'--manifest',manifest,
                 '--receipt',directory/'receipt.json','--fixtures',private,'--output',out/'evaluations'/ident)
        call('summarize','--plan',plan,'--inputs',out/'evaluations','--output',out/'summary.json')
        summary=json.loads((out/'summary.json').read_text())
        assert all(r['observed']==r['planned']==r['passed']==1 and r['unmeasured']==0 for r in summary['rows'])
        assert all(r['usage'][0]['usage']['input_tokens']==101 for r in summary['rows'])
        # Missing partner remains in the denominator, with no numeric rate.
        call('summarize','--plan',plan,'--inputs',out/'evaluations/go-action--001','--output',out/'partial.json')
        partial=json.loads((out/'partial.json').read_text())
        assert partial['rows'][0]['unmeasured']==1 and partial['rows'][0]['first_pass_rate'] is None
        call('summarize','--plan',plan,'--inputs',out/'evaluations','--inputs',out/'evaluations',expected=1)
        # Standalone kit runs with isolated Python imports and without repository imports.
        kit=temp/'standalone-kit';worker_out=out/'standalone-worker'
        call('prepare','--plan',plan,'--trial','go-action--001','--output',kit)
        assert set(p.name for p in kit.iterdir())=={'candidate','plan.json','manifest.json','job.json','worker.py'}
        process=subprocess.run([sys.executable,'-I',str(kit/'worker.py'),'--output',str(worker_out),
            '--synthetic-test-driver',str(fake)],cwd=kit,capture_output=True,text=True)
        assert process.returncode==0,(process.stdout,process.stderr)
        restored=temp/'worker-restored'
        call('restore','--plan',plan,'--trial','go-action--001','--bundle',worker_out/'candidate.bundle',
             '--receipt',worker_out/'receipt.json','--manifest',kit/'manifest.json','--candidate',restored)
        call('evaluate','--plan',plan,'--trial','go-action--001','--candidate',restored,'--manifest',kit/'manifest.json',
             '--receipt',worker_out/'receipt.json','--fixtures',private,'--output',out/'standalone-evaluation')
        # The third evaluation is a second synthetic transport test, not a new trial.
        # Fourth: the same transport through the Kiro engine with a fake kiro-cli (ACP stream-json).
        kplan=out/'kiro-plan.json'
        call('plan','--base','d8a5c576cc3c293ee2da884f5c618958df407b51','--model','synthetic-v3-control','--engine','kiro',
             '--fixtures',private,'--output',kplan,'--synthetic','--allow-dirty-harness')
        fake_kiro=temp/'fake-kiro'
        fake_kiro.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
if '--version' in sys.argv: print('synthetic-v3-kiro');sys.exit(0)
assert (Path(os.environ['KIRO_HOME'])/'agents/poc-v3-generator.json').exists() and 'DATABASE_URL' not in os.environ
sys.stdin.read();root=Path.cwd()
def emit(t,d): print(json.dumps({'type':t,'data':d}))
emit('runStarted',{'engine':'v3'})
emit('sessionUpdate',{'update':{'sessionUpdate':'session_info_update','_meta':{'kiro':{'kind':'turn_start'}}}})
if '--trust-tools=' in sys.argv: text='READY'
else:
 cell='go-action' if (root/'internal/action/approval.go').exists() else 'go-manual'
 for name,value in '''+repr(replacements)+'''[cell].items(): (root/name).write_text(value)
 text='Synthetic v3 Kiro control only'
emit('sessionUpdate',{'update':{'sessionUpdate':'session_info_update','_meta':{'kiro':{'kind':'session_usage','credits':[{'usage':0.25,'unit':'credit'}]}}}})
emit('runFinished',{'status':'success','stopReason':'end_turn','finalText':text})
''');fake_kiro.chmod(0o755)
        kkit=temp/'kiro-kit';kout=out/'kiro-worker'
        call('prepare','--plan',kplan,'--trial','go-manual--001','--output',kkit)
        process=subprocess.run([sys.executable,'-I',str(kkit/'worker.py'),'--output',str(kout),
            '--synthetic-test-driver',str(fake_kiro)],cwd=kkit,capture_output=True,text=True)
        assert process.returncode==0,(process.stdout,process.stderr)
        receipt=json.loads((kout/'receipt.json').read_text())
        assert receipt['generation']['engine']=='kiro' and receipt['generation']['usage']=={'credits':0.25},receipt
        restored=temp/'kiro-restored'
        call('restore','--plan',kplan,'--trial','go-manual--001','--bundle',kout/'candidate.bundle',
             '--receipt',kout/'receipt.json','--manifest',kkit/'manifest.json','--candidate',restored)
        call('evaluate','--plan',kplan,'--trial','go-manual--001','--candidate',restored,'--manifest',kkit/'manifest.json',
             '--receipt',kout/'receipt.json','--fixtures',private,'--output',out/'kiro-evaluation')
        assert json.loads((out/'kiro-evaluation/result.json').read_text())['passed']
    report={'kind':'synthetic-v3-cohort-control','new_model_generations':0,
            'human_evaluations':6,'reference_passed':2,'negative_controls_detected':4,
            'driver_evaluations':4,'kiro_driver_evaluations':1,'cases_per_evaluation':66,'bundle_replays':4,'standalone_worker_passed':True,
            'partial_denominator_checked':True,'duplicate_rejected':True,'controls':evaluations}
    report['source_parent_commit']=ctl.git(ctl.ROOT,'rev-parse','HEAD')
    report['source_checkout_dirty']=bool(ctl.git(ctl.ROOT,'status','--porcelain'))
    report['source_sha256']={p:ctl.digest(ctl.ROOT/p) for p in ctl.FROZEN+(
        'benchmark/phase3/controls.py','benchmark/phase3/driver_smoke.py','benchmark/tests/test_phase3.py')}
    ctl.save(out/'control.json',report);print(json.dumps(report))

if __name__=='__main__':main()
