#!/usr/bin/env python3
"""Post-hoc replay and manual interventions on PR #7 archived candidates.
Original scores/evidence remain untouched. These are not new model samples.
"""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / 'docs/evidence/model-pilot-success-2026-11-14/pilot-handoff-003'


def command(args, **kwargs):
    return subprocess.run(list(map(str, args)), check=True, capture_output=True, text=True, **kwargs)


def sql(statement, params=None):
    result = command(['node', ROOT / 'research/sql.mjs', 'exec'],
                     input=json.dumps({'sql': statement, 'params': params or []}), cwd=ROOT)
    return json.loads(result.stdout)


def request(path, user='alice'):
    port = os.environ.get('POC_HTTP_PORT', '8080')
    headers = {} if user is None else {'Authorization': 'Bearer demo_' + user}
    try:
        with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:' + port + path, headers=headers), timeout=10) as result:
            return result.status, json.load(result)
    except urllib.error.HTTPError as error:
        value = json.load(error)
        if error.code >= 500:
            raise RuntimeError('candidate server error: ' + str(error.code))
        return error.code, value


@contextmanager
def server(candidate, route, out):
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR', 'DATABASE_URL', 'POC_HTTP_PORT') if key in os.environ}
    if route == 'zenstack':
        args = ['node', '--import', 'tsx', 'src/server.ts']
        cwd = candidate / 'apps/zenstack'
    else:
        binary = out / 'server'
        command(['go', 'build', '-buildvcs=false', '-o', binary, './cmd/server'], cwd=candidate)
        args = [str(binary), '--mode', route]
        cwd = candidate
    with (out / 'server.log').open('w') as log:
        process = subprocess.Popen(args, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError('candidate exited before readiness')
                try:
                    with urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('POC_HTTP_PORT', '8080') + '/health', timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError('candidate readiness timeout')
            yield
        finally:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


def posthoc(task):
    results = []

    def equals(name, path, user, expected):
        actual = request(path, user)
        assert actual == (200, expected), (name, actual, expected)
        results.append({'case': name, 'passed': True, 'response': actual[1]})

    def ids(name, path, user, expected):
        status, rows = request(path, user)
        actual = sorted(r['id'] for r in rows)
        assert status == 200 and actual == sorted(expected), (name, status, actual, expected)
        assert all(r.get('internalNote') is None for r in rows)
        results.append({'case': name, 'passed': True, 'ids': actual})

    if task == 'tenant-summary':
        equals('initial tenant-a', '/purchases/summary', 'alice', {'count': 4, 'totalAmount': 800})
        sql('''INSERT INTO "Purchase" (id,"tenantId","createdBy",title,amount,status)
          SELECT 'extra-b-'||n, 'tenant-b','dave','Extra tenant-b',7,'submitted' FROM generate_series(1,50) n''')
        equals('other tenant growth does not change tenant-a', '/purchases/summary', 'alice', {'count': 4, 'totalAmount': 800})
        equals('same-tenant approver sees the same aggregate', '/purchases/summary', 'carol', {'count': 4, 'totalAmount': 800})
        equals('tenant-b growth included only in tenant-b', '/purchases/summary', 'dave', {'count': 51, 'totalAmount': 750})
        sql('DELETE FROM "Purchase" WHERE "tenantId"=$1', ['tenant-b'])
        equals('empty tenant aggregate', '/purchases/summary', 'dave', {'count': 0, 'totalAmount': 0})
    elif task == 'title-search':
        title = "Vendor 100%_O'Reilly \\ Laptop"
        for ident, tenant, user in [('special-a', 'tenant-a', 'alice'), ('special-b', 'tenant-b', 'dave')]:
            sql('''INSERT INTO "Purchase" (id,"tenantId","createdBy",title,amount,status)
                VALUES ($1,$2,$3,$4,50,'submitted')''', [ident, tenant, user, title])
        for search in ['%', '_', "O'Reilly", '\\']:
            ids('positive literal ' + search, '/purchases?title=' + urllib.parse.quote(search, safe=''), 'alice', ['special-a'])
        ids('case insensitive with extra row', '/purchases?title=LAPTOP', 'alice', ['a-own', 'a-other', 'a-done', 'special-a'])
        ids('same literal in another tenant', '/purchases?title=%25', 'dave', ['special-b'])
    elif task == 'status-filter':
        sql('''INSERT INTO "Purchase" (id,"tenantId","createdBy",title,amount,status) VALUES
         ('new-a-approved','tenant-a','alice','New A',50,'approved'),
         ('new-b-approved','tenant-b','dave','New B',60,'approved')''')
        ids('new approved in tenant-a', '/purchases?status=approved', 'alice', ['a-done', 'new-a-approved'])
        ids('new approved in tenant-b', '/purchases?status=approved', 'dave', ['new-b-approved'])
        ids('submitted excludes new approved', '/purchases?status=submitted', 'alice', ['a-own', 'a-other', 'a-self'])
        ids('approver remains in tenant-a', '/purchases?status=approved', 'carol', ['a-done', 'new-a-approved'])
    else:
        raise ValueError('unknown task')
    return results


def remove_summary_tenant(candidate, route):
    relative = 'apps/zenstack/src/server.ts' if route == 'zenstack' else 'internal/app/server.go'
    path = candidate / relative
    text = path.read_text()
    marker = "parts[1]==='summary'" if route == 'zenstack' else 'parts[1] == "summary"'
    location = text.index(marker)
    prefix = '\n  if(' if route == 'zenstack' else '\n\tif '
    start = text.rfind(prefix, 0, location)
    end = text.index(prefix, location)
    original = text[start:end]
    if route == 'zenstack':
        needle = 'where:{tenantId:user.tenantId},'
        assert original.count(needle) == 1
        changed = original.replace(needle, '')
    else:
        assert original.count(' WHERE "tenantId"=$1') == 1 and original.count(', u.Tenant)') == 1
        changed = original.replace(' WHERE "tenantId"=$1', '').replace(', u.Tenant)', ')')
    updated = text[:start] + changed + text[end:]
    path.write_text(updated)
    return ''.join(difflib.unified_diff(text.splitlines(True), updated.splitlines(True), fromfile=relative, tofile=relative))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=ROOT / 'reports/research')
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / 'followup.json').exists():
        raise ValueError('do not overwrite a previous research result')
    plan = json.loads((ARCHIVE / 'plan.json').read_text())
    results = []
    mutations = []
    with tempfile.TemporaryDirectory(prefix='enterprise-agent-research-') as temp:
        temp = Path(temp)
        harness = temp / 'harness'
        command(['git', '-C', ROOT, 'worktree', 'add', '--detach', harness, plan['harness_sha']])
        (harness / 'apps/zenstack/node_modules').symlink_to((ROOT / 'apps/zenstack/node_modules').resolve(), target_is_directory=True)
        try:
            for trial in plan['trials']:
                ident, route = trial['id'], trial['route']
                target = out / ident
                target.mkdir(exist_ok=False)
                candidate = temp / ident
                command(['git', '-C', ROOT, 'worktree', 'add', '--detach', candidate, plan['base_sha']])
                try:
                    command(['git', '-C', candidate, 'fetch', ARCHIVE / 'generation' / ident / 'candidate.bundle', 'HEAD'])
                    command(['git', '-C', candidate, 'checkout', '--detach', 'FETCH_HEAD'])
                    if route == 'zenstack':
                        shutil.copytree(ROOT / 'apps/zenstack/node_modules', candidate / 'apps/zenstack/node_modules', symlinks=True)
                    # Run the exact frozen evaluator first. The replay is explicitly not a new model sample.
                    command([sys.executable, harness / 'benchmark/benchctl.py', 'evaluate', '--plan', ARCHIVE / 'plan.json',
                             '--trial', ident, '--candidate', candidate, '--attempt', '1', '--output', target / 'frozen-replay'])
                    replay = json.loads((target / 'frozen-replay' / (ident + '--attempt-001.json')).read_text())
                    assert replay['passed'] and all(c['passed'] for c in replay['cases'])
                    command(['node', harness / 'scripts/db.mjs', 'reset', route], cwd=harness)
                    with server(candidate, route, target):
                        cases = posthoc(trial['task'])
                    row = {'trial_id': ident, 'candidate_sha': replay['candidate_sha'], 'route': route,
                           'original_oracle_cases': len(replay['cases']), 'original_oracle_passed': replay['passed'],
                           'posthoc_cases': cases, 'counts_as_new_model_sample': False}
                    results.append(row)
                    if trial['task'] == 'tenant-summary':
                        patch = remove_summary_tenant(candidate, route)
                        (target / 'manual-remove-tenant.patch').write_text(patch)
                        command(['node', harness / 'scripts/db.mjs', 'reset', route], cwd=harness)
                        intervention = target / 'manual-intervention'
                        intervention.mkdir()
                        with server(candidate, route, intervention):
                            checks = []
                            for user, expected in [('alice', {'count': 4, 'totalAmount': 800}),
                                                   ('dave', {'count': 1, 'totalAmount': 400}),
                                                   ('carol', {'count': 4, 'totalAmount': 800})]:
                                status, response = request('/purchases/summary', user)
                                assert status == 200, (route, status, response)
                                protected = response == expected
                                assert protected == (route != 'baseline'), (route, user, response, expected)
                                checks.append({'user': user, 'response': response, 'tenant_invariant_preserved': protected})
                        mutations.append({'trial_id': ident, 'route': route, 'kind': 'manual-remove-new-summary-tenant-filter',
                                          'patch_sha256': hashlib.sha256(patch.encode()).hexdigest(), 'availability_preserved': True, 'checks': checks})
                    print(json.dumps({'candidate': ident, 'frozen_cases': row['original_oracle_cases'], 'posthoc_cases': len(cases)}), flush=True)
                finally:
                    command(['git', '-C', ROOT, 'worktree', 'remove', '--force', candidate])
            command(['node', harness / 'scripts/db.mjs', 'reset', 'guarded'], cwd=harness)
            probe = json.loads(command(['node', ROOT / 'research/sql.mjs', 'batch-probe'], cwd=ROOT).stdout)
        finally:
            command(['git', '-C', ROOT, 'worktree', 'remove', '--force', harness])
    result = {'kind': 'archived-replay-posthoc-and-manual-intervention', 'source_pr': 7,
              'plan_sha256': hashlib.sha256((ARCHIVE / 'plan.json').read_bytes()).hexdigest(),
              'new_model_generations': 0,
              'database_version': sql('SELECT version() AS version')['rows'][0]['version'],
              'research_sources_sha256': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in ['research/replay_followup.py', 'research/sql.mjs']},
              'frozen_replay_candidates': len(results),
              'frozen_replay_cases': sum(r['original_oracle_cases'] for r in results),
              'posthoc_cases': sum(len(r['posthoc_cases']) for r in results),
              'replays': results, 'manual_tenant_filter_removal': mutations, 'business_contract_probe': probe}
    (out / 'followup.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('kind', 'new_model_generations', 'frozen_replay_candidates', 'frozen_replay_cases', 'posthoc_cases')}))


if __name__ == '__main__':
    main()
