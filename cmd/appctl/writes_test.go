package main

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

func TestWriteFindings(t *testing.T) {
	root := t.TempDir()
	put := func(rel, src string) {
		p := filepath.Join(root, rel)
		_ = os.MkdirAll(filepath.Dir(p), 0o755)
		if err := os.WriteFile(p, []byte(src), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	put("kernel/pg/runner.go", "package pg\nconst q = `INSERT INTO kernel.audit VALUES (1)`\n")
	put("internal/procurement/ok.go", "package procurement\nconst q = `UPDATE procurement.purchase SET status = 'x'`\n")
	put("internal/procurement/migrate.go", "package procurement\nimport _ \"database/sql\"\n")
	put("internal/procurement/x_test.go", "package procurement\nconst q = `DELETE FROM procurement.receipt`\n")
	put("cmd/product/main.go", "package main\nconst q = `insert into procurement.receipt (a) values (1)`\n")
	put("internal/procurement/audit.go", "package procurement\nconst q = `INSERT INTO kernel.audit (x) VALUES (1)`\n")
	put("internal/procurement/pool.go", "package procurement\nimport \"github.com/jackc/pgx/v5/pgxpool\"\nfunc f(p *pgxpool.Pool) { p.Begin(nil) }\n")
	put("internal/other/read.go", "package other\nconst q = `SELECT * FROM procurement.purchase`\n")
	put("internal/other/quoted.go", "package other\nconst q = \"UPDATE \\\"procurement\\\".purchase\\nSET x=1\"\n")
	put("internal/procurement/multi.go", "package procurement\nconst q = `UPDATE procurement.purchase SET a=1; INSERT INTO kernel.audit (x) VALUES (1)`\n")
	put("internal/procurement/alias.go", "package procurement\nimport driver \"github.com/jackc/pgx/v5\"\nvar _ = driver.Connect\n")
	var got []string
	for _, f := range writeFindings(root) {
		got = append(got, f.Code+" "+strings.SplitN(f.Path, ":", 2)[0])
	}
	slices.Sort(got)
	want := []string{
		"DOMAIN_BYPASSES_SESSION internal/procurement/alias.go",
		"DOMAIN_BYPASSES_SESSION internal/procurement/pool.go",
		"DOMAIN_BYPASSES_SESSION internal/procurement/pool.go",
		"KERNEL_TABLE_WRITE internal/procurement/audit.go",
		"KERNEL_TABLE_WRITE internal/procurement/multi.go",
		"WRITE_OUTSIDE_DOMAIN cmd/product/main.go",
		"WRITE_OUTSIDE_DOMAIN internal/other/quoted.go",
	}
	if !slices.Equal(got, want) {
		t.Fatalf("got %q\nwant %q", got, want)
	}
	// The real repository is clean.
	if f := writeFindings("../.."); len(f) != 0 {
		t.Fatalf("repository findings: %+v", f)
	}
}
