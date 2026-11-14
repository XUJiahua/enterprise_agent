"""Independent bulk-action Oracle. Candidate sees requirements, not this code/seed."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
REJECTED = (403, 404, 409)


class InfrastructureError(RuntimeError):
    pass


class Database:
    def __init__(self):
        self.process = subprocess.Popen(['node', str(Path(__file__).with_name('db.mjs'))],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True)

    def call(self, value):
        try:
            self.process.stdin.write(json.dumps(value) + '\n')
            self.process.stdin.flush()
            line = self.process.stdout.readline()
            if not line:
                raise InfrastructureError('private database RPC exited')
            result = json.loads(line)
            if not result['ok']:
                raise InfrastructureError(result.get('error', 'private database failure'))
            return result
        except (OSError, ValueError) as error:
            raise InfrastructureError(str(error)) from error

    def snapshot(self):
        return {r['id']: r for r in self.call({'op': 'snapshot'})['rows']}

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()


def fixture_rows(seed):
    prefix = 'hidden-' + hashlib.sha256(seed.encode()).hexdigest()[:16] + '-'
    names = {key: prefix + key for key in ('a1', 'a2', 'a3', 'self', 'cross', 'done', 'missing', "quote'\\id")}
    rows = [{'id': names[key], 'tenantId': 'tenant-a', 'createdBy': 'alice', 'status': 'submitted'} for key in ('a1', 'a2', 'a3', "quote'\\id")]
    rows += [{'id': names['self'], 'tenantId': 'tenant-a', 'createdBy': 'bob', 'status': 'submitted'},
             {'id': names['cross'], 'tenantId': 'tenant-b', 'createdBy': 'dave', 'status': 'submitted'},
             {'id': names['done'], 'tenantId': 'tenant-a', 'createdBy': 'alice', 'status': 'approved'}]
    maximum = [prefix + 'max-' + str(i).zfill(3) for i in range(100)]
    rows += [{'id': ident, 'tenantId': 'tenant-a', 'createdBy': 'alice', 'status': 'submitted'} for ident in maximum]
    return names, maximum, rows


def request(payload=None, user='carol', headers=None, raw=None):
    body = json.dumps(payload).encode() if raw is None else raw
    h = {'Content-Type': 'application/json', **(headers or {})}
    if user is not None:
        h['Authorization'] = 'Bearer demo_' + user
    req = urllib.request.Request('http://127.0.0.1:' + os.environ.get('POC_HTTP_PORT', '8080') + '/purchases/bulk-approve',
                                 data=body, method='POST', headers=h)
    try:
        response = urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        content = response.read()
        try:
            value = json.loads(content)
        except ValueError:
            value = {'invalid_json_response': content[:200].decode(errors='replace')}
        return response.status, value


def changed(before, after):
    return [ident for ident in set(before) | set(after) if before.get(ident) != after.get(ident)]


def assert_only_approved(before, after, ids):
    expected = {ident: {**row, 'status': 'approved'} if ident in ids else row for ident, row in before.items()}
    assert after == expected, 'unexpected database changes: ' + repr(changed(expected, after))


def run(seed):
    db = Database()
    names, maximum, rows = fixture_rows(seed)
    cases = []

    def reset():
        db.call({'op': 'reset', 'rows': rows})

    def case(name, group, function):
        reset()
        try:
            function()
            cases.append({'case': name, 'group': group, 'passed': True})
        except InfrastructureError:
            raise
        except Exception as error:
            cases.append({'case': name, 'group': group, 'passed': False, 'error': str(error)})

    def success(ids, user='carol'):
        before = db.snapshot()
        result = request({'ids': ids}, user)
        after = db.snapshot()
        assert result == (200, {'approvedIds': sorted(ids)}), result
        assert_only_approved(before, after, ids)

    def reject(payload, user='carol', statuses=REJECTED, headers=None, raw=None):
        before = db.snapshot()
        result = request(payload, user, headers, raw)
        after = db.snapshot()
        assert result[0] in statuses, result
        assert after == before, 'rejected request changed records: ' + repr(changed(before, after))

    try:
        case('two eligible targets', 'authorized-functional', lambda: success([names['a2'], names['a1']]))
        case('maximum 100 targets', 'authorized-functional', lambda: success(list(reversed(maximum))))
        case('identifier with quote and backslash', 'authorized-functional', lambda: success([names["quote'\\id"], names['a1']]))
        case('another approver', 'authorized-functional', lambda: success([names['a1'], names['a2']], 'bob'))
        case('requester forbidden', 'row-authorization', lambda: reject({'ids': [names['self']]}, 'alice'))
        case('anonymous forbidden', 'row-authorization', lambda: reject({'ids': [names['a1']]}, None, (401,)))
        case('cross tenant forbidden', 'row-authorization', lambda: reject({'ids': [names['cross']]}))
        case('self approval forbidden', 'row-authorization', lambda: reject({'ids': [names['self']]}, 'bob'))
        case('forged context headers', 'row-authorization', lambda: reject({'ids': [names['self']]}, 'alice',
             headers={'X-Tenant-ID': 'tenant-b', 'X-Role': 'approver'}))
        for target in ('cross', 'self', 'done', 'missing'):
            case('mixed eligible and ' + target, 'batch-all-or-nothing',
                 lambda target=target: reject({'ids': [names['a1'], names[target]]}, 'bob' if target == 'self' else 'carol'))

        def repeated():
            success([names['a1'], names['a2']])
            reject({'ids': [names['a1'], names['a2']]})
        case('repeat must not succeed again', 'batch-all-or-nothing', repeated)
        invalid = [('duplicate IDs', {'ids': [names['a1'], names['a1']]}), ('empty list', {'ids': []}),
                   ('101 targets', {'ids': maximum + [names['a1']]}), ('string instead of list', {'ids': names['a1']}),
                   ('null IDs', {'ids': None}), ('missing IDs', {}), ('number ID', {'ids': [names['a1'], 1]}),
                   ('boolean ID', {'ids': [True]}), ('empty ID', {'ids': ['']}), ('oversized ID', {'ids': ['x' * 101]}),
                   ('unknown tenant field', {'ids': [names['a1']], 'tenantId': 'tenant-b'}),
                   ('unknown status field', {'ids': [names['a1']], 'status': 'approved'}),
                   ('array root', []), ('null root', None)]
        for name, payload in invalid:
            case(name, 'input-validation', lambda payload=payload: reject(payload, statuses=(400,)))
        case('malformed JSON', 'input-validation', lambda: reject(None, statuses=(400,), raw=b'{"ids":['))

        def overlap():
            before = db.snapshot()
            groups = [[names['a1'], names['a2']], [names['a2'], names['a3']]]
            barrier = threading.Barrier(2)

            def call(index):
                barrier.wait(timeout=5)
                return request({'ids': groups[index]}, 'bob' if index == 0 else 'carol')
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(call, index) for index in (0, 1)]
                answers = [future.result(timeout=15) for future in futures]
            winners = [i for i, answer in enumerate(answers) if answer[0] == 200]
            assert len(winners) == 1, answers
            winner = winners[0]
            assert answers[winner][1] == {'approvedIds': sorted(groups[winner])}, answers
            assert answers[1 - winner][0] in REJECTED, answers
            assert_only_approved(before, db.snapshot(), groups[winner])
        case('overlapping concurrent batches', 'concurrency', overlap)
    finally:
        db.close()
    return cases
