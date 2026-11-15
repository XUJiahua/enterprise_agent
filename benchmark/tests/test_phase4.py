import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
ctl=load('phase4_ctl_tests','benchmark/phase4/ctl.py')
controls=load('phase4_controls_tests','benchmark/phase4/controls.py')

class Phase4Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='phase4-unit-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.private=self.root/'private.json';self.plan=self.root/'plan.json'
        ctl.fixtures(argparse.Namespace(output=self.private))
        ctl.plan(argparse.Namespace(base='d8a5c576cc3c293ee2da884f5c618958df407b51',model='synthetic',fixtures=self.private,
           output=self.plan,repetitions=2,time_budget=600,reasoning_effort='medium',seed=7,synthetic=True,
           allow_dirty_harness=True,engine='kiro'))
        self.p=ctl.load_plan(self.plan);self.cells={c['id']:c for c in ctl.contract()['cells']}

    def rewrite(self,fn):
        value=json.loads(self.plan.read_text());fn(value);self.plan.write_text(json.dumps(value))

    def export(self,cell):
        args=argparse.Namespace(plan=self.plan,trial=cell+'--001',candidate=self.root/cell,manifest=self.root/(cell+'.json'))
        ctl.export_candidate(args);return args

    def test_cells_share_everything_but_existing_approval_and_kernel(self):
        manual,action=(ctl.files(self.p,self.cells[c]) for c in ('go-manual','go-action'))
        self.assertEqual(set(action)-set(manual),{'internal/action/action.go'})
        differing={k for k in manual if manual[k]!=action[k]}
        self.assertEqual(differing,{'internal/candidate/approval.go'})
        self.assertIn(b'bulk-reject.v4',manual['SCHEMA.sql'])

    def test_task_and_prompts_do_not_state_implicit_conventions(self):
        forbidden=ctl.contract()['forbidden_in_task']
        for text in [ctl.contract()['requirements']]+[t['prompt'] for t in self.p['trials']]:
            for token in forbidden: self.assertNotIn(token.lower(),text.lower(),token)

    def test_every_convention_has_exported_code_evidence_in_both_cells(self):
        conventions=ctl.contract()['conventions']
        self.assertEqual(set(conventions),{g for g,v in ctl.contract()['groups'].items() if v['kind']=='implicit'})
        for name,value in conventions.items():
            for cell in self.cells:
                exported=ctl.files(self.p,self.cells[cell])
                self.assertTrue(value['sources'][cell],(name,cell))
                for path,needle in value['sources'][cell]:
                    self.assertIn(needle.encode(),exported[path],(name,cell,path))

    def test_kit_has_no_oracle_seed_controls_or_other_cell(self):
        kit=self.root/'kit';ctl.prepare(argparse.Namespace(plan=self.plan,trial='go-manual--001',output=kit))
        names={str(p.relative_to(kit)) for p in kit.rglob('*') if p.is_file() and '.git/' not in str(p)}
        self.assertFalse(any(w in n for n in names for w in ('oracle','controls','db.mjs','private')))
        self.assertFalse((kit/'candidate/internal/action').exists())
        self.assertNotIn(json.loads(self.private.read_text())['seed'],(kit/'plan.json').read_text())
        job=json.loads((kit/'job.json').read_text())
        self.assertEqual((job['engine'],job['worker_sha256']),('kiro',self.p['frozen'][ctl.WORKER]))

    def test_protected_ledger_kernel_and_adapter_changes_rejected(self):
        for cell,path in [('go-action','internal/action/action.go'),('go-manual','internal/ledger/ledger.go'),
                          ('go-manual','internal/protocol/protocol.go')]:
            a=self.export(cell);target=a.candidate/path;target.write_text(target.read_text()+'\n// edited\n')
            ctl.process(['git','add','.'],cwd=a.candidate)
            ctl.process(['git','-c','user.name=U','-c','user.email=u@example.invalid','commit','-m','u'],cwd=a.candidate)
            with self.assertRaisesRegex(ValueError,'protected'): ctl.validate_candidate(a,self.p)
            import shutil;shutil.rmtree(a.candidate);a.manifest.unlink()

    def test_plan_tamper_rejected(self):
        original=self.plan.read_text()
        for fn,pattern in [(lambda p:p['frozen'].pop('benchmark/phase4/oracle.py'),'manifest'),
                           (lambda p:p['trials'].pop(),'block'),
                           (lambda p:p['trials'][0].update(prompt='altered'),'prompt'),
                           (lambda p:p.update(version=3),'v4'),
                           (lambda p:p['exports']['go-action'].pop('internal/action/action.go'),'export')]:
            self.plan.write_text(original);self.rewrite(fn)
            with self.assertRaisesRegex(ValueError,pattern): ctl.load_plan(self.plan)

    def cases(self,fail=None):
        return [{'group':g,'case':g+str(i),'passed':g!=fail} for g,n in ctl.counts().items() for i in range(n)]

    def test_score_separates_explicit_and_implicit(self):
        s=ctl.score(self.cases('implicit-request-hash'))
        self.assertTrue(s['explicit_passed']);self.assertFalse(s['passed'])
        self.assertEqual([g for g,ok in s['conventions'].items() if not ok],['implicit-request-hash'])
        self.assertEqual(s['explicit']['total']+s['implicit']['total'],108)
        with self.assertRaisesRegex(ValueError,'incomplete'): ctl.score(self.cases()[1:])

    def test_summary_keeps_denominator_and_rejects_duplicates_or_forged_scores(self):
        t=self.p['trials'][0];d=self.root/'results'
        ctl.save(d/'a/result.json',{'version':3,'kind':self.p['kind'],'plan_sha256':ctl.digest(self.plan),'trial':t,
            'outcome':'provider-error','passed':False,'generation':{'usage':None}})
        out=self.root/'summary.json';ctl.summarize(argparse.Namespace(plan=self.plan,inputs=[d],output=out))
        rows=json.loads(out.read_text())['rows']
        self.assertEqual(sum(r['unmeasured'] for r in rows),4)
        self.assertTrue(all(r['first_pass_rate'] is None and r['explicit_pass_rate'] is None for r in rows))
        with self.assertRaisesRegex(ValueError,'duplicate'):
            ctl.summarize(argparse.Namespace(plan=self.plan,inputs=[d,d],output=None))
        forged=self.root/'forged';cases=self.cases('implicit-audit')
        ctl.save(forged/'result.json',{'version':4,'kind':self.p['kind'],'plan_sha256':ctl.digest(self.plan),'trial':t,
            'outcome':'acceptance-failed','passed':False,'cases':cases,**{**ctl.score(cases),'explicit_passed':False}})
        with self.assertRaisesRegex(ValueError,'score'):
            ctl.summarize(argparse.Namespace(plan=self.plan,inputs=[forged],output=None))

    def test_each_implicit_control_changes_reference(self):
        reference=controls.reject_source('correct')
        for cell,modes in controls.MODES.items():
            for mode,(violated,_) in modes.items():
                if violated: self.assertNotEqual(controls.reject_source(mode),reference,mode)
        self.assertEqual(sum(bool(v) for m in controls.MODES.values() for v,_ in m.values()),8)

    def test_v4b_changes_only_action_capability_line(self):
        manual,action=ctl.contract()['cells']
        if manual['capability']: manual,action=action,manual
        self.assertEqual(ctl.prompt(manual,'v4b'),ctl.prompt(manual,'v4'))
        a,b=ctl.prompt(action,'v4').splitlines(),ctl.prompt(action,'v4b').splitlines()
        diff=[(x,y) for x,y in zip(a,b) if x!=y]
        self.assertEqual(len(a),len(b));self.assertEqual(len(diff),1)
        self.assertEqual(diff[0][1],diff[0][0]+' '+ctl.EXTENSION_HINT)
        for word in ctl.contract()['forbidden_in_task']: self.assertNotIn(word.lower(),ctl.EXTENSION_HINT.lower())

if __name__=='__main__':unittest.main()
