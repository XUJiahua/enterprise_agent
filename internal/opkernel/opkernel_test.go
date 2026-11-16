package opkernel

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"net/url"
	"os"
	"sort"
	"strings"
	"testing"

	"github.com/XUJiahua/enterprise_agent/internal/app"
	"github.com/XUJiahua/enterprise_agent/internal/opdef"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Schema files applied in order, as in benchmark/phase6b/db.mjs plus 6a and the union fixup.
var schema = []string{"db/migrations/001_purchase.sql", "generated/guard.sql", "poc/transactional-action/schema.sql",
	"benchmark/phase4/schema.sql", "benchmark/phase5/schema.sql", "benchmark/phase6a/schema.sql",
	"benchmark/phase6b/schema.sql", "internal/opkernel/testdata/union.sql"}

var (
	alice = app.Identity{User: "alice", Tenant: "tenant-a", Role: "requester"}
	bob   = app.Identity{User: "bob", Tenant: "tenant-a", Role: "approver"}
	carol = app.Identity{User: "carol", Tenant: "tenant-a", Role: "approver"}
)

type db struct {
	t     *testing.T
	admin *pgx.Conn
	exec  *Executor
}

// open needs a disposable *_poc database (POC_DB_RESET=1), like scripts/db.mjs.
func open(t *testing.T) *db {
	admin, runtime := os.Getenv("ADMIN_DATABASE_URL"), os.Getenv("DATABASE_URL")
	if os.Getenv("POC_DB_RESET") != "1" || admin == "" || runtime == "" {
		if os.Getenv("POC_REQUIRE_DB") == "1" { // CI: a missing database must not pass as a skip
			t.Fatal("POC_REQUIRE_DB=1 but no disposable database configured")
		}
		t.Skip("needs POC_DB_RESET=1, ADMIN_DATABASE_URL and DATABASE_URL")
	}
	if u, err := url.Parse(admin); err != nil || !strings.HasSuffix(u.Path, "_poc") {
		t.Fatal("ADMIN_DATABASE_URL must name a disposable *_poc database")
	}
	ctx := context.Background()
	conn, err := pgx.Connect(ctx, admin)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close(ctx) })
	must := func(sql string) {
		if _, err := conn.Exec(ctx, sql); err != nil {
			t.Fatalf("%v\n%.200s", err, sql)
		}
	}
	must(`DROP TABLE IF EXISTS "AssignmentHistory","PurchaseEvent","ApprovalAudit","ActionRequest","Purchase","TenantMember" CASCADE`)
	must(`DROP FUNCTION IF EXISTS purchase_tier_guard() CASCADE`)
	var exists bool
	if err = conn.QueryRow(ctx, `SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='app_runtime')`).Scan(&exists); err != nil {
		t.Fatal(err)
	}
	if !exists {
		must(read(t, "db/bootstrap.sql"))
	}
	for _, p := range schema {
		must(read(t, p))
	}
	pool, err := pgxpool.New(ctx, runtime)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)
	exec, err := New(ctx, pool)
	if err != nil {
		t.Fatal(err)
	}
	return &db{t, conn, exec}
}

func read(t *testing.T, p string) string {
	b, err := os.ReadFile("../../" + p)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

// purchase inserts a tenant-a purchase as the administrator.
func (d *db) purchase(id, creator string, amount int, status string) {
	if _, err := d.admin.Exec(context.Background(), `INSERT INTO "Purchase" (id,"tenantId","createdBy",title,amount,status) VALUES ($1,'tenant-a',$2,'fixture',$3,$4)`,
		id, creator, amount, status); err != nil {
		d.t.Fatal(err)
	}
}

func (d *db) rows(sql string, args ...any) []string {
	rows, err := d.admin.Query(context.Background(), sql, args...)
	if err != nil {
		d.t.Fatal(err)
	}
	defer rows.Close()
	var out []string
	for rows.Next() {
		v, err := rows.Values()
		if err != nil {
			d.t.Fatal(err)
		}
		out = append(out, strings.TrimSuffix(strings.TrimPrefix(fmt.Sprint(v), "["), "]"))
	}
	return out
}

func (d *db) want(label string, got []string, want ...string) {
	d.t.Helper()
	if strings.Join(got, "|") != strings.Join(want, "|") {
		d.t.Fatalf("%s:\n got  %q\n want %q", label, got, want)
	}
}

func status(err error) int {
	var r *Rejection
	if errors.As(err, &r) {
		return r.Status
	}
	return -1
}

// ledger is the v6 internal/ledger.Fingerprint, copied to pin compatibility.
func ledger(operation string, ids []string) string {
	sorted := append([]string(nil), ids...)
	sort.Strings(sorted)
	h := sha256.New()
	fmt.Fprint(h, operation, "\n")
	for _, id := range sorted {
		fmt.Fprintf(h, "%d:%s", len(id), id)
	}
	return hex.EncodeToString(h.Sum(nil))
}

func TestCatalogOperationsDB(t *testing.T) {
	d := open(t)
	ctx := context.Background()
	d.purchase("small", "alice", 9, "submitted")
	d.purchase("big", "alice", 60000, "submitted")
	d.purchase("r1", "alice", 9, "submitted")
	d.purchase("own", "bob", 9, "submitted")

	// bulk-approve.v3: tier from the catalog; exempt, so no event.
	ids, replayed, err := d.exec.Run(ctx, bob, opdef.BulkApprove, "k1", Request{IDs: []string{"small", "big"}})
	if err != nil || replayed || strings.Join(ids, ",") != "big,small" {
		t.Fatal(ids, replayed, err)
	}
	d.want("tier", d.rows(`SELECT id,status FROM "Purchase" WHERE id IN ('small','big') ORDER BY id`), "big pending-second", "small approved")
	d.want("approve audit", d.rows(`SELECT "purchaseId","fromStatus","toStatus" FROM "ApprovalAudit" WHERE operation='bulk-approve.v3' ORDER BY 1`),
		"big submitted pending-second", "small submitted approved")
	d.want("approve exempt from events", d.rows(`SELECT type FROM "PurchaseEvent"`))

	// countersign.v6: separation and amount confirmation come from the catalog.
	if _, _, err = d.exec.Run(ctx, bob, opdef.Countersign, "k2", Request{IDs: []string{"big"}, Amount: 60000}); status(err) != 409 {
		t.Fatal("first approver countersigned:", err)
	}
	if _, _, err = d.exec.Run(ctx, carol, opdef.Countersign, "k2", Request{IDs: []string{"big"}, Amount: 59999}); status(err) != 409 {
		t.Fatal("wrong amount accepted:", err)
	}
	if _, _, err = d.exec.Run(ctx, carol, opdef.Countersign, "k3", Request{IDs: []string{"big"}, Amount: 60000}); err != nil {
		t.Fatal(err)
	}
	d.want("countersign", d.rows(`SELECT p.status,e.type,e."actorId" FROM "Purchase" p JOIN "PurchaseEvent" e ON e."purchaseId"=p.id WHERE p.id='big'`),
		"approved purchase.countersigned carol")
	d.want("countersign response", d.rows(`SELECT response::text FROM "ActionRequest" WHERE operation='countersign.v6'`), `{"id": "big", "status": "approved"}`)

	// bulk-reject.v4 then reopen.v5: reason is recorded, never hashed.
	if _, _, err = d.exec.Run(ctx, bob, opdef.BulkReject, "k4", Request{IDs: []string{"r1"}}); err != nil {
		t.Fatal(err)
	}
	if _, _, err = d.exec.Run(ctx, alice, opdef.Reopen, "k5", Request{IDs: []string{"r1"}, Reason: "fixed quote"}); err != nil {
		t.Fatal(err)
	}
	if _, replayed, err = d.exec.Run(ctx, alice, opdef.Reopen, "k5", Request{IDs: []string{"r1"}, Reason: "other words"}); err != nil || !replayed {
		t.Fatal("reason changed the fingerprint:", replayed, err)
	}
	d.want("reopen", d.rows(`SELECT a.reason,e.type FROM "ApprovalAudit" a JOIN "PurchaseEvent" e ON e."purchaseId"=a."purchaseId" AND e."idempotencyKey"=a."idempotencyKey" WHERE a.operation='reopen.v5'`),
		"fixed quote purchase.reopened")
	d.want("reject event", d.rows(`SELECT type FROM "PurchaseEvent" WHERE "idempotencyKey"='k4'`), "purchase.rejected")

	// bulk-reassign.v6: a field update with history and event; assignee is hashed.
	if _, _, err = d.exec.Run(ctx, bob, opdef.BulkReassign, "k6", Request{IDs: []string{"r1"}, Value: "frank"}); status(err) != 409 {
		t.Fatal("inactive assignee accepted:", err)
	}
	if _, _, err = d.exec.Run(ctx, bob, opdef.BulkReassign, "k6", Request{IDs: []string{"r1", "own"}, Value: "carol"}); status(err) != 409 {
		t.Fatal("own purchase reassigned:", err)
	}
	d.want("refused reassign wrote nothing", d.rows(`SELECT 1 FROM "AssignmentHistory" UNION ALL SELECT 1 FROM "ActionRequest" WHERE key='k6'`))
	if _, _, err = d.exec.Run(ctx, bob, opdef.BulkReassign, "k6", Request{IDs: []string{"r1"}, Value: "carol"}); err != nil {
		t.Fatal(err)
	}
	if _, replayed, err = d.exec.Run(ctx, bob, opdef.BulkReassign, "k6", Request{IDs: []string{"r1"}, Value: "carol"}); err != nil || !replayed {
		t.Fatal("replay:", replayed, err)
	}
	if _, _, err = d.exec.Run(ctx, bob, opdef.BulkReassign, "k6", Request{IDs: []string{"r1"}, Value: "hana"}); status(err) != 409 {
		t.Fatal("different assignee replayed:", err)
	}
	d.want("reassign", d.rows(`SELECT p."assigneeId",h."fromAssignee",h."toAssignee",e.type FROM "Purchase" p JOIN "AssignmentHistory" h ON h."purchaseId"=p.id
	 JOIN "PurchaseEvent" e ON e."purchaseId"=p.id AND e."idempotencyKey"='k6' WHERE p.id='r1'`), "carol <nil> carol purchase.reassigned")
	d.want("reassign status kept, no audit", d.rows(`SELECT status FROM "Purchase" WHERE id='r1' UNION ALL SELECT 'audit' FROM "ApprovalAudit" WHERE "idempotencyKey"='k6'`), "submitted")
	d.want("v6 ledger compatible", d.rows(`SELECT "requestHash" FROM "ActionRequest" WHERE key='k6'`), ledger("bulk-reassign.v6\ncarol", []string{"r1"}))
	d.want("reassign response", d.rows(`SELECT response::text FROM "ActionRequest" WHERE key='k6'`), `{"assignee": "carol", "reassignedIds": ["r1"]}`)

	// Outside the catalog or its inputs: refused before any write.
	if _, _, err = d.exec.Run(ctx, bob, "bulk-reassign.v7", "k7", Request{IDs: []string{"r1"}, Value: "hana"}); err == nil || status(err) != -1 {
		t.Fatal("unknown operation ran:", err)
	}
	if _, _, err = d.exec.Run(ctx, bob, opdef.BulkReject, "k7", Request{IDs: []string{"r1"}, Amount: 9}); err == nil || status(err) != -1 {
		t.Fatal("undeclared input accepted:", err)
	}
	if _, _, err = d.exec.Run(ctx, alice, opdef.BulkReject, "k7", Request{IDs: []string{"r1"}}); status(err) != 403 {
		t.Fatal("wrong role:", err)
	}

	// The event is written in the same transaction: an outbox failure rolls back everything.
	d.purchase("f1", "alice", 9, "submitted")
	for _, sql := range []string{`CREATE FUNCTION opkernel_fail() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'outbox down'; END $$`,
		`CREATE TRIGGER opkernel_fail BEFORE INSERT ON "PurchaseEvent" FOR EACH ROW EXECUTE FUNCTION opkernel_fail()`} {
		if _, err = d.admin.Exec(ctx, sql); err != nil {
			t.Fatal(err)
		}
	}
	defer d.admin.Exec(ctx, `DROP FUNCTION opkernel_fail() CASCADE`)
	if _, _, err = d.exec.Run(ctx, bob, opdef.BulkReject, "k8", Request{IDs: []string{"f1"}}); err == nil {
		t.Fatal("outbox failure ignored")
	}
	d.want("rolled back", d.rows(`SELECT status FROM "Purchase" WHERE id='f1' UNION ALL SELECT 'audit' FROM "ApprovalAudit" WHERE "idempotencyKey"='k8' UNION ALL SELECT 'request' FROM "ActionRequest" WHERE key='k8'`), "submitted")
}

func TestFingerprintMatchesLedger(t *testing.T) {
	for _, o := range opdef.All() {
		req := Request{Reason: "why", Amount: 7, Value: "carol"}
		domain := string(o.Name)
		if o.Param != "" {
			domain += "\ncarol"
		}
		if got := Fingerprint(o, []string{"b", "a"}, req); got != ledger(domain, []string{"a", "b"}) {
			t.Errorf("%s: fingerprint differs from the v6 ledger digest", o.Name)
		}
	}
}
