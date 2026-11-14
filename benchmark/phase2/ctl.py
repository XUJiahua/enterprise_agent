#!/usr/bin/env python3
"""Four-cell atomic bulk-approval experiment. Separate from frozen v1 pilots."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'benchmark'))
import codex_runner as driver

EXPORT = ('go.mod', 'go.sum', 'cmd/server/main.go', 'internal/app/server.go')
FROZEN = ('benchmark/phase2/ctl.py', 'benchmark/phase2/oracle.py', 'benchmark/phase2/db.mjs',
          'benchmark/phase2/contract.json', 'benchmark/phase2/public_smoke.py',
          'benchmark/codex_runner.py', 'tests/acceptance.py', 'scripts/db.mjs',
          'db/bootstrap.sql', 'db/baseline-grants.sql', 'db/migrations/001_purchase.sql',
          'db/seed.sql', 'generated/guard.sql')
IGNORES = 'bin/\n*.log\n__pycache__/\n'
OBSERVED = ('passed', 'acceptance-failed', 'candidate-error', 'budget-exceeded', 'scope-violation')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write('\n')


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def process(args, **kwargs):
    return subprocess.run(list(map(str, args)), capture_output=True, text=True, check=True, **kwargs)


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def load_plan(path):
    value = json.loads(Path(path).read_text())
    if value.get('version') != 2 or value.get('kind') not in ('model-trial', 'synthetic-smoke'):
        raise ValueError('phase 2 plan required')
    if set(value['frozen']) != set(FROZEN):
        raise ValueError('incomplete evaluator manifest')
    for name, checksum in value['frozen'].items():
        if sha((ROOT / name).read_bytes()) != checksum:
            raise ValueError('evaluator drift: ' + name)
    contract = json.loads((HERE / 'contract.json').read_text())
    cells = {c['id']: c for c in contract['cells']}
    identifiers = set()
    for trial in value['trials']:
        if trial['id'] in identifiers or trial['cell'] not in cells or trial['route'] != cells[trial['cell']]['route']:
            raise ValueError('invalid/duplicate cell trial')
        identifiers.add(trial['id'])
        if trial['prompt'] != prompt(cells[trial['cell']], contract) or sha(trial['prompt'].encode()) != trial['prompt_sha256']:
            raise ValueError('prompt differs from registered cell')
    blocks = {}
    for trial in value['trials']:
        if type(trial['instance']) is not int or trial['instance'] < 1 or trial['id'] != trial['cell'] + '--' + str(trial['instance']).zfill(3):
            raise ValueError('invalid instance identity')
        blocks.setdefault(trial['instance'], set()).add(trial['cell'])
    if not blocks or sorted(blocks) != list(range(1, len(blocks) + 1)) or any(group != set(cells) for group in blocks.values()):
        raise ValueError('incomplete four-cell block')
    if set(value['export_files']) != set(EXPORT):
        raise ValueError('invalid source export manifest')
    for name, checksum in value['export_files'].items():
        content = subprocess.check_output(['git', '-C', str(ROOT), 'show', value['base_sha'] + ':' + name])
        if sha(content) != checksum:
            raise ValueError('source base differs from export checksum')
    return value


def prompt(cell, contract):
    common = '\n'.join([
        contract['requirements'],
        'Runtime mode: ' + cell['route'] + '. Preserve the existing interfaces and runtime identity binding.',
        'Only modify/add internal/app/ source. Do not change dependencies, main, task, visible tests or Git settings.',
        'Do not read evaluator files, private fixtures, credentials or other checkouts; do not use other agents.',
        'Use installed dependencies. Do not commit or push. Candidate changes will be committed and independently evaluated.',
        'Visible commands: go test ./...; python3 visible_smoke.py --base-url http://127.0.0.1:8080 (when a public test service is available).',
        'PRIVATE evaluation database credentials are not supplied during generation. PUBLIC_DATABASE_URL, if provided by the operator, is for public fixtures only.',
    ])
    return common + ('\n' + contract['process_guidance'] if cell['process_guidance'] else '')


def fixtures(args):
    save(args.output, {'version': 1, 'seed': secrets.token_hex(16)})


def plan(args):
    if args.allow_dirty_harness and not args.synthetic:
        raise ValueError('dirty harness is allowed only for synthetic controls')
    if git(ROOT, 'status', '--porcelain') and not args.allow_dirty_harness:
        raise ValueError('commit evaluator changes before freezing a model cohort')
    if args.repetitions not in range(1, 101) or args.time_budget < 1:
        raise ValueError('invalid repetition count or time budget')
    private = json.loads(Path(args.fixtures).read_text())
    if private.get('version') != 1 or len(private.get('seed', '')) < 16:
        raise ValueError('private fixture seed file required')
    base = git(ROOT, 'rev-parse', '--verify', args.base + '^{commit}')
    contract = json.loads((HERE / 'contract.json').read_text())
    trials = []
    for repetition in range(1, args.repetitions + 1):
        # One block uses one fixture set across all four cells.
        block = []
        for cell in contract['cells']:
            text = prompt(cell, contract)
            block.append({'id': cell['id'] + '--' + str(repetition).zfill(3), 'cell': cell['id'],
                          'route': cell['route'], 'instance': repetition, 'prompt': text,
                          'prompt_sha256': sha(text.encode())})
        random.Random(args.seed + repetition).shuffle(block)
        trials.extend(block)
    value = {'version': 2, 'kind': 'synthetic-smoke' if args.synthetic else 'model-trial',
             'task': contract['task'], 'base_sha': base, 'harness_sha': git(ROOT, 'rev-parse', 'HEAD'),
             'frozen': {p: sha((ROOT / p).read_bytes()) for p in FROZEN},
             'export_files': {p: sha(subprocess.check_output(['git', '-C', str(ROOT), 'show', base + ':' + p])) for p in EXPORT},
             'fixtures_sha256': sha(Path(args.fixtures).read_bytes()), 'seed': args.seed,
             'model': args.model, 'reasoning_effort': args.reasoning_effort,
             'generation_budget_seconds': args.time_budget, 'trials': trials}
    save(args.output, value)
    print(json.dumps({'planned': len(trials), 'kind': value['kind']}))


def trial_for(experiment, ident):
    values = [t for t in experiment['trials'] if t['id'] == ident]
    if len(values) != 1:
        raise ValueError('trial not in plan')
    return values[0]


def export_contents(experiment):
    content = {p: subprocess.check_output(['git', '-C', str(ROOT), 'show', experiment['base_sha'] + ':' + p]) for p in EXPORT}
    contract = json.loads((HERE / 'contract.json').read_text())
    content['TASK.md'] = (contract['requirements'] + '\n').encode()
    content['visible_smoke.py'] = (HERE / 'public_smoke.py').read_bytes()
    content['.gitignore'] = IGNORES.encode()
    return content


def export_candidate(args):
    experiment = load_plan(args.plan)
    trial = trial_for(experiment, args.trial)
    candidate = Path(args.candidate).resolve()
    manifest = Path(args.manifest).resolve()
    if candidate == ROOT or candidate.is_relative_to(ROOT) or manifest.is_relative_to(candidate):
        raise ValueError('candidate must be outside evaluator checkout; manifest must be outside candidate')
    if candidate.exists() or manifest.exists():
        raise ValueError('candidate/export manifest already exists')
    candidate.mkdir(parents=True)
    files = export_contents(experiment)
    for name, data in files.items():
        path = candidate / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    process(['git', 'init', '-b', 'candidate'], cwd=candidate)
    process(['git', 'add', '.'], cwd=candidate)
    process(['git', '-c', 'user.name=POC Export', '-c', 'user.email=poc@example.invalid', 'commit', '-m', 'isolated candidate base'], cwd=candidate)
    save(manifest, {'version': 2, 'trial_id': trial['id'], 'plan_sha256': sha(Path(args.plan).read_bytes()),
                    'initial_sha': git(candidate, 'rev-parse', 'HEAD'), 'source_base_sha': experiment['base_sha'],
                    'files': {p: sha(data) for p, data in files.items()}})
    print(json.dumps({'candidate': str(candidate), 'initial_sha': git(candidate, 'rev-parse', 'HEAD')}))


def validate_candidate(args, experiment):
    candidate = Path(args.candidate).resolve()
    manifest = json.loads(Path(args.manifest).read_text())
    if Path(args.manifest).resolve().is_relative_to(candidate) or candidate == ROOT:
        raise ValueError('trusted export attestation cannot be inside candidate')
    if manifest['trial_id'] != args.trial or manifest['plan_sha256'] != sha(Path(args.plan).read_bytes()) or manifest['source_base_sha'] != experiment['base_sha']:
        raise ValueError('export attestation identity mismatch')
    expected = {p: sha(data) for p, data in export_contents(experiment).items()}
    if manifest['files'] != expected:
        raise ValueError('export file manifest differs from plan')
    initial = manifest['initial_sha']
    if git(candidate, 'rev-list', '--count', initial) != '1' or git(candidate, 'remote'):
        raise ValueError('candidate export must have one initial commit and no remote')
    names = git(candidate, 'ls-tree', '-r', '--name-only', initial).splitlines()
    if set(names) != set(expected):
        raise ValueError('candidate history exposes unexpected files')
    for path, checksum in expected.items():
        if sha(subprocess.check_output(['git', '-C', str(candidate), 'show', initial + ':' + path])) != checksum:
            raise ValueError('candidate initial snapshot changed')
    process(['git', '-C', candidate, 'merge-base', '--is-ancestor', initial, 'HEAD'])
    if git(candidate, 'status', '--porcelain'):
        raise ValueError('candidate must be committed and clean')
    changed = git(candidate, 'diff', '--name-only', initial, 'HEAD').splitlines()
    if any(not p.startswith('internal/app/') for p in changed):
        raise ValueError('candidate modified protected export files')
    return candidate


def evaluate(args):
    experiment = load_plan(args.plan)
    trial = trial_for(experiment, args.trial)
    candidate = validate_candidate(args, experiment)
    if sha(Path(args.fixtures).read_bytes()) != experiment['fixtures_sha256']:
        raise ValueError('private fixture checksum differs from plan')
    private = json.loads(Path(args.fixtures).read_text())
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=False)
    result = {'version': 2, 'kind': experiment['kind'], 'plan_sha256': sha(Path(args.plan).read_bytes()),
              'trial': trial, 'candidate_sha': git(candidate, 'rev-parse', 'HEAD'),
              'manifest_sha256': sha(Path(args.manifest).read_bytes()), 'cases': [], 'groups': {}, 'passed': False}
    server = None
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', int(os.environ.get('POC_HTTP_PORT', '8080')))) == 0:
            raise ValueError('evaluation port occupied')
    # A private reset failure is infrastructure, never an observed coding failure.
    started = time.monotonic()
    try:
        process(['node', ROOT / 'scripts/db.mjs', 'reset', trial['route']], cwd=ROOT)
    except subprocess.CalledProcessError as error:
        result.update({'outcome': 'infrastructure-blocked', 'error': error.stderr})
        save(out / 'result.json', result)
        return 3
    oracle = module('phase2_oracle', HERE / 'oracle.py')
    binary = out / 'server'
    with (out / 'candidate.log').open('x') as log:
        try:
            subprocess.run(['go', 'build', '-buildvcs=false', '-o', str(binary), './cmd/server'], cwd=candidate,
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
            env = {k: os.environ[k] for k in ('PATH', 'HOME', 'LANG', 'TMPDIR', 'DATABASE_URL', 'POC_HTTP_PORT') if k in os.environ}
            server = subprocess.Popen([str(binary), '--mode', trial['route']], cwd=candidate, env=env,
                                      stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            import urllib.request
            for _ in range(100):
                if server.poll() is not None:
                    raise RuntimeError('candidate exited before readiness')
                try:
                    with urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('POC_HTTP_PORT', '8080') + '/health', timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError('candidate readiness timeout')
            seed = private['seed'] + ':' + str(trial['instance'])
            cases = oracle.run(seed)
            common = module('phase2_common', ROOT / 'tests/acceptance.py')
            cases += [{'group': 'original-regression', **case} for case in common.run()]
            result['cases'] = cases
            for group in {case['group'] for case in cases}:
                selected = [case for case in cases if case['group'] == group]
                result['groups'][group] = {'passed': sum(c['passed'] for c in selected), 'total': len(selected)}
            expected = json.loads((HERE / 'contract.json').read_text())['scoring_counts']
            if {group: count['total'] for group, count in result['groups'].items()} != expected:
                raise oracle.InfrastructureError('incomplete evaluator case manifest')
            result['passed'] = all(case['passed'] for case in cases)
            result['outcome'] = 'passed' if result['passed'] else 'acceptance-failed'
        except oracle.InfrastructureError as error:
            result.update({'outcome': 'infrastructure-blocked', 'error': str(error)})
        except (subprocess.SubprocessError, RuntimeError, OSError) as error:
            result.update({'outcome': 'candidate-error', 'error': str(error)})
        finally:
            if server:
                try:
                    os.killpg(server.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(server.pid, signal.SIGKILL)
                    server.wait()
            binary.unlink(missing_ok=True)
    result['evaluation_seconds'] = round(time.monotonic() - started, 3)
    save(out / 'result.json', result)
    print(json.dumps({'trial': trial['id'], 'outcome': result['outcome'], 'cases': len(result['cases'])}))
    return 0 if result['passed'] else (3 if result['outcome'] == 'infrastructure-blocked' else 2)


def summarize(args):
    experiment = load_plan(args.plan)
    known = {t['id']: t for t in experiment['trials']}
    records = {}
    for directory in args.inputs:
        for path in Path(directory).rglob('result.json'):
            value = json.loads(path.read_text())
            ident = value['trial']['id']
            if (value['plan_sha256'] != sha(Path(args.plan).read_bytes()) or value['kind'] != experiment['kind']
                    or value['trial'] != known.get(ident)):
                raise ValueError('foreign cohort or trial result')
            if value.get('passed') is True:
                expected = json.loads((HERE / 'contract.json').read_text())['scoring_counts']
                counts = {group: sum(c['group'] == group for c in value.get('cases', [])) for group in expected}
                if value['outcome'] != 'passed' or counts != expected or not all(c['passed'] for c in value['cases']) or {c['group'] for c in value['cases']} != set(expected):
                    raise ValueError('passed result lacks complete grouped evidence')
            elif value['outcome'] == 'passed':
                raise ValueError('inconsistent passed outcome')
            if ident in records:
                raise ValueError('duplicate/conflicting trial result: ' + ident)
            records[ident] = value
    rows = []
    for cell in json.loads((HERE / 'contract.json').read_text())['cells']:
        planned = [t for t in experiment['trials'] if t['cell'] == cell['id']]
        values = [records[t['id']] for t in planned if t['id'] in records]
        observed = [v for v in values if v['outcome'] in OBSERVED]
        passed = sum(v.get('passed') is True for v in observed)
        rows.append({'cell': cell['id'], 'planned': len(planned), 'observed': len(observed), 'passed': passed,
                     'unmeasured': len(planned) - len(observed),
                     'first_pass_rate': passed / len(planned) if planned and len(observed) == len(planned) else None})
    value = {'version': 2, 'kind': experiment['kind'], 'rows': rows,
             'note': 'Synthetic controls are not model samples; missing/infrastructure trials remain unmeasured.'}
    if args.output:
        save(args.output, value)
    print(json.dumps(value, indent=2))


def run(args):
    experiment = load_plan(args.plan)
    if bool(args.synthetic_test_driver) != (experiment['kind'] == 'synthetic-smoke'):
        raise ValueError('synthetic driver and model plan cannot be mixed')
    if sha(Path(args.fixtures).read_bytes()) != experiment['fixtures_sha256']:
        raise ValueError('private fixture checksum differs from plan')
    selected = [t for t in experiment['trials'] if not args.trial or t['id'] == args.trial]
    if not selected or len(selected) > 8:
        raise ValueError('select between 1 and 8 trials')
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=False)
    binary = args.synthetic_test_driver or 'codex'
    check = driver.preflight(binary, experiment['model'], experiment['reasoning_effort'], out,
                             experiment['kind'] == 'synthetic-smoke')
    batch = {'version': 2, 'kind': experiment['kind'], 'plan_sha256': sha(Path(args.plan).read_bytes()), 'trials': []}
    if not check['ready']:
        batch['status'] = 'preflight-blocked'
        save(out / 'batch.json', batch)
        return 3
    import tempfile
    candidates = Path(args.candidate_root).resolve() if args.candidate_root else Path(tempfile.mkdtemp(prefix='poc-phase2-candidates-'))
    batch['candidate_root'] = str(candidates)
    for trial in selected:
        candidate = candidates / trial['id']
        manifest = out / 'exports' / (trial['id'] + '.json')
        export_candidate(argparse.Namespace(plan=args.plan, trial=trial['id'], candidate=candidate, manifest=manifest))
        directory = out / 'generation' / trial['id']
        generated = driver.invoke(binary, experiment['model'], experiment['reasoning_effort'], candidate,
                                  trial['prompt'], directory, experiment['generation_budget_seconds'], 'workspace-write')
        load_plan(args.plan)
        row = {'trial': trial, 'generation_status': generated['status'], 'usage': generated['usage'],
               'generation_seconds': generated['generation_seconds'], 'prompt_sha256': trial['prompt_sha256']}
        batch['trials'].append(row)
        if generated['status'] != 'completed':
            row['outcome'] = generated['status']
            save(out / 'evaluations' / trial['id'] / 'result.json', {'version': 2, 'kind': experiment['kind'],
                 'plan_sha256': sha(Path(args.plan).read_bytes()), 'trial': trial, 'outcome': generated['status'], 'passed': False})
            if generated['status'] != 'budget-exceeded':
                batch['status'] = 'generation-blocked'
                break
            continue
        changes = driver.changed_paths(candidate)
        if any(not p.startswith('internal/app/') for p in changes):
            row['outcome'] = 'scope-violation'
            save(out / 'evaluations' / trial['id'] / 'result.json', {'version': 2, 'kind': experiment['kind'],
                 'plan_sha256': sha(Path(args.plan).read_bytes()), 'trial': trial, 'outcome': 'scope-violation', 'passed': False})
            continue
        if changes:
            process(['git', '-C', candidate, 'add', '--', *changes])
        (directory / 'candidate.patch').write_bytes(subprocess.check_output(['git', '-C', str(candidate), 'diff', '--cached', '--binary', '--no-ext-diff']))
        process(['git', '-C', candidate, '-c', 'user.name=POC Phase 2', '-c', 'user.email=poc@example.invalid',
                 'commit', '--allow-empty', '-m', 'phase 2 candidate'])
        row['candidate_sha'] = git(candidate, 'rev-parse', 'HEAD')
        process(['git', '-C', candidate, 'bundle', 'create', directory / 'candidate.bundle', '--all'])
        code = evaluate(argparse.Namespace(plan=args.plan, trial=trial['id'], candidate=candidate,
                         manifest=manifest, fixtures=args.fixtures, output=out / 'evaluations' / trial['id']))
        row['evaluation_exit'] = code
        if code == 3:
            batch['status'] = 'evaluation-blocked'
            break
    else:
        batch['status'] = 'selected-completed'
    save(out / 'batch.json', batch)
    return 0 if batch['status'] == 'selected-completed' else 3


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    f = sub.add_parser('fixtures'); f.add_argument('--output', required=True)
    p = sub.add_parser('plan')
    for name in ('base', 'model', 'fixtures', 'output'): p.add_argument('--' + name, required=True)
    p.add_argument('--repetitions', type=int, default=1); p.add_argument('--time-budget', type=int, default=180)
    p.add_argument('--reasoning-effort', choices=('low', 'medium', 'high'), default='medium')
    p.add_argument('--seed', type=int, default=20261114); p.add_argument('--synthetic', action='store_true'); p.add_argument('--allow-dirty-harness', action='store_true')
    for action in ('export', 'evaluate'):
        s = sub.add_parser(action)
        for name in ('plan', 'trial', 'candidate', 'manifest'): s.add_argument('--' + name, required=True)
        if action == 'evaluate':
            s.add_argument('--fixtures', required=True); s.add_argument('--output', required=True)
    s = sub.add_parser('summarize'); s.add_argument('--plan', required=True); s.add_argument('--inputs', action='append', required=True); s.add_argument('--output')
    s = sub.add_parser('run'); s.add_argument('--plan', required=True); s.add_argument('--fixtures', required=True); s.add_argument('--output', required=True)
    s.add_argument('--trial'); s.add_argument('--synthetic-test-driver'); s.add_argument('--candidate-root')
    args = parser.parse_args()
    try:
        return {'fixtures': fixtures, 'plan': plan, 'export': export_candidate, 'evaluate': evaluate,
                'summarize': summarize, 'run': run}[args.command](args) or 0
    except Exception as error:
        print('phase 2 error: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
