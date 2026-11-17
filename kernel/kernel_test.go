package kernel

import (
	"encoding/json"
	"errors"
	"fmt"
	"os/exec"
	"strings"
	"testing"
)

func TestTargets(t *testing.T) {
	got, err := Targets([]string{"b", "a"}, 2)
	if err != nil || strings.Join(got, ",") != "a,b" {
		t.Fatal(got, err)
	}
	for _, bad := range [][]string{nil, {"a", "b", "c"}, {"a", "a"}, {""}, {"a\x00"}, {strings.Repeat("x", 101)}, {"\xff"}} {
		if _, err := Targets(bad, 2); CodeOf(err) != Invalid {
			t.Errorf("%q accepted: %v", bad, err)
		}
	}
}

func TestFingerprint(t *testing.T) {
	ref := Ref{"op", 1}
	in := Input{Targets: []string{"b", "a"}, Payload: json.RawMessage(`{"reason":"x"}`)}
	base := Fingerprint(ref, in)
	if base != Fingerprint(ref, Input{Targets: []string{"a", "b"}, Payload: in.Payload}) {
		t.Fatal("target order changed the fingerprint")
	}
	for name, other := range map[string]string{
		"version": Fingerprint(Ref{"op", 2}, in),
		"name":    Fingerprint(Ref{"op2", 1}, in),
		"payload": Fingerprint(ref, Input{Targets: in.Targets, Payload: json.RawMessage(`{"reason":"y"}`)}),
		"targets": Fingerprint(ref, Input{Targets: []string{"a"}, Payload: in.Payload}),
		"split":   Fingerprint(ref, Input{Targets: []string{"ab"}, Payload: in.Payload}),
	} {
		if other == base {
			t.Errorf("%s not bound by the fingerprint", name)
		}
	}
}

func TestDefinitionValidate(t *testing.T) {
	ev := []EventType{{"x.done", 1}}
	ok := []Definition{
		{Ref: Ref{"x.do", 1}, MaxTargets: 1, Events: ev},
		{Ref: Ref{"x.do", 1}, MaxTargets: 100, NoEventReason: "local only"},
	}
	bad := []Definition{
		{Ref: Ref{"", 1}, MaxTargets: 1, Events: ev},
		{Ref: Ref{"X", 1}, MaxTargets: 1, Events: ev},
		{Ref: Ref{"x.do", 0}, MaxTargets: 1, Events: ev},
		{Ref: Ref{"x.do", 1}, MaxTargets: 0, Events: ev},
		{Ref: Ref{"x.do", 1}, MaxTargets: 1},
		{Ref: Ref{"x.do", 1}, MaxTargets: 1, Events: ev, NoEventReason: "both"},
		{Ref: Ref{"x.do", 1}, MaxTargets: 1, Events: []EventType{{"x.done", 1}, {"x.done", 1}}},
		{Ref: Ref{"x.do", 1}, MaxTargets: 1, Events: []EventType{{"x.done", 0}}},
	}
	for _, d := range ok {
		if err := d.Validate(); err != nil {
			t.Errorf("%+v: %v", d, err)
		}
	}
	for _, d := range bad {
		if d.Validate() == nil {
			t.Errorf("%+v accepted", d)
		}
	}
	c := ok[0].Clone()
	c.Events[0].Version = 9
	if ok[0].Events[0].Version != 1 {
		t.Fatal("Clone shares Events")
	}
}

func TestCodeOf(t *testing.T) {
	if CodeOf(nil) != "" || CodeOf(errors.New("x")) != Internal || CodeOf(fmt.Errorf("w: %w", Deniedf("no"))) != Denied {
		t.Fatal("CodeOf misclassifies")
	}
	if !ValidKey("k-1.a:b_c") || ValidKey("") || ValidKey("a b") || ValidKey(strings.Repeat("k", 129)) {
		t.Fatal("ValidKey")
	}
}

// The public kernel packages must not depend on this repo's internal code,
// experiments or oracle, so other modules can import them.
func TestKernelImportsNoInternalCode(t *testing.T) {
	out, err := exec.Command("go", "list", "-deps", "-test", "github.com/XUJiahua/enterprise_agent/kernel/...").CombinedOutput()
	if err != nil {
		t.Fatalf("%v\n%s", err, out)
	}
	for _, dep := range strings.Fields(string(out)) {
		if strings.HasPrefix(dep, "github.com/XUJiahua/enterprise_agent/") && !strings.HasPrefix(dep, "github.com/XUJiahua/enterprise_agent/kernel") {
			t.Errorf("kernel depends on %s", dep)
		}
	}
}
