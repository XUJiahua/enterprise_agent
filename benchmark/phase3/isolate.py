#!/usr/bin/env python3
"""Operator-side macOS filesystem isolation for one v3 generation kit.

Runs `python3 -I <kit>/worker.py` under sandbox-exec (Seatbelt):
- reads AND writes denied under every --deny path (evaluator checkouts, private
  seed, other kits/results, global agent config);
- writes denied everywhere except the kit, the output and the --allow-write
  caches the toolchain/CLI needs.
Network is NOT restricted (the model API needs it; Seatbelt cannot filter by
host), so the public repository remains reachable over the network.
ponytail: Seatbelt is deprecated but functional on current macOS; the upgrade
path is a separate VM/container host per kit.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys


def quote(path):
    return '"' + str(Path(path).resolve()).replace('\\', '\\\\').replace('"', '\\"') + '"'


def profile(kit, output, deny, allow_write):
    rules = ['(version 1)', '(allow default)']
    rules += ['(deny file-read* file-write* (subpath %s))' % quote(p) for p in deny]
    rules.append('(deny file-write* (subpath "/"))')
    writable = [kit, output, '/private/tmp', '/private/var/folders', '/dev', *allow_write]
    rules += ['(allow file-write* (subpath %s))' % quote(p) for p in writable]
    # Deny wins again if a writable cache sits inside a denied tree.
    rules += ['(deny file-read* file-write* (subpath %s))' % quote(p) for p in deny]
    return '\n'.join(rules) + '\n'


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--kit', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--deny', action='append', default=[])
    p.add_argument('--allow-write', action='append', default=[])
    p.add_argument('--print-profile', action='store_true')
    p.add_argument('command', nargs='*', help='override command (controls only)')
    a = p.parse_args()
    kit, output = Path(a.kit).resolve(), Path(a.output).resolve()
    for d in a.deny:
        if kit.is_relative_to(Path(d).resolve()) or output.is_relative_to(Path(d).resolve()):
            raise SystemExit('kit/output must not be inside a denied path: ' + d)
    output.parent.mkdir(parents=True, exist_ok=True)
    text = profile(kit, output, a.deny, a.allow_write)
    if a.print_profile:
        print(text, end='')
        return 0
    command = a.command or [sys.executable, '-I', str(kit / 'worker.py'), '--output', str(output)]
    env = {k: v for k, v in os.environ.items() if k not in
           ('ADMIN_DATABASE_URL', 'DATABASE_URL', 'POC_DB_RESET', 'POC_MUTATION', 'POC_ALLOW_MUTATIONS')}
    return subprocess.run(['sandbox-exec', '-p', text, *command], cwd=kit, env=env).returncode


if __name__ == '__main__':
    sys.exit(main())
