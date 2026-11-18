#!/usr/bin/env python3
"""Fail if a shell script can signal a whole process group.

`kill 0`, `kill -- -PGID`, `kill -9 -123` or an unset pid defaulting to 0
(`kill ${pid:-0}`) also hit whatever started the script in the same group,
such as an agent supervisor. Each line is split on shell separators and
tokenized with shlex (so quotes are understood); quoted strings such as trap
bodies are checked again as commands.

Known limits (acceptable for this repo's scripts): pids held in variables,
commands built at run time (eval "$x"), and pids fed through xargs are not
seen; literal heredoc data that reads like `kill 0` is reported.

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
        if t.rsplit("/", 1)[-1] != "kill":  # kill, /bin/kill, command kill
            continue
        args, options = tokens[i + 1:], True
        for j, a in enumerate(args):
            if ":-0}" in a:
                return True
            if options and a == "--":
                options = False
                continue
            if options and a.startswith("-") and not re.fullmatch(r"-[\d$].*", a):
                continue  # signal option such as -9, -TERM, -s handled loosely
            if options and re.fullmatch(r"-\d+", a) and j == 0:
                continue  # `kill -9 pid`: a numeric signal comes first
            options = False
            if re.fullmatch(r"0+", a) or a.startswith("-"):
                return True
    return False


def scan(path):
    """Check each logical line (backslash-newline continuations joined)."""
    found, start, logical = [], 1, ""
    for n, line in enumerate(open(path, encoding="utf-8"), 1):
        if not logical:
            start = n
        if line.rstrip("\n").endswith("\\"):
            logical += line.rstrip("\n")[:-1] + " "
            continue
        logical += line
        if any(bad(c) for c in commands(logical)):
            found.append(f"{path}:{start}: {logical.strip()[:200]}")
        logical = ""
    return found


BAD = ["kill 0", "kill -TERM 0", "kill -- -$pgid", 'kill -- "-$pgid"', "kill -9 -123", "kill -TERM -$p",
       "kill ${pid:-0}", "trap 'kill 0' EXIT", "kill 0; echo done", "x && kill 0", 'trap "kill ${pid:-0} || true" EXIT',
       "/bin/kill 0", "kill -9 -9", "command kill 0", "kill 00"]
GOOD = ['kill "$pid"', "kill -9 $pid", 'kill -TERM "$pid"', 'kill -9 "$pid"',
        """trap '[ -n "${pid:-}" ] && kill "$pid" 2>/dev/null || true; psql -c "DROP"' EXIT""",
        'stop() { kill "$pid"; wait "$pid" 2>/dev/null || true; }', "echo skill 0", "# kill 0 in a comment"]

if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
            f.write("echo ok \\\n  && kill \\\n  0\n")
        continued = scan(f.name)
        wrong = ([] if continued else ["kill \\<newline> 0"]) + [s for s in BAD if not any(bad(c) for c in commands(s))] + \
                [s for s in GOOD if any(bad(c) for c in commands(s))]
        print("self-test", "ok" if not wrong else f"FAILED: {wrong}")
        sys.exit(1 if wrong else 0)
    hits = [h for p in sys.argv[1:] for h in scan(p)]
    print("\n".join(hits) or "no process-group kill")
    sys.exit(1 if hits else 0)
