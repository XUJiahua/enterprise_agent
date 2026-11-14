#!/usr/bin/env python3
"""Post-hoc diagnostic for the first Kiro v3 cohort. NOT a re-score.

The frozen v3 Oracle requires ActionRequest.requestHash to equal the reference
kernel's exact fingerprint formula, which the task text, prompt and public
schema never specify (schema: any 64 lowercase hex). This re-runs the same
66 cases with only that equality relaxed to the schema constraint, to show
whether a failure is behavioural or solely caused by the unspecified formula.
Replay/409/concurrency behaviour is still checked over HTTP and table snapshots.
Output goes to a separate directory; frozen first results are unchanged.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'benchmark/phase3'))
import ctl  # noqa: E402

HEX64 = re.compile(r'^[0-9a-f]{64}$')


def relax(oracle):
    strict = oracle.assert_transition

    def assert_transition(before, after, ids, user='carol', key='key', tenant='tenant-a'):
        scope = (tenant, user, oracle.OPERATION, key)

        def fix(rows):
            return [{**r, 'requestHash': oracle.fingerprint(ids)}
                    if (r['tenantId'], r['actorId'], r['operation'], r['key']) == scope and HEX64.match(r['requestHash'])
                    else r for r in rows]
        strict({**before, 'requests': fix(before['requests'])}, {**after, 'requests': fix(after['requests'])},
               ids, user, key, tenant)
    oracle.assert_transition = assert_transition
    return oracle


original_module = ctl.module


def module(name, path):
    value = original_module(name, path)
    if name == 'v3_cohort_oracle':
        load = value.load
        value.load = lambda n, p: relax(load(n, p)) if n == 'phase3_seeded_oracle' else load(n, p)
    return value


if __name__ == '__main__':
    ctl.module = module
    sys.argv[0] = 'ctl.py'
    sys.exit(ctl.main())
