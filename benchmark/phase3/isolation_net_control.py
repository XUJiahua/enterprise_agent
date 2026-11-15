#!/usr/bin/env python3
"""Positive/negative control for isolate_net.py on macOS (no model call unless --kiro).

Keeps every isolation_control.py file check and adds network checks: github via the
proxy, direct IPs bypassing the proxy (each paired with an unsandboxed baseline that
succeeds), DNS, other localhost ports, the allowlisted path, log forgery and, with
--kiro, a real READY plus a real tool call where the model itself runs curl github.
"""
import argparse
import json
import shlex
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from generation_worker import KIRO_AGENT  # noqa: E402  (same agent as real trials)
from isolate_net import DEFAULT_ALLOW  # noqa: E402

GITHUB_LITERAL_IP = '140.82.112.3'  # a published github.com address


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--kiro', action='store_true', help='also run real Kiro READY + curl-tool probes inside the sandbox')
    p.add_argument('--deny', action='append', default=[], help='extra real paths that must be unreadable')
    p.add_argument('--allow-write', action='append', default=[], help='CLI/toolchain cache paths')
    p.add_argument('--evidence', help='copy each run network.log / Kiro events here')
    a = p.parse_args()
    evidence = Path(a.evidence).resolve() if a.evidence else None
    if evidence:
        evidence.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='v3-net-isolation-') as temp:
        t = Path(temp).resolve()
        evaluator, seed, kit = t / 'evaluator', t / 'private', t / 'kit'
        for d in (evaluator, seed, kit):
            d.mkdir()
        (evaluator / 'oracle.py').write_text('secret oracle')
        (seed / 'fixtures.json').write_text('{"seed":"x"}')
        runs = []

        def run(cmd, name=None):
            out = t / 'out' / str(len(runs))
            base = [sys.executable, str(HERE / 'isolate_net.py'), '--kit', str(kit), '--output', str(out),
                    '--deny', str(evaluator), '--deny', str(seed), *sum((['--deny', d] for d in a.deny), []),
                    *sum((['--allow-write', d] for d in a.allow_write), [])]
            r = subprocess.run(['perl', '-e', 'alarm 300; exec @ARGV', *base, '--', 'sh', '-c', cmd.replace('{out}', str(out))],
                               capture_output=True, text=True)
            log = [json.loads(line) for line in (out / 'network.log').read_text().splitlines()]
            runs.append({'name': name or cmd[:80], 'returncode': r.returncode, 'network_log': log})
            if evidence and name:
                (evidence / (name + '.network.log')).write_text((out / 'network.log').read_text())
            return r.returncode == 0, log, out, r

        def outside(cmd):
            return subprocess.run(['perl', '-e', 'alarm 30; exec @ARGV', 'sh', '-c', cmd], capture_output=True).returncode == 0

        def denied(log, host):
            return any(e.get('host') == host and e.get('decision') == 'deny' for e in log)

        def allowed(log, host):
            return any(e.get('host') == host and e.get('decision') == 'allow' for e in log)

        checks = {
            'evaluator unreadable': not run('cat ' + str(evaluator / 'oracle.py'))[0],
            'seed unreadable': not run('cat ' + str(seed / 'fixtures.json'))[0],
            'evaluator unlistable': not run('ls ' + str(evaluator))[0],
            'home not writable': not run('touch "$HOME/.v3-isolation-probe"')[0],
            'kit writable': run('echo ok > ' + str(kit / 'probe'))[0] and (kit / 'probe').read_text() == 'ok\n',
            'output writable': run('mkdir -p {out} && echo ok > {out}/probe')[0],
        }
        for d in a.deny:
            checks['unreadable: ' + d] = not run('ls ' + json.dumps(d))[0]

        for url, host in (('https://github.com', 'github.com'), ('https://api.github.com', 'api.github.com')):
            ok, log, *_ = run('curl -sS -m 15 -o /dev/null ' + url, 'curl-' + host)
            checks['curl %s fails (proxy deny logged)' % url] = not ok and denied(log, host)
        ok, log, *_ = run('GIT_TERMINAL_PROMPT=0 git ls-remote https://github.com/XUJiahua/enterprise_agent', 'git-ls-remote')
        checks['git ls-remote github fails (proxy deny logged)'] = not ok and denied(log, 'github.com')

        model_ip = socket.gethostbyname(DEFAULT_ALLOW[-1])
        for label, ip in (('github literal ' + GITHUB_LITERAL_IP, GITHUB_LITERAL_IP),
                          ('github resolved ' + socket.gethostbyname('github.com'), socket.gethostbyname('github.com')),
                          ('allowlisted model host IP ' + model_ip, model_ip)):
            direct = "curl -k -sS -m 15 -o /dev/null --noproxy '*' https://%s" % ip
            baseline = outside(direct)
            ok, log, *_ = run(direct)
            checks['direct %s: reachable outside, blocked inside' % label] = baseline and not ok and len(log) == 2
        checks['DNS lookup inside sandbox fails'] = not run(
            'python3 -c "import socket; socket.getaddrinfo(\'github.com\', 443)"')[0]

        listener = socket.create_server(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        threading.Thread(target=lambda: [listener.accept()[0].close() for _ in iter(int, 1)], daemon=True).start()
        probe = 'python3 -c "import socket; socket.create_connection((\'127.0.0.1\', %d), timeout=5)"' % port
        checks['other localhost port: reachable outside, blocked inside'] = outside(probe) and not run(probe)[0]

        host = DEFAULT_ALLOW[-1]
        ok, log, *_ = run('curl -sS -m 15 -o /dev/null https://' + host, 'curl-allowlisted')
        checks['allowlisted model host reachable via proxy'] = ok and allowed(log, host)
        ok, log, out, _ = run('mkdir -p {out} && echo forged > {out}/network.log')
        checks['child-written network.log is replaced'] = ok and log[-1].get('replaced_child_written_log') is True

        if a.kiro:
            def kiro(name, trust, prompt):
                home = t / ('kh-' + name)
                (home / 'agents').mkdir(parents=True)
                (home / 'agents' / 'poc-v3-generator.json').write_text(json.dumps(KIRO_AGENT))
                cmd = ('KIRO_HOME=%s kiro-cli chat --no-interactive --output-format stream-json --agent-engine v3 '
                       '--agent poc-v3-generator %s %s </dev/null > {out}.events.jsonl 2>{out}.stderr.log'
                       % (home, trust, shlex.quote(prompt)))
                ok, log, out, _ = run('mkdir -p {out} && ' + cmd, 'kiro-' + name)
                text = Path(str(out) + '.events.jsonl').read_text()
                events = [json.loads(line) for line in text.splitlines() if line.strip()]
                final = [e['data'].get('finalText', '') for e in events if e['type'] == 'runFinished']
                if evidence:
                    (evidence / ('kiro-' + name + '.events.jsonl')).write_text(text)
                return ok, log, final, text

            ok, log, final, _ = kiro('ready', '--trust-tools=', 'Reply exactly READY. Do not read or modify files. Do not call tools.')
            checks['kiro READY inside sandbox'] = ok and final == ['READY'] and allowed(log, DEFAULT_ALLOW[-1])
            ok, log, final, text = kiro('curl-github', '--trust-all-tools',
                'Use your shell tool to run exactly this command: curl -sS -m 15 -o /dev/null -w "%{http_code}" https://github.com; echo " EXIT=$?" '
                'Then reply with one line: the exact output of that command, and nothing else.')
            checks['kiro tool call: model curl github is blocked'] = (
                ok and len(final) == 1 and 'EXIT=' in final[0] and 'EXIT=0' not in final[0]
                and 'execute_bash' in text and denied(log, 'github.com') and allowed(log, DEFAULT_ALLOW[-1]))
            checks['kiro final answer'] = final  # recorded verbatim, not a pass/fail value
    result = {k: v for k, v in checks.items() if k != 'kiro final answer'}
    passed = all(result.values())
    print(json.dumps({'kind': 'network-isolation-control', 'allowlist': list(DEFAULT_ALLOW),
                      'checks': result, 'kiro_final_answer': checks.get('kiro final answer'),
                      'runs': [{k: r[k] for k in ('name', 'returncode')} | {
                          'proxy_decisions': [(e.get('host'), e['decision']) for e in r['network_log'] if 'decision' in e]}
                          for r in runs],
                      'passed': passed}, indent=2))
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
