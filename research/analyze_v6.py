#!/usr/bin/env python3
"""Pre-registered v6 analysis (docs/preregistration-v6.md). Reads archived evidence only.

Usage: analyze_v6.py <evidence-root>     # <root>/<T1|T2|T3>/<model>/<trial>/...
Primary: H1 (K), H2 (E) exact one-sided paired sign tests; H3 (S) one-sided Wilcoxon
signed-rank; Holm over H1-H3. Main analysis excludes nothing observed; sensitivity
drops blocks with a retrieval-attempt trial (appendix A).
"""
import json
import math
import re
import statistics
import sys
from pathlib import Path

UNMEASURED = {'runtime-blocked', 'provider-error', 'protocol-error', 'infrastructure-blocked'}
T1_ENFORCEMENT = {'implicit-audit': 'kernel', 'implicit-request-hash': 'kernel',
                  'implicit-uniform-denial': 'kernel', 'implicit-outbox': 'declared'}  # appendix C
PHASE = {'T1': 'phase5b', 'T2': 'phase6a', 'T3': 'phase6b'}
TELEMETRY = {'telemetry.eu-central-1.kiro.dev', 'client-telemetry.us-east-1.amazonaws.com',
             'telemetry.us-east-1.kiro.dev', 'app.kiro.dev'}  # appendix C, F
RETRIEVAL = re.compile(r'kiro-cli|\bq chat\b|web_search|\bcurl\b|\bwget\b|git clone|https?://(?!127\.0\.0\.1|localhost)')
ROOT = Path(__file__).resolve().parents[1]


def enforcement(task):
    if task == 'T1':
        return T1_ENFORCEMENT
    cohort = json.loads((ROOT / 'benchmark' / PHASE[task] / 'cohort.json').read_text())
    return {k: v['action_enforcement'] for k, v in cohort['conventions'].items()}


def shell_commands(events):
    out = []
    for line in events.read_text().splitlines():
        e = json.loads(line)
        if e['type'] != 'sessionUpdate':
            continue
        u = e['data']['update']
        if u.get('sessionUpdate') == 'tool_call' and isinstance(u.get('rawInput'), dict):
            c = u['rawInput'].get('command')
            if isinstance(c, str):
                out.append(c)
    return out


def trial(d, enf):
    out = json.loads((d / 'outcome.json').read_text())
    ev = json.loads((d / 'evaluation.json').read_text()) if (d / 'evaluation.json').exists() else None
    outcome = ev['outcome'] if ev else out['outcome']
    scored = ev is not None and outcome in ('passed', 'acceptance-failed')
    conv = ev['conventions'] if scored else None
    # U per preregistration: only authorization/atomicity cases (incl. regression suites).
    unsafe = [c['group'] + ':' + c['case'] for c in (ev or {}).get('cases', [])
              if not c['passed'] and "'state_changed': True" in str(c.get('error'))
              and re.search(r'authori|atomic', c['group'] + ':' + c['case'])]
    unsafe_any_group = sum(1 for c in (ev or {}).get('cases', [])
                           if not c['passed'] and "'state_changed': True" in str(c.get('error')))
    cmds = shell_commands(d / 'generation/events.jsonl')
    denied = []
    if (d / 'network.log').exists():
        for line in (d / 'network.log').read_text().splitlines():
            e = json.loads(line)
            if e.get('decision') == 'deny' and e.get('host') not in TELEMETRY:
                denied.append(e.get('host') or e.get('target'))
    return {
        'outcome': outcome, 'unmeasured': outcome in UNMEASURED,
        'K': all(conv[k] for k, v in enf.items() if v == 'kernel') if conv else None,
        'D': all(conv[k] for k, v in enf.items() if v == 'declared') if conv else None,
        'E': bool(ev and ev.get('explicit_passed')) if outcome not in UNMEASURED else None,
        'A': outcome == 'passed',
        'S': out['generation'].get('generation_seconds'),
        'credits': (out['generation'].get('usage') or {}).get('credits'),
        'U': unsafe, 'state_changed_any_group': unsafe_any_group, 'conventions': conv,
        'retrieval_attempt': [c[:200] for c in cmds if RETRIEVAL.search(c)],
        'network_denied': denied,
    }


def sign_p(wins, losses):  # one-sided: P(X >= wins | n, 1/2)
    n = wins + losses
    return 1.0 if n == 0 else sum(math.comb(n, i) for i in range(wins, n + 1)) / 2 ** n


def wilcoxon_p(diffs):  # one-sided H3: action - manual < 0
    d = [x for x in diffs if x != 0]
    n = len(d)
    if n == 0:
        return 1.0
    order = sorted(range(n), key=lambda i: abs(d[i]))
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and abs(d[order[j + 1]]) == abs(d[order[i]]):
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    w_plus = sum(r for r, x in zip(ranks, d) if x > 0)
    if n <= 50:  # exact distribution of W+ (no ties correction)
        counts = {0: 1}
        for r in range(1, n + 1):
            nxt = dict(counts)
            for s, c in counts.items():
                nxt[s + r] = nxt.get(s + r, 0) + c
            counts = nxt
        total = 2 ** n
        return sum(c for s, c in counts.items() if s <= w_plus) / total
    mu, sd = n * (n + 1) / 4, math.sqrt(n * (n + 1) * (2 * n + 1) / 24)
    return 0.5 * math.erfc(-((w_plus - mu + 0.5) / sd) / math.sqrt(2))


def holm(ps):
    order = sorted(ps, key=ps.get)
    adj, running = {}, 0.0
    for i, k in enumerate(order):
        running = max(running, min(1.0, (len(ps) - i) * ps[k]))
        adj[k] = running
    return adj


def tests(blocks):
    def pair(metric):
        w = l = n = 0
        for m, a in blocks:
            if m[metric] is None or a[metric] is None:
                continue
            n += 1
            w += a[metric] and not m[metric]
            l += m[metric] and not a[metric]
        return {'blocks_with_both_scored': n, 'action_only': w, 'manual_only': l, 'p_one_sided': sign_p(w, l)}
    diffs = [a['S'] - m['S'] for m, a in blocks if a['S'] is not None and m['S'] is not None]
    res = {'H1_K': pair('K'), 'H2_E': pair('E'),
           'H3_S': {'n': len(diffs), 'median_diff_seconds': statistics.median(diffs) if diffs else None,
                    'p_one_sided': wilcoxon_p(diffs)}}
    adj = holm({k: v['p_one_sided'] for k, v in res.items()})
    for k in res:
        res[k]['p_holm'] = adj[k]
    res['A_pair'] = pair('A')
    res['blocks'] = len(blocks)
    return res


def main(root):
    root = Path(root)
    strata, all_blocks, sens_blocks, unsafe, flags = {}, [], [], [], []
    for task in ('T1', 'T2', 'T3'):
        enf = enforcement(task)
        for mdir in sorted((root / task).iterdir()):
            if not (mdir / 'plan.json').exists():
                continue
            plan = json.loads((mdir / 'plan.json').read_text())
            t = {x['id']: dict(trial(mdir / x['id'], enf), cell=x['cell'], instance=x['instance']) for x in plan['trials']}
            blocks, unmeasured = [], 0
            for i in range(1, max(x['instance'] for x in t.values()) + 1):
                m, a = t[f'go-manual--{i:03d}'], t[f'go-action--{i:03d}']
                unmeasured += m['unmeasured'] + a['unmeasured']
                if m['unmeasured'] or a['unmeasured']:
                    continue
                blocks.append((m, a))
                if not (m['retrieval_attempt'] or a['retrieval_attempt']):
                    sens_blocks.append((m, a))
            all_blocks += blocks
            for ident, x in t.items():
                if x['U']:
                    unsafe.append({'task': task, 'model': mdir.name, 'trial': ident, 'cases': x['U']})
                if x['retrieval_attempt'] or x['network_denied']:
                    flags.append({'task': task, 'model': mdir.name, 'trial': ident,
                                  'retrieval_attempt': x['retrieval_attempt'], 'network_denied': x['network_denied']})
            cells = {}
            for c in ('go-manual', 'go-action'):
                r = [x for x in t.values() if x['cell'] == c]
                sc = [x for x in r if x['K'] is not None]
                cells[c] = {'planned': len(r), 'unmeasured': sum(x['unmeasured'] for x in r),
                            'A': sum(x['A'] for x in r), 'E': sum(bool(x['E']) for x in r),
                            'K': [sum(x['K'] for x in sc), len(sc)], 'D': [sum(x['D'] for x in sc), len(sc)],
                            'conventions': {k: [sum(x['conventions'][k] for x in sc), len(sc)] for k in enf},
                            'scope_violation': sum(x['outcome'] == 'scope-violation' for x in r),
                            'median_S': statistics.median(x['S'] for x in r if x['S'] is not None),
                            'median_credits': statistics.median(x['credits'] for x in r if x['credits'] is not None),
                            'outcomes': {o: sum(x['outcome'] == o for x in r) for o in sorted({x['outcome'] for x in r})}}
            strata[task + '/' + mdir.name] = {'cells': cells, 'unmeasured_fraction': unmeasured / len(t),
                                              'exploratory_pair_tests': tests(blocks)}
    result = {'preregistration': 'docs/preregistration-v6.md', 'primary': tests(all_blocks),
              'sensitivity_excluding_retrieval_attempt_blocks': tests(sens_blocks),
              'U_unsafe_trials': unsafe, 'flags': flags, 'strata': strata}
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main(sys.argv[1])
