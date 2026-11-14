#!/usr/bin/env python3
"""Exercise phase 2 generation plumbing with an explicitly synthetic driver."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
os.chdir(ROOT)
OUT=ROOT/'reports/phase2-driver-control'
OUT.mkdir(parents=True,exist_ok=False)
spec=importlib.util.spec_from_file_location('manual_controls',HERE/'controls.py')
control=importlib.util.module_from_spec(spec);spec.loader.exec_module(control)


def call(*args,expected=0):
    p=subprocess.run(['python3',str(HERE/'ctl.py'),*map(str,args)],capture_output=True,text=True)
    assert p.returncode==expected,(p.stdout,p.stderr)
    return p


private=OUT/'private.json';plan=OUT/'plan.json'
call('fixtures','--output',private)
call('plan','--base','HEAD','--model','synthetic-phase2-driver','--fixtures',private,
     '--synthetic','--allow-dirty-harness','--output',plan)
body=control.BODY.replace('__REJECT_ALL__','').replace('__COMPLETE_BEFORE__','if len(eligible)!=len(input.IDs) {return nil,403,nil}').replace('__COMPLETE_AFTER__','if len(approved)!=len(input.IDs) {return nil,403,nil}')
with tempfile.TemporaryDirectory(prefix='poc-phase2-driver-') as temp:
    temp=Path(temp);driver=temp/'fake-codex'
    driver.write_text('''#!/usr/bin/env python3
import sys,json
from pathlib import Path
if '--version' in sys.argv:print('codex-cli synthetic-phase2-test');sys.exit(0)
prompt=sys.stdin.read()
print(json.dumps({'type':'turn.started'}))
if sys.argv[sys.argv.index('--sandbox')+1]=='read-only':text='READY'
else:
 assert '编码前列出' in prompt
 root=Path(sys.argv[sys.argv.index('-C')+1]);p=root/'internal/app/server.go'
 text=p.read_text().replace('"strings"','"strings"\\n"sort"')
 needle='\\tctx := r.Context()\\n'
 p.write_text(text.replace(needle,needle+''' + repr(body) + ''',1))
 text='Synthetic control only'
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':text}}))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':101,'cached_input_tokens':19,'output_tokens':37}}))
''')
    driver.chmod(0o755)
    output=OUT/'run'
    call('run','--plan',plan,'--fixtures',private,'--trial','go-runtime-process--001',
         '--synthetic-test-driver',driver,'--candidate-root',temp/'candidates','--output',output)
    batch=json.loads((output/'batch.json').read_text());assert batch['status']=='selected-completed'
    assert batch['trials'][0]['evaluation_exit']==0 and batch['trials'][0]['usage']['input_tokens']==101
    assert (output/'generation/go-runtime-process--001/candidate.bundle').is_file()
    call('summarize','--plan',plan,'--inputs',output/'evaluations','--output',OUT/'summary.json')
    summary=json.loads((OUT/'summary.json').read_text())
    assert sum(r['observed'] for r in summary['rows'])==1 and sum(r['unmeasured'] for r in summary['rows'])==3
    model=json.loads(plan.read_text());model['kind']='model-trial';(OUT/'model-plan.json').write_text(json.dumps(model))
    call('run','--plan',OUT/'model-plan.json','--fixtures',private,'--synthetic-test-driver',driver,
         '--output',OUT/'invalid-mix',expected=1)
report={'kind':'synthetic-smoke','model_calls':0,'driver_evaluations':1,'passed':True}
(OUT/'control.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
