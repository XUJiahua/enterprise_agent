#!/usr/bin/env python3
"""Execute bounded Codex trials and retain provenance outside each candidate.

This driver never changes authentication, CODEX_HOME, approval rules, or sandbox
policy. A runtime/provider failure is an unmeasured trial, not a coding defect.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

import benchctl as bench

ROOT = bench.ROOT
ALLOWED = {
    'baseline': ('internal/app/',),
    'guarded': ('internal/app/',),
    'zenstack': ('apps/zenstack/src/',),
}


def parse_events(stdout, stderr, returncode, timed_out=False):
    events = []
    malformed = False
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            if not isinstance(event, dict) or not isinstance(event.get('type'), str):
                raise ValueError('invalid event')
            events.append(event)
        except ValueError:
            malformed = True
    started = any(e['type'] == 'turn.started' for e in events)
    completed = [e for e in events if e['type'] == 'turn.completed']
    errors = any(e['type'] in ('error', 'turn.failed') for e in events)
    usage = None
    if len(completed) == 1:
        reported = completed[0].get('usage')
        if isinstance(reported, dict):
            keys = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens')
            usage = {k: v for k, v in reported.items() if k in keys and type(v) is int and v >= 0}
            if not usage:
                usage = None
    if timed_out:
        status = 'budget-exceeded' if started else 'runtime-blocked'
    elif returncode != 0 or errors:
        status = 'provider-error' if started else 'runtime-blocked'
    elif malformed or len(completed) != 1 or not started:
        status = 'protocol-error'
    else:
        status = 'completed'
    return {
        'status': status,
        'turn_started': started,
        'turn_completed': len(completed) == 1,
        'usage': usage,
        'event_count': len(events),
        'returncode': returncode,
        'timed_out': timed_out,
        'agent_messages': [e.get('item', {}).get('text', '') for e in events
                           if e['type'] == 'item.completed' and e.get('item', {}).get('type') == 'agent_message'],
        'diagnostic': stderr[-2000:],
    }


def invoke(binary, model, effort, cwd, prompt, directory, timeout, sandbox):
    directory.mkdir(parents=True, exist_ok=False)
    command = [binary, 'exec', '--json', '--ephemeral', '--color', 'never',
               '--sandbox', sandbox, '--model', model, '-C', str(cwd),
               '-c', 'model_reasoning_effort=' + json.dumps(effort), '-']
    env = {k: v for k, v in os.environ.items()
           if k not in ('ADMIN_DATABASE_URL', 'DATABASE_URL', 'POC_DB_RESET', 'POC_MUTATION', 'POC_ALLOW_MUTATIONS')}
    started = time.monotonic()
    timed_out = False
    with (directory / 'events.jsonl').open('x') as stdout, (directory / 'stderr.log').open('x') as stderr:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                   stdout=stdout, stderr=stderr, text=True, start_new_session=True)
        try:
            process.communicate(prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
    result = parse_events((directory / 'events.jsonl').read_text(),
                          (directory / 'stderr.log').read_text(), process.returncode, timed_out)
    result.update({'generation_seconds': round(time.monotonic() - started, 3),
                   'requested_model': model, 'reasoning_effort': effort,
                   'sandbox': sandbox, 'command': command})
    bench.save(directory / 'invocation.json', result)
    return result


def preflight(binary, model, effort, out, synthetic=False):
    try:
        version = subprocess.check_output([binary, '--version'], text=True, timeout=10).strip()
        invocation = invoke(binary, model, effort, ROOT,
                            'Reply exactly READY. Do not read or modify files. Do not call tools.',
                            out / 'probe', 30, 'read-only')
    except (OSError, subprocess.SubprocessError) as error:
        version = None
        invocation = {'status': 'runtime-blocked', 'turn_started': False,
                      'turn_completed': False, 'usage': None, 'diagnostic': str(error)}
    ready = invocation['status'] == 'completed' and invocation['agent_messages'][-1:] == ['READY']
    result = {
        'kind': 'synthetic-smoke' if synthetic else 'model-runtime-preflight',
        'ready': ready, 'cli_version': version,
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'codex_home_writable': os.access(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')), os.W_OK),
        'invocation': invocation,
    }
    bench.save(out / 'preflight.json', result)
    return result


def doctor(args):
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=False)
    result = preflight(args.codex_bin, args.model, args.reasoning_effort, out)
    print(json.dumps({'ready': result['ready'], 'status': result['invocation']['status'],
                      'report': str(out / 'preflight.json')}))
    return 0 if result['ready'] else 3


def prompt_for(trial):
    return '\n'.join([
        'Implement one bounded enterprise application task in this candidate checkout.',
        'Route: ' + trial['route'],
        'Task: ' + trial['prompt'],
        'Allowed code paths: ' + ', '.join(ALLOWED[trial['route']]),
        'Preserve existing behavior, authorization and tenant boundaries.',
        'Do not edit or inspect benchmark/, tests/, generated/, policies/, db/, .github/ or experiment results.',
        'Do not commit, push, change Git settings, read credentials or use other agents.',
        'Use the existing installed dependencies. Do not install new dependencies.',
        'The independent evaluator will run after this process finishes.',
    ])


def changed_paths(candidate):
    tracked = subprocess.check_output(['git', '-C', str(candidate), 'diff', '--name-only', '-z', 'HEAD'])
    untracked = subprocess.check_output(['git', '-C', str(candidate), 'ls-files', '--others', '--exclude-standard', '-z'])
    return sorted({p.decode() for p in (tracked + untracked).split(b'\0') if p})


def finish_batch(out, batch):
    observed = [r for r in batch['trials'] if r['status'] in
                ('passed', 'acceptance-failed', 'candidate-error', 'scope-violation', 'budget-exceeded')]
    batch['observed_trials'] = len(observed)
    batch['passed_trials'] = sum(r['status'] == 'passed' for r in observed)
    batch['unmeasured_trials'] = batch['planned_trials'] - len(observed)
    # A blocked/incomplete cohort must never acquire a numeric first-pass rate.
    batch['first_pass_rate'] = (batch['passed_trials'] / len(observed)
                               if observed and len(observed) == batch['planned_trials'] else None)
    batch['finished_at'] = datetime.now(timezone.utc).isoformat()
    bench.save(out / 'batch.json', batch)
    print(json.dumps({'status': batch['status'], 'planned': batch['planned_trials'],
                      'observed': batch['observed_trials'], 'unmeasured': batch['unmeasured_trials'],
                      'first_pass_rate': batch['first_pass_rate']}))


def run(args):
    plan_path = Path(args.plan).resolve()
    experiment = json.loads(plan_path.read_text())
    bench.validate(experiment)
    synthetic = bool(args.synthetic_test_driver)
    if synthetic != (experiment['kind'] == 'synthetic-smoke'):
        raise ValueError('synthetic test drivers and model plans cannot be mixed')
    selected = [t for t in experiment['trials'] if not args.trial or t['id'] == args.trial]
    if not selected:
        raise ValueError('trial not found')
    if len(selected) > 9:
        raise ValueError('pilot is limited to 9 trials; select one trial or freeze a smaller plan')
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(plan_path, out / 'plan.json')
    binary = args.synthetic_test_driver or args.codex_bin
    effort = experiment['generation_reasoning_effort']
    batch = {'version': 1, 'kind': experiment['kind'], 'plan_sha256': bench.digest(plan_path),
             'model': experiment['model'], 'reasoning_effort': effort,
             'planned_trials': len(experiment['trials']), 'selected_trials': len(selected),
             'status': 'running', 'trials': []}
    check = preflight(binary, experiment['model'], effort, out, synthetic)
    if not check['ready']:
        batch.update({'status': 'blocked', 'blocked_stage': 'preflight'})
        finish_batch(out, batch)
        return 3
    batch['cli_version'] = check['cli_version']
    try:
        for trial in selected:
            print(json.dumps({'trial': trial['id'], 'stage': 'generation'}), flush=True)
            candidate = out / 'candidates' / trial['id']
            candidate.parent.mkdir(exist_ok=True)
            subprocess.run(['git', '-C', str(ROOT), 'worktree', 'add', '--detach', str(candidate),
                            experiment['base_sha']], check=True, capture_output=True)
            if trial['route'] == 'zenstack':
                shutil.copytree(ROOT / 'apps/zenstack/node_modules', candidate / 'apps/zenstack/node_modules', symlinks=True)
            directory = out / 'generation' / trial['id']
            invocation = invoke(binary, experiment['model'], effort, candidate, prompt_for(trial),
                                directory, experiment['generation_budget_seconds'], 'workspace-write')
            row = {'trial_id': trial['id'], 'route': trial['route'],
                   'generation_status': invocation['status'], 'usage': invocation['usage'],
                   'generation_seconds': invocation['generation_seconds']}
            batch['trials'].append(row)
            bench.validate(experiment)
            if invocation['status'] != 'completed':
                row['status'] = invocation['status']
                if invocation['status'] != 'budget-exceeded':
                    batch.update({'status': 'blocked', 'blocked_stage': 'generation'})
                    break
                continue
            paths = changed_paths(candidate)
            invalid = [p for p in paths if not p.startswith(ALLOWED[trial['route']])]
            if invalid:
                row.update({'status': 'scope-violation', 'paths': invalid})
                continue
            if paths:
                subprocess.run(['git', '-C', str(candidate), 'add', '--', *paths], check=True)
            patch = subprocess.check_output(['git', '-C', str(candidate), 'diff', '--cached', '--binary', '--no-ext-diff'])
            (directory / 'candidate.patch').write_bytes(patch)
            subprocess.run(['git', '-C', str(candidate), '-c', 'user.name=POC Model Pilot',
                            '-c', 'user.email=poc@example.invalid', 'commit', '--allow-empty', '-m',
                            'model pilot: ' + trial['id']], check=True, capture_output=True)
            row['candidate_sha'] = bench.git(candidate, 'rev-parse', 'HEAD')
            subprocess.run(['git', '-C', str(candidate), 'bundle', 'create', str(directory / 'candidate.bundle'),
                            experiment['base_sha'] + '..HEAD'], check=True, capture_output=True)
            print(json.dumps({'trial': trial['id'], 'stage': 'independent-evaluation'}), flush=True)
            command = [sys.executable, str(ROOT / 'benchmark/benchctl.py'), 'evaluate', '--plan', str(plan_path),
                       '--trial', trial['id'], '--candidate', str(candidate), '--attempt', '1',
                       '--output', str(out / 'attempts')]
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            (directory / 'evaluation.log').write_text(result.stdout + result.stderr)
            if result.returncode not in (0, 2):
                row['status'] = 'evaluation-blocked'
                batch.update({'status': 'blocked', 'blocked_stage': 'evaluation'})
                break
            assessment = json.loads((out / 'attempts' / (trial['id'] + '--attempt-001.json')).read_text())
            row['status'] = assessment['outcome']
            print(json.dumps({'trial': trial['id'], 'status': row['status']}), flush=True)
        else:
            batch['status'] = 'completed' if len(selected) == len(experiment['trials']) else 'selected-completed'
    except Exception as error:
        batch.update({'status': 'blocked', 'blocked_stage': 'runner', 'error': str(error)})
    finish_batch(out, batch)
    return 0 if batch['status'] in ('completed', 'selected-completed') else 3


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    probe = sub.add_parser('doctor')
    probe.add_argument('--model', required=True)
    probe.add_argument('--reasoning-effort', choices=('low', 'medium', 'high'), default='medium')
    probe.add_argument('--codex-bin', default='codex')
    probe.add_argument('--output', required=True)
    execute = sub.add_parser('run')
    execute.add_argument('--plan', required=True)
    execute.add_argument('--trial')
    execute.add_argument('--codex-bin', default='codex')
    execute.add_argument('--synthetic-test-driver')
    execute.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        return {'doctor': doctor, 'run': run}[args.command](args)
    except Exception as error:
        print('runner error: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
