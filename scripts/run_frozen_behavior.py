#!/usr/bin/env python3
"""Run the frozen behavior, event and constraint cases; require every case to run and pass.

`go test` passing is not enough: a test filter or an init hook in the code
under test could make it run nothing. This counts the cases in
internal/procurement/behavior/*.json and requires exactly that many passing
subtests of TestFrozenBehavior, and likewise for internal/procurement/events
and TestFrozenEvents, and internal/procurement/constraints and
TestFrozenConstraints, with no skips. Needs KERNEL_TEST_ADMIN_URL.
"""
import glob
import json
import os
import subprocess
import sys

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
cases = set()
for f in glob.glob(os.path.join(root, "internal/procurement/behavior/*.json")):
    b = json.load(open(f))
    ref = "%s@%d" % (b["ref"]["name"], b["ref"]["version"])
    for c in b["cases"]:
        cases.add("TestFrozenBehavior/" + ref + "/" + c["name"].replace(" ", "_"))

for f in glob.glob(os.path.join(root, "internal/procurement/events/*.json")):
    b = json.load(open(f))
    key = "%s/%s@%d" % (b["consumer"], b["event"]["type"], b["event"]["version"])
    for c in b["cases"]:
        cases.add("TestFrozenEvents/" + key + "/" + c["name"].replace(" ", "_"))

for f in glob.glob(os.path.join(root, "internal/procurement/constraints/*.json")):
    b = json.load(open(f))
    for c in b["cases"]:
        cases.add("TestFrozenConstraints/" + b["table"] + "/" + c["name"].replace(" ", "_"))

env = dict(os.environ, KERNEL_REQUIRE_DB="1")
passed, other, exits = set(), [], []
for test, pkg in (("TestFrozenBehavior", "./internal/procurement/behavior/"), ("TestFrozenEvents", "./internal/procurement/events/"),
                  ("TestFrozenConstraints", "./internal/procurement/constraints/")):
    p = subprocess.run(["go", "test", "-count=1", "-json", "-run", "^" + test + "$", pkg], cwd=root, env=env, capture_output=True, text=True)
    exits.append(p.returncode)
    for line in p.stdout.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        t = e.get("Test", "")
        if e.get("Action") == "pass" and t in cases:
            passed.add(t)
        elif e.get("Action") in ("fail", "skip") and t:
            other.append(e["Action"] + " " + t)

missing = sorted(cases - passed)
print(json.dumps({"cases": len(cases), "passed": len(passed & cases), "missing": missing[:20],
                  "failed_or_skipped": other[:20], "go_test_exit": max(exits)}))
sys.exit(0 if cases and not missing and not other and max(exits) == 0 else 1)
