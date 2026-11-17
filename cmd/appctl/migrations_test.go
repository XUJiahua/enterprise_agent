package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestProductMigrationsAgainstBaseline(t *testing.T) {
	root := t.TempDir()
	dir := filepath.Join(root, productDir)
	sh := func(args ...string) {
		t.Helper()
		if out, err := exec.Command("git", append([]string{"-C", root, "-c", "user.email=t@t", "-c", "user.name=t"}, args...)...).CombinedOutput(); err != nil {
			t.Fatalf("%v: %s", err, out)
		}
	}
	write := func(name, body string) {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	codes := func() string {
		var c []string
		for _, f := range productMigrations(root, "HEAD") {
			c = append(c, f.Code+":"+filepath.Base(f.Path))
		}
		return strings.Join(c, ",")
	}
	_ = os.MkdirAll(dir, 0o755)
	sh("init", "-q")
	write("0001_a.up.sql", "a")
	write("0002_b.up.sql", "b")
	sh("add", ".")
	sh("commit", "-qm", "base")
	if got := codes(); got != "" {
		t.Fatal("clean:", got)
	}
	write("0003_c.up.sql", "c")
	if got := codes(); got != "" {
		t.Fatal("appended:", got)
	}
	write("0002_b.up.sql", "b2")
	write("0001b_late.up.sql", "x")
	_ = os.Remove(filepath.Join(dir, "0001_a.up.sql"))
	if got := codes(); got != "MIGRATION_REMOVED:0001_a.up.sql,PUBLISHED_MIGRATION_CHANGED:0002_b.up.sql,MIGRATION_OUT_OF_ORDER:0001b_late.up.sql" {
		t.Fatal(got)
	}
}
