package extcheck

import (
	"slices"
	"strings"
	"testing"

	"github.com/XUJiahua/enterprise_agent/internal/opdef"
)

// v6b-shaped legacy kernel: Operation and Update declarations.
const legacyKernel = `package action

const NoEvent = "none"

type Operation struct {
	Name, Role string
	Own        bool
	From, To   string
	Field      string
	Event      string
}

type Update struct {
	Name, Role string
	Own        bool
	Status     string
	Column     string
	Field      string
	Event      string
}
`

const legacyOps = `package candidate

import "example.com/app/internal/action"

var approveOp = action.Operation{Name: "bulk-approve.v3", Role: "approver", From: "submitted", To: "approved", Field: "approvedIds", Event: action.NoEvent}
`

const reassignDecl = `package candidate

import "example.com/app/internal/action"

var reassignOp = action.Update{Name: "bulk-reassign.v6", Role: "approver", Own: false, Status: "submitted", Column: "assigneeId", Field: "reassignedIds", Event: @EVENT@}
`

const ledgerPkg = "package ledger\n\nfunc Fingerprint(op string, ids []string) string { return op }\n"

// Hand-written reassign shaped like protocol.AssignHandler (ctx, u, key, ids, assignee).
const manualReassign = `package candidate

import (
	"context"

	"example.com/app/internal/ledger"
)

const opReassign = "bulk-reassign.v6"

func newReassign() func(ctx context.Context, u any, key string, ids []string, assignee string) (any, bool, error) {
	return func(ctx context.Context, u any, key string, ids []string, assignee string) (any, bool, error) {
		hash := @HASH@
		exec(` + "`INSERT INTO \"AssignmentHistory\" (operation) VALUES ($1)`" + `, opReassign)
		exec(` + "`INSERT INTO \"PurchaseEvent\" (type) VALUES ('purchase.reassigned')`" + `)
		exec(` + "`INSERT INTO \"ActionRequest\" (operation,key,\"requestHash\") VALUES ($1,$2,$3)`" + `, opReassign, key, hash)
		return nil, false, nil
	}
}

func exec(string, ...any) {}
`

func opCodes(t *testing.T, base, change map[string]string, cfg OpConfig) ([]string, string) {
	t.Helper()
	dir, sha := repo(t, base, change)
	rep, err := CheckOperations(dir, sha, cfg)
	if err != nil {
		t.Fatal(err)
	}
	var out []string
	for _, f := range rep.Findings {
		out = append(out, f.Code)
	}
	slices.Sort(out)
	if rep.Catalog != opdef.Digest() {
		t.Fatal("report not bound to the catalog digest")
	}
	return out, rep.Verdict
}

func legacyBase() map[string]string {
	return map[string]string{"internal/action/action.go": legacyKernel, "internal/candidate/operations.go": legacyOps}
}

func expect(t *testing.T, label string, got []string, verdict string, want ...string) {
	t.Helper()
	slices.Sort(want)
	wantVerdict := "pass"
	if len(want) > 0 {
		wantVerdict = "fail"
	}
	if slices.Contains(want, "UNVERIFIABLE") {
		wantVerdict = "unverifiable"
	}
	if !slices.Equal(got, want) || verdict != wantVerdict {
		t.Errorf("%s: got %v (%s), want %v (%s)", label, got, verdict, want, wantVerdict)
	}
}

func TestLegacyDeclarationsBoundToCatalog(t *testing.T) {
	decl := func(event string) map[string]string {
		return map[string]string{"internal/candidate/reassign.go": replaceAll(reassignDecl, "@EVENT@", event)}
	}
	got, v := opCodes(t, legacyBase(), decl(`"purchase.reassigned"`), DefaultOps)
	expect(t, "correct update", got, v)
	got, v = opCodes(t, legacyBase(), decl("action.NoEvent"), DefaultOps)
	expect(t, "NoEvent copied from approve", got, v, "EVENT_EXEMPTION_NOT_IN_CATALOG")
	got, v = opCodes(t, legacyBase(), decl(`"purchase.assigned"`), DefaultOps)
	expect(t, "wrong event", got, v, "EVENT_MISMATCH")
	got, v = opCodes(t, legacyBase(), map[string]string{"internal/candidate/operations.go": replaceAll(legacyOps, "action.NoEvent", `"purchase.approved"`)}, DefaultOps)
	expect(t, "exempt entry given an event", got, v, "EVENT_MISMATCH")
	got, v = opCodes(t, legacyBase(), map[string]string{"internal/candidate/reassign.go": replaceAll(replaceAll(reassignDecl, "@EVENT@", `"purchase.reassigned"`), "Own: false", "Own: true")}, DefaultOps)
	expect(t, "own purchases", got, v, "DECLARATION_MISMATCH")
	got, v = opCodes(t, legacyBase(), map[string]string{"internal/candidate/reassign.go": replaceAll(replaceAll(reassignDecl, "@EVENT@", `"purchase.reassigned"`), "bulk-reassign.v6", "bulk-reassign.v1")}, DefaultOps)
	expect(t, "unknown name", got, v, "OPERATION_NOT_IN_CATALOG")
	got, v = opCodes(t, legacyBase(), map[string]string{"internal/candidate/reassign.go": replaceAll(reassignDecl, "@EVENT@", "event")}, DefaultOps)
	expect(t, "non-constant event", got, v, "UNVERIFIABLE")
}

func TestUnknownKernelTypeIsUnverifiable(t *testing.T) {
	cfg := DefaultOps
	cfg.Kernels = Kernels{"internal/action": {"Operation": RoleLegacy}}
	got, v := opCodes(t, legacyBase(), map[string]string{"internal/candidate/reassign.go": replaceAll(reassignDecl, "@EVENT@", "action.NoEvent")}, cfg)
	// Both the unknown kernel type and its literal block the gate; nothing passes silently.
	expect(t, "Update unknown", got, v, "UNVERIFIABLE", "UNVERIFIABLE")
	got, v = opCodes(t, legacyBase(), map[string]string{"internal/candidate/broken.go": "package candidate\nfunc {"}, DefaultOps)
	expect(t, "parse error", got, v, "UNVERIFIABLE")
}

func TestManualFingerprintFromCatalog(t *testing.T) {
	base := map[string]string{"internal/ledger/ledger.go": ledgerPkg}
	src := func(hash string) map[string]string {
		return map[string]string{"internal/candidate/reassign.go": replaceAll(manualReassign, "@HASH@", hash)}
	}
	// v6 frozen rules flagged this (assignee taken for a reason); the catalog says assignee is hashed.
	got, v := opCodes(t, base, src(`ledger.Fingerprint(opReassign+"\n"+assignee, ids)`), DefaultOps)
	expect(t, "catalog fingerprint", got, v)
	got, v = opCodes(t, base, src(`ledger.Fingerprint(opReassign, ids)`), DefaultOps)
	expect(t, "assignee omitted", got, v, "FINGERPRINT_INPUT_MISSING")
	got, v = opCodes(t, base, src(`ledger.Fingerprint(opReassign+"\n"+key, ids)`), DefaultOps)
	expect(t, "key hashed", got, v, "FINGERPRINT_EXTRA_INPUT")
	got, v = opCodes(t, base, src(`ledger.Fingerprint(opReassign, append(ids, assignee))`), DefaultOps)
	expect(t, "assignee mixed into ids", got, v, "FINGERPRINT_SHAPE")
	got, v = opCodes(t, base, src(`ledger.Fingerprint("bulk-approve.v3"+"\n"+assignee, ids)`), DefaultOps)
	expect(t, "other operation", got, v, "FINGERPRINT_OPERATION_MISSING")
	got, v = opCodes(t, base, map[string]string{"internal/candidate/reassign.go": replaceAll(replaceAll(manualReassign, "@HASH@", `ledger.Fingerprint(opReassign+"\n"+assignee, ids)`), "'purchase.reassigned'", "'purchase.assigned'")}, DefaultOps)
	expect(t, "wrong hand-written event", got, v, "EVENT_NOT_IN_CATALOG", "EVENT_NOT_WRITTEN")
}

// Changes after the first retrospective run (docs/operation-semantics.md, change log).
func TestManualHelperAndHandRolledHash(t *testing.T) {
	base := map[string]string{"internal/ledger/ledger.go": ledgerPkg}
	delegating := `package candidate

import (
	"context"

	"example.com/app/internal/ledger"
)

const opReassign = "bulk-reassign.v6"

func newReassign(pool any) func(ctx context.Context, u any, key string, ids []string, assignee string) (any, bool, error) {
	return func(ctx context.Context, u any, key string, ids []string, assignee string) (any, bool, error) {
		return reassign(ctx, pool, u, key, ids, assignee)
	}
}

func other() func(ctx context.Context, u any, key string, id string, reason string) (any, bool, error) { return nil }

func reassign(ctx context.Context, pool any, u any, key string, ids []string, who string) (any, bool, error) {
	hash := @HASH@
	exec(` + "`INSERT INTO \"AssignmentHistory\" (operation) VALUES ($1)`" + `, opReassign)
	exec(` + "`INSERT INTO \"PurchaseEvent\" (type) VALUES ('purchase.reassigned')`" + `)
	exec(` + "`INSERT INTO \"ActionRequest\" (operation,key,\"requestHash\") VALUES ($1,$2,$3)`" + `, opReassign, key, hash)
	return nil, false, nil
}

func digest(parts ...string) string { return "" }

func exec(string, ...any) {}
`
	src := func(hash string) map[string]string {
		return map[string]string{"internal/candidate/reassign.go": strings.ReplaceAll(delegating, "@HASH@", hash)}
	}
	got, v := opCodes(t, base, src(`ledger.Fingerprint(opReassign+"\n"+who, ids)`), DefaultOps)
	expect(t, "helper mapped through its call", got, v)
	got, v = opCodes(t, base, src(`ledger.Fingerprint(opReassign, ids)`), DefaultOps)
	expect(t, "helper omits assignee", got, v, "FINGERPRINT_INPUT_MISSING")
	got, v = opCodes(t, base, src(`digest(opReassign, who, key)`), DefaultOps)
	expect(t, "hand-rolled digest with key", got, v, "FINGERPRINT_EXTRA_INPUT")
	got, v = opCodes(t, base, src(`digest(opReassign, who)`), DefaultOps)
	expect(t, "hand-rolled digest otherwise unprovable", got, v, "UNVERIFIABLE")
}

func TestCatalogLiteralAndDrift(t *testing.T) {
	base := map[string]string{}
	for name, src := range opdef.SourceFiles() {
		base["internal/opdef/"+name] = string(src)
	}
	// go.mod module differs from the real one; opdef imports only the standard library.
	lit := "package candidate\n\nimport \"example.com/app/internal/opdef\"\n\nvar op = opdef.Operation{Name: opdef.BulkReassign, EventExemption: \"not needed\"}\n\nvar n = opdef.Name(\"bulk-transfer.v1\")\n"
	got, v := opCodes(t, base, map[string]string{"internal/candidate/op.go": lit}, DefaultOps)
	expect(t, "catalog literal", got, v, "DECLARATION_LITERAL", "EVENT_EXEMPTION_NOT_IN_CATALOG", "OPERATION_NOT_IN_CATALOG")
	got, v = opCodes(t, base, map[string]string{"internal/opdef/catalog.go": base["internal/opdef/catalog.go"] + "\n// edited\n"}, DefaultOps)
	expect(t, "catalog edited", got, v, "CATALOG_DRIFT", "PROTECTED_PATH_CHANGED")
}

func replaceAll(s, old, new string) string { return strings.ReplaceAll(s, old, new) }
