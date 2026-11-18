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
	"slices"
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
	// RefusedBy, required when allowed is false, names the rule that must
	// refuse it: a constraint name, not_null:<column>, rls:<policy> or
	// trigger:<schema>.<function> (see refusal).
	RefusedBy string `json:"refusedBy,omitempty"`
}

//go:embed *.json
var frozen embed.FS

// appRole is the group role the application login role belongs to.
const appRole = "enterprise_runtime"

// TestFrozenConstraints runs every case in its own database and then checks
// coverage: for each (table, column, INSERT|UPDATE) the application role
// holds, the table's file needs a case of that kind that writes the column,
// has "allowed": false, and is refused by a rule on that column (see
// credited). It checks that such a refusal exists, not that it covers every
// rule Normalize applies to the column.
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
			t.Run(cf.Table+"/"+c.Name, func(t *testing.T) {
				s, err := parse(c.SQL)
				switch {
				case err != nil:
					t.Fatalf("unsupported statement (%v); write INSERT INTO <table> (<columns>) VALUES (<literal>, ...) or UPDATE <table> SET <column> = <literal>[, ...] [WHERE ...]; a literal is a number, string, TRUE, FALSE or NULL with an optional ::type", err)
				case s.table != cf.Table:
					t.Fatalf("writes %s; cases in %s.json must write %s", s.table, cf.Table, cf.Table)
				case c.Allowed == (c.RefusedBy != ""):
					t.Fatal(`"refusedBy" is required exactly when "allowed" is false`)
				}
				runCase(t, c)
			})
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
	byTable := map[string]rules{}
	for _, g := range grants {
		if byTable[g.table] == nil {
			byTable[g.table] = tableRules(t, d, g.table)
		}
		ok := false
		for _, c := range cases[g.table] {
			s, err := parse(c.SQL)
			ok = ok || err == nil && !c.Allowed && s.priv == g.priv && credited(s, c.RefusedBy, g.column, byTable[g.table])
		}
		if !ok {
			t.Errorf("%s.%s %s: %s may write it; add a case to constraints/%s.json: a %s that writes %s, \"allowed\": false, and \"refusedBy\" a rule on %s (a constraint on it, its NOT NULL, or a trigger function reading NEW.%s; row-level security does not count)",
				g.table, g.column, g.priv, appRole, g.table, g.priv, g.column, g.column, g.column)
		}
	}
}

// rules maps a refusal (as refusal() names it) to the columns it is about.
type rules map[string]map[string]bool

// tableRules reads, on the migrated schema with the standard seed only (no
// case seed), the constraints, NOT NULL columns and trigger functions of
// table and the columns each one refers to.
func tableRules(t *testing.T, d *pgtest.DB, table string) rules {
	r := rules{}
	add := func(rule, column string) {
		if r[rule] == nil {
			r[rule] = map[string]bool{}
		}
		r[rule][column] = true
	}
	for _, row := range d.Strings(t, `SELECT c.conname, a.attname FROM pg_constraint c
	  JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey) WHERE c.conrelid = $1::regclass`, table) {
		f := strings.Fields(row)
		add(f[0], f[1])
	}
	cols := d.Strings(t, `SELECT attname FROM pg_attribute WHERE attrelid = $1::regclass AND attnum > 0 AND NOT attisdropped`, table)
	for _, c := range d.Strings(t, `SELECT attname FROM pg_attribute WHERE attrelid = $1::regclass AND attnum > 0 AND NOT attisdropped AND attnotnull`, table) {
		add("not_null:"+c, c)
	}
	// Row-level security policies are deliberately absent: they are the
	// tenant floor, not a rule about a column, so an rls:<policy> refusal is
	// allowed as refusedBy but never covers a column.
	for _, row := range d.Strings(t, `SELECT n.nspname||'.'||p.proname, p.prosrc FROM pg_trigger tg
	  JOIN pg_proc p ON p.oid = tg.tgfoid JOIN pg_namespace n ON n.oid = p.pronamespace
	 WHERE tg.tgrelid = $1::regclass AND NOT tg.tgisinternal`, table) {
		fn, src, _ := strings.Cut(row, " ")
		for _, c := range cols {
			if regexp.MustCompile(`(?i)\bNEW\.` + regexp.QuoteMeta(c) + `\b`).MatchString(src) {
				add("trigger:"+fn, c)
			}
		}
	}
	return r
}

// credited reports whether a refused case s counts as covering column: s
// assigns column, and the declared (and, by runCase, observed) refusal is a
// rule that refers to column: a constraint on it, its NOT NULL, or a trigger
// function that reads NEW.<column>. A refusal by some other rule (a foreign
// key, a status check in a trigger, row-level security) says nothing about
// the column.
func credited(s statement, refusedBy, column string, r rules) bool {
	return slices.Contains(s.columns, column) && r[refusedBy][column]
}

// statement is what parse understood of a case: its privilege kind, the
// table it writes and the columns it assigns (INSERT column list or UPDATE
// SET targets).
type statement struct {
	priv, table string
	columns     []string
}

// parse accepts only `INSERT INTO t (c, ...) VALUES (lit, ...)` (one row)
// and `UPDATE t SET c = lit [, ...] [WHERE ...]`, where lit is a number,
// string, TRUE, FALSE or NULL with an optional ::type, after removing
// comments. Anything else (expressions, function calls, SELECT, CTEs,
// aliases, FROM, multi-column assignment, dollar quoting, several statements)
// is refused, so the assigned columns are exactly the ones named before each
// literal and a case cannot be credited for a column it does not assign.
func parse(sql string) (statement, error) {
	toks, err := tokenize(sql)
	if err != nil {
		return statement{}, err
	}
	i := 0
	next := func() string {
		if i >= len(toks) {
			return ""
		}
		i++
		return toks[i-1]
	}
	word := func(want string) bool { return i < len(toks) && toks[i] == want && func() bool { i++; return true }() }
	table := func() (string, error) {
		a, ok := ident(next())
		if !ok {
			return "", errors.New("expected a table name")
		}
		if !word(".") {
			return a, nil
		}
		b, ok := ident(next())
		if !ok {
			return "", errors.New("expected a table name after the schema")
		}
		return a + "." + b, nil
	}
	// literal consumes a number, string, TRUE, FALSE or NULL, optionally
	// signed and optionally followed by ::<type name>.
	literal := func() bool {
		word("-")
		t := next()
		if t != "'" && t != "true" && t != "false" && t != "null" && !regexp.MustCompile(`^[0-9][0-9.e]*$`).MatchString(t) {
			return false
		}
		if word(":") { // tokenize splits "::" into two ":" tokens
			if !word(":") {
				return false
			}
			_, ok := ident(next())
			return ok
		}
		return true
	}
	if slices.Contains(toks, ";") {
		return statement{}, errors.New("one statement per case")
	}
	var s statement
	switch {
	case word("insert"):
		s.priv = "INSERT"
		if !word("into") {
			return s, errors.New("expected INTO")
		}
		if s.table, err = table(); err != nil {
			return s, err
		}
		if !word("(") {
			return s, errors.New("INSERT needs a column list")
		}
		for {
			c, ok := ident(next())
			if !ok {
				return s, errors.New("expected a column name")
			}
			s.columns = append(s.columns, c)
			if word(")") {
				break
			}
			if !word(",") {
				return s, errors.New("expected , or ) in the column list")
			}
		}
		if !word("values") || !word("(") {
			return s, errors.New("expected VALUES ( after the column list")
		}
		for n := 0; ; n++ {
			if !literal() {
				return s, errors.New("each VALUES item must be a literal, optionally ::type")
			}
			if word(")") {
				if n+1 != len(s.columns) || i != len(toks) {
					return s, errors.New("need one row with one value per column, and nothing after it")
				}
				break
			}
			if !word(",") {
				return s, errors.New("expected , or ) in VALUES")
			}
		}
	case word("update"):
		s.priv = "UPDATE"
		if s.table, err = table(); err != nil {
			return s, err
		}
		if !word("set") {
			return s, errors.New("expected SET right after the table (no ONLY, no alias)")
		}
		for {
			c, ok := ident(next())
			if !ok || !word("=") {
				return s, errors.New("each SET item must be <column> = <expression>")
			}
			s.columns = append(s.columns, c)
			if !literal() {
				return s, errors.New("each SET value must be a literal, optionally ::type")
			}
			if word(",") {
				continue
			}
			if i != len(toks) && !word("where") {
				return s, errors.New("expected , WHERE or the end after a SET item")
			}
			break
		}
	default:
		return s, errors.New("only INSERT and UPDATE are supported")
	}
	return s, nil
}

// ident returns the identifier a token stands for: unquoted names are
// lowercased by tokenize, quoted ones keep their case.
func ident(tok string) (string, bool) {
	if strings.HasPrefix(tok, `"`) {
		return tok[1:], len(tok) > 1
	}
	return tok, regexp.MustCompile(`^[a-z_][a-z0-9_$]*$`).MatchString(tok)
}

// tokenize splits sql into lowercased words, quoted identifiers (prefixed
// with "), punctuation and a placeholder ' for each string literal. Comments
// are dropped; dollar quoting and parameters are refused.
func tokenize(sql string) ([]string, error) {
	var out []string
	for i := 0; i < len(sql); {
		c := sql[i]
		switch {
		case strings.ContainsRune(" \t\n\r\f\v", rune(c)):
			i++
		case strings.HasPrefix(sql[i:], "--"):
			if j := strings.IndexByte(sql[i:], '\n'); j >= 0 {
				i += j
			} else {
				i = len(sql)
			}
		case strings.HasPrefix(sql[i:], "/*"):
			j := strings.Index(sql[i+2:], "*/")
			if j < 0 || strings.Contains(sql[i+2:i+2+j], "/*") {
				return nil, errors.New("unterminated or nested comment")
			}
			i += j + 4
		case c == '\'' || (c == 'e' || c == 'E') && i+1 < len(sql) && sql[i+1] == '\'':
			escapes := c != '\''
			if escapes {
				i++
			}
			for i++; ; i++ {
				switch {
				case i >= len(sql):
					return nil, errors.New("unterminated string")
				case escapes && sql[i] == '\\':
					i++
				case sql[i] == '\'' && i+1 < len(sql) && sql[i+1] == '\'':
					i++
				case sql[i] == '\'':
					goto literal
				}
			}
		literal:
			i++
			out = append(out, "'")
		case c == '"':
			j := strings.IndexByte(sql[i+1:], '"')
			if j < 0 || i+2+j < len(sql) && sql[i+2+j] == '"' {
				return nil, errors.New("unterminated quoted identifier or \"\" inside one")
			}
			out = append(out, `"`+sql[i+1:i+1+j])
			i += j + 2
		case c == '$':
			return nil, errors.New("dollar quoting and parameters are not supported")
		case c == '_' || c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= 0x80 || c >= '0' && c <= '9':
			j := i
			for j < len(sql) && (sql[j] == '_' || sql[j] == '$' || sql[j] >= 'a' && sql[j] <= 'z' || sql[j] >= 'A' && sql[j] <= 'Z' || sql[j] >= '0' && sql[j] <= '9' || sql[j] >= 0x80 || sql[j] == '.' && c >= '0' && c <= '9') {
				j++
			}
			out = append(out, strings.ToLower(sql[i:j]))
			i = j
		default:
			out = append(out, string(c))
			i++
		}
	}
	return out, nil
}

// TestStatementParser pins what counts as writing a column, including the
// forms that must not be credited.
func TestStatementParser(t *testing.T) {
	for sql, want := range map[string]string{
		`INSERT INTO procurement.purchase (tenant_id,id,title) VALUES ('t1','x','a')`:                         "INSERT procurement.purchase tenant_id,id,title",
		`insert into procurement.purchase ("title", amount, note) values (E'it\'s', -1, NULL::text)`:          "INSERT procurement.purchase title,amount,note",
		`UPDATE procurement.purchase SET title = '   ' WHERE id='s1'`:                                         "UPDATE procurement.purchase title",
		`UPDATE procurement.purchase SET status = 'x', "title"=E'it\'s, title=', amount=1e12 WHERE id='s1'`:   "UPDATE procurement.purchase status,title,amount",
		`UPDATE procurement.purchase SET status='title=x', flag=TRUE::boolean WHERE title='a'`:                "UPDATE procurement.purchase status,flag",
		`UPDATE procurement.purchase SET status='closed' /* title='x' */ -- , title='y'` + "\n WHERE id='s1'": "UPDATE procurement.purchase status",
		`UPDATE procurement.purchase SET status='closed' WHERE id=(SELECT 'a' WHERE title='x')`:               "UPDATE procurement.purchase status",
		// values that are not a single literal are refused, not guessed
		`UPDATE procurement.purchase SET status=CASE WHEN title='pen' THEN 'closed' ELSE 'closed' END WHERE id='s1'`: "error",
		`UPDATE procurement.purchase SET status=ARRAY[amount=1, amount=2]::text WHERE id='s1'`:                       "error",
		`UPDATE procurement.purchase SET status=coalesce(NULL, 'x') WHERE id='s1'`:                                   "error",
		`UPDATE procurement.purchase SET status=(SELECT 'closed') WHERE id='s1'`:                                     "error",
		`UPDATE procurement.purchase SET status='closed' FROM procurement.receipt r WHERE r.amount=1`:                "error",
		`UPDATE procurement.purchase SET status='a' || 'b'`:                                                          "error",
		`INSERT INTO procurement.purchase (title, amount) VALUES (repeat('a', 2), 1)`:                                "error",
		`INSERT INTO procurement.purchase (title, amount) VALUES ('a', 1), ('b', 2)`:                                 "error",
		`INSERT INTO procurement.purchase (title, amount) VALUES ('a')`:                                              "error",
		`INSERT INTO procurement.purchase (title, amount) VALUES ('a', 1) RETURNING id`:                              "error",
		`insert into procurement.purchase ("title", amount) select 'a', 1`:                                           "error",
		`WITH x AS (SELECT 1) UPDATE procurement.purchase SET title='a'`:                                             "error",
		`UPDATE procurement.purchase p SET title='a'`:                                                                "error",
		`UPDATE ONLY procurement.purchase SET title='a'`:                                                             "error",
		`UPDATE procurement.purchase SET (title, status) = ('a', 'b')`:                                               "error",
		`UPDATE procurement.purchase SET title=$$a$$`:                                                                "error",
		`INSERT INTO procurement.purchase VALUES ('t1')`:                                                             "error",
		`INSERT INTO procurement.purchase (title) DEFAULT VALUES`:                                                    "error",
		`UPDATE procurement.purchase SET title='a'; SELECT 1`:                                                        "error",
		`UPDATE procurement.purchase SET title='a`:                                                                   "error",
		`DELETE FROM procurement.purchase`:                                                                           "error",
	} {
		s, err := parse(sql)
		got := s.priv + " " + s.table + " " + strings.Join(s.columns, ",")
		if err != nil {
			got = "error"
		}
		if got != want {
			t.Errorf("%s\n got  %s (%v)\n want %s", sql, got, err, want)
		}
	}
	// As tableRules reads them on the current schema.
	r := rules{"purchase_title_not_blank": {"title": true}, "receipt_tenant_id_purchase_id_fkey": {"tenant_id": true, "purchase_id": true},
		"trigger:procurement.purchase_guard": {"tenant_id": true, "id": true, "created_by": true, "amount": true, "status": true},
		"trigger:procurement.receipt_guard":  {"tenant_id": true, "purchase_id": true}}
	for _, c := range []struct {
		sql, refusedBy, column string
		want                   bool
	}{
		{`UPDATE procurement.purchase SET title='  ' WHERE id='s1'`, "purchase_title_not_blank", "title", true},
		{`UPDATE procurement.purchase SET status='closed' WHERE id='s1'`, "trigger:procurement.purchase_guard", "status", true},
		// the status trigger refuses; title is not what it refused
		{`UPDATE procurement.purchase SET status='closed' WHERE title='pen'`, "trigger:procurement.purchase_guard", "title", false},
		// row-level security never covers a column, whatever its expression mentions
		{`INSERT INTO procurement.purchase (tenant_id,title) VALUES ('t2','x')`, "rls:tenant_floor", "tenant_id", false},
		{`UPDATE procurement.purchase SET title='ok', status='closed' WHERE id='s1'`, "trigger:procurement.purchase_guard", "title", false},
		{`UPDATE procurement.purchase SET title='  ' WHERE id='s1'`, "trigger:procurement.purchase_guard", "title", false},
		{`INSERT INTO procurement.receipt (tenant_id,purchase_id,amount) VALUES ('t1','s1',1)`, "trigger:procurement.receipt_guard", "purchase_id", true},
		{`INSERT INTO procurement.receipt (tenant_id,purchase_id,amount) VALUES ('t1','s1',1)`, "trigger:procurement.receipt_guard", "amount", false},
		// an unrelated foreign key does not cover the amount
		{`INSERT INTO procurement.receipt (tenant_id,purchase_id,amount) VALUES ('t1','zz',1)`, "receipt_tenant_id_purchase_id_fkey", "amount", false},
		{`INSERT INTO procurement.receipt (tenant_id,purchase_id,amount) VALUES ('t1','zz',1)`, "receipt_tenant_id_purchase_id_fkey", "purchase_id", true},
	} {
		s, err := parse(c.sql)
		if got := err == nil && credited(s, c.refusedBy, c.column, r); got != c.want {
			t.Errorf("credited(%s, %s, %s) = %v, want %v", c.sql, c.refusedBy, c.column, got, c.want)
		}
	}
}

// refusal names the integrity rule behind err: a constraint name, not_null:<column>,
// trigger:<function> for an error raised in a PL/pgSQL trigger function,
// rls:<policy> for a row-level security WITH CHECK, or "" for anything else
// (a missing grant, a syntax error).
func refusal(err error) string {
	var pe *pgconn.PgError
	if !errors.As(err, &pe) {
		return ""
	}
	if pe.Code == "42501" && strings.Contains(pe.Message, "row-level security") {
		if m := regexp.MustCompile(`policy "([^"]+)"`).FindStringSubmatch(pe.Message); m != nil {
			return "rls:" + m[1]
		}
		return "rls"
	}
	switch {
	case !strings.HasPrefix(pe.Code, "23"):
		return ""
	case pe.ConstraintName != "":
		return pe.ConstraintName
	case pe.Code == "23502" && pe.ColumnName != "":
		return "not_null:" + pe.ColumnName
	}
	if m := regexp.MustCompile(`PL/pgSQL function ([\w.]+)\(`).FindStringSubmatch(pe.Where); m != nil {
		return "trigger:" + m[1]
	}
	return ""
}

// runCase runs the statement as the application role inside a transaction
// bound to tenant t1 and the case's actor, then rolls back. An allowed case
// must write a row; a refused one must fail with exactly the integrity rule
// it names in refusedBy (see refusal).
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
	switch got := refusal(err); {
	case c.Allowed && (err != nil || tag.RowsAffected() == 0):
		t.Fatalf("must succeed and write a row: rows=%d err=%v", tag.RowsAffected(), err)
	case !c.Allowed && err == nil:
		t.Fatalf("must be refused by the database (%s), but it wrote %d row(s)", c.RefusedBy, tag.RowsAffected())
	case !c.Allowed && got != c.RefusedBy:
		t.Fatalf("must be refused by %s, got refusal %q: %v", c.RefusedBy, got, err)
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
