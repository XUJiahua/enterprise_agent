package pg_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"strings"
	"sync"
	"testing"

	"github.com/XUJiahua/enterprise_agent/kernel"
	"github.com/XUJiahua/enterprise_agent/kernel/pg"
	"github.com/XUJiahua/enterprise_agent/kernel/pg/pgtest"
	"github.com/jackc/pgx/v5/pgxpool"
)

// mark is a toy operation on toy.item: it sets marked=true on unmarked items.
// fault lets a test make Execute misreport or fail after it has written.
type mark struct {
	version  int
	fault    func(*kernel.Outcome) error
	dropNote bool   // Normalize forgets to carry note into the payload
	leak     string // Execute denies with this message instead of a neutral one
}

var markedEvent = kernel.EventType{Type: "toy.marked", Version: 1}

func (m mark) Definition() kernel.Definition {
	return kernel.Definition{Ref: kernel.Ref{Name: "toy.mark", Version: m.version}, MaxTargets: 10, Events: []kernel.EventType{markedEvent}}
}

func (m mark) Normalize(raw json.RawMessage) (kernel.Input, error) {
	var req struct {
		IDs  []string `json:"ids"`
		Note string   `json:"note"`
	}
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&req); err != nil {
		return kernel.Input{}, kernel.Invalidf("bad request")
	}
	payload, _ := json.Marshal(map[string]string{"note": req.Note})
	if m.dropNote {
		payload = nil
	}
	return kernel.Input{Targets: req.IDs, Payload: payload}, nil
}

func (mark) Authorize(p kernel.Principal) error {
	if !p.Has("marker") {
		return kernel.Deniedf("not permitted")
	}
	return nil
}

func (m mark) Execute(ctx context.Context, s *pg.Session, in kernel.Input) (kernel.Outcome, error) {
	rows, err := s.Query(ctx, `SELECT id FROM toy.item WHERE id = ANY($1) AND NOT marked ORDER BY id COLLATE "C" FOR UPDATE`, in.Targets)
	if err != nil {
		return kernel.Outcome{}, err
	}
	var locked []string
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return kernel.Outcome{}, err
		}
		locked = append(locked, id)
	}
	if err := rows.Err(); err != nil {
		return kernel.Outcome{}, err
	}
	if len(locked) != len(in.Targets) {
		if m.leak != "" {
			return kernel.Outcome{}, kernel.Deniedf("%s", m.leak)
		}
		return kernel.Outcome{}, kernel.Deniedf("targets not eligible")
	}
	if _, err := s.Exec(ctx, `UPDATE toy.item SET marked = true WHERE id = ANY($1)`, in.Targets); err != nil {
		return kernel.Outcome{}, err
	}
	out := kernel.Outcome{Targets: locked, Result: map[string]any{"marked": locked}}
	for _, id := range locked {
		out.Audit = append(out.Audit, kernel.Audit{Target: id, Change: json.RawMessage(`{"marked":{"from":false,"to":true}}`)})
		out.Events = append(out.Events, kernel.Event{EventType: markedEvent, Target: id, Payload: json.RawMessage(fmt.Sprintf(`{"id":%q}`, id))})
	}
	if m.fault != nil {
		return out, m.fault(&out)
	}
	return out, nil
}

const toySchema = `CREATE SCHEMA toy;
CREATE TABLE toy.item (tenant_id text NOT NULL, id text PRIMARY KEY, marked boolean NOT NULL DEFAULT false);
ALTER TABLE toy.item ENABLE ROW LEVEL SECURITY, FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON toy.item USING (tenant_id = current_setting('app.tenant', true));
GRANT USAGE ON SCHEMA toy, kernel TO kernel_test_runtime;
GRANT SELECT, UPDATE (marked) ON toy.item TO kernel_test_runtime;
GRANT SELECT, INSERT ON kernel.request, kernel.audit, kernel.outbox, kernel.inbox TO kernel_test_runtime;
GRANT EXECUTE ON FUNCTION kernel.pending_tenants(text, text[]) TO kernel_test_runtime;
CREATE TABLE toy.log (tenant_id text NOT NULL, event_id bigint UNIQUE NOT NULL, item text NOT NULL);
ALTER TABLE toy.log ENABLE ROW LEVEL SECURITY, FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON toy.log USING (tenant_id = current_setting('app.tenant', true));
GRANT SELECT, INSERT ON toy.log TO kernel_test_runtime;
INSERT INTO toy.item (tenant_id, id) VALUES ('t1','a'),('t1','b'),('t1','c'),('t1','d'),('t2','x');`

var (
	ann = kernel.Principal{Tenant: "t1", Actor: "ann", Roles: []string{"marker"}}
	ref = kernel.Ref{Name: "toy.mark", Version: 1}
)

func setup(t *testing.T, ops ...pg.Operation) (*pgtest.DB, *pg.Runner, *pgxpool.Pool) {
	d := pgtest.New(t)
	d.Exec(t, pg.Schema)
	d.Exec(t, pg.InboxSchema)
	d.Exec(t, toySchema)
	pool := d.Pool(t)
	if len(ops) == 0 {
		ops = []pg.Operation{mark{version: 1}}
	}
	r, err := pg.New(context.Background(), pool, registry(ops...))
	if err != nil {
		t.Fatal(err)
	}
	return d, r, pool
}

func want(t *testing.T, label string, got []string, want ...string) {
	t.Helper()
	if !slices.Equal(got, want) {
		t.Fatalf("%s:\n got  %q\n want %q", label, got, want)
	}
}

// counts returns the number of marked items and kernel rows, to prove zero writes.
func counts(t *testing.T, d *pgtest.DB) string {
	return d.Strings(t, `SELECT (SELECT count(*) FROM toy.item WHERE marked)||'/'||(SELECT count(*) FROM kernel.request)||'/'||
	 (SELECT count(*) FROM kernel.audit)||'/'||(SELECT count(*) FROM kernel.outbox)`)[0]
}

func run(r *pg.Runner, p kernel.Principal, key, raw string) (pg.Result, error) {
	return r.Run(context.Background(), p, ref, key, json.RawMessage(raw))
}

func TestRunWritesAndReplays(t *testing.T) {
	d, r, _ := setup(t)
	res, err := run(r, ann, "k1", `{"ids":["b","a"],"note":"n"}`)
	if err != nil || res.Replayed || string(res.Body) != `{"marked":["a","b"]}` {
		t.Fatal(res, err)
	}
	want(t, "audit", d.Strings(t, `SELECT target_id, change::text, operation, version FROM kernel.audit ORDER BY target_id`),
		`a {"marked": {"to": true, "from": false}} toy.mark 1`, `b {"marked": {"to": true, "from": false}} toy.mark 1`)
	want(t, "outbox", d.Strings(t, `SELECT target_id, event_type, event_version, payload->>'id' FROM kernel.outbox ORDER BY target_id`),
		"a toy.marked 1 a", "b toy.marked 1 b")

	again, err := run(r, ann, "k1", `{"ids":["a","b"],"note":"n"}`)
	if err != nil || !again.Replayed || string(again.Body) != string(res.Body) {
		t.Fatal("replay:", again, err)
	}
	for name, raw := range map[string]string{"payload": `{"ids":["a","b"],"note":"other"}`, "targets": `{"ids":["a"],"note":"n"}`} {
		if _, err = run(r, ann, "k1", raw); kernel.CodeOf(err) != kernel.Conflict {
			t.Errorf("different %s under the same key: %v", name, err)
		}
	}
	want(t, "replay/conflict wrote nothing", []string{counts(t, d)}, "2/1/2/2")
}

func TestRejectionsWriteNothing(t *testing.T) {
	d, r, _ := setup(t)
	cases := []struct {
		name string
		p    kernel.Principal
		ref  kernel.Ref
		key  string
		raw  string
		code kernel.Code
	}{
		{"no identity", kernel.Principal{Roles: []string{"marker"}}, ref, "k", `{"ids":["a"]}`, kernel.Denied},
		{"no tenant", kernel.Principal{Actor: "ann", Roles: []string{"marker"}}, ref, "k", `{"ids":["a"]}`, kernel.Denied},
		{"role", kernel.Principal{Tenant: "t1", Actor: "ann"}, ref, "k", `{"ids":["a"]}`, kernel.Denied},
		{"unknown op", ann, kernel.Ref{Name: "toy.mark", Version: 2}, "k", `{"ids":["a"]}`, kernel.Invalid},
		{"bad key", ann, ref, "a b", `{"ids":["a"]}`, kernel.Invalid},
		{"unknown field", ann, ref, "k", `{"ids":["a"],"tenant":"t2"}`, kernel.Invalid},
		{"duplicate", ann, ref, "k", `{"ids":["a","a"]}`, kernel.Invalid},
		{"too many", ann, ref, "k", `{"ids":["1","2","3","4","5","6","7","8","9","10","11"]}`, kernel.Invalid},
		{"cross-tenant mix", ann, ref, "k", `{"ids":["a","x"]}`, kernel.Denied},
		{"missing target", ann, ref, "k", `{"ids":["a","nope"]}`, kernel.Denied},
	}
	for _, c := range cases {
		if _, err := r.Run(context.Background(), c.p, c.ref, c.key, json.RawMessage(c.raw)); kernel.CodeOf(err) != c.code {
			t.Errorf("%s: got %v, want %s", c.name, err, c.code)
		}
	}
	// Mixed-tenant and missing targets share one error message: no existence leak.
	_, e1 := run(r, ann, "k", `{"ids":["a","x"]}`)
	_, e2 := run(r, ann, "k", `{"ids":["a","nope"]}`)
	if e1.Error() != e2.Error() {
		t.Errorf("denials differ: %q vs %q", e1, e2)
	}
	want(t, "nothing written", []string{counts(t, d)}, "0/0/0/0")
}

func TestOutcomeContractAndFailuresRollBack(t *testing.T) {
	faults := map[string]func(*kernel.Outcome) error{
		"target missing":   func(o *kernel.Outcome) error { o.Targets = o.Targets[:1]; return nil },
		"extra target":     func(o *kernel.Outcome) error { o.Targets = append(o.Targets, "c"); return nil },
		"audit missing":    func(o *kernel.Outcome) error { o.Audit = o.Audit[:1]; return nil },
		"audit not object": func(o *kernel.Outcome) error { o.Audit[0].Change = json.RawMessage(`[1]`); return nil },
		"event missing":    func(o *kernel.Outcome) error { o.Events = o.Events[:1]; return nil },
		"event undeclared": func(o *kernel.Outcome) error { o.Events[0].Version = 2; return nil },
		"event off-target": func(o *kernel.Outcome) error { o.Events[0].Target = "c"; return nil },
		"result":           func(o *kernel.Outcome) error { o.Result = func() {}; return nil },
		"post-check fails": func(*kernel.Outcome) error { return kernel.Deniedf("post-update check failed") },
		"domain error":     func(*kernel.Outcome) error { return errors.New("boom") },
	}
	for name, fault := range faults {
		t.Run(name, func(t *testing.T) {
			d, r, _ := setup(t, mark{version: 1, fault: fault})
			if _, err := run(r, ann, "k", `{"ids":["a","b"]}`); err == nil {
				t.Fatal("accepted")
			}
			want(t, "rolled back", []string{counts(t, d)}, "0/0/0/0")
		})
	}
	// Database-side failures of the audit, outbox and request inserts also roll back.
	for _, table := range []string{"kernel.request", "kernel.audit", "kernel.outbox"} {
		t.Run("fail "+table, func(t *testing.T) {
			d, r, _ := setup(t)
			d.Exec(t, `CREATE FUNCTION fail() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'down'; END $$`)
			d.Exec(t, `CREATE TRIGGER fail BEFORE INSERT ON `+table+` FOR EACH ROW EXECUTE FUNCTION fail()`)
			if _, err := run(r, ann, "k", `{"ids":["a","b"]}`); kernel.CodeOf(err) != kernel.Internal {
				t.Fatal(err)
			}
			want(t, "rolled back", []string{counts(t, d)}, "0/0/0/0")
		})
	}
}

func TestIdempotencyScope(t *testing.T) {
	d, r, _ := setup(t, mark{version: 1}, mark{version: 2})
	ctx := context.Background()
	raw := func(id string) json.RawMessage { return json.RawMessage(`{"ids":["` + id + `"]}`) }
	bea := kernel.Principal{Tenant: "t1", Actor: "bea", Roles: []string{"marker"}}
	other := kernel.Principal{Tenant: "t2", Actor: "ann", Roles: []string{"marker"}}
	for _, step := range []struct {
		p   kernel.Principal
		ref kernel.Ref
		id  string
	}{{ann, ref, "a"}, {bea, ref, "b"}, {other, ref, "x"}, {ann, kernel.Ref{Name: "toy.mark", Version: 2}, "c"}} {
		if res, err := r.Run(ctx, step.p, step.ref, "same", raw(step.id)); err != nil || res.Replayed {
			t.Fatalf("%v %v: key not scoped by actor/tenant/version: %v %v", step.p, step.ref, res, err)
		}
	}
	want(t, "four requests", d.Strings(t, `SELECT tenant_id, actor_id, version, targets[1] FROM kernel.request ORDER BY 1,2,3`),
		"t1 ann 1 a", "t1 ann 2 c", "t1 bea 1 b", "t2 ann 1 x")
	// A replay still needs the current operation-level permission.
	if _, err := r.Run(ctx, kernel.Principal{Tenant: "t1", Actor: "ann"}, ref, "same", raw("a")); kernel.CodeOf(err) != kernel.Denied {
		t.Fatal("replay bypassed authorization:", err)
	}
}

func TestConcurrentSameKeyExecutesOnce(t *testing.T) {
	d, r, _ := setup(t)
	const n = 8
	var wg sync.WaitGroup
	start := make(chan struct{})
	results := make([]pg.Result, n)
	errs := make([]error, n)
	for i := range n {
		wg.Add(1)
		go func() {
			defer wg.Done()
			<-start
			results[i], errs[i] = run(r, ann, "k", `{"ids":["a","b"]}`)
		}()
	}
	close(start)
	wg.Wait()
	fresh := 0
	for i := range n {
		if errs[i] != nil {
			t.Fatal(errs[i])
		}
		if !results[i].Replayed {
			fresh++
		}
	}
	if fresh != 1 {
		t.Fatalf("%d executions, want 1", fresh)
	}
	want(t, "one execution", []string{counts(t, d)}, "2/1/2/2")
}

// One pooled connection serves two tenants in turn: the principal context is
// transaction-local, and RLS hides kernel rows when no tenant is bound.
func TestPooledConnectionDoesNotLeakIdentity(t *testing.T) {
	d, _, _ := setup(t)
	ctx := context.Background()
	cfg, _ := pgxpool.ParseConfig(d.RuntimeURL)
	cfg.MaxConns = 1
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()
	r, err := pg.New(ctx, pool, registry(mark{version: 1}))
	if err != nil {
		t.Fatal(err)
	}
	if _, err = run(r, ann, "k", `{"ids":["a"]}`); err != nil {
		t.Fatal(err)
	}
	var tenant *string
	var visible int
	if err = pool.QueryRow(ctx, `SELECT nullif(current_setting('app.tenant', true), ''), (SELECT count(*) FROM kernel.request)`).Scan(&tenant, &visible); err != nil {
		t.Fatal(err)
	}
	if tenant != nil || visible != 0 {
		t.Fatalf("identity leaked to the next pool user: tenant=%v visible=%d", deref(tenant), visible)
	}
	other := kernel.Principal{Tenant: "t2", Actor: "ann", Roles: []string{"marker"}}
	if _, err = run(r, other, "k", `{"ids":["a"]}`); kernel.CodeOf(err) != kernel.Denied {
		t.Fatal("t2 reached a t1 target or t1's request on a reused connection:", err)
	}
	// RLS on kernel tables: the runtime role cannot write another tenant's rows.
	if _, err = pool.Exec(ctx, `INSERT INTO kernel.request VALUES ('t2','ann','toy.mark',1,'z','h','{}','{}')`); err == nil {
		t.Fatal("insert without bound tenant accepted")
	}
	// A later permissive policy cannot widen the restrictive tenant floor.
	d.Exec(t, `CREATE POLICY wide ON kernel.request USING (true)`)
	if err = pool.QueryRow(ctx, `SELECT count(*) FROM kernel.request`).Scan(&visible); err != nil || visible != 0 {
		t.Fatalf("permissive policy widened the tenant floor: %d %v", visible, err)
	}
}

func TestNewRejectsPrivilegedRuntimeAndBadDefinitions(t *testing.T) {
	d, _, pool := setup(t)
	ctx := context.Background()
	admin, err := pgxpool.New(ctx, d.AdminURL)
	if err != nil {
		t.Fatal(err)
	}
	defer admin.Close()
	if _, err = pg.New(ctx, admin, registry(mark{version: 1})); err == nil {
		t.Fatal("superuser runtime accepted")
	}
	if _, err = pg.New(ctx, pool, registry(mark{version: 1}, mark{version: 1})); err == nil {
		t.Fatal("duplicate registration accepted")
	}
	if _, err = pg.New(ctx, pool, registry(mark{version: 0})); err == nil {
		t.Fatal("invalid definition accepted")
	}
	d.Exec(t, `ALTER TABLE kernel.audit NO FORCE ROW LEVEL SECURITY`)
	if _, err = pg.New(ctx, pool, registry(mark{version: 1})); err == nil {
		t.Fatal("unforced RLS accepted")
	}
}

func deref(s *string) string {
	if s == nil {
		return "<nil>"
	}
	return *s
}

func TestReadIsTenantBoundAndReadOnly(t *testing.T) {
	_, r, _ := setup(t)
	ctx := context.Background()
	var ids []string
	err := r.Read(ctx, ann, func(s *pg.Session) error {
		rows, err := s.Query(ctx, `SELECT id FROM toy.item ORDER BY id`)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var id string
			if err := rows.Scan(&id); err != nil {
				return err
			}
			ids = append(ids, id)
		}
		return rows.Err()
	})
	if err != nil || strings.Join(ids, ",") != "a,b,c,d" {
		t.Fatal(ids, err)
	}
	err = r.Read(ctx, ann, func(s *pg.Session) error {
		_, err := s.Exec(ctx, `UPDATE toy.item SET marked = true`)
		return err
	})
	if kernel.CodeOf(err) != kernel.Internal || !strings.Contains(err.Error(), "read-only") {
		t.Fatal("write inside Read:", err)
	}
	if err = r.Read(ctx, kernel.Principal{}, func(*pg.Session) error { return nil }); kernel.CodeOf(err) != kernel.Denied {
		t.Fatal("Read without identity:", err)
	}
}

// Kernel-owned conventions hold even when the domain code gets them wrong.
func TestKernelOwnsFingerprintAndDenial(t *testing.T) {
	_, r, _ := setup(t, mark{version: 1, dropNote: true, leak: "item nope does not exist"})
	if _, err := run(r, ann, "k", `{"ids":["a"],"note":"x"}`); err != nil {
		t.Fatal(err)
	}
	if _, err := run(r, ann, "k", `{"ids":["a"],"note":"y"}`); kernel.CodeOf(err) != kernel.Conflict {
		t.Fatal("a field Normalize dropped did not separate the requests:", err)
	}
	_, err := run(r, ann, "k2", `{"ids":["b","nope"]}`)
	var e *kernel.Error
	if !errors.As(err, &e) || e.Code != kernel.Denied || e.Msg != kernel.UniformDenial {
		t.Fatalf("domain denial message reached the client: %v", err)
	}
	if _, err = r.Run(context.Background(), kernel.Principal{Tenant: "t1", Actor: "ann"}, ref, "k3", json.RawMessage(`{"ids":["a"]}`)); !errors.As(err, &e) || e.Msg != kernel.UniformDenial {
		t.Fatalf("authorization denial not uniform: %v", err)
	}
}
