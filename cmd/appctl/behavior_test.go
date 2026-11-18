package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"slices"
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
	_ = os.WriteFile(filepath.Join(dir, "hook_test.go"), []byte("package behavior_test\nfunc init() {}\n"), 0o644)
	if got := codes(""); got != "BEHAVIOR_HARNESS_BYPASS hook_test.go" {
		t.Fatal("extra Go file next to the harness:", got)
	}
	// The repository has a file for every registered version.
	if f := behaviorFindings("../..", ""); len(f) != 0 {
		t.Fatalf("%+v", f)
	}
}

func TestEventFindings(t *testing.T) {
	c, err := pg.Describe(procurement.Registry())
	if err != nil {
		t.Fatal(err)
	}
	root := t.TempDir()
	dir := filepath.Join(root, eventsDir)
	_ = os.MkdirAll(dir, 0o755)
	var files []string
	for _, cc := range c.Consumers {
		for _, e := range cc.Events {
			files = append(files, cc.Name+"_"+e.String()+".json")
		}
	}
	for _, f := range files {
		_ = os.WriteFile(filepath.Join(dir, f), []byte("{}"), 0o644)
	}
	git := func(args ...string) {
		t.Helper()
		if out, err := exec.Command("git", append([]string{"-C", root, "-c", "user.email=t@t", "-c", "user.name=t"}, args...)...).CombinedOutput(); err != nil {
			t.Fatalf("%v: %s", err, out)
		}
	}
	codes := func(base string) string {
		var out []string
		for _, f := range eventFindings(root, base) {
			out = append(out, f.Code+" "+filepath.Base(f.Path))
		}
		return strings.Join(out, ",")
	}
	git("init", "-q")
	git("add", ".")
	git("commit", "-qm", "base")
	if got := codes("HEAD"); got != "" {
		t.Fatal("unchanged:", got)
	}
	_ = os.WriteFile(filepath.Join(dir, files[0]), []byte(`{"x":1}`), 0o644)
	if got := codes("HEAD"); got != "EVENT_CONTRACT_CHANGED "+files[0] {
		t.Fatal("edited contract:", got)
	}
	_ = os.Remove(filepath.Join(dir, files[0]))
	if got := codes("HEAD"); got != "EVENT_CONTRACT_MISSING "+files[0]+",EVENT_CONTRACT_CHANGED "+files[0] {
		t.Fatal("removed contract:", got)
	}
	_ = os.WriteFile(filepath.Join(dir, "hook_test.go"), []byte("package events_test\n"), 0o644)
	if got := codes(""); !strings.Contains(got, "BEHAVIOR_HARNESS_BYPASS hook_test.go") {
		t.Fatal("extra Go file:", got)
	}
	if f := eventFindings("../..", ""); len(f) != 0 {
		t.Fatalf("repository: %+v", f)
	}
}

func TestConstraintFindings(t *testing.T) {
	root := t.TempDir()
	dir := filepath.Join(root, constraintsDir)
	_ = os.MkdirAll(dir, 0o755)
	const name = "procurement.purchase.json"
	put := func(body string) {
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
		for _, f := range constraintFindings(root, base) {
			out = append(out, f.Code+" "+filepath.Base(f.Path))
		}
		return strings.Join(out, ",")
	}
	a, b := `{"name":"a","sql":"x","allowed":false}`, `{"name":"b","sql":"y","allowed":true}`
	put(`{"table":"procurement.purchase","cases":[` + a + `,` + b + `]}`)
	git("init", "-q")
	git("add", ".")
	git("commit", "-qm", "base")
	for label, body := range map[string]string{
		"unchanged":   `{"table":"procurement.purchase","cases":[` + a + `,` + b + `]}`,
		"reformatted": "{\n  \"cases\": [\n    " + a + ",\n    " + b + "\n  ],\n  \"table\": \"procurement.purchase\"\n}\n",
		"appended":    `{"table":"procurement.purchase","cases":[` + a + `,` + b + `,{"name":"c","sql":"z","allowed":false}]}`,
	} {
		put(body)
		if got := codes("HEAD"); got != "" {
			t.Fatalf("%s: %s", label, got)
		}
	}
	for label, body := range map[string]string{
		"case edited":     `{"table":"procurement.purchase","cases":[` + strings.Replace(a, "false", "true", 1) + `,` + b + `]}`,
		"case removed":    `{"table":"procurement.purchase","cases":[` + a + `]}`,
		"cases reordered": `{"table":"procurement.purchase","cases":[` + b + `,` + a + `]}`,
		"inserted before": `{"table":"procurement.purchase","cases":[{"name":"c","sql":"z","allowed":false},` + a + `,` + b + `]}`,
		"table changed":   `{"table":"procurement.receipt","cases":[` + a + `,` + b + `]}`,
		"field added":     `{"table":"procurement.purchase","x":1,"cases":[` + a + `,` + b + `]}`,
		"not JSON":        `{`,
	} {
		put(body)
		if got := codes("HEAD"); got != "CONSTRAINT_CASES_CHANGED "+name {
			t.Fatalf("%s: %q", label, got)
		}
	}
	_ = os.Remove(filepath.Join(dir, name))
	if got := codes("HEAD"); got != "CONSTRAINT_CASES_CHANGED "+name {
		t.Fatal("removed file:", got)
	}
	if got := codes("no-such-ref"); !strings.Contains(got, "BASE_INVALID") {
		t.Fatal("invalid base:", got)
	}
	_ = os.WriteFile(filepath.Join(dir, "hook_test.go"), []byte("package constraints_test\n"), 0o644)
	if got := codes(""); got != "BEHAVIOR_HARNESS_BYPASS hook_test.go" {
		t.Fatal("extra Go file:", got)
	}
	if !slices.Contains(behaviorHarness, constraintsDir+"/constraints_test.go") {
		t.Fatal("constraints harness is not frozen")
	}
	if f := constraintFindings("../..", ""); len(f) != 0 {
		t.Fatalf("repository: %+v", f)
	}
}
