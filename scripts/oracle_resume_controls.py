#!/usr/bin/env python3
"""Apply the oracle handoff patch to its pinned public base and run no-model controls."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BASE = 'e75c744a280d13854f154f637c462f4dc4faa192'
URL = 'https://github.com/goodloris/oracle.git'


def call(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='reports/oracle-resume-controls.json')
    args = parser.parse_args()
    output = Path(args.output).resolve()
    if output.exists():
        raise ValueError('refusing to overwrite control evidence')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='oracle-resume-controls-') as tmp:
        checkout = Path(tmp)
        # Fetch only the pinned history and working files required by the patch.
        # No model/host credentials or historical experiment artifacts are used.
        call(['git', 'init', '-q', str(checkout)])
        call(['git', '-C', str(checkout), 'remote', 'add', 'origin', URL])
        call(['git', '-C', str(checkout), 'sparse-checkout', 'init', '--no-cone'])
        call(['git', '-C', str(checkout), 'sparse-checkout', 'set', '/scripts/', '/README.md'])
        call(['git', '-C', str(checkout), 'fetch', '-q', '--depth=1', '--filter=blob:none', 'origin', BASE])
        call(['git', '-C', str(checkout), 'checkout', '-q', '--detach', BASE])
        call(['git', '-C', str(checkout), 'apply', '--check', str(ROOT/'maintenance/oracle-v9-resume.patch')])
        call(['git', '-C', str(checkout), 'apply', str(ROOT/'maintenance/oracle-v9-resume.patch')])
        call(['bash', '-n', str(checkout/'scripts/resume_v9.sh')])
        evidence = checkout/'control.json'
        runner = '''import json,sys,unittest
suite=unittest.defaultTestLoader.discover('tests')
result=unittest.TextTestRunner(verbosity=2).run(suite)
with open(sys.argv[1],'x') as f:json.dump({'ok':result.wasSuccessful(),'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped)},f)
sys.exit(0 if result.wasSuccessful() else 1)
'''
        call([sys.executable, '-c', runner, str(evidence)], cwd=checkout)
        result = json.loads(evidence.read_text())
        assert result['tests'] == 13 and result['ok'] and not result['skipped']
        result.update({'oracle_base_sha': BASE, 'patch_applied': True, 'new_model_generations': 0,
                       'real_host_contacted': False})
        with output.open('x') as stream:
            json.dump(result, stream, indent=2)
            stream.write('\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
