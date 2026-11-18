package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/XUJiahua/enterprise_agent/internal/procurement"
	"github.com/XUJiahua/enterprise_agent/kernel/pg"
)

func TestBehaviorFindings(t *testing.T) {
	c, err := pg.Describe(procurement.Registry())
	if err != nil {
		t.Fatal(err)
	}
	root := t.TempDir()
	dir := filepath.Join(root, behaviorDir)
	_ = os.MkdirAll(dir, 0o755)
	put := func(name, body string) {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	git := func(args ...string) {
		t.Helper()
		if out, err := exec.Command("git", append([]string{"-C", root, "-c", "user.email=t@t", "-c", "user.name=t"}, args...)...).CombinedOutput(); err != nil {
			t.Fatalf("%v: %s", err, out)
		}
	}
	codes := func(base string) string {
		var out []string
		for _, f := range behaviorFindings(root, base) {
			out = append(out, f.Code+" "+filepath.Base(f.Path))
		}
		return strings.Join(out, ",")
	}
	first := c.Operations[0].Ref.String() + ".json"
	for _, op := range c.Operations {
		put(op.Ref.String()+".json", "{}")
	}
	put("procurement.retired@1.json", "{}")
	git("init", "-q")
	git("add", ".")
	git("commit", "-qm", "base")
	if got := codes("HEAD"); got != "" {
		t.Fatal("unchanged:", got)
	}
	_ = os.Remove(filepath.Join(dir, "procurement.retired@1.json"))
	if got := codes("HEAD"); got != "" {
		t.Fatal("retired version's file removed with its registration:", got)
	}
	put(first, `{"changed":true}`)
	if got := codes("HEAD"); got != "BEHAVIOR_CHANGED "+first {
		t.Fatal("edited published file:", got)
	}
	_ = os.Remove(filepath.Join(dir, first))
	if got := codes("HEAD"); got != "BEHAVIOR_MISSING "+first+",BEHAVIOR_CHANGED "+first {
		t.Fatal("deleted file of a registered version:", got)
	}
	if got := codes("no-such-ref"); !strings.Contains(got, "BASE_INVALID") {
		t.Fatal("invalid base:", got)
	}
	// Harness files published at base are frozen; a TestMain is refused.
	harness := filepath.Join(root, behaviorHarness[0])
	_ = os.WriteFile(harness, []byte("package procurement_test\n"), 0o644)
	put(first, "{}")
	git("add", ".")
	git("commit", "-qm", "harness")
	_ = os.WriteFile(harness, []byte("package procurement_test\n// weakened\n"), 0o644)
	if got := codes("HEAD"); got != "BEHAVIOR_HARNESS_CHANGED behavior_test.go" {
		t.Fatal("edited harness:", got)
	}
	_ = os.WriteFile(harness, []byte("package procurement_test\n"), 0o644)
	_ = os.WriteFile(filepath.Join(root, "internal/procurement/main_test.go"), []byte("package procurement_test\nfunc TestMain(m *testing.M) {}\n"), 0o644)
	if got := codes(""); got != "BEHAVIOR_HARNESS_BYPASS main_test.go" {
		t.Fatal("TestMain:", got)
	}
	// The repository has a file for every registered version.
	if f := behaviorFindings("../..", ""); len(f) != 0 {
		t.Fatalf("%+v", f)
	}
}
