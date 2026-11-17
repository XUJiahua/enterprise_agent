package procurement_test

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/XUJiahua/enterprise_agent/internal/procurement"
	"github.com/XUJiahua/enterprise_agent/kernel"
	"github.com/XUJiahua/enterprise_agent/kernel/pg"
	"github.com/XUJiahua/enterprise_agent/kernel/pg/pgtest"
)

func op(r *pg.Runner, p kernel.Principal, ref kernel.Ref, key, raw string) (pg.Result, error) {
	return r.Run(context.Background(), p, ref, key, json.RawMessage(raw))
}

// approved returns a setup where bob approved p1 (900) and p2 (300).
func approved(t *testing.T) (*pgtest.DB, *pg.Runner) {
	d, r := setup(t)
	if _, err := op(r, bob, procurement.ApproveRef, "a", `{"ids":["p1","p2"]}`); err != nil {
		t.Fatal(err)
	}
	return d, r
}

func snapshot(t *testing.T, d *pgtest.DB) string {
	return strings.Join(d.Strings(t, `SELECT (SELECT string_agg(id||'='||status, ',' ORDER BY id COLLATE "C") FROM procurement.purchase)
	 ||' r'||(SELECT count(*) FROM procurement.receipt)||' q'||(SELECT count(*) FROM kernel.request)
	 ||' a'||(SELECT count(*) FROM kernel.audit)||' o'||(SELECT count(*) FROM kernel.outbox)`), "")
}

func TestReceiveAndClose(t *testing.T) {
	d, r := approved(t)
	recv := func(key, id string, amount int) error {
		_, err := op(r, ann, procurement.ReceiveRef, key, `{"purchaseId":"`+id+`","amount":`+itoa(amount)+`}`)
		return err
	}
	if err := recv("r1", "p1", 500); err != nil {
		t.Fatal(err)
	}
	before := snapshot(t, d)
	for name, raw := range map[string]string{"short p1": `{"ids":["p1"],"reason":"r"}`, "p2 has none": `{"ids":["p1","p2"],"reason":"r"}`, "submitted": `{"ids":["own"],"reason":"r"}`, "other tenant": `{"ids":["x1"],"reason":"r"}`} {
		if _, err := op(r, bob, procurement.CloseRef, "c", raw); kernel.CodeOf(err) != kernel.Denied {
			t.Errorf("close %s: %v", name, err)
		}
	}
	if _, err := op(r, ann, procurement.CloseRef, "c", `{"ids":["p1"],"reason":"done"}`); kernel.CodeOf(err) != kernel.Denied {
		t.Error("requester closed:", err)
	}
	for _, raw := range []string{`{"ids":["p1"]}`, `{"ids":["p1"],"reason":"  "}`} {
		if _, err := op(r, bob, procurement.CloseRef, "c", raw); kernel.CodeOf(err) != kernel.Invalid {
			t.Errorf("close without reason %s: %v", raw, err)
		}
	}
	if _, err := op(r, bob, kernel.Ref{Name: "procurement.close", Version: 1}, "c", `{"ids":["p1"]}`); kernel.CodeOf(err) != kernel.Invalid {
		t.Error("retired close@1 still runs:", err)
	}
	for name, err := range map[string]error{
		"approver receives": func() error {
			_, err := op(r, bob, procurement.ReceiveRef, "r", `{"purchaseId":"p1","amount":1}`)
			return err
		}(),
		"submitted purchase": recv("r", "own", 1),
		"other tenant":       recv("r", "x1", 1),
		"missing":            recv("r", "nope", 1),
	} {
		if kernel.CodeOf(err) != kernel.Denied {
			t.Errorf("receive %s: %v", name, err)
		}
	}
	if err := recv("r", "p1", 0); kernel.CodeOf(err) != kernel.Invalid {
		t.Error("zero amount:", err)
	}
	if got := snapshot(t, d); got != before {
		t.Fatalf("refusals wrote:\n %s\n %s", before, got)
	}

	if err := recv("r2", "p1", 400); err != nil {
		t.Fatal(err)
	}
	if err := recv("r3", "p2", 300); err != nil {
		t.Fatal(err)
	}
	res, err := op(r, bob, procurement.CloseRef, "c1", `{"ids":["p2","p1"],"reason":"received"}`)
	if err != nil || string(res.Body) != `{"closedIds":["p1","p2"]}` {
		t.Fatal(res, err)
	}
	want(t, "closed", d.Strings(t, `SELECT id, status FROM procurement.purchase WHERE id IN ('p1','p2') ORDER BY id`), "p1 closed", "p2 closed")
	want(t, "close audit", d.Strings(t, `SELECT target_id, change->'status'->>'to', change->>'received', change->>'reason' FROM kernel.audit WHERE operation='procurement.close' ORDER BY 1`),
		"p1 closed 900 received", "p2 closed 300 received")
	want(t, "outbox", d.Strings(t, `SELECT target_id, event_type, event_version, payload::text FROM kernel.outbox ORDER BY 1`),
		`p1 procurement.purchase.closed 1 {"id": "p1", "amount": 900, "received": 900}`,
		`p2 procurement.purchase.closed 1 {"id": "p2", "amount": 300, "received": 300}`)
	if again, err := op(r, bob, procurement.CloseRef, "c1", `{"ids":["p1","p2"],"reason":"received"}`); err != nil || !again.Replayed {
		t.Fatal("replay:", again, err)
	}
	// After close, the receipts it was judged by cannot grow.
	if err := recv("r4", "p1", 1); kernel.CodeOf(err) != kernel.Denied {
		t.Fatal("receipt on closed purchase:", err)
	}
}

func TestCloseOutboxFailureRollsBack(t *testing.T) {
	d, r := approved(t)
	if _, err := op(r, ann, procurement.ReceiveRef, "r", `{"purchaseId":"p2","amount":300}`); err != nil {
		t.Fatal(err)
	}
	d.Exec(t, `CREATE FUNCTION fail() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'outbox down'; END $$`)
	d.Exec(t, `CREATE TRIGGER fail BEFORE INSERT ON kernel.outbox FOR EACH ROW EXECUTE FUNCTION fail()`)
	before := snapshot(t, d)
	if _, err := op(r, bob, procurement.CloseRef, "c", `{"ids":["p2"],"reason":"done"}`); kernel.CodeOf(err) != kernel.Internal {
		t.Fatal(err)
	}
	if got := snapshot(t, d); got != before {
		t.Fatalf("partial commit:\n %s\n %s", before, got)
	}
}

// barrier pauses the first operation of ref right after it holds its purchase
// locks, until release is closed.
func barrier(t *testing.T, ref kernel.Ref) (locked chan struct{}, release chan struct{}) {
	locked, release = make(chan struct{}), make(chan struct{})
	first := true
	procurement.SetAfterLock(func(got kernel.Ref) {
		if got == ref && first {
			first = false
			close(locked)
			<-release
		}
	})
	t.Cleanup(func() { procurement.SetAfterLock(nil) })
	return locked, release
}

// waitBlocked waits until some backend of this database waits on a row lock.
func waitBlocked(t *testing.T, d *pgtest.DB) {
	t.Helper()
	for range 250 {
		if d.Strings(t, `SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'`)[0] != "0" {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatal("second operation never blocked on the purchase lock")
}

func async(f func() error) chan error {
	ch := make(chan error, 1)
	go func() { ch <- f() }()
	return ch
}

// Close holds p2's lock first; a concurrent receipt waits, then sees "closed"
// and is refused. Without the parent lock in receive the receipt would land on
// a closed purchase.
func TestCloseBeforeReceiveRefusesReceipt(t *testing.T) {
	d, r := approved(t)
	if _, err := op(r, ann, procurement.ReceiveRef, "r0", `{"purchaseId":"p2","amount":300}`); err != nil {
		t.Fatal(err)
	}
	locked, release := barrier(t, procurement.CloseRef)
	closing := async(func() error {
		_, err := op(r, bob, procurement.CloseRef, "c", `{"ids":["p2"],"reason":"done"}`)
		return err
	})
	<-locked
	receiving := async(func() error {
		_, err := op(r, ann, procurement.ReceiveRef, "r1", `{"purchaseId":"p2","amount":50}`)
		return err
	})
	waitBlocked(t, d)
	close(release)
	if err := <-closing; err != nil {
		t.Fatal("close:", err)
	}
	if err := <-receiving; kernel.CodeOf(err) != kernel.Denied {
		t.Fatal("receipt after close accepted:", err)
	}
	want(t, "receipts", d.Strings(t, `SELECT purchase_id, amount FROM procurement.receipt ORDER BY id`), "p2 300")
}

// A receipt holds p1's lock first; a concurrent close waits and then counts
// that receipt, so the purchase closes with the full amount.
func TestReceiveBeforeCloseCountsReceipt(t *testing.T) {
	d, r := approved(t)
	if _, err := op(r, ann, procurement.ReceiveRef, "r0", `{"purchaseId":"p1","amount":500}`); err != nil {
		t.Fatal(err)
	}
	locked, release := barrier(t, procurement.ReceiveRef)
	receiving := async(func() error {
		_, err := op(r, ann, procurement.ReceiveRef, "r1", `{"purchaseId":"p1","amount":400}`)
		return err
	})
	<-locked
	closing := async(func() error {
		_, err := op(r, bob, procurement.CloseRef, "c", `{"ids":["p1"],"reason":"done"}`)
		return err
	})
	waitBlocked(t, d)
	close(release)
	if err := <-receiving; err != nil {
		t.Fatal("receive:", err)
	}
	if err := <-closing; err != nil {
		t.Fatal("close did not see the committed receipt:", err)
	}
	want(t, "closed with full receipts", d.Strings(t, `SELECT change->>'received' FROM kernel.audit WHERE operation='procurement.close'`), "900")
}

func TestMigrateIsRepeatableAndRefusesDirty(t *testing.T) {
	d, _ := setup(t)
	if v, applied, err := procurement.Migrate(d.AdminURL); err != nil || applied || v != 6 {
		t.Fatal("second run:", v, applied, err)
	}
	d.Exec(t, `UPDATE procurement_schema_migrations SET dirty = true`)
	if _, _, err := procurement.Migrate(d.AdminURL); err == nil || !strings.Contains(strings.ToLower(err.Error()), "dirty") {
		t.Fatal("dirty database accepted:", err)
	}
}

func TestHTTP(t *testing.T) {
	_, r := approved(t)
	users := map[string]kernel.Principal{"ann": ann, "bob": bob}
	srv := httptest.NewServer(procurement.Handler(r, func(req *http.Request) (kernel.Principal, bool) {
		p, ok := users[strings.TrimPrefix(req.Header.Get("Authorization"), "Bearer ")]
		return p, ok
	}))
	defer srv.Close()
	call := func(method, path, user, key, body string) (int, http.Header, string) {
		req, _ := http.NewRequest(method, srv.URL+path, strings.NewReader(body))
		if user != "" {
			req.Header.Set("Authorization", "Bearer "+user)
		}
		req.Header.Set("Idempotency-Key", key)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		b, _ := io.ReadAll(resp.Body)
		return resp.StatusCode, resp.Header, strings.TrimSpace(string(b))
	}
	recv := `{"purchaseId":"p2","amount":300}`
	for _, c := range []struct {
		method, path, user, key, body string
		status                        int
		replayed, contains            string
	}{
		{"POST", "/v1/operations/procurement.receive/1", "", "k", recv, 401, "", "unauthenticated"},
		{"POST", "/v1/operations/procurement.receive/1", "ann", "k", recv, 200, "false", `"amount":300`},
		{"POST", "/v1/operations/procurement.receive/1", "ann", "k", recv, 200, "true", `"amount":300`},
		{"POST", "/v1/operations/procurement.receive/1", "ann", "k", `{"purchaseId":"p2","amount":1}`, 409, "", "conflict"},
		{"POST", "/v1/operations/procurement.receive/x", "ann", "k", recv, 400, "", "invalid version"},
		{"POST", "/v1/operations/procurement.nope/1", "ann", "k", recv, 400, "", "unknown operation"},
		{"POST", "/v1/operations/procurement.close/2", "bob", "c", `{"ids":["p1"],"reason":"r"}`, 403, "", `"message":"one or more targets are not eligible"`},
		{"POST", "/v1/operations/procurement.close/2", "bob", "c", `{"ids":["x1"],"reason":"r"}`, 403, "", `"message":"one or more targets are not eligible"`},
		{"POST", "/v1/operations/procurement.close/2", "bob", "c2", `{"ids":["p2"]}`, 400, "", "reason is required"},
		{"POST", "/v1/operations/procurement.close/1", "bob", "c2", `{"ids":["p2"]}`, 400, "", "unknown operation procurement.close@1"},
		{"POST", "/v1/operations/procurement.close/2", "bob", "c2", `{"ids":["p2"],"reason":"received"}`, 200, "false", `{"closedIds":["p2"]}`},
	} {
		status, h, body := call(c.method, c.path, c.user, c.key, c.body)
		if status != c.status || h.Get("Idempotent-Replayed") != c.replayed || !strings.Contains(body, c.contains) {
			t.Errorf("%s %s %s: %d replayed=%q %s", c.method, c.path, c.body, status, h.Get("Idempotent-Replayed"), body)
		}
	}
	status, _, body := call("GET", "/v1/purchases", "bob", "", "")
	var got struct{ Purchases []map[string]any }
	if status != 200 || json.Unmarshal([]byte(body), &got) != nil {
		t.Fatal(status, body)
	}
	var ids []string
	for _, p := range got.Purchases {
		keys := make([]string, 0, len(p))
		for k := range p {
			keys = append(keys, k)
		}
		slices.Sort(keys)
		if strings.Join(keys, ",") != "amount,createdBy,id,received,status,title" {
			t.Fatalf("serialized fields %v", keys)
		}
		ids = append(ids, p["id"].(string))
	}
	if strings.Join(ids, ",") != "done,own,p1,p2" || strings.Contains(body, "bank-") || strings.Contains(body, "secret") {
		t.Fatalf("list leaked or wrong: %s", body)
	}
	if !strings.Contains(body, `"id":"p2","title":"desk","amount":300,"status":"closed","createdBy":"ann","received":300`) {
		t.Fatalf("p2 row: %s", body)
	}
}

func itoa(n int) string { b, _ := json.Marshal(n); return string(b) }

func TestCloseNotifiesCreatorOnce(t *testing.T) {
	d, r := approved(t)
	c := consumer(t, d)
	ctx := context.Background()
	for _, id := range []string{"p1", "p2"} {
		amount := map[string]string{"p1": "900", "p2": "300"}[id]
		if _, err := op(r, ann, procurement.ReceiveRef, "r-"+id, `{"purchaseId":"`+id+`","amount":`+amount+`}`); err != nil {
			t.Fatal(err)
		}
	}
	if n, err := c.Drain(ctx, procurement.Notifier); n != 0 || err != nil {
		t.Fatal("events before close:", n, err)
	}
	if _, err := op(r, bob, procurement.CloseRef, "c", `{"ids":["p1","p2"],"reason":"done"}`); err != nil {
		t.Fatal(err)
	}
	for i, wantN := range []int{2, 0} {
		if n, err := c.Drain(ctx, procurement.Notifier); n != wantN || err != nil {
			t.Fatalf("drain %d: %d %v", i, n, err)
		}
	}
	want(t, "notifications", d.Strings(t, `SELECT recipient, purchase_id, kind FROM procurement.notification ORDER BY purchase_id`), "ann p1 closed", "ann p2 closed")
	a, err := procurement.ListActivity(ctx, r, ann)
	if err != nil || len(a.Notifications) != 2 || len(a.Audit) == 0 {
		t.Fatal(a, err)
	}
	if a, _ = procurement.ListActivity(ctx, r, bob); len(a.Notifications) != 0 {
		t.Fatal("bob sees ann's notifications:", a.Notifications)
	}
	// A closed event whose payload does not match closed@1 is reported and left pending.
	d.Exec(t, `INSERT INTO kernel.outbox (tenant_id, actor_id, operation, version, key, event_type, event_version, target_id, payload)
	 VALUES ('t1','bob','procurement.close',2,'c','procurement.purchase.closed',1,'p1','{"id":"p1","amount":900}')`)
	if n, err := c.Drain(ctx, procurement.Notifier); n != 0 || err == nil || !strings.Contains(err.Error(), "payload does not match") {
		t.Fatal(n, err)
	}
	want(t, "still two", d.Strings(t, `SELECT count(*) FROM procurement.notification`), "2")
}

// Role separation: the application role cannot act as the consumer, and the
// consumer role cannot change business data.
func TestConsumerRoleSeparation(t *testing.T) {
	d, r := approved(t)
	ctx := context.Background()
	if _, err := op(r, ann, procurement.ReceiveRef, "r", `{"purchaseId":"p2","amount":300}`); err != nil {
		t.Fatal(err)
	}
	if _, err := op(r, bob, procurement.CloseRef, "c", `{"ids":["p2"],"reason":"done"}`); err != nil {
		t.Fatal(err)
	}
	if _, err := r.Drain(ctx, procurement.Notifier); err == nil || !strings.Contains(err.Error(), "permission denied") {
		t.Fatal("application role drained:", err)
	}
	c := consumer(t, d)
	if n, err := c.Drain(ctx, procurement.Notifier); n != 1 || err != nil {
		t.Fatal(n, err)
	}
	if _, err := op(c, bob, procurement.ApproveRef, "x", `{"ids":["own"]}`); err == nil {
		t.Fatal("consumer role ran a business operation")
	}
	want(t, "unchanged", d.Strings(t, `SELECT status FROM procurement.purchase WHERE id='own'`), "submitted")
}

func TestCreate(t *testing.T) {
	d, r := setup(t)
	res, err := op(r, ann, procurement.CreateRef, "n1", `{"id":"new-1","title":"  monitor ","amount":250}`)
	if err != nil || string(res.Body) != `{"id":"new-1","status":"submitted"}` {
		t.Fatal(res, err)
	}
	want(t, "row", d.Strings(t, `SELECT tenant_id, created_by, title, amount, status FROM procurement.purchase WHERE id='new-1'`), "t1 ann monitor 250 submitted")
	for name, c := range map[string]struct {
		p    kernel.Principal
		raw  string
		code kernel.Code
	}{
		"approver":       {bob, `{"id":"n2","title":"x","amount":1}`, kernel.Denied},
		"taken id":       {ann, `{"id":"p1","title":"x","amount":1}`, kernel.Denied},
		"zero amount":    {ann, `{"id":"n2","title":"x","amount":0}`, kernel.Invalid},
		"blank title":    {ann, `{"id":"n2","title":"  ","amount":1}`, kernel.Invalid},
		"bad id":         {ann, `{"id":"a b","title":"x","amount":1}`, kernel.Invalid},
		"status in body": {ann, `{"id":"n2","title":"x","amount":1,"status":"approved"}`, kernel.Invalid},
	} {
		if _, err := op(r, c.p, procurement.CreateRef, "n2", c.raw); kernel.CodeOf(err) != c.code {
			t.Errorf("%s: %v", name, err)
		}
	}
	// Same id in another tenant is a different purchase.
	dave := kernel.Principal{Tenant: "t2", Actor: "dave", Roles: []string{procurement.Requester}}
	if _, err := op(r, dave, procurement.CreateRef, "n1", `{"id":"new-1","title":"y","amount":1}`); err != nil {
		t.Fatal(err)
	}
	// Full lifecycle on the created purchase.
	steps := []struct {
		p   kernel.Principal
		ref kernel.Ref
		raw string
	}{
		{bob, procurement.ApproveRef, `{"ids":["new-1"]}`},
		{ann, procurement.ReceiveRef, `{"purchaseId":"new-1","amount":250}`},
		{bob, procurement.CloseRef, `{"ids":["new-1"],"reason":"done"}`},
	}
	for i, s := range steps {
		if _, err := op(r, s.p, s.ref, "life-"+itoa(i), s.raw); err != nil {
			t.Fatal(s.ref, err)
		}
	}
	want(t, "closed", d.Strings(t, `SELECT tenant_id, status FROM procurement.purchase WHERE id='new-1' ORDER BY 1`), "t1 closed", "t2 submitted")
}
