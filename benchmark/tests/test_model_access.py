import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('model_access', Path(__file__).resolve().parents[2] / 'scripts/model_access.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ModelAccessTests(unittest.TestCase):
    def test_local_is_unknown(self):
        self.assertIsNone(module.record()['openai_api_key_configured_for_run'])

    def test_ci_presence_is_explicit(self):
        self.assertIs(module.record('true')['openai_api_key_configured_for_run'], True)
        self.assertIs(module.record('false')['openai_api_key_configured_for_run'], False)
        with self.assertRaises(ValueError):
            module.record('')
