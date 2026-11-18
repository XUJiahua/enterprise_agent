// Package behavior_test runs the frozen behavior cases in this directory. It
// is its own package so no other test file can change its state, and
// appctl validate refuses any other Go file here and any edit to this file
// once published.
package behavior_test

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/XUJiahua/enterprise_agent/internal/procurement"
	"github.com/XUJiahua/enterprise_agent/kernel"
	"github.com/XUJiahua/enterprise_agent/kernel/pg"
	"github.com/XUJiahua/enterprise_agent/kernel/pg/pgtest"
)

// behaviorFile is the frozen behavior of one published operation version.
// Once published, `appctl validate --base` refuses any edit to the file, so a
// change in what these cases observe must ship as a new version.
type behaviorFile struct {
	Ref   kernel.Ref     `json:"ref"`
	Cases []behaviorCase `json:"cases"`
}

type behaviorCase struct {
	Name    string          `json:"name"`
	Seed    []string        `json:"seed,omitempty"` // admin SQL after the standard seed
	As      string          `json:"as"`             // ann, amy (requesters), bob, cat (approvers), zed (t2 approver)
	Request json.RawMessage `json:"request"`
	Code    kernel.Code     `json:"code,omitempty"`   // "" for success
	Result  json.RawMessage `json:"result,omitempty"` // success body
	Check   []behaviorCheck `json:"check,omitempty"`  // admin queries after the call
}

type behaviorCheck struct {
	SQL  string   `json:"sql"`
	Want []string `json:"want"`
}

var behaviorPrincipals = map[string]kernel.Principal{
	"ann": {Tenant: "t1", Actor: "ann", Roles: []string{procurement.Requester}},
	"bob": {Tenant: "t1", Actor: "bob", Roles: []string{procurement.Approver}},
	"amy": {Tenant: "t1", Actor: "amy", Roles: []string{procurement.Requester}},
	"cat": {Tenant: "t1", Actor: "cat", Roles: []string{procurement.Approver}},
	"zed": {Tenant: "t2", Actor: "zed", Roles: []string{procurement.Approver}},
}

// TestFrozenBehavior runs every behaviorFile against a fresh database. Each
// registered operation version must have one (appctl validate checks this
// too); each case runs in its own database.
//
// This file is self-contained (its own roles, seed, principals and
// comparisons) and appctl validate refuses edits to it once published, so the
// harness cannot be weakened alongside a behavior change.
func TestFrozenBehavior(t *testing.T) {
	files, _ := filepath.Glob("*.json")
	have := map[kernel.Ref]bool{}
	for _, f := range files {
		raw, err := os.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		var b behaviorFile
		dec := json.NewDecoder(strings.NewReader(string(raw)))
		dec.DisallowUnknownFields()
		if err = dec.Decode(&b); err != nil || len(b.Cases) == 0 {
			t.Fatalf("%s: %v (need at least one case)", f, err)
		}
		if filepath.Base(f) != b.Ref.String()+".json" {
			t.Fatalf("%s must be named %s.json", f, b.Ref)
		}
		have[b.Ref] = true
		for _, c := range b.Cases {
			t.Run(b.Ref.String()+"/"+c.Name, func(t *testing.T) {
				d, r := behaviorSetup(t)
				for _, sql := range c.Seed {
					d.Exec(t, sql)
				}
				p, ok := behaviorPrincipals[c.As]
				if !ok {
					t.Fatalf("unknown principal %q", c.As)
				}
				res, err := r.Run(context.Background(), p, b.Ref, "frozen", c.Request)
				if got := kernel.CodeOf(err); got != c.Code {
					t.Fatalf("code %q (%v), want %q", got, err, c.Code)
				}
				if c.Code == "" && !jsonEqual(res.Body, c.Result) {
					t.Fatalf("result %s, want %s", res.Body, c.Result)
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
	for _, op := range c.Operations {
		if !have[op.Ref] {
			t.Errorf("%s has no behavior/%s.json", op.Ref, op.Ref)
		}
	}
}

// behaviorSetup migrates a fresh database and loads the frozen seed.
func behaviorSetup(t *testing.T) (*pgtest.DB, *pg.Runner) {
	d := pgtest.New(t)
	for _, sql := range []string{
		`DO $$ BEGIN CREATE ROLE enterprise_runtime NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$`,
		`DO $$ BEGIN CREATE ROLE enterprise_consumer NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$`,
		`GRANT enterprise_runtime TO ` + pgtest.Runtime + ` WITH INHERIT TRUE`,
	} {
		d.Exec(t, sql)
	}
	if _, _, err := procurement.Migrate(d.AdminURL); err != nil {
		t.Fatal("migrate:", err)
	}
	d.Exec(t, `INSERT INTO procurement.purchase (tenant_id, id, created_by, title, amount, status, internal_note) VALUES
	 ('t1','p1','ann','laptop',900,'submitted','bank-1'), ('t1','p2','ann','desk',300,'submitted','bank-2'),
	 ('t1','own','bob','chair',50,'submitted',''), ('t1','done','ann','pen',5,'approved',''),
	 ('t2','x1','zed','server',5000,'submitted','secret')`)
	r, err := pg.New(context.Background(), d.Pool(t), procurement.Registry())
	if err != nil {
		t.Fatal(err)
	}
	return d, r
}

func jsonEqual(a, b json.RawMessage) bool {
	var x, y any
	return json.Unmarshal(a, &x) == nil && json.Unmarshal(b, &y) == nil && reflect.DeepEqual(x, y)
}
