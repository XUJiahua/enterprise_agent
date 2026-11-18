package pg_test

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"strings"
	"sync"
	"sync/atomic"
	"testing"

	"github.com/XUJiahua/enterprise_agent/kernel"
	"github.com/XUJiahua/enterprise_agent/kernel/pg"
	"github.com/XUJiahua/enterprise_agent/kernel/pg/pgtest"
)

// logger projects toy.marked@1 into toy.log. failures > 0 makes that many
// Handle calls fail after writing, like a crash before commit.
type logger struct{ failures atomic.Int32 }

func (*logger) Name() string               { return "toy.log" }
func (*logger) Events() []kernel.EventType { return []kernel.EventType{markedEvent} }

func (l *logger) Handle(ctx context.Context, s *pg.Session, d pg.Delivery) error {
	var p struct{ ID string }
	dec := json.NewDecoder(bytes.NewReader(d.Event.Payload))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&p); err != nil || p.ID != d.Event.Target {
		return kernel.Internalf("unexpected payload %s", d.Event.Payload)
	}
	if _, err := s.Exec(ctx, `INSERT INTO toy.log (tenant_id, event_id, item) VALUES ($1, $2, $3)`, s.Principal().Tenant, d.ID, p.ID); err != nil {
		return err
	}
	if l.failures.Add(-1) >= 0 {
		return errors.New("crashed before commit")
	}
	return nil
}

var toyLogger = &logger{}

func registry(ops ...pg.Operation) pg.Registry {
	if len(ops) == 0 {
		ops = []pg.Operation{mark{version: 1}}
	}
	return pg.Registry{Operations: ops, Consumers: []pg.Consumer{toyLogger}}
}

func logged(t *testing.T, d *pgtest.DB) []string {
	return d.Strings(t, `SELECT tenant_id||' '||item FROM toy.log ORDER BY event_id`)
}

func TestRegistryMatchesEventsAndConsumers(t *testing.T) {
	d := pgtest.New(t)
	d.Exec(t, pg.Schema)
	d.Exec(t, pg.InboxSchema)
	d.Exec(t, toySchema)
	pool := d.Pool(t)
	ctx := context.Background()
	if _, err := pg.New(ctx, pool, pg.Registry{Operations: []pg.Operation{mark{version: 1}}}); err == nil || !strings.Contains(err.Error(), "no registered consumer") {
		t.Fatal("declared event without consumer:", err)
	}
	if _, err := pg.New(ctx, pool, pg.Registry{Consumers: []pg.Consumer{toyLogger}}); err == nil || !strings.Contains(err.Error(), "no registered operation declares") {
		t.Fatal("consumer of undeclared event:", err)
	}
	if _, err := pg.New(ctx, pool, pg.Registry{Operations: []pg.Operation{mark{version: 1}}, Consumers: []pg.Consumer{toyLogger, toyLogger}}); err == nil {
		t.Fatal("duplicate consumer accepted")
	}
	// A consumer may keep an older version of a declared type, not a newer or unknown one.
	if _, err := pg.Describe(pg.Registry{Operations: []pg.Operation{markV2Event{}}, Consumers: []pg.Consumer{multi{markedEvent, markedV2}}}); err != nil {
		t.Fatal("older version of a declared type refused:", err)
	}
	if _, err := pg.Describe(pg.Registry{Operations: []pg.Operation{mark{version: 1}}, Consumers: []pg.Consumer{multi{markedEvent, markedV2}}}); err == nil {
		t.Fatal("newer undeclared version accepted")
	}
}

func TestDrainProjectsOnceAndRecovers(t *testing.T) {
	d, r, _ := setup(t)
	ctx := context.Background()
	toyLogger.failures.Store(0)

	// A rolled-back operation leaves nothing to consume.
	if _, err := run(r, ann, "k0", `{"ids":["a","nope"]}`); kernel.CodeOf(err) != kernel.Denied {
		t.Fatal(err)
	}
	if n, err := r.Drain(ctx, "toy.log"); n != 0 || err != nil {
		t.Fatal("drained a rolled-back event:", n, err)
	}

	if _, err := run(r, ann, "k1", `{"ids":["a","b"]}`); err != nil {
		t.Fatal(err)
	}
	// Crash before commit: nothing projected, nothing marked handled.
	toyLogger.failures.Store(1)
	if n, err := r.Drain(ctx, "toy.log"); n != 1 || err == nil || !strings.Contains(err.Error(), "crashed") {
		t.Fatal("crash:", n, err)
	}
	want(t, "one event survived the crash, one projected", logged(t, d), "t1 b")
	// Restart: the pending event is picked up; a further drain does nothing.
	if n, err := r.Drain(ctx, "toy.log"); n != 1 || err != nil {
		t.Fatal("recovery:", n, err)
	}
	if n, err := r.Drain(ctx, "toy.log"); n != 0 || err != nil {
		t.Fatal("repeat drain:", n, err)
	}
	want(t, "projected once each", logged(t, d), "t1 a", "t1 b")
	want(t, "inbox", d.Strings(t, `SELECT consumer, tenant_id, count(*) FROM kernel.inbox GROUP BY 1, 2`), "toy.log t1 2")

	// A second tenant's events run under that tenant (toy.log RLS would reject otherwise).
	t2 := kernel.Principal{Tenant: "t2", Actor: "zed", Roles: []string{"marker"}}
	if _, err := run(r, t2, "k1", `{"ids":["x"]}`); err != nil {
		t.Fatal(err)
	}
	if n, err := r.Drain(ctx, "toy.log"); n != 1 || err != nil {
		t.Fatal(n, err)
	}
	want(t, "tenant-bound projection", logged(t, d), "t1 a", "t1 b", "t2 x")
}

func TestConcurrentDrainsProjectOnce(t *testing.T) {
	d, r, _ := setup(t)
	toyLogger.failures.Store(0)
	if _, err := run(r, ann, "k", `{"ids":["a","b","c","d"]}`); err != nil {
		t.Fatal(err)
	}
	var total atomic.Int32
	var wg sync.WaitGroup
	for range 4 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			n, err := r.Drain(context.Background(), "toy.log")
			if err != nil {
				t.Error(err)
			}
			total.Add(int32(n))
		}()
	}
	wg.Wait()
	if total.Load() != 4 {
		t.Fatalf("handled %d, want 4", total.Load())
	}
	want(t, "no duplicates", d.Strings(t, `SELECT count(*), count(DISTINCT item) FROM toy.log`), "4 4")
}

// Events that do not match the consumer's contract are reported, not recorded
// as handled, and do not block valid events.
func TestDrainRejectsVersionAndPayloadMismatch(t *testing.T) {
	d, r, _ := setup(t)
	toyLogger.failures.Store(0)
	d.Exec(t, `INSERT INTO kernel.request VALUES ('t1','ann','toy.mark',1,'raw','h','{a}','{}')`)
	d.Exec(t, `INSERT INTO kernel.outbox (tenant_id, actor_id, operation, version, key, event_type, event_version, target_id, payload) VALUES
	 ('t1','ann','toy.mark',1,'raw','toy.marked',2,'a','{"id":"a"}'),
	 ('t1','ann','toy.mark',1,'raw','toy.marked',1,'b','{"id":"b","extra":1}'),
	 ('t1','ann','toy.mark',1,'raw','toy.marked',1,'c','{"id":"c"}')`)
	n, err := r.Drain(context.Background(), "toy.log")
	if n != 1 || err == nil || !strings.Contains(err.Error(), "unsupported event toy.marked@2") || !strings.Contains(err.Error(), "unexpected payload") {
		t.Fatal(n, err)
	}
	want(t, "only the valid event", logged(t, d), "t1 c")
	want(t, "mismatches stay pending", d.Strings(t, `SELECT count(*) FROM kernel.inbox`), "1")
	if _, err := r.Drain(context.Background(), "unknown"); kernel.CodeOf(err) != kernel.Invalid {
		t.Fatal("unknown consumer:", err)
	}
}

// More than one page of failing events must not hide a valid event behind them.
func TestDrainPagesPastFailingEvents(t *testing.T) {
	d, r, _ := setup(t)
	toyLogger.failures.Store(0)
	d.Exec(t, `INSERT INTO kernel.request VALUES ('t1','ann','toy.mark',1,'raw','h','{a}','{}')`)
	d.Exec(t, `INSERT INTO kernel.outbox (tenant_id, actor_id, operation, version, key, event_type, event_version, target_id, payload)
	 SELECT 't1','ann','toy.mark',1,'raw','toy.marked',2,'a','{"id":"a"}' FROM generate_series(1, 1001)`)
	d.Exec(t, `INSERT INTO kernel.outbox (tenant_id, actor_id, operation, version, key, event_type, event_version, target_id, payload)
	 VALUES ('t1','ann','toy.mark',1,'raw','toy.marked',1,'c','{"id":"c"}')`)
	if n, err := r.Drain(context.Background(), "toy.log"); n != 1 || err == nil {
		t.Fatal(n, err)
	}
	want(t, "valid event behind the failing page", logged(t, d), "t1 c")
}

var markedV2 = kernel.EventType{Type: "toy.marked", Version: 2}

// markV2Event declares toy.marked@2 instead of @1.
type markV2Event struct{ mark }

func (markV2Event) Definition() kernel.Definition {
	d := mark{version: 9}.Definition()
	d.Events = []kernel.EventType{markedV2}
	return d
}

// multi consumes the given event versions.
type multi []kernel.EventType

func (multi) Name() string                                           { return "toy.multi" }
func (m multi) Events() []kernel.EventType                           { return m }
func (multi) Handle(context.Context, *pg.Session, pg.Delivery) error { return nil }
