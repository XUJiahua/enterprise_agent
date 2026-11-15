import argparse
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
ctl=load('phase6b_ctl_tests','benchmark/phase6b/ctl.py').ctl
controls=load('phase6b_controls_tests','benchmark/phase6b/controls.py')
oracle=load('phase6b_oracle_tests','benchmark/phase6b/oracle.py')
BASE='d8a5c576cc3c293ee2da884f5c618958df407b51'

class Phase6bTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='phase6b-unit-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.private=self.root/'private.json';self.plan=self.root/'plan.json'
        ctl.fixtures(argparse.Namespace(output=self.private))
        ctl.plan(argparse.Namespace(base=BASE,model='synthetic',fixtures=self.private,output=self.plan,repetitions=2,time_budget=600,
           reasoning_effort='medium',seed=7,synthetic=True,allow_dirty_harness=True,engine='kiro'))
        self.p=ctl.load_plan(self.plan);self.cells={c['id']:c for c in ctl.contract()['cells']}
        self.exports={c:ctl.files(self.p,self.cells[c]) for c in self.cells}

    def export(self,cell):
        args=argparse.Namespace(plan=self.plan,trial=cell+'--001',candidate=self.root/cell,manifest=self.root/(cell+'.json'))
        ctl.export_candidate(args);return args

    def test_contract_is_v6b_not_v5(self):
        self.assertEqual(ctl.contract()['task'],'add-bulk-reassign-following-scattered-conventions-v6b')
        self.assertEqual(ctl.HERE,ROOT/'benchmark/phase6b')
        self.assertEqual(sum(ctl.counts().values()),190)

    def test_cells_share_everything_but_candidate_package_and_kernel(self):
        manual,action=self.exports['go-manual'],self.exports['go-action']
        self.assertEqual(set(action)-set(manual),{'internal/action/action.go','internal/candidate/operations.go'})
        self.assertEqual(set(manual)-set(action),{'internal/candidate/reject.go','internal/candidate/reopen.go'})
        self.assertEqual({k for k in manual if k in action and manual[k]!=action[k]},{'internal/candidate/approval.go'})
        self.assertIn(b'bulk-reassign.v6',manual['SCHEMA.sql']);self.assertIn(b'/server\n',manual['.gitignore'])
        self.assertIn(b'func Assign(',manual['internal/protocol/protocol.go'])

    def test_task_and_prompts_do_not_state_implicit_conventions(self):
        for text in [ctl.contract()['requirements']]+[t['prompt'] for t in self.p['trials']]:
            for token in ctl.contract()['forbidden_in_task']: self.assertNotIn(token.lower(),text.lower(),token)

    def test_every_convention_is_scattered_over_exported_files(self):
        conventions=ctl.contract()['conventions']
        self.assertEqual(set(conventions),{g for g,v in ctl.contract()['groups'].items() if v['kind']=='implicit'})
        self.assertIn(len(conventions),(3,4))
        for name,value in conventions.items():
            for cell,exported in self.exports.items():
                sources=value['sources'][cell]
                for path,needle in sources: self.assertIn(needle.encode(),exported[path],(name,cell,path))
                self.assertGreaterEqual(len({p for p,_ in sources}),2,(name,cell))
                self.assertTrue(any(not p.startswith('internal/candidate/') for p,_ in sources),(name,cell))

    def test_every_convention_declares_action_enforcement(self):
        conventions=ctl.contract()['conventions']
        values=[v.get('action_enforcement') for v in conventions.values()]
        self.assertTrue(all(v in ('kernel','declared') for v in values),values)
        self.assertGreaterEqual(values.count('kernel'),2);self.assertGreaterEqual(values.count('declared'),1)
        # A declared convention's field is required by the kernel (fail-loud), and the author must fill it.
        kernel=self.exports['go-action']['internal/action/action.go'].decode()
        self.assertIn('Event is required (use action.NoEvent)',kernel)

    def test_uncommented_convention_has_no_comment_hint(self):
        conventions=ctl.contract()['conventions']
        self.assertTrue(any(not v['commented'] for v in conventions.values()))
        words=ctl.contract()['forbidden_comment_words']
        for cell,exported in self.exports.items():
            for path,data in exported.items():
                comments=' '.join(re.findall(r'(?://|--)(.*)',data.decode())).lower()
                for word in words: self.assertNotIn(word,comments,(cell,path,word))

    def test_no_copyable_reassign_example_in_candidate_package(self):
        for cell,exported in self.exports.items():
            for path,data in exported.items():
                if path.startswith('internal/candidate/'): self.assertNotIn(b'assign',data.lower(),(cell,path))
        kernel=self.exports['go-action']['internal/action/action.go'].decode()
        self.assertIsNone(re.search(r'=\s*(Operation|Update)\{',kernel))
        self.assertIn(b'action.Operation{',self.exports['go-action']['internal/candidate/operations.go'])

    def test_plan_freezes_every_harness_file(self):
        harness={str(p.relative_to(ROOT)) for p in (ROOT/'benchmark/phase6b').rglob('*') if p.is_file() and '__pycache__' not in p.parts}
        self.assertEqual(harness-set(self.p['frozen']),{'benchmark/phase6b/controls.py','benchmark/phase6b/driver_smoke.py'})
        for path in ('benchmark/phase5/ctl.py','benchmark/phase5/oracle.py','benchmark/phase4/oracle.py',ctl.WORKER):
            self.assertIn(path,self.p['frozen'])

    def test_kit_has_no_oracle_seed_controls_or_other_cell(self):
        kit=self.root/'kit';ctl.prepare(argparse.Namespace(plan=self.plan,trial='go-manual--001',output=kit))
        self.assertEqual({p.name for p in kit.iterdir()},{'candidate','plan.json','manifest.json','job.json','worker.py'})
        names={str(p.relative_to(kit)) for p in kit.rglob('*') if p.is_file() and '.git/' not in str(p)}
        self.assertFalse(any(w in n for n in names for w in ('oracle','controls','db.mjs','private')))
        self.assertFalse((kit/'candidate/internal/action').exists())
        self.assertNotIn(json.loads(self.private.read_text())['seed'],(kit/'plan.json').read_text())
        self.assertEqual((kit/'worker.py').read_bytes(),(ROOT/'benchmark/phase3/generation_worker.py').read_bytes())
        job=json.loads((kit/'job.json').read_text())
        self.assertEqual((job['engine'],job['worker_sha256']),('kiro',self.p['frozen'][ctl.WORKER]))

    def test_protected_kernel_infrastructure_and_adapter_changes_rejected(self):
        for cell,path in [('go-action','internal/action/action.go'),('go-manual','internal/ledger/reconcile.go'),
                          ('go-manual','internal/notify/notify.go'),('go-action','internal/protocol/protocol.go')]:
            a=self.export(cell);target=a.candidate/path;target.write_text(target.read_text()+'\n// edited\n')
            ctl.process(['git','add','.'],cwd=a.candidate)
            ctl.process(['git','-c','user.name=U','-c','user.email=u@example.invalid','commit','-m','u'],cwd=a.candidate)
            with self.assertRaisesRegex(ValueError,'protected'): ctl.validate_candidate(a,self.p)
            shutil.rmtree(a.candidate);a.manifest.unlink()

    def test_plan_tamper_rejected(self):
        original=self.plan.read_text()
        for fn,pattern in [(lambda p:p['frozen'].pop('benchmark/phase6b/oracle.py'),'manifest'),
                           (lambda p:p['frozen'].update({'benchmark/phase6b/cohort.json':'0'*64}),'drift'),
                           (lambda p:p['trials'].pop(),'block'),
                           (lambda p:p['trials'][0].update(prompt='altered'),'prompt'),
                           (lambda p:p['exports']['go-manual'].pop('internal/candidate/reopen.go'),'export')]:
            value=json.loads(original);fn(value);self.plan.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,pattern): ctl.load_plan(self.plan)

    def test_cli_is_drivable_by_run_kiro_cohort(self):
        script=(ROOT/'benchmark/phase3/run_kiro_cohort.sh').read_text()
        self.assertIn('CTL=${CTL:-',script)
        for command in ('restore','evaluate'): self.assertIn('"$CTL" '+command,script)
        out=subprocess.run([sys.executable,str(ROOT/'benchmark/phase6b/ctl.py'),'summarize','--plan',str(self.plan),
                            '--inputs',str(self.root)],capture_output=True,text=True,timeout=60)
        self.assertEqual(out.returncode,0,out.stderr)
        self.assertEqual({r['cell'] for r in json.loads(out.stdout)['rows']},{'go-manual','go-action'})

    def cases(self,fail=None):
        return [{'group':g,'case':g+str(i),'passed':g!=fail} for g,n in ctl.counts().items() for i in range(n)]

    def test_score_separates_explicit_and_implicit(self):
        s=ctl.score(self.cases('implicit-history'))
        self.assertTrue(s['explicit_passed']);self.assertFalse(s['passed'])
        self.assertEqual([g for g,ok in s['conventions'].items() if not ok],['implicit-history'])
        self.assertEqual((s['explicit']['total'],s['implicit']['total']),(178,12))
        with self.assertRaisesRegex(ValueError,'incomplete'): ctl.score(self.cases()[1:])

    def test_each_implicit_control_changes_reference(self):
        reference=controls.reassign_source('correct')
        for cell,modes in controls.MODES.items():
            for mode,(violated,_) in modes.items():
                if violated and not (cell=='go-action' and mode in controls.KERNEL):
                    self.assertNotEqual(controls.reassign_source(mode),reference,mode)
        conventions=set(ctl.contract()['conventions'])
        for cell,modes in controls.MODES.items():
            self.assertEqual({v for v,_ in modes.values() if v},conventions,cell)
            self.assertGreaterEqual(sum(not v and not e for v,e in modes.values()),2,cell)
        # The declared convention is violated through the declaration itself in go-action.
        self.assertEqual(controls.MODES['go-action']['no-event'],('implicit-outbox',True))
        self.assertEqual(controls.MODES['go-action']['omit-event'],(None,False))

    def test_history_chain_ignores_timestamps(self):
        self.assertEqual(len(oracle.chains_of([('hana','bob'),('bob','hana')],'bob','bob')),1)
        self.assertEqual(len(oracle.chains_of([('bob','bob'),('bob','hana')],'bob','hana')),1)
        self.assertEqual(len(oracle.chains_of([('bob','hana'),('bob','carol')],'bob','carol')),0)  # lost update
        self.assertEqual(len(oracle.chains_of([('hana','carol'),('carol','hana')],'bob','hana')),0)  # wrong first previous
        self.assertNotIn('createdAt',Path(oracle.__file__).read_text().split('def chains_of')[1].split('def reassign')[0])
        self.assertEqual(controls.MODES['go-manual']['now-timestamp'],(None,True))
        self.assertIn('"createdAt") VALUES ($1,$2,$3,$4,$5,$6,$7,now())',controls.reassign_source('now-timestamp'))

if __name__=='__main__':unittest.main()
