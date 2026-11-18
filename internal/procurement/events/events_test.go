// Package events_test runs the frozen event contracts in this directory: for
// each published event version, which payloads its consumers must accept
// (and what they project) and which they must reject. It is its own package,
// embeds its case files, and appctl validate refuses any other Go file here
// and any edit to this file or to a published case file.
package events_test

import (
	"context"
	"embed"
	"encoding/json"
	"fmt"
	"io/fs"
	"net/url"
	"reflect"
	"strings"
	"testing"

	"github.com/XUJiahua/enterprise_agent/internal/procurement"
	"github.com/XUJiahua/enterprise_agent/kernel"
	"github.com/XUJiahua/enterprise_agent/kernel/pg"
	"github.com/XUJiahua/enterprise_agent/kernel/pg/pgtest"
	"github.com/jackc/pgx/v5/pgxpool"
)

// eventFile is the frozen contract of one event version for one consumer.
type eventFile struct {
	Event    kernel.EventType `json:"event"`
	Consumer string           `json:"consumer"`
	Cases    []eventCase      `json:"cases"`
}

type eventCase struct {
	Name    string          `json:"name"`
	Target  string          `json:"target"` // a purchase id from the seed
	Payload json.RawMessage `json:"payload"`
	Accept  bool            `json:"accept"`          // the consumer handles it (true) or reports it and leaves it pending (false)
	Check   []eventCheck    `json:"check,omitempty"` // admin queries after the drain
}

type eventCheck struct {
	SQL  string   `json:"sql"`
	Want []string `json:"want"`
}

//go:embed *.json
var frozen embed.FS

// TestFrozenEvents delivers each case's payload as an outbox event and drains
// it with the consumer role. Every event version a registered consumer handles
// must have a contract file.
func TestFrozenEvents(t *testing.T) {
	files, _ := fs.Glob(frozen, "*.json")
	have := map[string]bool{}
	for _, f := range files {
		raw, err := frozen.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		var ef eventFile
		dec := json.NewDecoder(strings.NewReader(string(raw)))
		dec.DisallowUnknownFields()
		if err = dec.Decode(&ef); err != nil || len(ef.Cases) == 0 {
			t.Fatalf("%s: %v (need at least one case)", f, err)
		}
		key := ef.Consumer + "/" + ef.Event.String()
		if f != fileName(key) {
			t.Fatalf("%s must be named %s", f, fileName(key))
		}
		have[key] = true
		for _, c := range ef.Cases {
			t.Run(key+"/"+c.Name, func(t *testing.T) {
				d, consumer := eventSetup(t)
				d.Exec(t, `INSERT INTO kernel.request VALUES ('t1','frozen','frozen.event',1,'k','h','{}','{}')`)
				d.Exec(t, `INSERT INTO kernel.outbox (tenant_id, actor_id, operation, version, key, event_type, event_version, target_id, payload)
				 VALUES ('t1','frozen','frozen.event',1,'k',$1,$2,$3,$4::jsonb)`, ef.Event.Type, ef.Event.Version, c.Target, string(c.Payload))
				n, err := consumer.Drain(context.Background(), ef.Consumer)
				handled := d.Strings(t, `SELECT count(*) FROM kernel.inbox`)[0]
				if c.Accept && (n != 1 || err != nil || handled != "1") {
					t.Fatalf("must accept: handled=%d inbox=%s err=%v", n, handled, err)
				}
				if !c.Accept && (n != 0 || err == nil || handled != "0") {
					t.Fatalf("must reject and leave pending: handled=%d inbox=%s err=%v", n, handled, err)
				}
				for _, ch := range c.Check {
					if got := d.Strings(t, ch.SQL); !reflect.DeepEqual(append([]string{}, got...), append([]string{}, ch.Want...)) {
						t.Fatalf("%s:\n got  %q\n want %q", ch.SQL, got, ch.Want)
					}
				}
			})
		}
	}
	c, err := pg.Describe(procurement.Registry())
	if err != nil {
		t.Fatal(err)
	}
	for _, cc := range c.Consumers {
		for _, e := range cc.Events {
			if key := cc.Name + "/" + e.String(); !have[key] {
				t.Errorf("%s has no events/%s", key, fileName(key))
			}
		}
	}
}

// fileName is <consumer>_<event type>@<version>.json.
func fileName(key string) string { return strings.ReplaceAll(key, "/", "_") + ".json" }

const consumerRole = "kernel_events_consumer"

// eventSetup migrates a fresh database, seeds purchases and returns a Runner
// connected as a consumer-role login.
func eventSetup(t *testing.T) (*pgtest.DB, *pg.Runner) {
	d := pgtest.New(t)
	for _, sql := range []string{
		`DO $$ BEGIN CREATE ROLE enterprise_runtime NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$`,
		`DO $$ BEGIN CREATE ROLE enterprise_consumer NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$`,
		`DO $$ BEGIN CREATE ROLE ` + consumerRole + ` LOGIN PASSWORD 'test-only' NOSUPERUSER NOBYPASSRLS; EXCEPTION WHEN duplicate_object THEN NULL; END $$`,
		`GRANT enterprise_consumer TO ` + consumerRole + ` WITH INHERIT TRUE`,
	} {
		d.Exec(t, sql)
	}
	if _, _, err := procurement.Migrate(d.AdminURL); err != nil {
		t.Fatal("migrate:", err)
	}
	d.Exec(t, `INSERT INTO procurement.purchase (tenant_id, id, created_by, title, amount, status) VALUES
	 ('t1','c1','ann','laptop',1000,'closed'), ('t1','c2','amy','desk',300,'closed'), ('t1','s1','ann','pen',5,'submitted')`)
	u, _ := url.Parse(d.RuntimeURL)
	u.User = url.UserPassword(consumerRole, "test-only")
	pool, err := pgxpool.New(context.Background(), u.String())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)
	r, err := pg.New(context.Background(), pool, procurement.Registry())
	if err != nil {
		t.Fatal(fmt.Errorf("consumer runner: %w", err))
	}
	return d, r
}
