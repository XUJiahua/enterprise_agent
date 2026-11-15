import argparse
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('phase5b_ctl_tests', ROOT / 'benchmark/phase5b/ctl.py')
wrapper = importlib.util.module_from_spec(spec); spec.loader.exec_module(wrapper)
ctl = wrapper.ctl


class Phase5bTests(unittest.TestCase):
    def test_exports_differ_from_v5_only_in_overridden_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            seed = Path(temp) / 's.json'; ctl.fixtures(argparse.Namespace(output=seed))
            plan = Path(temp) / 'p.json'
            ctl.plan(argparse.Namespace(base='d8a5c576cc3c293ee2da884f5c618958df407b51', model='synthetic', fixtures=seed,
                                        output=plan, repetitions=1, time_budget=600, reasoning_effort='medium', seed=1,
                                        synthetic=True, allow_dirty_harness=True, engine='kiro'))
            p = ctl.load_plan(plan)
            changed = {'go-manual': {'internal/candidate/approval.go', 'internal/candidate/reject.go'},
                       'go-action': {'internal/action/action.go', 'internal/candidate/operations.go'}}
            for cell in ctl.contract()['cells']:
                new = ctl.files(p, cell)
                old = {path: (ROOT / tpl.replace('phase5b', 'phase5')).read_bytes() for path, tpl in ctl.CELL[cell['id']].items()}
                self.assertEqual({k for k in old if old[k] != new[k]}, changed[cell['id']])
                for name, conv in ctl.contract()['conventions'].items():
                    for path, needle in conv['sources'][cell['id']]:
                        self.assertIn(needle.encode(), new[path], (name, cell['id'], path))
            self.assertEqual(ctl.prompt(ctl.contract()['cells'][0]).count('reveal'), 0)


if __name__ == '__main__':
    unittest.main()
