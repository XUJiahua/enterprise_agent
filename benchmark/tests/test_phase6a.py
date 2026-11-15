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
ctl=load('phase6a_ctl_tests','benchmark/phase6a/ctl.py')
controls=load('phase6a_controls_tests','benchmark/phase6a/controls.py')
BASE='d8a5c576cc3c293ee2da884f5c618958df407b51'

def comments(data):
    return ' '.join(re.findall(r'(?://|--)(.*)',data.decode())).lower()

class Phase6aTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='phase6a-unit-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.private=self.root/'private.json';self.plan=self.root/'plan.json'
        ctl.fixtures(argparse.Namespace(output=self.private))
        ctl.plan(argparse.Namespace(base=BASE,model='synthetic',fixtures=self.private,
           output=self.plan,repetitions=2,time_budget=600,reasoning_effort='medium',seed=7,synthetic=True,
           allow_dirty_harness=True,engine='kiro'))
        self.p=ctl.load_plan(self.plan);self.cells={c['id']:c for c in ctl.contract()['cells']}
        self.exports={c:ctl.files(self.p,self.cells[c]) for c in self.cells}

    def export(self,cell):
        args=argparse.Namespace(plan=self.plan,trial=cell+'--001',candidate=self.root/cell,manifest=self.root/(cell+'.json'))
        ctl.export_candidate(args);return args

    def test_cells_share_everything_but_candidate_package_and_kernel(self):
        manual,action=self.exports['go-manual'],self.exports['go-action']
        self.assertEqual(set(action)-set(manual),{'internal/action/action.go','internal/candidate/operations.go'})
        self.assertEqual(set(manual)-set(action),{'internal/candidate/reject.go','internal/candidate/reopen.go'})
        self.assertEqual({k for k in manual if k in action and manual[k]!=action[k]},{'internal/candidate/approval.go'})
        self.assertIn(b'countersign.v6',manual['SCHEMA.sql']);self.assertIn(b'/server\n',manual['.gitignore'])

    def test_conventions_declare_action_enforcement(self):
        # Used directly by the pre-registered primary hypothesis (kernel vs declared).
        values=[v.get('action_enforcement') for v in ctl.contract()['conventions'].values()]
        self.assertTrue(set(values)<={'kernel','declared'},values)
        self.assertGreaterEqual(values.count('kernel'),2);self.assertGreaterEqual(values.count('declared'),1)

    def test_conventions_count_and_commented_flags(self):
        conventions=ctl.contract()['conventions']
        self.assertIn(len(conventions),(3,4))
        self.assertTrue(any(not v['commented'] for v in conventions.values()))

    def test_task_and_prompts_do_not_state_implicit_conventions(self):
        for text in [ctl.contract()['requirements']]+[t['prompt'] for t in self.p['trials']]:
            for token in ctl.contract()['forbidden_in_task']: self.assertNotIn(token.lower(),text.lower(),token)

    def test_every_convention_is_scattered_over_exported_files(self):
        conventions=ctl.contract()['conventions']
        self.assertEqual(set(conventions),{g for g,v in ctl.contract()['groups'].items() if v['kind']=='implicit'})
        for name,value in conventions.items():
            for cell,exported in self.exports.items():
                sources=value['sources'][cell]
                for path,needle in sources: self.assertIn(needle.encode(),exported[path],(name,cell,path))
                self.assertGreaterEqual(len({p for p,_ in sources}),2,(name,cell))
                self.assertTrue(any(not p.startswith('internal/candidate/') for p,_ in sources),(name,cell))

    def test_uncommented_conventions_have_no_comment_hint(self):
        conventions=ctl.contract()['conventions'];words=list(ctl.contract()['forbidden_comment_words'])
        for v in conventions.values():
            if not v['commented']: self.assertTrue(v['comment_words']);words+=v['comment_words']
        for cell,exported in self.exports.items():
            for path,data in exported.items():
                for word in words: self.assertNotIn(word,comments(data),(cell,path,word))

    def test_old_and_new_style_clues_state_which_wins(self):
        # v5 lesson: where an older Action differs from the convention, the code says which one governs.
        for cell,exported in self.exports.items():
            notify=exported['internal/notify/notify.go'].decode()
            self.assertIn('bulk-approve.v3 never emits an event',notify)
            self.assertIn('That exception is\n// historical; every Action added after the outbox writes one event per target',notify)
        for path in ('internal/candidate/approval.go','internal/candidate/reject.go'):
            self.assertIn(b'unlike the legacy per-purchase handlers in internal/app, Actions never reveal existence',self.exports['go-manual'][path])

    def test_no_copyable_countersign_example_in_candidate_package(self):
        for cell,exported in self.exports.items():
            for path,data in exported.items():
                if path.startswith('internal/candidate/'): self.assertNotIn(b'countersign',data.lower(),(cell,path))
        kernel=self.exports['go-action']['internal/action/action.go'].decode()
        self.assertIsNone(re.search(r'=\s*Operation\{',kernel))
        self.assertNotIn('countersign',kernel.lower())
        self.assertIn(b'action.Operation{',self.exports['go-action']['internal/candidate/operations.go'])

    def test_kit_has_no_oracle_seed_controls_or_other_cell(self):
        kit=self.root/'kit';ctl.prepare(argparse.Namespace(plan=self.plan,trial='go-manual--001',output=kit))
        names={str(p.relative_to(kit)) for p in kit.rglob('*') if p.is_file() and '.git/' not in str(p)}
        self.assertFalse(any(w in n for n in names for w in ('oracle','controls','db.mjs','private')))
        self.assertFalse((kit/'candidate/internal/action').exists())
        self.assertNotIn(json.loads(self.private.read_text())['seed'],(kit/'plan.json').read_text())
        job=json.loads((kit/'job.json').read_text())
        self.assertEqual((job['engine'],job['worker_sha256']),('kiro',self.p['frozen'][ctl.WORKER]))
        self.assertEqual((kit/'worker.py').read_bytes(),(ROOT/'benchmark/phase3/generation_worker.py').read_bytes())

    def test_plan_freezes_every_harness_file(self):
        frozen=set(self.p['frozen'])
        for path in (ROOT/'benchmark/phase6a').rglob('*'):
            rel=str(path.relative_to(ROOT))
            if path.is_file() and path.suffix in ('.py','.json','.mjs','.sql','.txt') and path.name not in ('controls.py','driver_smoke.py'):
                self.assertIn(rel,frozen)
        self.assertTrue({'benchmark/phase5/oracle.py','benchmark/phase4/oracle.py','tests/acceptance.py',ctl.WORKER}<=frozen)

    def test_cli_matches_kiro_cohort_runner(self):
        script=(ROOT/'benchmark/phase3/run_kiro_cohort.sh').read_text()
        self.assertIn('CTL=${CTL:-',script)
        for command in ('fixtures','plan','prepare','restore','evaluate','summarize'):
            p=subprocess.run([sys.executable,str(ROOT/'benchmark/phase6a/ctl.py'),command,'--help'],capture_output=True,text=True)
            self.assertEqual(p.returncode,0,command)
        self.assertIn('--engine',subprocess.run([sys.executable,str(ROOT/'benchmark/phase6a/ctl.py'),'plan','--help'],capture_output=True,text=True).stdout)

    def test_protected_kernel_infrastructure_and_adapter_changes_rejected(self):
        for cell,path in [('go-action','internal/action/action.go'),('go-manual','internal/ledger/ledger.go'),
                          ('go-manual','internal/notify/notify.go'),('go-action','internal/protocol/protocol.go')]:
            a=self.export(cell);target=a.candidate/path;target.write_text(target.read_text()+'\n// edited\n')
            ctl.process(['git','add','.'],cwd=a.candidate)
            ctl.process(['git','-c','user.name=U','-c','user.email=u@example.invalid','commit','-m','u'],cwd=a.candidate)
            with self.assertRaisesRegex(ValueError,'protected'): ctl.validate_candidate(a,self.p)
            shutil.rmtree(a.candidate);a.manifest.unlink()

    def test_plan_tamper_rejected(self):
        original=self.plan.read_text()
        for fn,pattern in [(lambda p:p['frozen'].pop('benchmark/phase6a/oracle.py'),'manifest'),
                           (lambda p:p['trials'].pop(),'block'),
                           (lambda p:p['trials'][0].update(prompt='altered'),'prompt'),
                           (lambda p:p.update(version=5),'v6a'),
                           (lambda p:p['exports']['go-action'].pop('internal/candidate/operations.go'),'export')]:
            value=json.loads(original);fn(value);self.plan.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,pattern): ctl.load_plan(self.plan)

    def cases(self,fail=None):
        return [{'group':g,'case':g+str(i),'passed':g!=fail} for g,n in ctl.counts().items() for i in range(n)]

    def test_score_separates_explicit_and_implicit(self):
        s=ctl.score(self.cases('implicit-outbox'))
        self.assertTrue(s['explicit_passed']);self.assertFalse(s['passed'])
        self.assertEqual([g for g,ok in s['conventions'].items() if not ok],['implicit-outbox'])
        self.assertEqual((s['explicit']['total'],s['implicit']['total']),(180,13))
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
        ctl.save(forged/'result.json',{'version':6,'kind':self.p['kind'],'plan_sha256':ctl.digest(self.plan),'trial':t,
            'outcome':'acceptance-failed','passed':False,'cases':cases,**{**ctl.score(cases),'explicit_passed':False}})
        with self.assertRaisesRegex(ValueError,'score'):
            ctl.summarize(argparse.Namespace(plan=self.plan,inputs=[forged],output=None))

    def test_controls_cover_every_convention_in_both_cells(self):
        implicit={g for g,v in ctl.contract()['groups'].items() if v['kind']=='implicit'}
        reference=controls.countersign_source('correct')
        for cell,modes in controls.MODES.items():
            self.assertEqual({v for v,_ in modes.values() if v},implicit,cell)
            self.assertTrue(any(not v and not e for v,e in modes.values()),cell)
            for mode,(violated,_) in modes.items():
                if violated and not (cell=='go-action' and mode in controls.KERNEL):
                    self.assertNotEqual(controls.countersign_source(mode),reference,mode)
        # The declared convention is violated through the kernel declaration itself, not a bypass.
        self.assertIn('no-event',controls.KERNEL);self.assertIn('Event: action.NoEvent',controls.kernel_op('no-event'))

if __name__=='__main__':unittest.main()
