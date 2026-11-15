#!/usr/bin/env python3
"""v5b: the phase5 cohort with two platform-design changes (see cohort.json variant_changes).

Thin wrapper: reuses phase5 ctl/Oracle/db unchanged, swaps four source templates and the
cohort contract, and freezes the wrapper and overrides into every plan.
"""
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('phase5_ctl', HERE.parent / 'phase5/ctl.py')
ctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ctl)

OVERRIDES = {'benchmark/phase5/source/' + n: 'benchmark/phase5b/source/' + n for n in
             ('action.go.txt', 'action-operations.go.txt', 'manual-approval.go.txt', 'manual-reject.go.txt')}
for cell in ctl.CELL.values():
    for path, template in cell.items():
        cell[path] = OVERRIDES.get(template, template)
ctl.TEMPLATES = tuple(sorted(set(ctl.COMMON.values()) | {p for c in ctl.CELL.values() for p in c.values()}))
ctl.FROZEN = tuple(p for p in ctl.FROZEN if p not in OVERRIDES) + tuple(sorted(OVERRIDES.values())) + (
    'benchmark/phase5b/ctl.py', 'benchmark/phase5b/cohort.json')
ctl.contract = lambda: json.loads((HERE / 'cohort.json').read_text())

if __name__ == '__main__':
    sys.exit(ctl.main())
