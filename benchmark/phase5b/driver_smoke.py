#!/usr/bin/env python3
"""v5b controls: the phase5 driver run against the phase5b wrapper (real PostgreSQL, fake Kiro).

Expected difference from v5: in go-action, omitting Event is no longer a silent outbox
violation; the kernel fails every Run, so it must be caught by the explicit contract.
"""
import importlib.util
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
P5 = HERE.parent / 'phase5'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


wrapper = load('phase5b_ctl', HERE / 'ctl.py')
sys.modules['ctl'] = wrapper.ctl
controls = load('controls', P5 / 'controls.py')
controls.MODES['go-action']['omit-event'] = (None, False)
driver = load('phase5b_driver', P5 / 'driver_smoke.py')


def call(*args, expected=0):
    p = subprocess.run([sys.executable, str(HERE / 'ctl.py'), *map(str, args)], capture_output=True, text=True, timeout=900)
    if p.returncode != expected:
        raise AssertionError((args, p.returncode, p.stdout[-2000:], p.stderr[-2000:]))
    return p


driver.call = call
if __name__ == '__main__':
    if '--out' not in sys.argv:
        sys.argv += ['--out', 'reports/phase5b-controls']
    driver.main()
