package main

import (
	"go/ast"
	"go/parser"
	"go/token"
	"io/fs"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
)

// domainDir is the procurement domain module; kernel/ is the runner.
const domainDir = "internal/procurement"

// sqlWrite matches a write statement on a product schema table.
var sqlWrite = regexp.MustCompile(`(?is)\b(insert\s+into|update|delete\s+from|truncate(\s+table)?|merge\s+into)\s+(only\s+)?"?(procurement|kernel)"?\.`)

// bypassImports would let domain code open its own connection or transaction
// instead of using the runner's Session. migrate.go (admin tool) is exempt.
var bypassImports = map[string]bool{
	"database/sql":                         true,
	"github.com/jackc/pgx/v5/pgxpool":      true,
	"github.com/jackc/pgx/v5/stdlib":       true,
	"github.com/jackc/pgx/v5/pgconn":       true,
	"github.com/golang-migrate/migrate/v4": true,
}

// writeFindings statically checks where product tables are written:
//   - WRITE_OUTSIDE_DOMAIN: SQL writing procurement.* or kernel.* outside
//     kernel/ and the domain module (handlers, commands, other packages).
//   - KERNEL_TABLE_WRITE: the domain writes kernel.* itself instead of
//     returning audit/events in the Outcome.
//   - DOMAIN_BYPASSES_SESSION: the domain opens connections or transactions
//     (forbidden imports, pgx.Connect, Begin) instead of using pg.Session.
//
// It reads string literals only; SQL assembled at run time is not seen.
// Test files are skipped.
func writeFindings(root string) []Finding {
	var findings []Finding
	fset := token.NewFileSet()
	_ = filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		rel, _ := filepath.Rel(root, path)
		rel = filepath.ToSlash(rel)
		if d.IsDir() {
			if name := d.Name(); rel != "." && (strings.HasPrefix(name, ".") || name == "node_modules" || name == "testdata" || name == "vendor") {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(rel, ".go") || strings.HasSuffix(rel, "_test.go") {
			return nil
		}
		f, err := parser.ParseFile(fset, path, nil, parser.SkipObjectResolution)
		if err != nil {
			findings = append(findings, Finding{"GO_PARSE", rel, err.Error()})
			return nil
		}
		inKernel := strings.HasPrefix(rel, "kernel/")
		inDomain := strings.HasPrefix(rel, domainDir+"/")
		exempt := rel == domainDir+"/migrate.go"
		at := func(n ast.Node) string { return rel + ":" + strconv.Itoa(fset.Position(n.Pos()).Line) }
		if inDomain && !exempt {
			for _, imp := range f.Imports {
				if p, _ := strconv.Unquote(imp.Path.Value); bypassImports[p] {
					findings = append(findings, Finding{"DOMAIN_BYPASSES_SESSION", at(imp), "imports " + p + "; use the pg.Session passed to Execute/Handle"})
				}
			}
		}
		ast.Inspect(f, func(n ast.Node) bool {
			switch x := n.(type) {
			case *ast.BasicLit:
				if x.Kind != token.STRING {
					return true
				}
				m := sqlWrite.FindStringSubmatch(x.Value)
				switch {
				case m == nil, inKernel:
				case !inDomain:
					findings = append(findings, Finding{"WRITE_OUTSIDE_DOMAIN", at(x), "writes " + m[4] + ".*; only operations and consumers in " + domainDir + " may write product tables"})
				case strings.EqualFold(m[4], "kernel"):
					findings = append(findings, Finding{"KERNEL_TABLE_WRITE", at(x), "domain writes kernel.*; return audit and events in the Outcome instead"})
				}
			case *ast.SelectorExpr:
				if inDomain && !exempt {
					if id, ok := x.X.(*ast.Ident); ok && (id.Name == "pgx" && strings.HasPrefix(x.Sel.Name, "Connect") || x.Sel.Name == "Begin" || x.Sel.Name == "BeginTx") {
						findings = append(findings, Finding{"DOMAIN_BYPASSES_SESSION", at(x), "calls " + id.Name + "." + x.Sel.Name + "; the runner owns the transaction"})
					}
				}
			}
			return true
		})
		return nil
	})
	return findings
}
