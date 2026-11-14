#!/usr/bin/env python3
"""Actual Go/PG controls across all four cells. No model calls or AI scores."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
CLI = HERE / 'ctl.py'
os.chdir(ROOT)
OUT = ROOT / 'reports/phase2-controls'
OUT.mkdir(parents=True, exist_ok=False)
spec = importlib.util.spec_from_file_location('phase2_controls', HERE / 'controls.py')
controls = importlib.util.module_from_spec(spec); spec.loader.exec_module(controls)


def call(*args, expected=0):
    result = subprocess.run(['python3', str(CLI), *map(str, args)], capture_output=True, text=True)
    assert result.returncode == expected, (args, result.stdout, result.stderr)
    return result


private = OUT / 'private.json'
call('fixtures', '--output', private)
plan = OUT / 'plan.json'
call('plan', '--base', 'HEAD', '--model', 'synthetic-human-control', '--fixtures', private,
     '--synthetic', '--allow-dirty-harness', '--output', plan)
experiment = json.loads(plan.read_text())
call('summarize', '--plan', plan, '--inputs', OUT / 'empty', '--output', OUT / 'empty-summary.json')
assert all(row['unmeasured'] == 1 and row['first_pass_rate'] is None for row in json.loads((OUT/'empty-summary.json').read_text())['rows'])
results = []
with tempfile.TemporaryDirectory(prefix='poc-phase2-controls-') as temp:
    temp = Path(temp)
    export_hashes = []
    for mode in ('correct-reference', 'eligible-subset', 'reject-all'):
        for trial in experiment['trials']:
            ident = trial['id']
            candidate = temp / (mode + '-' + ident)
            manifest = OUT / mode / (ident + '-export.json')
            call('export', '--plan', plan, '--trial', ident, '--candidate', candidate, '--manifest', manifest)
            files = json.loads(manifest.read_text())['files']; export_hashes.append(files)
            assert not any(p.startswith(('benchmark/', 'research/', 'tests/', 'docs/', 'db/', '.github/')) for p in files)
            assert not subprocess.check_output(['git','-C',str(candidate),'remote'],text=True).strip()
            assert subprocess.check_output(['git','-C',str(candidate),'rev-list','--count','HEAD'],text=True).strip() == '1'
            controls.apply(candidate, mode)
            subprocess.run(['gofmt','-w','internal/app/server.go'],cwd=candidate,check=True)
            subprocess.run(['git','-C',str(candidate),'add','internal/app'],check=True)
            subprocess.run(['git','-C',str(candidate),'-c','user.name=POC Control','-c','user.email=poc@example.invalid',
                            'commit','-m',mode],check=True,capture_output=True)
            output = OUT / mode / ident
            call('evaluate','--plan',plan,'--trial',ident,'--candidate',candidate,'--manifest',manifest,
                 '--fixtures',private,'--output',output,expected=0 if mode=='correct-reference' else 2)
            result = json.loads((output/'result.json').read_text())
            assert len(result['cases']) == 46, result
            assert result['kind']=='synthetic-smoke'
            assert result['groups']['original-regression'] == {'passed':16,'total':16}
            if mode=='correct-reference':
                assert result['passed'] and all(c['passed'] for c in result['cases'])
            elif mode=='eligible-subset':
                for group in ['authorized-functional','row-authorization','input-validation']:
                    assert result['groups'][group]['passed']==result['groups'][group]['total'],result
                assert result['groups']['batch-all-or-nothing']['passed'] < result['groups']['batch-all-or-nothing']['total']
                assert result['groups']['concurrency']['passed']==0
            else:
                assert result['groups']['authorized-functional']['passed']==0
            results.append({'mode':mode,'cell':trial['cell'],'groups':result['groups'],'passed':result['passed']})
            print(json.dumps({'manual_control':mode,'cell':trial['cell'],'passed':result['passed']}),flush=True)
    assert all(value==export_hashes[0] for value in export_hashes)
# A complete correct cohort can be summarized; duplicate/foreign records must not enter it.
call('summarize','--plan',plan,'--inputs',OUT/'correct-reference','--output',OUT/'positive-summary.json')
value=json.loads((OUT/'positive-summary.json').read_text());assert all(row['observed']==1 and row['passed']==1 for row in value['rows'])
call('summarize','--plan',plan,'--inputs',OUT/'correct-reference','--inputs',OUT/'correct-reference',expected=1)
report={'kind':'synthetic-smoke','model_calls':0,'evaluations':len(results),'cases_per_evaluation':46,
        'correct_reference_passes':sum(r['mode']=='correct-reference' and r['passed'] for r in results),
        'controls':results,'export_same_source_all_cells':True,'evaluator_absent_from_candidate_history':True}
(OUT/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:report[k] for k in ['kind','model_calls','evaluations','cases_per_evaluation','correct_reference_passes']}))
