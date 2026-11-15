#!/usr/bin/env python3
"""v6b: bulk reassign (non-status column, validated parameter) with scattered conventions.

Thin wrapper over the phase5 controller (plan/prepare/export/restore/evaluate/summarize and
the v5 plan/result format are unchanged): swaps the cohort contract, the prompt, the source
templates, the schema and the Oracle (benchmark/phase6b/oracle.py), and freezes all of them.
"""
import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('phase5_ctl_for_v6b', HERE.parent / 'phase5/ctl.py')
ctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ctl)

ctl.HERE = HERE  # contract() -> phase6b/cohort.json, evaluate() -> phase6b/oracle.py
S = 'benchmark/phase6b/source/'
ctl.COMMON = {'cmd/server/main.go': 'benchmark/phase3/source/main.go.txt',
              'internal/protocol/protocol.go': S + 'protocol.go.txt',
              'internal/ledger/ledger.go': S + 'ledger.go.txt',
              'internal/ledger/reconcile.go': S + 'reconcile.go.txt',
              'internal/notify/notify.go': S + 'notify.go.txt'}
ctl.CELL = {'go-manual': {'internal/candidate/approval.go': S + 'manual-approval.go.txt',
                          'internal/candidate/reject.go': 'benchmark/phase5b/source/manual-reject.go.txt',
                          'internal/candidate/reopen.go': S + 'manual-reopen.go.txt'},
            'go-action': {'internal/candidate/approval.go': S + 'action-candidate.go.txt',
                          'internal/candidate/operations.go': S + 'action-operations.go.txt',
                          'internal/action/action.go': S + 'action.go.txt'}}
ctl.EXTRA_SQL = ('benchmark/phase4/schema.sql', 'benchmark/phase5/schema.sql', 'benchmark/phase6b/schema.sql')
ctl.TEMPLATES = tuple(sorted(set(ctl.COMMON.values()) | {p for c in ctl.CELL.values() for p in c.values()}))
ctl.FROZEN = ctl.BASE + ctl.TEMPLATES + ctl.EXTRA_SQL + (
    'benchmark/phase5/ctl.py', 'benchmark/phase6b/ctl.py', 'benchmark/phase6b/cohort.json', 'benchmark/phase6b/oracle.py',
    'benchmark/phase6b/db.mjs', 'benchmark/phase5/oracle.py', 'benchmark/phase4/oracle.py', ctl.WORKER,
    'benchmark/phase3/cohort_oracle.py', 'benchmark/phase3/oracle.py', 'tests/acceptance.py',
    'db/bootstrap.sql', 'db/seed.sql') + ctl.SQL


def prompt(cell):
    return '\n'.join([ctl.contract()['requirements'],
     'Add the bulk reassign Action described above to the existing service.',
     'Read the existing code first and follow its conventions.',
     'Both conditions use guarded RLS. The shared HTTP adapter in internal/protocol validates input and binds demo identity; register the new path with protocol.Assign.',
     'Only edit/add internal/candidate/ Go code. All other exports, dependencies, Git settings and tests are protected.',
     'Preserve the existing APIs, including bulk-approve, bulk-reject and reopen.',
     'Do not commit/push, read credentials, other checkouts or evaluator materials; do not use other agents.',
     'Use go build ./... and go test ./... as needed. Private database credentials are unavailable during generation. The public SQL schema is in SCHEMA.sql.',
     ('Capability: protected internal/action is the platform Action kernel: New(ctx,pool), the Operation and Update types, '
      'Executor.Run(ctx,identity,operation,key,ids,reason) and Executor.Set(ctx,identity,update,key,ids,value). The existing operations are '
      'declared as action.Operation values in internal/candidate/operations.go and served through the kernel; declare new operations '
      'and updates in internal/candidate as well. action.Rejection is distinct from protocol.Rejection; adapt at your boundary.'
      if cell['capability'] else
      'Capability: no platform Action kernel. The existing batch approval, batch rejection and reopen are hand-written in internal/candidate/.'),
     'This is a trusted-code pilot; directory export alone does not enforce host or network isolation.'])


ctl.prompt = prompt

if __name__ == '__main__':
    sys.exit(ctl.main())
