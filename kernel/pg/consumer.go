package pg

import (
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"strings"
	"time"

	"github.com/XUJiahua/enterprise_agent/kernel"
	"github.com/jackc/pgx/v5"
)

// InboxSchema creates kernel.inbox and kernel.pending_tenants; apply it after
// Schema.
//
//go:embed inbox.sql
var InboxSchema string

// Consumer projects outbox events inside the same database. Handle runs in
// the transaction that also records the event in kernel.inbox, so its writes
// commit exactly once per event even if delivery repeats. A Handle error
// (including an unexpected payload) rolls back and leaves the event pending.
type Consumer interface {
	Name() string
	Events() []kernel.EventType
	Handle(ctx context.Context, s *Session, d Delivery) error
}

// Delivery is one outbox event as a consumer sees it.
type Delivery struct {
	ID        int64
	Event     kernel.Event
	Operation kernel.Ref
	Actor     string // who ran the operation
}

// Registry is everything a Runner executes. Every event an operation declares
// must have a consumer, and every consumer event must be declared.
type Registry struct {
	Operations []Operation
	Consumers  []Consumer
}

type consumerReg struct {
	c      Consumer
	events []kernel.EventType
	types  []string
}

// Contract is the validated, DB-free description of a Registry. New and the
// developer tooling (appctl describe/validate) both derive it with Describe,
// so they cannot disagree.
type Contract struct {
	Operations []kernel.Definition `json:"operations"`
	Consumers  []ConsumerContract  `json:"consumers"`
}

// ConsumerContract is one consumer and the event versions it handles.
type ConsumerContract struct {
	Name   string             `json:"name"`
	Events []kernel.EventType `json:"events"`
}

// Describe validates reg (definitions, unique refs and consumer names, and an
// exact match between declared events and consumers) and returns its sorted
// contract.
func Describe(reg Registry) (Contract, error) {
	var c Contract
	refs := map[kernel.Ref]bool{}
	declared := map[kernel.EventType]bool{}
	for _, op := range reg.Operations {
		def := op.Definition().Clone()
		if err := def.Validate(); err != nil {
			return Contract{}, err
		}
		if refs[def.Ref] {
			return Contract{}, fmt.Errorf("pg: %s registered twice", def.Ref)
		}
		refs[def.Ref] = true
		for _, e := range def.Events {
			declared[e] = true
		}
		c.Operations = append(c.Operations, def)
	}
	consumed := map[kernel.EventType]bool{}
	names := map[string]bool{}
	for _, cs := range reg.Consumers {
		cc := ConsumerContract{Name: cs.Name(), Events: slices.Clone(cs.Events())}
		probe := kernel.Definition{Ref: kernel.Ref{Name: cc.Name, Version: 1}, Targets: "-", MaxTargets: 1, Events: cc.Events}
		if err := probe.Validate(); err != nil {
			return Contract{}, fmt.Errorf("pg: consumer %q: %w", cc.Name, err)
		}
		if names[cc.Name] {
			return Contract{}, fmt.Errorf("pg: consumer %q registered twice", cc.Name)
		}
		names[cc.Name] = true
		for _, e := range cc.Events {
			if !declared[e] {
				return Contract{}, fmt.Errorf("pg: consumer %q handles %s, which no registered operation declares", cc.Name, e)
			}
			consumed[e] = true
		}
		c.Consumers = append(c.Consumers, cc)
	}
	for e := range declared {
		if !consumed[e] {
			return Contract{}, fmt.Errorf("pg: event %s is declared but has no registered consumer", e)
		}
	}
	slices.SortFunc(c.Operations, func(a, b kernel.Definition) int { return strings.Compare(a.Ref.String(), b.Ref.String()) })
	slices.SortFunc(c.Consumers, func(a, b ConsumerContract) int { return strings.Compare(a.Name, b.Name) })
	return c, nil
}

// Drain handles every pending event of the named consumer once, oldest first,
// each in its own transaction, and returns how many it handled. Failed events
// stay pending; their errors are joined into err and the rest still run.
// Running Drain concurrently is safe: the inbox key lets one win per event.
func (r *Runner) Drain(ctx context.Context, name string) (handled int, err error) {
	reg, ok := r.consumers[name]
	if !ok {
		return 0, kernel.Invalidf("unknown consumer %q", name)
	}
	rows, err := r.pool.Query(ctx, `SELECT kernel.pending_tenants($1, $2)`, name, reg.types)
	if err != nil {
		return 0, classify(err)
	}
	tenants, err := pgx.CollectRows(rows, pgx.RowTo[string])
	if err != nil {
		return 0, classify(err)
	}
	var errs []error
	for _, tenant := range tenants {
		p := kernel.Principal{Tenant: tenant, Actor: "consumer:" + name}
		// Page by id so failing events at the head cannot hide newer ones.
		// ponytail: failing events are retried on every Drain; a dead-letter
		// table is the upgrade if poison events become common.
		for after := int64(0); ; {
			var ids []int64
			if err := r.Read(ctx, p, func(s *Session) error {
				rows, err := s.Query(ctx, `SELECT o.id FROM kernel.outbox o WHERE o.event_type = ANY ($2) AND o.id > $3
				 AND NOT EXISTS (SELECT 1 FROM kernel.inbox i WHERE i.consumer = $1 AND i.event_id = o.id) ORDER BY o.id LIMIT 1000`, name, reg.types, after)
				if err != nil {
					return err
				}
				ids, err = pgx.CollectRows(rows, pgx.RowTo[int64])
				return err
			}); err != nil {
				errs = append(errs, err)
				break
			}
			if len(ids) == 0 {
				break
			}
			for _, id := range ids {
				done, err := r.deliver(ctx, reg, p, id)
				if err != nil {
					errs = append(errs, fmt.Errorf("consumer %s event %d: %w", name, id, err))
				} else if done {
					handled++
				}
			}
			after = ids[len(ids)-1]
		}
	}
	return handled, errors.Join(errs...)
}

// deliver handles one event; done=false means another delivery already did.
func (r *Runner) deliver(ctx context.Context, reg consumerReg, p kernel.Principal, id int64) (done bool, err error) {
	tx, err := r.pool.Begin(ctx)
	if err != nil {
		return false, classify(err)
	}
	defer func() { _ = tx.Rollback(context.WithoutCancel(ctx)) }()
	if err = bind(ctx, tx, p); err != nil {
		return false, classify(err)
	}
	// Claim first: a concurrent claim of the same event waits here, then skips.
	tag, err := tx.Exec(ctx, `INSERT INTO kernel.inbox (consumer, event_id, tenant_id) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING`,
		reg.c.Name(), id, p.Tenant)
	if err != nil {
		return false, classify(err)
	}
	if tag.RowsAffected() == 0 {
		return false, nil
	}
	var d Delivery
	var payload []byte
	if err = tx.QueryRow(ctx, `SELECT id, event_type, event_version, target_id, payload, operation, version, actor_id FROM kernel.outbox WHERE id = $1`, id).
		Scan(&d.ID, &d.Event.Type, &d.Event.Version, &d.Event.Target, &payload, &d.Operation.Name, &d.Operation.Version, &d.Actor); err != nil {
		return false, classify(err)
	}
	d.Event.Payload = json.RawMessage(payload)
	if !slices.Contains(reg.events, d.Event.EventType) {
		return false, kernel.Internalf("unsupported event %s", d.Event.EventType)
	}
	if err = reg.c.Handle(ctx, &Session{tx, p}, d); err != nil {
		return false, classify(err)
	}
	if err = tx.Commit(ctx); err != nil {
		return false, &kernel.Error{Code: kernel.Retryable, Msg: "commit outcome unknown", Err: err}
	}
	return true, nil
}

// Consume runs Drain for the named consumer every interval until ctx ends,
// starting immediately, so events left by a crash are picked up on restart.
func (r *Runner) Consume(ctx context.Context, name string, every time.Duration, onErr func(error)) {
	t := time.NewTicker(every)
	defer t.Stop()
	for {
		if _, err := r.Drain(ctx, name); err != nil && ctx.Err() == nil {
			onErr(err)
		}
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
	}
}
