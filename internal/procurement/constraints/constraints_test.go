// Package constraints_test runs the frozen database constraint cases in this
// directory: raw SQL the application role may or may not get past the
// database, whatever the operation code does. It also requires every column
// the application role can INSERT or UPDATE to have at least one case the
// database must refuse, so a new grant cannot ship with its Normalize rules
// enforced only in Go. It is its own package, embeds its case files, and
// appctl validate refuses any other Go file here, any edit to this file, and
// any change to a published case file other than appending cases.
package constraints_test

import (
	"context"
	"embed"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"regexp"
	"strings"
	"testing"

	"github.com/XUJiahua/enterprise_agent/internal/procurement"
	"github.com/XUJiahua/enterprise_agent/kernel/pg/pgtest"
	"github.com/jackc/pgx/v5/pgconn"
)

// constraintFile holds the cases for one table, named <schema>.<table>.json.
type constraintFile struct {
	Table string           `json:"table"`
	Cases []constraintCase `json:"cases"`
}

type constraintCase struct {
	Name    string   `json:"name"`
	Actor   string   `json:"actor"`          // app.actor for the statement; "" allowed
	Seed    []string `json:"seed,omitempty"` // admin SQL after the standard seed
	SQL     string   `json:"sql"`            // one statement, run as the application role in tenant t1
	Allowed bool     `json:"allowed"`        // true: succeeds and writes a row; false: the database refuses it
}

//go:embed *.json
var frozen embed.FS

// appRole is the group role the application login role belongs to.
const appRole = "enterprise_runtime"

// TestFrozenConstraints runs every case in its own database and then checks
// coverage: for each (table, column, INSERT|UPDATE) the application role
// holds, the table's file needs a case of that kind naming the column with
// "allowed": false. It checks that such a refusal exists, not that it covers
// every rule Normalize applies to the column.
func TestFrozenConstraints(t *testing.T) {
	files, _ := fs.Glob(frozen, "*.json")
	cases := map[string][]constraintCase{}
	for _, f := range files {
		raw, err := frozen.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		var cf constraintFile
		dec := json.NewDecoder(strings.NewReader(string(raw)))
		dec.DisallowUnknownFields()
		if err = dec.Decode(&cf); err != nil || len(cf.Cases) == 0 {
			t.Fatalf("%s: %v (need at least one case)", f, err)
		}
		if f != cf.Table+".json" {
			t.Fatalf("%s must be named %s.json", f, cf.Table)
		}
		names := map[string]bool{}
		for _, c := range cf.Cases {
			if c.Name == "" || names[c.Name] {
				t.Fatalf("%s: empty or duplicate case name %q", f, c.Name)
			}
			names[c.Name] = true
			t.Run(cf.Table+"/"+c.Name, func(t *testing.T) { runCase(t, c) })
		}
		cases[cf.Table] = cf.Cases
	}

	// Coverage. kernel.* tables are excluded: the kernel writes them itself
	// and its own tests (kernel/pg) cover their constraints.
	d := setup(t)
	type grant struct{ table, column, priv string }
	var grants []grant
	for _, row := range d.Strings(t, `
	  SELECT n.nspname||'.'||c.relname, a.attname, p.priv
	    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
	    JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
	    CROSS JOIN (VALUES ('INSERT'), ('UPDATE')) p(priv)
	   WHERE c.relkind IN ('r', 'p', 'v') AND n.nspname <> 'kernel'
	     AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg\_%'
	     AND has_column_privilege($1, c.oid, a.attnum, p.priv)
	   ORDER BY 1, 2, 3`, appRole) {
		f := strings.Fields(row)
		grants = append(grants, grant{f[0], f[1], f[2]})
	}
	if len(grants) == 0 {
		t.Fatal("found no INSERT/UPDATE grants for " + appRole + "; the coverage query is broken")
	}
	for _, g := range grants {
		seen, refused := false, false
		for _, c := range cases[g.table] {
			if writes(c.SQL, g.priv, g.table, g.column) {
				seen = true
				refused = refused || !c.Allowed
			}
		}
		if !seen || !refused {
			t.Errorf("%s.%s %s: %s may write it; add a case with \"allowed\": false to constraints/%s.json (a %s naming %s that the database must refuse)",
				g.table, g.column, g.priv, appRole, g.table, g.priv, g.column)
		}
	}
}

// ponytail: regex matching, not a SQL parser. INSERT counts the column list,
// UPDATE the SET clause up to WHERE/RETURNING. Enough for single-table case
// statements; a case written another way (CTE, INSERT ... SELECT without a
// column list) simply does not count toward coverage.
var (
	insertRe = regexp.MustCompile(`(?is)^\s*INSERT\s+INTO\s+(\S+)\s*\(([^)]*)\)`)
	updateRe = regexp.MustCompile(`(?is)^\s*UPDATE\s+(\S+)\s+SET\s+(.*?)(?:\s+WHERE\s|\s+RETURNING\s|$)`)
)

// writes reports whether sql is a priv statement on table that names column.
func writes(sql, priv, table, column string) bool {
	if priv == "INSERT" {
		m := insertRe.FindStringSubmatch(sql)
		if m == nil || !strings.EqualFold(m[1], table) {
			return false
		}
		for _, c := range strings.Split(m[2], ",") {
			if strings.EqualFold(strings.Trim(strings.TrimSpace(c), `"`), column) {
				return true
			}
		}
		return false
	}
	m := updateRe.FindStringSubmatch(sql)
	return m != nil && strings.EqualFold(m[1], table) &&
		regexp.MustCompile(`(?i)(^|[\s,(])"?`+regexp.QuoteMeta(column)+`"?\s*=`).MatchString(m[2])
}

// runCase runs the statement as the application role inside a transaction
// bound to tenant t1 and the case's actor, then rolls back. A refusal must
// come from the database's integrity rules: an integrity constraint violation
// (SQLSTATE class 23, which the triggers raise too) or a row-level security
// WITH CHECK violation. A missing grant, a syntax error or a statement that
// writes nothing does not count either way.
func runCase(t *testing.T, c constraintCase) {
	d := setup(t)
	for _, sql := range c.Seed {
		d.Exec(t, sql)
	}
	ctx := context.Background()
	tx, err := d.Pool(t).Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = tx.Rollback(ctx) }()
	if _, err = tx.Exec(ctx, `SELECT set_config('app.tenant','t1',true), set_config('app.actor',$1,true)`, c.Actor); err != nil {
		t.Fatal(err)
	}
	// The extended protocol refuses multiple statements, so a case cannot
	// append a failing statement to make a write look refused. (pgx's Exec
	// switches to the simple protocol when there are no arguments.)
	res := tx.Conn().PgConn().ExecParams(ctx, c.SQL, nil, nil, nil, nil).Read()
	tag, err := res.CommandTag, res.Err
	var pe *pgconn.PgError
	refused := errors.As(err, &pe) && (strings.HasPrefix(pe.Code, "23") ||
		pe.Code == "42501" && strings.Contains(pe.Message, "row-level security"))
	switch {
	case c.Allowed && (err != nil || tag.RowsAffected() == 0):
		t.Fatalf("must succeed and write a row: rows=%d err=%v", tag.RowsAffected(), err)
	case !c.Allowed && err == nil:
		t.Fatalf("must be refused by the database, but it wrote %d row(s)", tag.RowsAffected())
	case !c.Allowed && !refused:
		t.Fatalf("must be refused by an integrity rule (SQLSTATE 23xxx or row-level security), got %v", err)
	}
}

// setup migrates a fresh database and seeds tenant t1 with one purchase in
// each status (s1 submitted, a1 approved with a 40 receipt, c1 closed) and
// tenant t2 with x1.
func setup(t *testing.T) *pgtest.DB {
	d := pgtest.New(t)
	for _, sql := range []string{
		`DO $$ BEGIN CREATE ROLE enterprise_runtime NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$`,
		`DO $$ BEGIN CREATE ROLE enterprise_consumer NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$`,
		`GRANT enterprise_runtime TO ` + pgtest.Runtime + ` WITH INHERIT TRUE`,
	} {
		d.Exec(t, sql)
	}
	if _, _, err := procurement.Migrate(d.AdminURL); err != nil {
		t.Fatal(fmt.Errorf("migrate: %w", err))
	}
	for _, sql := range []string{
		`INSERT INTO procurement.purchase (tenant_id, id, created_by, title, amount, status) VALUES
		 ('t1','s1','ann','pen',100,'submitted'), ('t1','a1','ann','desk',100,'approved'),
		 ('t1','c1','ann','laptop',100,'approved'), ('t2','x1','zed','server',5000,'submitted')`,
		`INSERT INTO procurement.receipt (tenant_id, purchase_id, amount, received_by) VALUES ('t1','a1',40,'ann'), ('t1','c1',100,'ann')`,
		`UPDATE procurement.purchase SET status='closed' WHERE tenant_id='t1' AND id='c1'`,
	} {
		d.Exec(t, sql)
	}
	return d
}
