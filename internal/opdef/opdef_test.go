package opdef

import (
	"strings"
	"testing"
)

// pinned changes only together with a reviewed catalog change.
const pinned = "sha256:98abea36467340ad3702d12341beb3894e649d42d9129cfac4e45e46a9de0055"

func TestCatalogComplete(t *testing.T) {
	if err := Validate(); err != nil {
		t.Fatal(err)
	}
	names := map[Name]bool{}
	for _, o := range All() {
		if names[o.Name] {
			t.Fatalf("duplicate %s", o.Name)
		}
		names[o.Name] = true
		if !versioned.MatchString(string(o.Name)) {
			t.Fatalf("%s: unversioned", o.Name)
		}
		if (o.Event == "") == (o.EventExemption == "") {
			t.Fatalf("%s: needs exactly one of event or exemption", o.Name)
		}
		if o.EventExemption != "" && len(strings.Fields(o.EventExemption)) < 4 {
			t.Fatalf("%s: exemption needs a reason", o.Name)
		}
	}
	for _, n := range []Name{BulkApprove, BulkReject, Reopen, Countersign, BulkReassign} {
		if !names[n] {
			t.Fatalf("constant %s has no entry", n)
		}
	}
	if len(names) != 5 {
		t.Fatalf("every entry needs a Name constant: %v", names)
	}
	// Only the historical approval is exempt.
	for _, o := range All() {
		if (o.EventExemption != "") != (o.Name == BulkApprove) {
			t.Fatalf("%s: unexpected exemption state", o.Name)
		}
	}
}

func TestDigestPinned(t *testing.T) {
	if d := Digest(); d != pinned {
		t.Fatalf("catalog digest %s != pinned %s: update pinned only with a reviewed catalog change", d, pinned)
	}
	if len(SourceFiles()["catalog.go"]) == 0 {
		t.Fatal("embedded catalog source missing")
	}
}

func TestLookupReturnsCopy(t *testing.T) {
	o, _ := Lookup(BulkReassign)
	o.Fingerprint[1] = "reason"
	o.Inputs[0] = "x"
	a, _ := Lookup(BulkApprove)
	a.Tier.Over = 1
	a.EventExemption = ""
	if again, _ := Lookup(BulkReassign); again.Fingerprint[1] != "assignee" || again.Inputs[0] != IDs {
		t.Fatal("Lookup leaked catalog slices")
	}
	if again, _ := Lookup(BulkApprove); again.Tier.Over != 50000 || again.EventExemption == "" {
		t.Fatal("Lookup leaked catalog tier/exemption")
	}
	if _, ok := Lookup("bulk-reassign.v7"); ok {
		t.Fatal("unknown name found")
	}
}

func TestValidateRejects(t *testing.T) {
	base, _ := Lookup(BulkReassign)
	cases := map[string]func(*Operation){
		"no event, no exemption": func(o *Operation) { o.Event = "" },
		"event and exemption":    func(o *Operation) { o.EventExemption = "historical reason for no event" },
		"unversioned":            func(o *Operation) { o.Name = "bulk-reassign" },
		"reason hashed": func(o *Operation) {
			o.Inputs = []string{IDs, "assignee", Reason}
			o.Fingerprint = []string{IDs, Reason}
		},
		"unknown input":     func(o *Operation) { o.Inputs = []string{IDs, "assignee", "note"} },
		"hash not input":    func(o *Operation) { o.Fingerprint = []string{IDs, "amount"} },
		"wrong record":      func(o *Operation) { o.Record = ApprovalAudit },
		"other denial":      func(o *Operation) { o.Denial = 404 },
		"bad event":         func(o *Operation) { o.Event = "Reassigned" },
		"unknown kind":      func(o *Operation) { o.Kind = "delete" },
		"transition fields": func(o *Operation) { o.From, o.To = "submitted", "approved" },
	}
	for name, mutate := range cases {
		o := base.clone()
		mutate(&o)
		if o.validate() == nil {
			t.Errorf("%s: accepted", name)
		}
	}
	saved := catalog
	defer func() { catalog = saved }()
	dup := append(append([]Operation(nil), saved...), saved[0])
	catalog = dup
	if Validate() == nil {
		t.Error("duplicate name accepted")
	}
}
