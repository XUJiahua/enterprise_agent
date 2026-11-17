#!/usr/bin/env python3
"""Archive a never-started trial without overwriting an earlier blocked probe.

Run only after stopping the lane. This does not restart generation, retry an
observed trial, change a plan, or implement the private v9 driver's state machine.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys


def archive(run, trial, day=None):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", trial):
        raise ValueError("invalid trial id")
    run = Path(run).resolve(strict=True)
    plan = json.loads((run / "plan.json").read_text())
    if sum(t.get("id") == trial for t in plan["trials"]) != 1:
        raise ValueError("trial must occur exactly once in the lane plan")
    results = run / "results"
    source = results / trial
    if results.is_symlink() or source.is_symlink() or not source.is_dir():
        raise ValueError("result must be a real directory inside the lane")
    # A partial/ambiguous generation is not a preflight block. Fail closed even
    # when a preflight record claims ready=false alongside generation evidence.
    if any(p.is_symlink() for p in source.rglob("*")):
        raise ValueError("symlinks in result are not supported")
    unexpected = set(p.name for p in source.iterdir()) - {"preflight.json", "probe", "network.log"}
    if unexpected:
        raise ValueError("result contains generation or unknown evidence: " + ", ".join(sorted(unexpected)))
    preflight = json.loads((source / "preflight.json").read_text())
    if preflight.get("ready") is not False:
        raise ValueError("only an explicit ready=false preflight may be archived")
    day = day or datetime.now(timezone.utc).strftime("%Y%m%d")
    if not re.fullmatch(r"[0-9]{8}", day):
        raise ValueError("invalid archive date")
    root = run / ("preflight-blocked-" + day)
    if root.is_symlink():
        raise ValueError("archive root must not be a symlink")
    root.mkdir(exist_ok=True)
    attempt = 1
    while True:
        slot = root / f"{trial}--{attempt:04d}"
        try:
            slot.mkdir()  # Reserve a fresh slot atomically; never reuse one.
            break
        except FileExistsError:
            attempt += 1
    destination = slot / "result"
    try:
        # Unlike mv source existing-dir, rename never silently nests the source
        # inside a previous archive. Failures leave source in place and propagate.
        os.rename(source, destination)
    except OSError:
        slot.rmdir()
        raise
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--trial", required=True)
    args = parser.parse_args()
    try:
        destination = archive(args.run, args.trial)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("preflight archive refused: " + str(error), file=sys.stderr)
        return 1
    print(json.dumps({"trial": args.trial, "archived": str(destination), "generation_restarted": False}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
