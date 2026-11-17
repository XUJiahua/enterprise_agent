#!/usr/bin/env python3
"""isolate.py + network restriction for one v3 generation kit (macOS, no VM).

File rules are exactly isolate.profile(). On top of them the Seatbelt profile
denies every outbound connection (TCP/UDP to any IP, DNS, unix sockets) except
127.0.0.1/::1 on the port of an HTTP CONNECT proxy that this script runs in-process,
outside the sandbox. The proxy only tunnels to allowlisted host names on port 443
and keeps every allow/deny decision in memory; at exit it is written to
<output>/network.log (JSON lines) so the sandboxed process cannot edit it.
The child gets HTTPS_PROXY/HTTP_PROXY/ALL_PROXY pointing at the proxy.
ponytail: host-name allowlisting cannot see inside TLS; a model endpoint that can
itself fetch/search the web is outside this boundary. Upgrade path: VM per kit.
"""
import argparse
import json
import os
from pathlib import Path
import select
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from isolate import profile  # noqa: E402  (file rules stay identical)

# Hosts kiro-cli 3.3.0 needs for READY + a tool-calling task (discovery in docs/network-isolation.md).
# Telemetry hosts it also contacts (telemetry.eu-central-1.kiro.dev,
# client-telemetry.us-east-1.amazonaws.com) are deliberately not allowed.
_DISCOVERED = ('management.eu-central-1.kiro.dev', 'runtime.eu-central-1.kiro.dev')
# The account's service region can change (2026-11-16: eu-central-1 -> us-east-1); re-run
# discovery + isolation_net_control.py and set KIRO_ALLOW_HOSTS rather than editing this file.
DEFAULT_ALLOW = tuple(h.strip().lower() for h in os.environ.get('KIRO_ALLOW_HOSTS', '').split(',') if h.strip()) or _DISCOVERED
PROXY_ENV = ('HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY', 'https_proxy', 'http_proxy', 'all_proxy')


class Proxy(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port, allow, discover):
        self.allow, self.discover, self.events, self.lock = set(allow), discover, [], threading.Lock()
        super().__init__(('127.0.0.1', port), Handler)

    def log(self, **event):
        with self.lock:
            self.events.append({'ts': round(time.time(), 3), **event})


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        s, client, buf = self.server, self.request, b''
        client.settimeout(30)
        while b'\r\n\r\n' not in buf and len(buf) < 16384:
            data = client.recv(4096)
            if not data:
                return
            buf += data
        head, _, pending = buf.partition(b'\r\n\r\n')
        parts = head.split(b'\r\n', 1)[0].decode('latin-1').split()
        if len(parts) != 3 or parts[0] != 'CONNECT':
            # Plain HTTP (absolute-URI) is never forwarded; only TLS tunnels to allowlisted names.
            target = parts[1] if len(parts) > 1 else head[:200]
            s.log(method=parts[0] if parts else '', target=target, decision='deny', reason='not-connect')
            client.sendall(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            return
        host, _, port = parts[1].rpartition(':')
        host = host.strip('[]').lower().rstrip('.')
        if not (port == '443' and (host in s.allow or s.discover)):
            s.log(method='CONNECT', host=host, port=port, decision='deny', reason='not-allowlisted')
            client.sendall(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            return
        try:
            upstream = socket.create_connection((host, 443), timeout=15)
        except OSError as error:
            s.log(method='CONNECT', host=host, port=port, decision='allow', error=str(error))
            client.sendall(b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            return
        peer = upstream.getpeername()[0]
        upstream.settimeout(None)
        client.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
        client.settimeout(None)
        upstream.sendall(pending)
        sent, received, start = len(pending), 0, time.monotonic()
        try:
            while True:
                ready, _, _ = select.select([client, upstream], [], [], 300)
                if not ready:
                    break
                for src in ready:
                    data = src.recv(65536)
                    if not data:
                        raise EOFError
                    (upstream if src is client else client).sendall(data)
                    if src is client:
                        sent += len(data)
                    else:
                        received += len(data)
        except (EOFError, OSError):
            pass
        finally:
            upstream.close()
        s.log(method='CONNECT', host=host, port=port, peer=peer,
              decision='allow-discover' if s.discover and host not in s.allow else 'allow',
              bytes_sent=sent, bytes_received=received, seconds=round(time.monotonic() - start, 3))


def start_proxy(allow, discover, first=18900, count=50):
    for port in range(first, first + count):
        try:
            proxy = Proxy(port, allow, discover)
        except OSError:
            continue
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        return proxy, port
    raise SystemExit('no free proxy port in %d-%d' % (first, first + count - 1))


def net_rules(port):
    return '\n'.join([
        '(deny network-outbound)',
        '(allow network-outbound (remote ip "localhost:%d"))' % port,
        '(deny network-bind)',
        '(allow network-bind (local ip "localhost:*"))',
    ]) + '\n'


def write_log(output, proxy, port, returncode, interrupted):
    output.mkdir(parents=True, exist_ok=True)
    log = output / 'network.log'
    meta = {'ts': round(time.time(), 3), 'event': 'stop', 'returncode': returncode, 'interrupted': interrupted}
    if log.exists() or log.is_symlink():
        log.unlink()  # anything here was written by the sandboxed child, not by us
        meta['replaced_child_written_log'] = True
    with proxy.lock:
        events = list(proxy.events)
    lines = [{'event': 'start', 'proxy': '127.0.0.1:%d' % port, 'allow': sorted(proxy.allow),
              'discover': proxy.discover}, *events, meta]
    with log.open('x') as stream:
        for line in lines:
            stream.write(json.dumps(line) + '\n')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--kit', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--deny', action='append', default=[])
    p.add_argument('--allow-write', action='append', default=[])
    p.add_argument('--allow-host', action='append', help='replaces the default model-endpoint allowlist')
    p.add_argument('--discover', action='store_true', help='host discovery only: tunnel any :443 host, log it')
    p.add_argument('--print-profile', action='store_true')
    p.add_argument('command', nargs='*', help='override command (controls only)')
    a = p.parse_args()
    kit, output = Path(a.kit).resolve(), Path(a.output).resolve()
    for d in a.deny:
        if kit.is_relative_to(Path(d).resolve()) or output.is_relative_to(Path(d).resolve()):
            raise SystemExit('kit/output must not be inside a denied path: ' + d)
    output.parent.mkdir(parents=True, exist_ok=True)
    allow = [h.lower() for h in (a.allow_host or DEFAULT_ALLOW)]
    if a.print_profile:
        print(profile(kit, output, a.deny, a.allow_write) + net_rules(18900), end='')
        return 0
    proxy, port = start_proxy(allow, a.discover)
    text = profile(kit, output, a.deny, a.allow_write) + net_rules(port)
    command = a.command or [sys.executable, '-I', str(kit / 'worker.py'), '--output', str(output)]
    env = {k: v for k, v in os.environ.items() if k.upper() not in
           ('ADMIN_DATABASE_URL', 'DATABASE_URL', 'POC_DB_RESET', 'POC_MUTATION', 'POC_ALLOW_MUTATIONS', 'NO_PROXY')}
    env.update({k: 'http://127.0.0.1:%d' % port for k in PROXY_ENV})
    env.update(NO_PROXY='localhost', no_proxy='localhost')
    rc = None

    def stop(signum, _frame):  # e.g. the operator's `perl -e 'alarm N'`: still write network.log
        raise SystemExit(128 + signum)
    for signum in (signal.SIGALRM, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, stop)
    child = subprocess.Popen(['sandbox-exec', '-p', text, *command], cwd=kit, env=env)
    try:
        rc = child.wait()
        return rc
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(5)
            except subprocess.TimeoutExpired:
                child.kill()
        proxy.shutdown()
        write_log(output, proxy, port, child.poll(), interrupted=rc is None)


if __name__ == '__main__':
    sys.exit(main())
