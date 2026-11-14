import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('phase2_ctl',ROOT/'benchmark/phase2/ctl.py')
ctl=importlib.util.module_from_spec(spec);spec.loader.exec_module(ctl)


class Phase2PlanTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='phase2-unit-')
        self.root=Path(self.temp.name)
        self.fixture=self.root/'private.json';ctl.save(self.fixture,{'version':1,'seed':'unit-private-fixture-seed'})
        self.plan=self.root/'plan.json'
        ctl.plan(argparse.Namespace(base='HEAD',model='synthetic-unit',fixtures=self.fixture,output=self.plan,
                 repetitions=1,time_budget=120,reasoning_effort='medium',seed=1,synthetic=True,allow_dirty_harness=True))
        self.value=ctl.load_plan(self.plan)

    def tearDown(self):self.temp.cleanup()

    def modified(self,value):
        path=self.root/'changed-plan.json';path.write_text(json.dumps(value));return path

    def test_four_cells_and_process_only_prompt_difference(self):
        cells={t['cell']:t for t in self.value['trials']}
        self.assertEqual(len(cells),4)
        self.assertEqual(cells['go-ordinary']['route'],cells['go-process']['route'])
        self.assertTrue(cells['go-process']['prompt'].startswith(cells['go-ordinary']['prompt']+'\n'))
        self.assertTrue(cells['go-runtime-process']['prompt'].startswith(cells['go-runtime']['prompt']+'\n'))

    def test_private_seed_is_not_in_plan(self):
        self.assertNotIn('unit-private-fixture-seed',self.plan.read_text())

    def test_incomplete_block_rejected(self):
        self.value['trials'].pop()
        with self.assertRaisesRegex(ValueError,'incomplete four-cell'):ctl.load_plan(self.modified(self.value))

    def test_prompt_tamper_rejected(self):
        self.value['trials'][0]['prompt']='different business requirements'
        with self.assertRaisesRegex(ValueError,'prompt differs'):ctl.load_plan(self.modified(self.value))

    def test_evaluator_drift_rejected(self):
        self.value['frozen']['benchmark/phase2/oracle.py']='0'*64
        with self.assertRaisesRegex(ValueError,'evaluator drift'):ctl.load_plan(self.modified(self.value))

    def test_export_contains_no_evaluator_or_old_git_history(self):
        trial=self.value['trials'][0]
        candidate=self.root/'candidate';manifest=self.root/'manifest.json'
        ctl.export_candidate(argparse.Namespace(plan=self.plan,trial=trial['id'],candidate=candidate,manifest=manifest))
        values=json.loads(manifest.read_text())
        self.assertEqual(set(values['files']),set(ctl.EXPORT)|{'TASK.md','visible_smoke.py','.gitignore'})
        self.assertEqual(ctl.git(candidate,'rev-list','--count','HEAD'),'1')
        self.assertEqual(ctl.git(candidate,'remote'),'')
        self.assertFalse((candidate/'benchmark').exists())
        self.assertFalse((candidate/'db').exists())


if __name__=='__main__':unittest.main()
