import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'benchmark'))
import codex_runner as runner


def stream(*events):
    return '\n'.join(json.dumps(event) for event in events)


class EventEvidenceTests(unittest.TestCase):
    def test_success_retains_reported_tokens(self):
        raw = stream({'type': 'turn.started'}, {'type': 'turn.completed', 'usage':
                     {'input_tokens': 100, 'cached_input_tokens': 75, 'output_tokens': 20}})
        result = runner.parse_events(raw, '', 0)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['usage']['cached_input_tokens'], 75)

    def test_readonly_runtime_is_unmeasured(self):
        result = runner.parse_events('', 'Read-only file system (os error 30)', 1)
        self.assertEqual(result['status'], 'runtime-blocked')
        self.assertFalse(result['turn_started'])
        self.assertIsNone(result['usage'])

    def test_provider_failure_is_not_candidate_failure(self):
        raw = stream({'type': 'turn.started'}, {'type': 'turn.failed', 'error': 'quota'})
        self.assertEqual(runner.parse_events(raw, '', 1)['status'], 'provider-error')

    def test_incomplete_zero_exit_is_not_success(self):
        self.assertEqual(runner.parse_events(stream({'type': 'turn.started'}), '', 0)['status'], 'protocol-error')

    def test_non_json_success_is_rejected(self):
        self.assertEqual(runner.parse_events('looks successful', '', 0)['status'], 'protocol-error')

    def test_timeout_after_start_is_budget_failure(self):
        result = runner.parse_events(stream({'type': 'turn.started'}), '', -15, True)
        self.assertEqual(result['status'], 'budget-exceeded')

    def test_timeout_before_start_is_infrastructure(self):
        self.assertEqual(runner.parse_events('', '', -15, True)['status'], 'runtime-blocked')

    def test_missing_usage_is_unknown_not_zero(self):
        raw = stream({'type': 'turn.started'}, {'type': 'turn.completed'})
        self.assertIsNone(runner.parse_events(raw, '', 0)['usage'])

    def test_duplicate_completion_is_rejected(self):
        raw = stream({'type': 'turn.started'}, {'type': 'turn.completed'}, {'type': 'turn.completed'})
        self.assertEqual(runner.parse_events(raw, '', 0)['status'], 'protocol-error')

    def test_blocked_batch_has_no_numeric_pass_rate(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            value = {'planned_trials': 9, 'trials': [], 'status': 'blocked'}
            runner.finish_batch(Path(temp), value)
            self.assertEqual(value['unmeasured_trials'], 9)
            self.assertIsNone(value['first_pass_rate'])


if __name__ == '__main__':
    unittest.main()
