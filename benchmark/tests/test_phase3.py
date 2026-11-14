import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('phase3_ctl_tests',ROOT/'benchmark/phase3/ctl.py')
ctl=importlib.util.module_from_spec(spec);spec.loader.exec_module(ctl)

class Phase3Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='phase3-unit-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.private=self.root/'private.json';self.plan=self.root/'plan.json'
        ctl.fixtures(argparse.Namespace(output=self.private))
        ctl.plan(argparse.Namespace(base='d8a5c576cc3c293ee2da884f5c618958df407b51',model='synthetic',fixtures=self.private,
           output=self.plan,repetitions=1,time_budget=180,reasoning_effort='medium',seed=42,synthetic=True,allow_dirty_harness=True))

    def rewrite(self,fn):
        value=json.loads(self.plan.read_text());fn(value);self.plan.write_text(json.dumps(value))

    def export(self,cell='go-action'):
        args=argparse.Namespace(plan=self.plan,trial=cell+'--001',candidate=self.root/cell,manifest=self.root/(cell+'.json'))
        ctl.export_candidate(args);return args

    def commit(self,candidate):
        ctl.process(['git','add','.'],cwd=candidate)
        ctl.process(['git','-c','user.name=Unit','-c','user.email=unit@example.invalid','commit','--allow-empty','-m','unit'],cwd=candidate)

    def test_shared_start_differs_only_by_capability(self):
        p=ctl.load_plan(self.plan);a,b=ctl.contract()['cells'];fa,fb=ctl.files(p,a),ctl.files(p,b)
        self.assertEqual(set(fb)-set(fa),{ctl.CAPABILITY})
        self.assertTrue(all(fb[k]==v for k,v in fa.items()))
        self.assertNotIn(json.loads(self.private.read_text())['seed'],self.plan.read_text())

    def test_export_has_no_oracle_history_or_remote(self):
        a=self.export();p=ctl.load_plan(self.plan);ctl.validate_candidate(a,p)
        self.assertEqual(ctl.git(a.candidate,'rev-list','--count','HEAD'),'1')
        self.assertFalse(any((a.candidate/n).exists() for n in ('benchmark','tests','docs','.github')))
        self.assertEqual(ctl.git(a.candidate,'remote'),'')

    def test_manual_generation_kit_contains_no_oracle_or_action_source(self):
        kit=self.root/'manual-kit'
        ctl.prepare(argparse.Namespace(plan=self.plan,trial='go-manual--001',output=kit))
        names={str(p.relative_to(kit)) for p in kit.rglob('*') if p.is_file()}
        self.assertFalse(any('oracle' in n or n.endswith('private.json') for n in names))
        self.assertFalse((kit/'candidate'/ctl.CAPABILITY).exists())
        self.assertNotIn(json.loads(self.private.read_text())['seed'],(kit/'plan.json').read_text())
        self.assertNotIn('import ctl',(kit/'worker.py').read_text())

    def test_protected_kernel_and_adapter_changes_rejected(self):
        a=self.export();path=a.candidate/ctl.CAPABILITY;path.write_text(path.read_text()+'\n// edited\n');self.commit(a.candidate)
        with self.assertRaisesRegex(ValueError,'protected'): ctl.validate_candidate(a,ctl.load_plan(self.plan))

    def test_symlink_in_editable_scope_rejected(self):
        a=self.export();(a.candidate/'internal/candidate/link.go').symlink_to('/etc/passwd');self.commit(a.candidate)
        with self.assertRaisesRegex(ValueError,'regular'): ctl.validate_candidate(a,ctl.load_plan(self.plan))

    def test_manifest_drift_prompt_tamper_and_missing_partner_rejected(self):
        original=self.plan.read_text()
        for fn,pattern in [(lambda p:p['frozen'].pop('benchmark/phase3/db.mjs'),'manifest'),
                           (lambda p:p['trials'].pop(),'block'),
                           (lambda p:p['trials'][0].update(prompt='altered'),'prompt'),
                           (lambda p:p['exports']['go-action'].pop(ctl.CAPABILITY),'export')]:
            self.plan.write_text(original);self.rewrite(fn)
            with self.assertRaisesRegex(ValueError,pattern): ctl.load_plan(self.plan)

    def test_infrastructure_failure_retains_denominator(self):
        p=ctl.load_plan(self.plan);t=p['trials'][0];directory=self.root/'results'
        ctl.save(directory/'one/result.json',{'version':3,'kind':p['kind'],'plan_sha256':ctl.digest(self.plan),
           'trial':t,'outcome':'infrastructure-blocked','passed':False,'generation':{'usage':None}})
        target=self.root/'summary.json'
        ctl.summarize(argparse.Namespace(plan=self.plan,inputs=[directory],output=target))
        rows=json.loads(target.read_text())['rows']
        self.assertEqual(sum(r['unmeasured'] for r in rows),2)
        self.assertTrue(all(r['first_pass_rate'] is None for r in rows))
        with self.assertRaisesRegex(ValueError,'duplicate'):
            ctl.summarize(argparse.Namespace(plan=self.plan,inputs=[directory,directory],output=None))

    def test_false_pass_cannot_omit_cases(self):
        p=ctl.load_plan(self.plan);t=p['trials'][0];directory=self.root/'results'
        ctl.save(directory/'result.json',{'version':3,'kind':p['kind'],'plan_sha256':ctl.digest(self.plan),
           'trial':t,'outcome':'passed','passed':True,'cases':[],'groups':{}})
        with self.assertRaisesRegex(ValueError,'evidence'):
            ctl.summarize(argparse.Namespace(plan=self.plan,inputs=[directory],output=None))

    def test_bundle_round_trip_and_tamper(self):
        a=self.export();p=ctl.load_plan(self.plan);directory=self.root/'snapshot';directory.mkdir()
        ctl.freeze_candidate(a,p,ctl.trials(p,a.trial)[0],{'status':'completed','usage':None},directory)
        restored=argparse.Namespace(**{**vars(a),'candidate':self.root/'restored','receipt':directory/'receipt.json','bundle':directory/'candidate.bundle'})
        ctl.restore(restored);ctl.validate_candidate(restored,p)
        with (directory/'candidate.bundle').open('ab') as stream: stream.write(b'tampered')
        restored.candidate=self.root/'other'
        with self.assertRaisesRegex(ValueError,'checksum'): ctl.restore(restored)

    def test_synthetic_driver_cannot_run_real_plan(self):
        self.rewrite(lambda p:p.update(kind='model-trial'))
        with self.assertRaisesRegex(ValueError,'mismatch'):
            ctl.generate(argparse.Namespace(plan=self.plan,synthetic_test_driver='fake',trial=None))

if __name__=='__main__':unittest.main()
