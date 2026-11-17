package procurement_test

import (
	"context"
	"encoding/json"
	"io/fs"
	"slices"
	"testing"

	"github.com/XUJiahua/enterprise_agent/internal/procurement"
	"github.com/XUJiahua/enterprise_agent/kernel"
	"github.com/XUJiahua/enterprise_agent/kernel/pg"
	"github.com/XUJiahua/enterprise_agent/kernel/pg/pgtest"
)

var (
	ann = kernel.Principal{Tenant: "t1", Actor: "ann", Roles: []string{procurement.Requester}}
	bob = kernel.Principal{Tenant: "t1", Actor: "bob", Roles: []string{procurement.Approver}}
)

func TestKernelMigrationIsVerbatimSchema(t *testing.T) {
	b, err := fs.ReadFile(procurement.Migrations, "migrations/0001_kernel.up.sql")
	if err != nil || string(b) != pg.Schema {
		t.Fatal("0001_kernel.up.sql must equal kernel/pg/schema.sql", err)
	}
}

// setup applies the product migrations to an empty database and seeds t1/t2.
func setup(t *testing.T, beforeSeed ...string) (*pgtest.DB, *pg.Runner) {
	d := pgtest.New(t)
	d.Exec(t, `DO $$ BEGIN CREATE ROLE enterprise_runtime NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$`)
	d.Exec(t, `GRANT enterprise_runtime TO `+pgtest.Runtime+` WITH INHERIT TRUE`)
	if v, applied, err := procurement.Migrate(d.AdminURL); err != nil || !applied || v != 3 {
		t.Fatal("migrate:", v, applied, err)
	}
	for _, sql := range beforeSeed {
		d.Exec(t, sql)
	}
	d.Exec(t, `INSERT INTO procurement.purchase (tenant_id, id, created_by, title, amount, status, internal_note) VALUES
	 ('t1','p1','ann','laptop',900,'submitted','bank-1'), ('t1','p2','ann','desk',300,'submitted','bank-2'),
	 ('t1','own','bob','chair',50,'submitted',''), ('t1','done','ann','pen',5,'approved',''),
	 ('t2','x1','zed','server',5000,'submitted','secret')`)
	ctx := context.Background()
	pool := d.Pool(t)
	if err := pg.CheckRuntime(ctx, pool, procurement.Tables...); err != nil {
		t.Fatal(err)
	}
	r, err := pg.New(ctx, pool, procurement.Operations()...)
	if err != nil {
		t.Fatal(err)
	}
	return d, r
}

func approve(r *pg.Runner, p kernel.Principal, key, raw string) (pg.Result, error) {
	return r.Run(context.Background(), p, procurement.ApproveRef, key, json.RawMessage(raw))
}

func want(t *testing.T, label string, got []string, want ...string) {
	t.Helper()
	if !slices.Equal(got, want) {
		t.Fatalf("%s:\n got  %q\n want %q", label, got, want)
	}
}

func statuses(t *testing.T, d *pgtest.DB) []string {
	return d.Strings(t, `SELECT tenant_id, id, status FROM procurement.purchase ORDER BY tenant_id, id COLLATE "C"`)
}

var initial = []string{"t1 done approved", "t1 own submitted", "t1 p1 submitted", "t1 p2 submitted", "t2 x1 submitted"}

func TestApprove(t *testing.T) {
	d, r := setup(t)
	res, err := approve(r, bob, "k1", `{"ids":["p2","p1"],"reason":"budget ok"}`)
	if err != nil || string(res.Body) != `{"approvedIds":["p1","p2"]}` {
		t.Fatal(res, err)
	}
	want(t, "status", statuses(t, d), "t1 done approved", "t1 own submitted", "t1 p1 approved", "t1 p2 approved", "t2 x1 submitted")
	want(t, "audit", d.Strings(t, `SELECT target_id, actor_id, change->'status'->>'from', change->'status'->>'to', change->>'reason' FROM kernel.audit ORDER BY 1`),
		"p1 bob submitted approved budget ok", "p2 bob submitted approved budget ok")
	if again, err := approve(r, bob, "k1", `{"ids":["p1","p2"],"reason":"budget ok"}`); err != nil || !again.Replayed {
		t.Fatal("replay:", again, err)
	}
	// reason is meaningful input: a different reason under the same key conflicts.
	if _, err = approve(r, bob, "k1", `{"ids":["p1","p2"],"reason":"other"}`); kernel.CodeOf(err) != kernel.Conflict {
		t.Fatal("reason not fingerprinted:", err)
	}
}

func TestApproveRefusalsChangeNothing(t *testing.T) {
	d, r := setup(t)
	for name, c := range map[string]struct {
		p    kernel.Principal
		raw  string
		code kernel.Code
	}{
		"requester role":   {ann, `{"ids":["p1"]}`, kernel.Denied},
		"own purchase":     {bob, `{"ids":["p1","own"]}`, kernel.Denied},
		"already approved": {bob, `{"ids":["p1","done"]}`, kernel.Denied},
		"cross-tenant mix": {bob, `{"ids":["p1","x1"]}`, kernel.Denied},
		"missing":          {bob, `{"ids":["p1","nope"]}`, kernel.Denied},
		"tenant in body":   {bob, `{"ids":["x1"],"tenant":"t2"}`, kernel.Invalid},
		"long reason":      {bob, `{"ids":["p1"],"reason":"` + string(make([]byte, 201)) + `"}`, kernel.Invalid},
		"trailing data":    {bob, `{"ids":["p1"]} {}`, kernel.Invalid},
	} {
		if _, err := approve(r, c.p, "k", c.raw); kernel.CodeOf(err) != c.code {
			t.Errorf("%s: got %v, want %s", name, err, c.code)
		}
	}
	// The other tenant's approver cannot reach t1 targets either.
	zed := kernel.Principal{Tenant: "t2", Actor: "yan", Roles: []string{procurement.Approver}}
	if _, err := approve(r, zed, "k", `{"ids":["p1"]}`); kernel.CodeOf(err) != kernel.Denied {
		t.Error("cross-tenant approve:", err)
	}
	want(t, "unchanged", statuses(t, d), initial...)
	want(t, "no kernel rows", d.Strings(t, `SELECT count(*) FROM kernel.request UNION ALL SELECT count(*) FROM kernel.audit`), "0", "0")
}

// Database floor beneath the module: column grants and a restrictive tenant
// policy hold even for SQL that bypasses the operation code.
func TestDatabaseFloor(t *testing.T) {
	d, _ := setup(t)
	ctx := context.Background()
	pool := d.Pool(t)
	tx, err := pool.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = tx.Rollback(ctx) }()
	if _, err = tx.Exec(ctx, `SELECT set_config('app.tenant','t1',true)`); err != nil {
		t.Fatal(err)
	}
	try := func(label, sql string, allowed bool) {
		t.Helper()
		_, err := tx.Exec(ctx, `SAVEPOINT s`)
		if err != nil {
			t.Fatal(err)
		}
		_, err = tx.Exec(ctx, sql)
		if (err == nil) != allowed {
			t.Errorf("%s: allowed=%v err=%v", label, allowed, err)
		}
		_, _ = tx.Exec(ctx, `ROLLBACK TO SAVEPOINT s`)
	}
	try("read granted columns", `SELECT id, status FROM procurement.purchase`, true)
	try("over-read internal_note", `SELECT internal_note FROM procurement.purchase`, false)
	try("write amount", `UPDATE procurement.purchase SET amount = 1`, false)
	try("delete", `DELETE FROM procurement.purchase`, false)
	try("rewrite audit", `UPDATE kernel.audit SET change = '{}'`, false)
	try("insert other tenant request", `INSERT INTO kernel.request VALUES ('t2','a','o',1,'k','h','{}','{}')`, false)
	var n int
	if err = tx.QueryRow(ctx, `SELECT count(*) FROM procurement.purchase`).Scan(&n); err != nil || n != 4 {
		t.Fatalf("t1 sees %d purchases, want 4: %v", n, err)
	}
	// A later permissive policy cannot widen the restrictive tenant floor.
	_ = tx.Rollback(ctx) // release the table lock CREATE POLICY needs
	d.Exec(t, `CREATE POLICY wide ON procurement.purchase USING (true)`)
	if err = pool.QueryRow(ctx, `SELECT count(*) FROM procurement.purchase`).Scan(&n); err != nil || n != 0 {
		t.Fatalf("no tenant bound, yet %d purchases visible: %v", n, err)
	}
	if tx, err = pool.Begin(ctx); err != nil {
		t.Fatal(err)
	}
	if _, err = tx.Exec(ctx, `SELECT set_config('app.tenant','t1',true)`); err != nil {
		t.Fatal(err)
	}
	if err = tx.QueryRow(ctx, `SELECT count(*) FROM procurement.purchase`).Scan(&n); err != nil || n != 4 {
		t.Fatalf("permissive policy widened tenant floor: %d %v", n, err)
	}
}

// Under a linguistic collation ("B2" < "a1" in bytes, "a1" < "B2" in ICU en-US)
// the runner pairs targets by ID, not by database order.
func TestApproveUnderICUCollation(t *testing.T) {
	d, r := setup(t, `ALTER TABLE procurement.purchase ALTER COLUMN id TYPE text COLLATE "en-US-x-icu"`)
	d.Exec(t, `INSERT INTO procurement.purchase (tenant_id, id, created_by, title, amount) VALUES ('t1','a1','ann','x',1), ('t1','B2','ann','y',1)`)
	if got := d.Strings(t, `SELECT string_agg(id, ',' ORDER BY id) FROM procurement.purchase WHERE id IN ('a1','B2')`); got[0] != "a1,B2" {
		t.Fatalf("collation not linguistic: %v", got)
	}
	res, err := approve(r, bob, "kc", `{"ids":["a1","B2"]}`)
	if err != nil || string(res.Body) != `{"approvedIds":["B2","a1"]}` {
		t.Fatal(res, err)
	}
	want(t, "audit per target", d.Strings(t, `SELECT target_id FROM kernel.audit ORDER BY target_id COLLATE "C"`), "B2", "a1")
}
