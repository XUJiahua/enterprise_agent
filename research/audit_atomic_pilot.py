#!/usr/bin/env python3
"""Audit PR 10 evidence, without model calls or private seed access."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / 'docs/evidence/phase2-model-pilot-2026-11-14'


def sha(value):
    return hashlib.sha256(value).hexdigest()


def git(*args, cwd=ROOT):
    return subprocess.check_output(['git', '-C', str(cwd), *map(str, args)])


def read(root, name):
    return json.loads((root / name).read_text())


def restore(bundle, directory):
    subprocess.run(['git', 'clone', '--quiet', str(bundle), str(directory)], check=True)
    subprocess.run(['git', '-C', str(directory), 'remote', 'remove', 'origin'], check=True)


def audit():
    manifest = read(ARCHIVE, 'sha256.json')
    actual = {str(p.relative_to(ARCHIVE)) for p in ARCHIVE.rglob('*')
              if p.is_file() and p.name != 'sha256.json'}
    assert actual == set(manifest), 'archive manifest coverage'
    for name, checksum in manifest.items():
        path = (ARCHIVE / name).resolve()
        assert path.is_relative_to(ARCHIVE) and sha(path.read_bytes()) == checksum, name
    plan_path = ARCHIVE / 'plan.json'
    plan = read(ARCHIVE, 'plan.json')
    spec = importlib.util.spec_from_file_location('atomic_ctl', ROOT / 'benchmark/phase2/ctl.py')
    ctl = importlib.util.module_from_spec(spec); spec.loader.exec_module(ctl)
    assert ctl.load_plan(plan_path) == plan
    assert plan['kind'] == 'model-trial' and len(plan['trials']) == 4
    for name, checksum in plan['frozen'].items():
        assert sha(git('show', plan['harness_sha'] + ':' + name)) == checksum, name
    for name, checksum in plan['export_files'].items():
        assert sha(git('show', plan['base_sha'] + ':' + name)) == checksum, name
    plan_hash = sha(plan_path.read_bytes())
    batch = read(ARCHIVE, 'run/batch.json')
    assert batch['status'] == 'selected-completed' and batch['kind'] == 'model-trial'
    assert batch['plan_sha256'] == plan_hash
    assert [r['trial'] for r in batch['trials']] == plan['trials']
    summary = read(ARCHIVE, 'summary.json')
    assert summary['kind'] == 'model-trial' and len(summary['rows']) == 4
    assert {r['cell'] for r in summary['rows']} == {t['cell'] for t in plan['trials']}
    assert all((r['planned'], r['observed'], r['passed'], r['unmeasured'], r['first_pass_rate'])
               == (1, 1, 1, 0, 1) for r in summary['rows'])
    contract = read(ROOT, 'benchmark/phase2/contract.json')
    rows = []; exports = []
    with tempfile.TemporaryDirectory(prefix='atomic-audit-') as temporary:
        for trial, generated in zip(plan['trials'], batch['trials']):
            ident = trial['id']; prefix = 'run/generation/' + ident + '/'
            invocation = read(ARCHIVE, prefix + 'invocation.json')
            events = [json.loads(line) for line in (ARCHIVE / (prefix + 'events.jsonl')).read_text().splitlines() if line.strip()]
            completed = [e for e in events if e['type'] == 'turn.completed']
            assert len(completed) == 1 and invocation['status'] == 'completed'
            assert invocation['usage'] == generated['usage']
            assert {k: completed[0]['usage'].get(k) for k in invocation['usage']} == invocation['usage']
            assert invocation['generation_seconds'] == generated['generation_seconds']
            assert 0 < invocation['generation_seconds'] <= plan['generation_budget_seconds']
            assert invocation['requested_model'] == plan['model']
            assert invocation['reasoning_effort'] == plan['reasoning_effort']
            assert invocation['sandbox'] == 'workspace-write'
            assert generated['prompt_sha256'] == trial['prompt_sha256'] == sha(trial['prompt'].encode())
            assert generated['generation_status'] == 'completed' and generated['evaluation_exit'] == 0
            result = read(ARCHIVE, 'run/evaluations/' + ident + '/result.json')
            assert result['trial'] == trial and result['kind'] == 'model-trial'
            assert result['plan_sha256'] == plan_hash and result['outcome'] == 'passed' and result['passed']
            counts = {g: sum(c['group'] == g for c in result['cases']) for g in contract['scoring_counts']}
            assert counts == contract['scoring_counts'] and all(c['passed'] for c in result['cases'])
            assert result['groups'] == {g: {'passed': n, 'total': n} for g, n in counts.items()}
            export_path = ARCHIVE / ('run/exports/' + ident + '.json')
            export = json.loads(export_path.read_text()); exports.append(export['files'])
            assert result['manifest_sha256'] == sha(export_path.read_bytes())
            candidate = Path(temporary) / ident
            restore(ARCHIVE / (prefix + 'candidate.bundle'), candidate)
            args = argparse.Namespace(plan=plan_path, trial=ident, candidate=candidate, manifest=export_path)
            ctl.validate_candidate(args, plan)
            assert git('rev-parse', 'HEAD', cwd=candidate).decode().strip() == result['candidate_sha'] == generated['candidate_sha']
            assert git('rev-list', '--count', 'HEAD', cwd=candidate).decode().strip() == '2'
            patch = git('diff', '--binary', '--no-ext-diff', export['initial_sha'], 'HEAD', cwd=candidate)
            assert patch == (ARCHIVE / (prefix + 'candidate.patch')).read_bytes()
            source = (candidate / 'internal/app/bulk_approve.go').read_text()
            commands = [e['item'] for e in events if e['type'] == 'item.completed'
                        and e.get('item', {}).get('type') == 'command_execution']
            usage = invocation['usage']; assert usage['cached_input_tokens'] <= usage['input_tokens']
            rows.append({'cell': trial['cell'], 'trial_id': ident, 'candidate_sha': result['candidate_sha'],
                         'original_cases': len(result['cases']), 'original_passed': result['passed'],
                         'generation_seconds': invocation['generation_seconds'], 'usage': usage,
                         'noncached_input_tokens': usage['input_tokens'] - usage['cached_input_tokens'],
                         'extra_reported_usage': {k: v for k, v in completed[0]['usage'].items() if k not in usage},
                         'successful_go_test_commands': sum('go test ./...' in c['command'] and c.get('exit_code') == 0 for c in commands),
                         'outside_candidate_agent_inventory_observed': any('rg --files -g AGENTS.md /home/' in c['command'] for c in commands),
                         'query_shape': 'per-id-lock-select' if 'tx.QueryRow(' in source else 'set-lock-select',
                         'static_lock_selects_for_100_ids': 100 if 'tx.QueryRow(' in source else 1,
                         'static_update_statements_for_100_ids': 1,
                         'explicit_body_limit_bytes': 65536 if '65536' in source else None,
                         'candidate_authored_test_files': git('ls-tree', '-r', '--name-only', 'HEAD', cwd=candidate).decode().count('_test.go')})
    assert all(value == exports[0] for value in exports), 'unequal exported source'
    replay = read(ARCHIVE, 'replay/result.json')
    target = next(r for r in rows if r['cell'] == 'go-runtime-process')
    assert replay['candidate_sha'] == target['candidate_sha'] and replay['plan_sha256'] == plan_hash
    assert replay['passed'] and len(replay['cases']) == 46 and all(c['passed'] for c in replay['cases'])
    return {'kind': 'archived-model-evidence-audit', 'new_model_generations': 0,
            'archive_files_verified': len(manifest), 'frozen_harness_files_verified': len(plan['frozen']),
            'plan_sha256': plan_hash, 'base_sha': plan['base_sha'], 'harness_sha': plan['harness_sha'],
            'trials': rows, 'original_planned': 4, 'original_observed': 4, 'original_unmeasured': 0,
            'source_exports_identical': True,
            'note': 'Static query counts describe these four code paths; no latency or framework-effect estimate. Original private fixture seed is unavailable here.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--out', default='reports/atomic-followup/audit.json')
    args = parser.parse_args(); report = audit()
    path = Path(args.out); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream: json.dump(report, stream, indent=2); stream.write('\n')
    print(json.dumps({'archive_files_verified': report['archive_files_verified'], 'candidates_verified': len(report['trials'])}))
