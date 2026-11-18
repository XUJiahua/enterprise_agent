#!/usr/bin/env python3
"""Fail if a shell script can signal a whole process group.

`kill 0`, `kill -- -PGID`, `kill -9 -123` or an unset pid defaulting to 0
(`kill ${pid:-0}`) also hit whatever started the script in the same group,
such as an agent supervisor. Each line is split on shell separators and
tokenized with shlex (so quotes are understood); quoted strings such as trap
bodies are checked again as commands.

usage: check_no_group_kill.py FILE...   |   check_no_group_kill.py --self-test
"""
import re
import shlex
import sys


def commands(text):
    """Yield the token list of each simple command in text, quotes respected."""
    lex = shlex.shlex(text.replace("$(", " ; ").replace("`", " ; "), posix=True, punctuation_chars=";&|()")
    lex.whitespace_split, lex.commenters = True, "#"
    try:
        tokens = list(lex)
    except ValueError:  # unbalanced quote: fall back to whitespace
        tokens = text.split()
    cmd = []
    for t in tokens + [";"]:
        if t and set(t) <= set(";&|()"):
            if cmd:
                yield cmd
            cmd = []
            continue
        cmd.append(t)
        if "kill" in t and any(c.isspace() for c in t):  # e.g. a trap body: trap 'kill 0' EXIT
            yield from commands(t)


def bad(tokens):
    for i, t in enumerate(tokens):
        if t != "kill":
            continue
        args, options = tokens[i + 1:], True
        for a in args:
            if ":-0}" in a:
                return True
            if options and a == "--":
                options = False
                continue
            if options and a.startswith("-") and not re.fullmatch(r"-[\d$].*", a):
                continue  # signal option such as -9, -TERM, -s handled loosely
            if options and re.fullmatch(r"-\d+", a) and a == args[0]:
                continue  # `kill -9 pid`: a numeric signal comes first
            options = False
            if a == "0" or a.startswith("-"):
                return True
    return False


def scan(path):
    found = []
    for n, line in enumerate(open(path, encoding="utf-8"), 1):
        if any(bad(c) for c in commands(line)):
            found.append(f"{path}:{n}: {line.strip()}")
    return found


BAD = ["kill 0", "kill -TERM 0", "kill -- -$pgid", 'kill -- "-$pgid"', "kill -9 -123", "kill -TERM -$p",
       "kill ${pid:-0}", "trap 'kill 0' EXIT", "kill 0; echo done", "x && kill 0", 'trap "kill ${pid:-0} || true" EXIT']
GOOD = ['kill "$pid"', "kill -9 $pid", 'kill -TERM "$pid"', 'kill -9 "$pid"',
        """trap '[ -n "${pid:-}" ] && kill "$pid" 2>/dev/null || true; psql -c "DROP"' EXIT""",
        'stop() { kill "$pid"; wait "$pid" 2>/dev/null || true; }', "echo skill 0", "# kill 0 in a comment"]

if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        wrong = [s for s in BAD if not any(bad(c) for c in commands(s))] + \
                [s for s in GOOD if any(bad(c) for c in commands(s))]
        print("self-test", "ok" if not wrong else f"FAILED: {wrong}")
        sys.exit(1 if wrong else 0)
    hits = [h for p in sys.argv[1:] for h in scan(p)]
    print("\n".join(hits) or "no process-group kill")
    sys.exit(1 if hits else 0)
