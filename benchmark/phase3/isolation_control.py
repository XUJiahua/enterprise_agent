#!/usr/bin/env python3
"""Positive/negative control for isolate.py on macOS (no model call unless --kiro)."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--kiro', action='store_true', help='also run a real Kiro READY probe inside the sandbox')
    p.add_argument('--deny', action='append', default=[], help='extra real paths that must be unreadable')
    p.add_argument('--allow-write', action='append', default=[], help='CLI/toolchain cache paths')
    a = p.parse_args()
    with tempfile.TemporaryDirectory(prefix='v3-isolation-') as temp:
        t = Path(temp).resolve()
        evaluator, seed, kit = t / 'evaluator', t / 'private', t / 'kit'
        for d in (evaluator, seed, kit): d.mkdir()
        (evaluator / 'oracle.py').write_text('secret oracle')
        (seed / 'fixtures.json').write_text('{"seed":"x"}')
        out = t / 'out'
        base = [sys.executable, str(HERE / 'isolate.py'), '--kit', str(kit), '--output', str(out),
                '--deny', str(evaluator), '--deny', str(seed), *sum((['--deny', d] for d in a.deny), []),
                *sum((['--allow-write', d] for d in a.allow_write), [])]

        def run(cmd):
            return subprocess.run([*base, '--', 'sh', '-c', cmd], capture_output=True, text=True).returncode == 0
        checks = {
            'evaluator unreadable': not run('cat ' + str(evaluator / 'oracle.py')),
            'seed unreadable': not run('cat ' + str(seed / 'fixtures.json')),
            'evaluator unlistable': not run('ls ' + str(evaluator)),
            'home not writable': not run('touch "$HOME/.v3-isolation-probe"'),
            'kit writable': run('echo ok > ' + str(kit / 'probe')) and (kit / 'probe').read_text() == 'ok\n',
            'output writable': run('mkdir -p %s && echo ok > %s/probe' % (out, out)),
        }
        for d in a.deny:
            checks['unreadable: ' + d] = not run('ls ' + json.dumps(d))
        if a.kiro:
            checks['kiro READY inside sandbox'] = run(
                'KIRO_HOME=%s kiro-cli chat --no-interactive --agent-engine v3 --trust-tools= '
                '"Reply exactly READY" </dev/null | grep -q READY' % (out / 'kh'))
    print(json.dumps({'kind': 'isolation-control', 'checks': checks, 'passed': all(checks.values())}, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
