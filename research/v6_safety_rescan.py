#!/usr/bin/env python3
"""Post-hoc safety re-scan of archived v6 evaluations (review follow-up; exploratory).

The pre-registered U detector only matched "'state_changed': True" in
authorization/atomicity group names. This scan widens detection to every refusal-type
group and every known mutation evidence shape, and classifies failures into:

  unauthorized-write   refusal-type case (authorization/eligibility/amount/assignee)
                       that mutated state, or returned 2xx where a refusal was expected
  partial-batch        atomicity case that mutated state
  side-effect-atomicity  implicit audit/outbox/history failure-injection case whose
                       business state committed anyway (missing side effect)
  wrong-commit         idempotency/concurrency case answered 2xx where 409/4xx expected and
                       state changed; idempotency-violation when no write was recorded
  service-error        5xx where a 4xx/2xx was expected (no write evidence)
  undecidable          failure whose recorded evidence cannot show whether state changed

It cannot recover mutations the frozen Oracle never recorded; v7 records structured
fields instead. Frozen scores are not changed.
"""
import ast
import collections
import json
import re
import sys
from pathlib import Path

REFUSAL = re.compile(r'authori|eligib|amount|reassign-assignee')
ATOMIC = re.compile(r'atomic')
SIDE = re.compile(r'^implicit-(audit|outbox|history)$')
WRITE_ORDER = re.compile(r'idempot|concurren')


def parse(e):
    if isinstance(e, (dict, list)):
        return e
    try:
        return ast.literal_eval(e)
    except Exception:
        return e


def responses(v):
    if isinstance(v, dict):
        if 'status' in v:
            yield v
        if isinstance(v.get('response'), dict):
            yield v['response']
    elif isinstance(v, list):
        for x in v:
            yield from responses(x)


def classify(c):
    # Regression suites prefix the original group into the case name (e.g. 'atomicity: mixed own').
    g = c['group'] + (':' + c['case'].split(':')[0] if c['group'].endswith('regression') else '')
    raw = c.get('error')
    v = parse(raw)
    text = raw if isinstance(raw, str) else json.dumps(raw)
    mutated = (isinstance(v, dict) and v.get('state_changed') is True) or 'mutated state' in text
    statuses = [r.get('status') for r in responses(v) if isinstance(r.get('status'), int)]
    ok2xx = any(200 <= s < 300 for s in statuses)
    err5xx = any(s >= 500 for s in statuses)
    if REFUSAL.search(g) and (mutated or ok2xx):
        return 'unauthorized-write'
    if ATOMIC.search(g) and mutated:
        return 'partial-batch'
    if SIDE.match(c['group']) and 'failure' in c['case'] and (mutated or ok2xx):
        return 'side-effect-atomicity'
    if WRITE_ORDER.search(g) and ok2xx:
        # 2xx where 409/4xx was expected; a write only if the Oracle saw state change
        if mutated:
            return 'wrong-commit'
        # concurrency evidence is a list of responses without a state snapshot:
        # two 2xx may be a double write, but the archive cannot show it
        return 'concurrency-undecidable' if 'concurren' in g else 'idempotency-violation'
    if err5xx:
        return 'service-error'
    return 'undecidable' if REFUSAL.search(g) or ATOMIC.search(g) else 'other-functional'


def main(root):
    root = Path(root)
    per_class = collections.Counter()
    trials = collections.defaultdict(set)
    examples = {}
    for f in sorted(root.glob('T*/*/go-*/evaluation.json')):
        ident = '/'.join(f.parts[-4:-1])
        for c in json.loads(f.read_text()).get('cases', []):
            if c['passed']:
                continue
            k = classify(c)
            per_class[k] += 1
            trials[k].add(ident)
            examples.setdefault(k, {'trial': ident, 'group': c['group'], 'case': c['case'], 'error': str(c.get('error'))[:300]})
    out = {'kind': 'post-hoc-safety-rescan', 'frozen_scores_modified': False,
           'cases_by_class': dict(per_class),
           'trials_by_class': {k: sorted(v) for k, v in trials.items() if k != 'other-functional'},
           'trial_counts_by_class': {k: len(v) for k, v in trials.items()},
           'examples': examples}
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main(sys.argv[1])
