package extcheck

import (
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

const kernel = `package action

const NoEvent = "none"

type Operation struct {
	Name, Role string
	Own        bool
	From, To   string
	Field      string
	Event      string
}
`

const notify = `package notify

func audience(t string) string {
	switch t {
	case "purchase.rejected":
		return "creator"
	case "purchase.reopened":
		return "approvers"
	}
	return ""
}
`

const ops = `package candidate

import "example.com/app/internal/action"

var approveOp = action.Operation{Name: "bulk-approve.v3", Role: "approver", From: "submitted", To: "approved", Field: "approvedIds", Event: action.NoEvent}
`

const manualBase = `package candidate

import "example.com/app/internal/ledger"

const opApprove = "bulk-approve.v3"

func approve(key string, ids []string) {
	hash := ledger.Fingerprint(opApprove, ids)
	exec(` + "`INSERT INTO \"ActionRequest\" (operation,key,\"requestHash\") VALUES ($1,$2,$3)`" + `, opApprove, key, hash)
}

func exec(string, ...any) {}
`

// repo commits base files, then applies change (path -> content; "" deletes).
func repo(t *testing.T, base, change map[string]string) (string, string) {
	t.Helper()
	dir := t.TempDir()
	write := func(files map[string]string) {
		for p, c := range files {
			full := filepath.Join(dir, p)
			if c == "" {
				_ = os.Remove(full)
				continue
			}
			if err := os.MkdirAll(filepath.Dir(full), 0o755); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(full, []byte(c), 0o644); err != nil {
				t.Fatal(err)
			}
		}
	}
	run := func(args ...string) string {
		out, err := exec.Command("git", append([]string{"-C", dir}, args...)...).CombinedOutput()
		if err != nil {
			t.Fatal(string(out))
		}
		return strings.TrimSpace(string(out))
	}
	write(map[string]string{"go.mod": "module example.com/app\n"})
	write(base)
	run("init", "-q")
	run("add", "-A")
	run("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "base")
	write(change)
	return dir, run("rev-parse", "HEAD")
}

func codes(t *testing.T, base, change map[string]string) []string {
	t.Helper()
	dir, sha := repo(t, base, change)
	findings, err := Check(dir, sha, Default)
	if err != nil {
		t.Fatal(err)
	}
	var out []string
	for _, f := range findings {
		out = append(out, f.Code)
	}
	slices.Sort(out)
	return out
}

func declarative() map[string]string {
	return map[string]string{"internal/action/action.go": kernel, "internal/notify/notify.go": notify, "internal/candidate/operations.go": ops}
}

func reopen(fields string) map[string]string {
	return map[string]string{"internal/candidate/reopen.go": "package candidate\n\nimport \"example.com/app/internal/action\"\n\nvar reopenOp = action.Operation{" + fields + "}\n"}
}

func TestDeclarativeRules(t *testing.T) {
	full := `Name: "reopen.v5", Role: "requester", Own: true, From: "rejected", To: "submitted", Event: "purchase.reopened"`
	for _, c := range []struct {
		name   string
		change map[string]string
		want   []string
	}{
		{"correct", reopen(full), nil},
		{"positional", reopen(`"reopen.v5", "requester", true, "rejected", "submitted", "", "purchase.reopened"`), nil},
		{"omitted event", reopen(strings.Replace(full, `, Event: "purchase.reopened"`, "", 1)), []string{"EVENT_EXPECTED_BY_CONSUMER", "FIELD_MISSING"}},
		{"NoEvent", reopen(strings.Replace(full, `"purchase.reopened"`, "action.NoEvent", 1)), []string{"EVENT_EXPECTED_BY_CONSUMER"}},
		{"literal none", reopen(strings.Replace(full, `"purchase.reopened"`, `"none"`, 1)), []string{"EVENT_EXPECTED_BY_CONSUMER"}},
		{"unknown event", reopen(strings.Replace(full, "reopened", "resubmitted", 1)), []string{"EVENT_NO_CONSUMER"}},
		{"unversioned", reopen(strings.Replace(full, "reopen.v5", "reopen", 1)), []string{"NAME_UNVERSIONED"}},
		{"duplicate", reopen(strings.Replace(full, "reopen.v5", "bulk-approve.v3", 1)), []string{"OPERATION_NAME_DUPLICATE"}},
		{"empty role", reopen(strings.Replace(full, `"requester"`, `""`, 1)), []string{"FIELD_EMPTY"}},
		// NoEvent is fine when no consumer event is named after the operation.
		{"cancel without event", reopen(strings.NewReplacer("reopen.v5", "cancel.v5", `"purchase.reopened"`, "action.NoEvent").Replace(full)), nil},
		{"kernel declaration", map[string]string{"internal/action/action.go": kernel + "\nvar Reopen = Operation{" + full + "}\n"},
			[]string{"DECLARATION_OUTSIDE_EXTENSION", "PROTECTED_PATH_CHANGED"}},
		{"untracked protected file", map[string]string{"internal/notify/extra.go": "package notify\n"}, []string{"PROTECTED_PATH_CHANGED"}},
	} {
		t.Run(c.name, func(t *testing.T) {
			if got := codes(t, declarative(), c.change); !slices.Equal(got, c.want) {
				t.Fatalf("got %v want %v", got, c.want)
			}
		})
	}
}

func TestManualRules(t *testing.T) {
	base := map[string]string{"internal/notify/notify.go": notify, "internal/candidate/approval.go": manualBase}
	handler := func(hash, event string) map[string]string {
		return map[string]string{"internal/candidate/reopen.go": `package candidate

import "example.com/app/internal/ledger"

const opReopen = "reopen.v5"

func reopen(ctx, u any, key, id, reason string) (any, bool, error) {
	hash := ` + hash + `
	` + event + `
	exec(` + "`INSERT INTO \"ActionRequest\" (operation,key,\"requestHash\") VALUES ($1,$2,$3)`" + `, opReopen, key, hash)
	return nil, false, nil
}

var _ = ledger.Fingerprint
`}
	}
	const event = "exec(`INSERT INTO \"PurchaseEvent\" (type) VALUES ('purchase.reopened')`)"
	for _, c := range []struct {
		name   string
		change map[string]string
		want   []string
	}{
		{"correct", handler("ledger.Fingerprint(opReopen, []string{id})", event), nil},
		{"reason in hash", handler("ledger.Fingerprint(opReopen, []string{id, reason})", event), []string{"FINGERPRINT_EXTRA_INPUT"}},
		{"concatenated reason", handler(`ledger.Fingerprint(opReopen, []string{id + ":" + reason})`, event), []string{"FINGERPRINT_EXTRA_INPUT"}},
		{"other operation", handler("ledger.Fingerprint(opApprove, []string{id})", event), []string{"FINGERPRINT_OPERATION_MISSING"}},
		{"no event", handler("ledger.Fingerprint(opReopen, []string{id})", ""), []string{"EVENT_EXPECTED_BY_CONSUMER"}},
		{"unknown event", handler("ledger.Fingerprint(opReopen, []string{id})", strings.Replace(event, "reopened", "resubmitted", 1)),
			[]string{"EVENT_EXPECTED_BY_CONSUMER", "EVENT_NO_CONSUMER"}},
		{"unchanged", nil, nil},
	} {
		t.Run(c.name, func(t *testing.T) {
			if got := codes(t, base, c.change); !slices.Equal(got, c.want) {
				t.Fatalf("got %v want %v", got, c.want)
			}
		})
	}
}

func TestNames(t *testing.T) {
	for _, c := range []struct {
		op, event string
		want      bool
	}{
		{"reopen.v5", "purchase.reopened", true},
		{"bulk-approve.v3", "purchase.approved", true},
		{"bulk-reject.v4", "purchase.rejected", true},
		{"submit.v2", "purchase.submitted", true},
		{"reopen.v5", "purchase.opened", false},
		{"open.v1", "purchase.reopened", false},
		{"bulk-approve.v3", "purchase.rejected", false},
		{"pay.v1", "invoice.paid", false}, // irregular verb: documented false negative
	} {
		if got := Names(c.event, Stem(c.op)); got != c.want {
			t.Errorf("Names(%q, Stem(%q)) = %v", c.event, c.op, got)
		}
	}
}
