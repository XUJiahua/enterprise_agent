package opdef

import (
	"slices"
	"testing"
)

// TestRegistryMatchesCatalog is the consistency check requirement #1 asks for.
// There is no runtime notify package in this repo to diff consumedEvents
// against (see registry.go), so the authoritative registry is instead held in
// lockstep with the catalog in both directions: every non-exempt entry's event
// is registered, every registered event is consumed by some entry, and the
// exemption list stays confined to entries that actually carry an exemption.
func TestRegistryMatchesCatalog(t *testing.T) {
	used := map[string]bool{}
	for _, o := range catalog {
		exempt := slices.Contains(exemptOperations, o.Name)
		if exempt {
			if o.EventExemption == "" {
				t.Errorf("%s: on exemptOperations but declares no EventExemption", o.Name)
			}
			continue
		}
		if o.EventExemption != "" {
			t.Errorf("%s: carries EventExemption but is not on exemptOperations", o.Name)
		}
		if !slices.Contains(consumedEvents, o.Event) {
			t.Errorf("%s: event %q is not in consumedEvents", o.Name, o.Event)
		}
		used[o.Event] = true
	}
	for _, e := range consumedEvents {
		if !used[e] {
			t.Errorf("consumedEvents lists %q but no catalog entry emits it", e)
		}
	}
	sorted := slices.Clone(consumedEvents) // Compact needs sorted input and mutates in place.
	slices.Sort(sorted)
	if len(slices.Compact(sorted)) != len(consumedEvents) {
		t.Errorf("consumedEvents has duplicates: %v", consumedEvents)
	}
	for _, n := range exemptOperations {
		if _, ok := Lookup(n); !ok {
			t.Errorf("exemptOperations lists unknown operation %q", n)
		}
	}
}

// TestEventRule covers requirement #3 directly against eventRule: a non-exempt
// entry with no event, an event with no registered consumer, and a non-exempt
// entry that tries to exempt itself are all rejected; the existing catalog's
// bulk-reject.v4 and the exempt bulk-approve.v3 both pass.
func TestEventRule(t *testing.T) {
	reject, _ := Lookup(BulkReject)  // non-exempt, Event purchase.rejected
	approve, _ := Lookup(BulkApprove) // exempt, carries EventExemption

	noEvent := reject.clone()
	noEvent.Event = ""

	unknownEvent := reject.clone()
	unknownEvent.Event = "purchase.teleported"

	selfExempt := reject.clone()
	selfExempt.Event = ""
	selfExempt.EventExemption = "trying to skip the outbox"

	cases := []struct {
		name string
		op   Operation
		pass bool
	}{
		{"catalog bulk-reject passes", reject, true},
		{"exempt bulk-approve passes", approve, true},
		{"missing event rejected", noEvent, false},
		{"event not in registry rejected", unknownEvent, false},
		{"non-exempt self-exemption rejected", selfExempt, false},
	}
	for _, c := range cases {
		if err := c.op.eventRule(); (err == nil) != c.pass {
			t.Errorf("%s: eventRule() err=%v, want pass=%v", c.name, err, c.pass)
		}
	}
}

// TestValidateRejectsDuplicateEvent is requirement #3's "event duplicated
// across entries" case: the pre-existing no-duplicate-events rule in Validate
// is preserved alongside the new eventRule.
func TestValidateRejectsDuplicateEvent(t *testing.T) {
	saved := catalog
	defer func() { catalog = saved }()

	reject, _ := Lookup(BulkReject)
	extra := reject.clone()
	extra.Name = "bulk-reject.v9" // unique, versioned name reusing a registered event
	catalog = append(append([]Operation(nil), saved...), extra)
	if err := Validate(); err == nil {
		t.Fatal("duplicate event across entries accepted")
	}
}

// TestValidateAcceptsCatalog is requirement #3's positive case: Validate, which
// now runs eventRule on every entry, accepts the reviewed catalog including the
// bulk-approve.v3 exemption.
func TestValidateAcceptsCatalog(t *testing.T) {
	if err := Validate(); err != nil {
		t.Fatalf("reviewed catalog rejected: %v", err)
	}
}
