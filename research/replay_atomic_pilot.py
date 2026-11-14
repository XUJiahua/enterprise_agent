#!/usr/bin/env python3
"""Post-hoc replay on NEW public fixtures; never re-score the frozen cohort."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time
import urllib.request

from audit_atomic_pilot import ARCHIVE, ROOT, audit, read, restore

SEED = 'posthoc-pr10-public-fixture-2026-11-14'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


def additional_probes(oracle):
    names, _, rows = oracle.fixture_rows(SEED)
    ascii_id = 'x' * 100; unicode_id = '界' * 33 + 'a'
    rows += [{'id': ident, 'tenantId': 'tenant-a', 'createdBy': 'alice', 'status': 'submitted'}
             for ident in (ascii_id, unicode_id)]
    db = oracle.Database(); cases = []; capacity = None
    probes = [('100 byte ASCII ID', {'ids': [ascii_id]}, None, (200,)),
              ('100 byte Unicode ID', {'ids': [unicode_id]}, None, (200,)),
              ('102 byte Unicode ID', {'ids': ['界' * 34]}, None, (400,)),
              ('null array element', {'ids': [names['a1'], None]}, None, (400,)),
              ('trailing JSON', None, b'{"ids":["x"]} {}', (400,)),
              ('NUL target does not exist', {'ids': ['\x00']}, None, (403, 404, 409)),
              ('exact field spelling', {'IDS': [names['a1']]}, None, (400,))]
    try:
        for name, payload, raw, statuses in probes:
            db.call({'op': 'reset', 'rows': rows}); before = db.snapshot()
            answer = oracle.request(payload, raw=raw); after = db.snapshot()
            assert answer[0] in statuses, (name, answer)
            if statuses == (200,):
                assert answer[1] == {'approvedIds': sorted(payload['ids'])}, (name, answer)
                oracle.assert_only_approved(before, after, payload['ids'])
            else:
                assert after == before, (name, 'rejection changed data')
            cases.append({'case': name, 'passed': True, 'status': answer[0]})
        # Observational capacity probe, NOT a newly retrofitted first-pass oracle.
        db.call({'op': 'reset', 'rows': rows}); before = db.snapshot()
        raw = b' ' * (70 * 1024) + json.dumps({'ids': [names['a1']]}).encode()
        answer = oracle.request(raw=raw); after = db.snapshot()
        assert answer[0] in (200, 400, 413), answer
        if answer[0] == 200:
            assert answer[1] == {'approvedIds': [names['a1']]}
            oracle.assert_only_approved(before, after, [names['a1']])
        else:
            assert after == before, 'capacity rejection changed records'
        capacity = {'name': 'valid JSON with 70 KiB leading whitespace', 'request_bytes': len(raw),
                    'status': answer[0], 'state_consistent_with_status': True,
                    'scored_in_original_cohort': False}
    finally:
        db.close()
    return cases, capacity


def run(out):
    audit()  # Also verifies the current Oracle bytes against the frozen plan.
    plan = read(ARCHIVE, 'plan.json')
    port = int(os.environ.get('POC_HTTP_PORT', '8080'))
    with socket.socket() as probe:
        assert probe.connect_ex(('127.0.0.1', port)) != 0, 'HTTP port occupied'
    oracle = module('atomic_oracle', ROOT / 'benchmark/phase2/oracle.py')
    common = module('atomic_original', ROOT / 'tests/acceptance.py')
    results = []
    with tempfile.TemporaryDirectory(prefix='atomic-replay-') as temporary:
        temporary = Path(temporary)
        for trial in plan['trials']:
            ident = trial['id']; candidate = temporary / ident
            restore(ARCHIVE / ('run/generation/' + ident + '/candidate.bundle'), candidate)
            directory = out / ident; directory.mkdir()
            with (directory / 'candidate-tests.log').open('x') as log:
                subprocess.run(['go', 'test', './...'], cwd=candidate, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
            subprocess.run(['node', str(ROOT / 'scripts/db.mjs'), 'reset', trial['route']], cwd=ROOT, check=True)
            binary = temporary / (ident + '-server')
            subprocess.run(['go', 'build', '-buildvcs=false', '-o', str(binary), './cmd/server'], cwd=candidate, check=True, timeout=120)
            env = {k: os.environ[k] for k in ('PATH', 'HOME', 'LANG', 'TMPDIR', 'DATABASE_URL', 'POC_HTTP_PORT') if k in os.environ}
            with (directory / 'candidate.log').open('x') as log:
                server = subprocess.Popen([str(binary), '--mode', trial['route']], cwd=candidate, env=env,
                                          stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    for _ in range(100):
                        assert server.poll() is None, 'candidate exited before readiness'
                        try:
                            with urllib.request.urlopen('http://127.0.0.1:' + str(port) + '/health', timeout=1) as response:
                                if response.status == 200: break
                        except OSError: time.sleep(.1)
                    else: raise RuntimeError('readiness timeout')
                    cases = oracle.run(SEED)
                    cases += [{'group': 'original-regression', **case} for case in common.run()]
                    assert len(cases) == 46 and all(c['passed'] for c in cases), cases
                    extra, capacity = additional_probes(oracle)
                    row = {'cell': trial['cell'], 'kind': 'posthoc-existing-candidate-replay',
                           'fixture_seed': SEED, 'exact_original_fixture_replay': False,
                           'replayed_cases': cases, 'additional_cases': extra, 'capacity_probe': capacity}
                    (directory / 'result.json').write_text(json.dumps(row, indent=2) + '\n')
                    results.append(row)
                    print(json.dumps({'cell': trial['cell'], 'replayed_cases_passed': len(cases),
                                      'additional_cases_passed': len(extra), 'capacity_status': capacity['status']}), flush=True)
                finally:
                    if server.poll() is None: os.killpg(server.pid, signal.SIGTERM)
                    try: server.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(server.pid, signal.SIGKILL); server.wait()
                    binary.unlink(missing_ok=True)
    report = {'kind': 'posthoc-existing-candidate-replay', 'new_model_generations': 0,
              'fixture_seed': SEED, 'exact_original_fixture_replay': False,
              'candidate_count': len(results), 'replayed_cases_passed': sum(len(r['replayed_cases']) for r in results),
              'additional_cases_passed': sum(len(r['additional_cases']) for r in results),
              'candidate_authored_go_tests_passed': 4,
              'capacity_probes': [{'cell': r['cell'], **r['capacity_probe']} for r in results],
              'note': 'Capacity behavior is observational. Original plan, fixtures checksum and first-pass results are unchanged.'}
    (out / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--out', default='reports/atomic-followup/replay')
    args = parser.parse_args(); out = Path(args.out).resolve(); out.mkdir(parents=True, exist_ok=False)
    os.chdir(ROOT)
    print(json.dumps(run(out)))
