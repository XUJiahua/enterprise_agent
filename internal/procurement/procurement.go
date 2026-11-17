// Package procurement is the purchase-order domain module. It registers its
// operations with kernel/pg and owns all procurement SQL, roles and states;
// the kernel owns the transaction, idempotency, audit and outbox writes.
package procurement

import (
	"bytes"
	"context"
	"embed"
	"encoding/json"
	"unicode/utf8"

	"github.com/XUJiahua/enterprise_agent/kernel"
	"github.com/XUJiahua/enterprise_agent/kernel/pg"
)

// Migrations holds the product schema in golang-migrate file naming
// (NNNN_name.up.sql), applied in order by an admin tool, never by requests.
//
//go:embed migrations/*.up.sql
var Migrations embed.FS

// Tables are checked with pg.CheckRuntime at startup.
var Tables = []string{"procurement.purchase"}

// Role names carried in kernel.Principal.Roles.
const (
	Requester = "requester"
	Approver  = "approver"
)

// Operations returns every procurement operation for pg.New.
func Operations() []pg.Operation { return []pg.Operation{approve{}} }

// ApproveRef is the bulk approval: submitted -> approved, by an approver,
// never of their own purchases.
var ApproveRef = kernel.Ref{Name: "procurement.approve", Version: 1}

type approve struct{}

type approveRequest struct {
	IDs    []string `json:"ids"`
	Reason string   `json:"reason,omitempty"`
}

func (approve) Definition() kernel.Definition {
	return kernel.Definition{Ref: ApproveRef, MaxTargets: 100,
		NoEventReason: "no registered consumer yet; the notification projection (M2) will add a versioned event"}
}

func (approve) Normalize(raw json.RawMessage) (kernel.Input, error) {
	var req approveRequest
	if err := strict(raw, &req); err != nil {
		return kernel.Input{}, err
	}
	if len(req.Reason) > 200 || !utf8.ValidString(req.Reason) {
		return kernel.Input{}, kernel.Invalidf("reason must be at most 200 bytes of UTF-8")
	}
	payload, _ := json.Marshal(map[string]string{"reason": req.Reason})
	return kernel.Input{Targets: req.IDs, Payload: payload}, nil
}

func (approve) Authorize(p kernel.Principal) error {
	if !p.Has(Approver) {
		return kernel.Deniedf("not permitted")
	}
	return nil
}

func (approve) Execute(ctx context.Context, s *pg.Session, in kernel.Input) (kernel.Outcome, error) {
	var payload struct{ Reason string }
	if err := json.Unmarshal(in.Payload, &payload); err != nil {
		return kernel.Outcome{}, err
	}
	actor := s.Principal().Actor
	// Lock every visible target in one fixed order (C collation) before checking.
	rows, err := s.Query(ctx, `SELECT id, status, created_by FROM procurement.purchase
	 WHERE id = ANY($1) ORDER BY id COLLATE "C" FOR UPDATE`, in.Targets)
	if err != nil {
		return kernel.Outcome{}, err
	}
	eligible := 0
	for rows.Next() {
		var id, status, creator string
		if err := rows.Scan(&id, &status, &creator); err != nil {
			return kernel.Outcome{}, err
		}
		if status == "submitted" && creator != actor {
			eligible++
		}
	}
	if err := rows.Err(); err != nil {
		return kernel.Outcome{}, err
	}
	if eligible != len(in.Targets) {
		return kernel.Outcome{}, deny() // missing, other tenant, own or wrong state: same error
	}
	rows, err = s.Query(ctx, `UPDATE procurement.purchase SET status = 'approved'
	 WHERE id = ANY($1) AND status = 'submitted' RETURNING id`, in.Targets)
	if err != nil {
		return kernel.Outcome{}, err
	}
	var changed []string
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return kernel.Outcome{}, err
		}
		changed = append(changed, id)
	}
	if err := rows.Err(); err != nil {
		return kernel.Outcome{}, err
	}
	change, _ := json.Marshal(map[string]any{"status": map[string]string{"from": "submitted", "to": "approved"}, "reason": payload.Reason})
	out := kernel.Outcome{Targets: changed, Result: map[string][]string{"approvedIds": in.Targets}}
	for _, id := range changed {
		out.Audit = append(out.Audit, kernel.Audit{Target: id, Change: change})
	}
	return out, nil
}

func deny() error { return kernel.Deniedf("one or more targets are not eligible") }

// strict decodes exactly one JSON object with no unknown fields.
func strict(raw json.RawMessage, v any) error {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if dec.Decode(v) != nil || dec.More() {
		return kernel.Invalidf("malformed request")
	}
	return nil
}
