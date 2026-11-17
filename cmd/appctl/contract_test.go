package main

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/XUJiahua/enterprise_agent/internal/procurement"
	"github.com/XUJiahua/enterprise_agent/kernel/pg"
)

func TestContractFindings(t *testing.T) {
	c, err := pg.Describe(procurement.Registry())
	if err != nil {
		t.Fatal(err)
	}
	root := t.TempDir()
	path := filepath.Join(root, contractFile)
	_ = os.MkdirAll(filepath.Dir(path), 0o755)
	write := func(c pg.Contract) {
		b, _ := json.MarshalIndent(c, "", "  ")
		if err := os.WriteFile(path, append(b, '\n'), 0o644); err != nil {
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
		for _, f := range contractFindings(root, base) {
			out = append(out, f.Code)
		}
		return strings.Join(out, ",")
	}

	// Baseline published every current operation, one of them with a different MaxTargets.
	prev := c
	prev.Operations = slices.Clone(c.Operations)
	prev.Operations[0].MaxTargets++
	write(prev)
	git("init", "-q")
	git("add", ".")
	git("commit", "-qm", "base")

	write(c)
	if got := codes(""); got != "" {
		t.Fatal("current contract:", got)
	}
	if got := codes("HEAD"); got != "CONTRACT_CHANGED_WITHOUT_VERSION" {
		t.Fatal("changed published version:", got)
	}
	write(prev)
	if got := codes(""); got != "CONTRACT_DRIFT" {
		t.Fatal("stale contract file:", got)
	}
}
