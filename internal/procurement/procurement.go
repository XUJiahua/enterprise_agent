// Package procurement is the purchase-order domain module. It registers its
// operations with kernel/pg and owns all procurement SQL, roles and states;
// the kernel owns the transaction, idempotency, audit and outbox writes.
//
// Parent-lock protocol: every operation that changes a purchase or the
// receipts it is judged by first locks the purchase rows (lockPurchases, FOR
// UPDATE in C-collation id order), then reads anything it checks, including
// receipt sums, in later statements. Under Read Committed each later
// statement sees every receipt committed before the lock was granted, and no
// receipt for a locked purchase can commit meanwhile. Receipts are
// append-only (no UPDATE/DELETE grant). A new write path that affects amount,
// status or receipts must follow the same protocol.
package procurement

import (
	"bytes"
	"context"
	"embed"
	"encoding/json"
	"strings"
	"unicode/utf8"

	"github.com/XUJiahua/enterprise_agent/kernel"
	"github.com/XUJiahua/enterprise_agent/kernel/pg"
)

// Migrations holds the product schema in golang-migrate file naming
// (NNNN_name.up.sql). Apply with Migrate from an admin tool, never per request.
//
//go:embed migrations/*.up.sql
var Migrations embed.FS

// Tables are checked with pg.CheckRuntime at startup.
var Tables = []string{"procurement.purchase", "procurement.receipt", "procurement.notification"}

// Role names carried in kernel.Principal.Roles.
const (
	Requester = "requester"
	Approver  = "approver"
)

var (
	// ApproveRef: submitted -> approved, by an approver, never their own purchases.
	ApproveRef = kernel.Ref{Name: "procurement.approve", Version: 1}
	// ReceiveRef records a goods receipt on one approved purchase.
	ReceiveRef = kernel.Ref{Name: "procurement.receive", Version: 1}
	// CloseRef: approved -> closed for 1..100 purchases whose receipts cover
	// the amount. Version 2 requires a reason (audit requirement); version 1,
	// which allowed an empty reason, is retired and no longer registered.
	CloseRef = kernel.Ref{Name: "procurement.close", Version: 2}
	// ClosedEvent payload: {"id","amount","received"}.
	ClosedEvent = kernel.EventType{Type: "procurement.purchase.closed", Version: 1}
)

// Registry returns every procurement operation and consumer for pg.New.
func Registry() pg.Registry {
	return pg.Registry{Operations: []pg.Operation{approve{}, receive{}, closeOp{}}, Consumers: []pg.Consumer{notifier{}}}
}

// afterLock, when set by tests, runs right after an operation holds its
// purchase locks; it lets tests order concurrent operations deterministically.
var afterLock func(kernel.Ref)

type purchase struct {
	status, creator string
	amount          int64
}

// lockPurchases locks the visible purchases among ids in a fixed order and
// returns them by id. Missing and other-tenant ids are simply absent.
func lockPurchases(ctx context.Context, s *pg.Session, ref kernel.Ref, ids []string) (map[string]purchase, error) {
	rows, err := s.Query(ctx, `SELECT id, status, created_by, amount FROM procurement.purchase
	 WHERE id = ANY($1) ORDER BY id COLLATE "C" FOR UPDATE`, ids)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := map[string]purchase{}
	for rows.Next() {
		var id string
		var p purchase
		if err := rows.Scan(&id, &p.status, &p.creator, &p.amount); err != nil {
			return nil, err
		}
		out[id] = p
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	if afterLock != nil {
		afterLock(ref)
	}
	return out, nil
}

// setStatus moves ids from -> to and returns the ids actually changed.
func setStatus(ctx context.Context, s *pg.Session, ids []string, from, to string) ([]string, error) {
	rows, err := s.Query(ctx, `UPDATE procurement.purchase SET status = $3 WHERE id = ANY($1) AND status = $2 RETURNING id`, ids, from, to)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var changed []string
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return nil, err
		}
		changed = append(changed, id)
	}
	return changed, rows.Err()
}

func deny() error { return kernel.Deniedf("one or more targets are not eligible") }

func requireRole(p kernel.Principal, role string) error {
	if !p.Has(role) {
		return kernel.Deniedf("not permitted")
	}
	return nil
}

// strict decodes exactly one JSON object with no unknown fields.
func strict(raw json.RawMessage, v any) error {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if dec.Decode(v) != nil || dec.More() {
		return kernel.Invalidf("malformed request")
	}
	return nil
}

func reason(r string) (json.RawMessage, error) {
	if len(r) > 200 || !utf8.ValidString(r) {
		return nil, kernel.Invalidf("reason must be at most 200 bytes of UTF-8")
	}
	b, _ := json.Marshal(map[string]string{"reason": r})
	return b, nil
}

type bulkRequest struct {
	IDs    []string `json:"ids"`
	Reason string   `json:"reason,omitempty"`
}

func normalizeBulk(raw json.RawMessage, requireReason bool) (kernel.Input, error) {
	var req bulkRequest
	if err := strict(raw, &req); err != nil {
		return kernel.Input{}, err
	}
	if requireReason && strings.TrimSpace(req.Reason) == "" {
		return kernel.Input{}, kernel.Invalidf("reason is required")
	}
	payload, err := reason(req.Reason)
	return kernel.Input{Targets: req.IDs, Payload: payload}, err
}

func payloadReason(in kernel.Input) string {
	var p struct{ Reason string }
	_ = json.Unmarshal(in.Payload, &p)
	return p.Reason
}

type approve struct{}

func (approve) Definition() kernel.Definition {
	return kernel.Definition{Ref: ApproveRef, MaxTargets: 100,
		NoEventReason: "no registered consumer for approvals; the M2 notification projection consumes closes only"}
}

func (approve) Normalize(raw json.RawMessage) (kernel.Input, error) { return normalizeBulk(raw, false) }
func (approve) Authorize(p kernel.Principal) error                  { return requireRole(p, Approver) }

func (approve) Execute(ctx context.Context, s *pg.Session, in kernel.Input) (kernel.Outcome, error) {
	actor := s.Principal().Actor
	locked, err := lockPurchases(ctx, s, ApproveRef, in.Targets)
	if err != nil {
		return kernel.Outcome{}, err
	}
	for _, id := range in.Targets {
		if p, ok := locked[id]; !ok || p.status != "submitted" || p.creator == actor {
			return kernel.Outcome{}, deny() // missing, other tenant, own or wrong state: same error
		}
	}
	changed, err := setStatus(ctx, s, in.Targets, "submitted", "approved")
	if err != nil {
		return kernel.Outcome{}, err
	}
	change, _ := json.Marshal(map[string]any{"status": map[string]string{"from": "submitted", "to": "approved"}, "reason": payloadReason(in)})
	out := kernel.Outcome{Targets: changed, Result: map[string][]string{"approvedIds": in.Targets}}
	for _, id := range changed {
		out.Audit = append(out.Audit, kernel.Audit{Target: id, Change: change})
	}
	return out, nil
}

type receive struct{}

func (receive) Definition() kernel.Definition {
	return kernel.Definition{Ref: ReceiveRef, MaxTargets: 1, NoEventReason: "receipts are read through the purchase; no consumer"}
}

func (receive) Normalize(raw json.RawMessage) (kernel.Input, error) {
	var req struct {
		PurchaseID string `json:"purchaseId"`
		Amount     int64  `json:"amount"`
	}
	if err := strict(raw, &req); err != nil {
		return kernel.Input{}, err
	}
	if req.Amount < 1 || req.Amount > 1e12 {
		return kernel.Input{}, kernel.Invalidf("amount must be 1..1e12")
	}
	payload, _ := json.Marshal(map[string]int64{"amount": req.Amount})
	return kernel.Input{Targets: []string{req.PurchaseID}, Payload: payload}, nil
}

func (receive) Authorize(p kernel.Principal) error { return requireRole(p, Requester) }

func (receive) Execute(ctx context.Context, s *pg.Session, in kernel.Input) (kernel.Outcome, error) {
	var req struct{ Amount int64 }
	if err := json.Unmarshal(in.Payload, &req); err != nil {
		return kernel.Outcome{}, err
	}
	id := in.Targets[0]
	locked, err := lockPurchases(ctx, s, ReceiveRef, in.Targets)
	if err != nil {
		return kernel.Outcome{}, err
	}
	// Status is read under the lock, so a close that committed first is seen.
	if locked[id].status != "approved" {
		return kernel.Outcome{}, deny()
	}
	p := s.Principal()
	if _, err = s.Exec(ctx, `INSERT INTO procurement.receipt (tenant_id, purchase_id, amount, received_by) VALUES ($1,$2,$3,$4)`,
		p.Tenant, id, req.Amount, p.Actor); err != nil {
		return kernel.Outcome{}, err
	}
	change, _ := json.Marshal(map[string]any{"receipt": map[string]int64{"amount": req.Amount}})
	return kernel.Outcome{Targets: in.Targets, Audit: []kernel.Audit{{Target: id, Change: change}},
		Result: map[string]any{"purchaseId": id, "amount": req.Amount}}, nil
}

type closeOp struct{}

func (closeOp) Definition() kernel.Definition {
	return kernel.Definition{Ref: CloseRef, MaxTargets: 100, Events: []kernel.EventType{ClosedEvent}}
}

func (closeOp) Normalize(raw json.RawMessage) (kernel.Input, error) { return normalizeBulk(raw, true) }
func (closeOp) Authorize(p kernel.Principal) error                  { return requireRole(p, Approver) }

func (closeOp) Execute(ctx context.Context, s *pg.Session, in kernel.Input) (kernel.Outcome, error) {
	locked, err := lockPurchases(ctx, s, CloseRef, in.Targets)
	if err != nil {
		return kernel.Outcome{}, err
	}
	for _, id := range in.Targets {
		if p, ok := locked[id]; !ok || p.status != "approved" {
			return kernel.Outcome{}, deny()
		}
	}
	// A new statement after the locks: sees all receipts committed before them.
	rows, err := s.Query(ctx, `SELECT purchase_id, sum(amount)::bigint FROM procurement.receipt WHERE purchase_id = ANY($1) GROUP BY purchase_id`, in.Targets)
	if err != nil {
		return kernel.Outcome{}, err
	}
	received := map[string]int64{}
	for rows.Next() {
		var id string
		var sum int64
		if err := rows.Scan(&id, &sum); err != nil {
			rows.Close()
			return kernel.Outcome{}, err
		}
		received[id] = sum
	}
	rows.Close()
	if err = rows.Err(); err != nil {
		return kernel.Outcome{}, err
	}
	for _, id := range in.Targets {
		if received[id] < locked[id].amount {
			return kernel.Outcome{}, deny()
		}
	}
	changed, err := setStatus(ctx, s, in.Targets, "approved", "closed")
	if err != nil {
		return kernel.Outcome{}, err
	}
	out := kernel.Outcome{Targets: changed, Result: map[string][]string{"closedIds": in.Targets}}
	for _, id := range changed {
		change, _ := json.Marshal(map[string]any{"status": map[string]string{"from": "approved", "to": "closed"},
			"received": received[id], "reason": payloadReason(in)})
		payload, _ := json.Marshal(map[string]any{"id": id, "amount": locked[id].amount, "received": received[id]})
		out.Audit = append(out.Audit, kernel.Audit{Target: id, Change: change})
		out.Events = append(out.Events, kernel.Event{EventType: ClosedEvent, Target: id, Payload: payload})
	}
	return out, nil
}

// Notifier is the consumer name of the notification projection.
const Notifier = "procurement.notify"

// notifier turns each procurement.purchase.closed@1 into a notification for
// the purchase's creator.
type notifier struct{}

func (notifier) Name() string               { return Notifier }
func (notifier) Events() []kernel.EventType { return []kernel.EventType{ClosedEvent} }

func (notifier) Handle(ctx context.Context, s *pg.Session, d pg.Delivery) error {
	var p struct {
		ID       string `json:"id"`
		Amount   int64  `json:"amount"`
		Received int64  `json:"received"`
	}
	if err := strict(d.Event.Payload, &p); err != nil || p.ID != d.Event.Target || p.Amount < 1 || p.Received < p.Amount {
		return kernel.Internalf("%s: payload does not match %s: %s", Notifier, ClosedEvent, d.Event.Payload)
	}
	tag, err := s.Exec(ctx, `INSERT INTO procurement.notification (tenant_id, event_id, recipient, purchase_id, kind)
	 SELECT tenant_id, $2, created_by, id, 'closed' FROM procurement.purchase WHERE id = $1`, p.ID, d.ID)
	if err == nil && tag.RowsAffected() != 1 {
		err = kernel.Internalf("%s: purchase %q not found", Notifier, p.ID)
	}
	return err
}
