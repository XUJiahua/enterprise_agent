#!/usr/bin/env python3
"""Audit existing model evidence; this script never calls a model or edits it."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'docs/evidence/model-pilot-success-2026-11-14'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def audit(root):
    root = root.resolve()
    manifest = json.loads((root / 'sha256.json').read_text())
    for relative, checksum in manifest.items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise ValueError('manifest path leaves archive')
        if sha(path.read_bytes()) != checksum:
            raise ValueError('archive checksum mismatch: ' + relative)
    actual = {str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and p.name != 'sha256.json'}
    if actual != set(manifest):
        raise ValueError('archive manifest coverage differs from files')
    cohort = root / 'pilot-handoff-003'
    plan_bytes = (cohort / 'plan.json').read_bytes()
    plan = json.loads(plan_bytes)
    batch = json.loads((cohort / 'batch.json').read_text())
    if batch['plan_sha256'] != sha(plan_bytes) or plan['dirty_harness']:
        raise ValueError('invalid plan provenance')
    for path, checksum in plan['frozen'].items():
        content = subprocess.check_output(['git', '-C', str(ROOT), 'show', plan['harness_sha'] + ':' + path])
        if sha(content) != checksum:
            raise ValueError('frozen harness mismatch: ' + path)
    if len({t['id'] for t in plan['trials']}) != len(plan['trials']):
        raise ValueError('duplicate planned trial')
    batch_rows = {r['trial_id']: r for r in batch['trials']}
    rows = []
    for trial in plan['trials']:
        ident = trial['id']
        directory = cohort / 'generation' / ident
        invocation = json.loads((directory / 'invocation.json').read_text())
        events = [json.loads(line) for line in (directory / 'events.jsonl').read_text().splitlines() if line.strip()]
        completed = [e for e in events if e['type'] == 'turn.completed']
        if len(completed) != 1 or invocation['status'] != 'completed':
            raise ValueError('incomplete generation: ' + ident)
        raw_usage = completed[0]['usage']
        for key, value in invocation['usage'].items():
            if raw_usage.get(key) != value:
                raise ValueError('usage mismatch: ' + ident + ':' + key)
        evaluation = json.loads((cohort / 'attempts' / (ident + '--attempt-001.json')).read_text())
        if (evaluation['trial'] != trial or evaluation['plan_sha256'] != sha(plan_bytes)
                or evaluation['base_sha'] != plan['base_sha'] or evaluation['harness_sha'] != plan['harness_sha']):
            raise ValueError('evaluation identity mismatch: ' + ident)
        expected_sha = batch_rows[ident]['candidate_sha']
        if evaluation['candidate_sha'] != expected_sha:
            raise ValueError('candidate differs across artifacts: ' + ident)
        bundle = directory / 'candidate.bundle'
        subprocess.run(['git', '-C', str(ROOT), 'bundle', 'verify', str(bundle)],
                       check=True, capture_output=True)
        heads = subprocess.check_output(['git', 'bundle', 'list-heads', str(bundle)], text=True).splitlines()
        if expected_sha + ' HEAD' not in heads:
            raise ValueError('bundle HEAD differs: ' + ident)
        commands = [e['item']['command'] for e in events if e.get('type') == 'item.completed'
                    and e.get('item', {}).get('type') == 'command_execution']
        usage = invocation['usage']
        if usage['cached_input_tokens'] > usage['input_tokens']:
            raise ValueError('cached input exceeds total input')
        rows.append({'trial_id': ident, 'task': trial['task'], 'route': trial['route'],
                     'candidate_sha': expected_sha, 'passed': evaluation['passed'],
                     'cases': len(evaluation['cases']), 'all_cases_passed': all(c['passed'] for c in evaluation['cases']),
                     'generation_seconds': invocation['generation_seconds'],
                     'evaluation_seconds': evaluation['evaluation_seconds'],
                     'input_tokens': usage['input_tokens'], 'cached_input_tokens': usage['cached_input_tokens'],
                     'noncached_input_tokens': usage['input_tokens'] - usage['cached_input_tokens'],
                     'output_tokens': usage['output_tokens'], 'command_count': len(commands),
                     'extra_reported_usage': {k: v for k, v in raw_usage.items() if k not in usage}})
    routes = []
    for route in ('baseline', 'guarded', 'zenstack'):
        group = [r for r in rows if r['route'] == route]
        routes.append({'route': route, 'planned': len(group), 'passed': sum(r['passed'] for r in group),
                       'generation_mean_seconds': round(statistics.mean(r['generation_seconds'] for r in group), 3),
                       'generation_median_seconds': statistics.median(r['generation_seconds'] for r in group),
                       'input_tokens': sum(r['input_tokens'] for r in group),
                       'cached_input_tokens': sum(r['cached_input_tokens'] for r in group),
                       'noncached_input_tokens': sum(r['noncached_input_tokens'] for r in group),
                       'output_tokens': sum(r['output_tokens'] for r in group)})
    return {'kind': 'existing-evidence-audit', 'source_pr': 7,
            'source_head': '5ab6856c7750722910471b5a5bd19a4ac1b1b079',
            'cohort': 'pilot-handoff-003', 'manifest_files_verified': len(manifest),
            'harness_sha': plan['harness_sha'], 'base_sha': plan['base_sha'],
            'plan_sha256': sha(plan_bytes), 'trials': rows, 'routes': routes,
            'new_model_generations': 0, 'cost': None,
            'audit_script_sha256': sha(Path(__file__).read_bytes()),
            'interpretation': 'Descriptive statistics for nine existing trials; no population inference or billed cost.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', type=Path, default=DEFAULT)
    parser.add_argument('--out', type=Path, default=ROOT / 'reports/research/pilot-audit.json')
    args = parser.parse_args()
    result = audit(args.archive)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'verified_files': result['manifest_files_verified'], 'candidates': len(result['trials']),
                      'passed': sum(t['passed'] for t in result['trials']), 'new_model_generations': 0}))


if __name__ == '__main__':
    main()
