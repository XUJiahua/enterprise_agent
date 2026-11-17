// Package pg runs kernel operations on PostgreSQL with pgx. Runner.Run is the
// single managed write path: it normalizes and authorizes the request, opens
// one transaction bound to the principal, serializes on the idempotency
// scope, calls the domain's Execute, checks the reported Outcome against the
// request and Definition, writes the audit, outbox and result rows, and
// commits. Any failure before commit leaves no business, audit, outbox or
// result row behind.
//
// The Session handed to domain code hides the pool and transaction control.
// That prevents accidental misuse; it is not a sandbox for untrusted Go code.
package pg

import (
	"bytes"
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"strings"

	"github.com/XUJiahua/enterprise_agent/kernel"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Schema creates the kernel tables (schema "kernel"); include it in the host's
// migrations. It is not applied by the runner.
//
//go:embed schema.sql
var Schema string

// MaxRequestBytes bounds the raw request accepted by Run.
const MaxRequestBytes = 64 << 10

// Operation is one registered operation version, implemented by a domain
// module. Normalize and Authorize must not touch the database.
type Operation interface {
	Definition() kernel.Definition
	// Normalize strictly parses raw into sorted targets and a canonical payload.
	Normalize(raw json.RawMessage) (kernel.Input, error)
	// Authorize is the operation-level permission check for p.
	Authorize(p kernel.Principal) error
	// Execute locks, checks and changes the targets through s and reports
	// exactly what it changed. It must not leak target existence: an
	// ineligible target returns the same kernel.Denied error as a missing one.
	Execute(ctx context.Context, s *Session, in kernel.Input) (kernel.Outcome, error)
}

// Session is the operation's view of the running transaction.
type Session struct {
	tx        pgx.Tx
	principal kernel.Principal
}

func (s *Session) Principal() kernel.Principal {
	p := s.principal
	p.Roles = slices.Clone(p.Roles)
	return p
}

func (s *Session) Query(ctx context.Context, sql string, args ...any) (pgx.Rows, error) {
	return s.tx.Query(ctx, sql, args...)
}

func (s *Session) QueryRow(ctx context.Context, sql string, args ...any) pgx.Row {
	return s.tx.QueryRow(ctx, sql, args...)
}

func (s *Session) Exec(ctx context.Context, sql string, args ...any) (pgconn.CommandTag, error) {
	return s.tx.Exec(ctx, sql, args...)
}

type registered struct {
	op  Operation
	def kernel.Definition // frozen copy taken at registration
}

// Runner executes registered operations. Create it with New.
type Runner struct {
	pool      *pgxpool.Pool
	ops       map[string]registered
	consumers map[string]consumerReg
}

// Result is the stored outcome of one request; a replay returns the same Body.
type Result struct {
	Targets  []string
	Body     json.RawMessage
	Replayed bool
}

// New validates and freezes the operation definitions, checks that declared
// events and registered consumers match exactly, and checks that the pool's
// role cannot bypass the kernel tables' row level security.
func New(ctx context.Context, pool *pgxpool.Pool, reg Registry) (*Runner, error) {
	if pool == nil {
		return nil, errors.New("pg: nil pool")
	}
	r := &Runner{pool: pool, ops: map[string]registered{}}
	for _, op := range reg.Operations {
		def := op.Definition().Clone()
		if err := def.Validate(); err != nil {
			return nil, err
		}
		id := def.Ref.String()
		if _, dup := r.ops[id]; dup {
			return nil, fmt.Errorf("pg: %s registered twice", id)
		}
		r.ops[id] = registered{op, def}
	}
	if err := r.registerConsumers(reg.Consumers); err != nil {
		return nil, err
	}
	tables := []string{"kernel.request", "kernel.audit", "kernel.outbox"}
	if len(r.consumers) > 0 {
		tables = append(tables, "kernel.inbox")
	}
	if err := CheckRuntime(ctx, pool, tables...); err != nil {
		return nil, err
	}
	return r, nil
}

// CheckRuntime fails unless the pool's role is not superuser, has no
// BYPASSRLS, does not own (or inherit ownership of) any of tables, and every
// table forces row level security.
func CheckRuntime(ctx context.Context, pool *pgxpool.Pool, tables ...string) error {
	var ok bool
	err := pool.QueryRow(ctx, `SELECT count(*) = cardinality($1::text[]) AND bool_and(c.relrowsecurity AND c.relforcerowsecurity
	  AND NOT pg_has_role(current_user, c.relowner, 'MEMBER'))
	  AND (SELECT NOT (rolsuper OR rolbypassrls) FROM pg_roles WHERE rolname = current_user)
	 FROM pg_class c WHERE c.oid = ANY (SELECT to_regclass(t) FROM unnest($1::text[]) t)`, tables).Scan(&ok)
	if err != nil {
		return fmt.Errorf("pg: runtime check: %w", err)
	}
	if !ok {
		return fmt.Errorf("pg: runtime role must be non-owner, non-superuser, without BYPASSRLS, on tables with forced RLS: %v", tables)
	}
	return nil
}

// Describe returns the frozen definitions, sorted by ref.
func (r *Runner) Describe() []kernel.Definition {
	out := make([]kernel.Definition, 0, len(r.ops))
	for _, o := range r.ops {
		out = append(out, o.def.Clone())
	}
	slices.SortFunc(out, func(a, b kernel.Definition) int { return strings.Compare(a.Ref.String(), b.Ref.String()) })
	return out
}

// Read runs fn in a READ ONLY transaction bound to p, for queries that must
// see only the principal's tenant through row level security.
func (r *Runner) Read(ctx context.Context, p kernel.Principal, fn func(*Session) error) error {
	if p.Tenant == "" || p.Actor == "" {
		return kernel.Deniedf("no identity")
	}
	tx, err := r.pool.BeginTx(ctx, pgx.TxOptions{AccessMode: pgx.ReadOnly})
	if err != nil {
		return classify(err)
	}
	defer func() { _ = tx.Rollback(context.WithoutCancel(ctx)) }()
	if err = bind(ctx, tx, p); err != nil {
		return classify(err)
	}
	if err = fn(&Session{tx, p}); err != nil {
		return classify(err)
	}
	return classify(tx.Commit(ctx))
}

func bind(ctx context.Context, tx pgx.Tx, p kernel.Principal) error {
	_, err := tx.Exec(ctx, `SELECT set_config('app.tenant',$1,true), set_config('app.actor',$2,true), set_config('app.roles',$3,true)`,
		p.Tenant, p.Actor, strings.Join(p.Roles, ","))
	return err
}

// Run executes ref for principal p under idempotency key. A repeat of the same
// key and request returns the stored result (Replayed); the same key with a
// different request is a Conflict. If commit fails the outcome is unknown and
// the error is Retryable: retry with the same key to learn it.
func (r *Runner) Run(ctx context.Context, p kernel.Principal, ref kernel.Ref, key string, raw json.RawMessage) (Result, error) {
	reg, ok := r.ops[ref.String()]
	if !ok {
		return Result{}, kernel.Invalidf("unknown operation %s", ref)
	}
	if p.Tenant == "" || p.Actor == "" {
		return Result{}, kernel.Deniedf("no identity")
	}
	if !kernel.ValidKey(key) {
		return Result{}, kernel.Invalidf("invalid idempotency key")
	}
	if len(raw) > MaxRequestBytes {
		return Result{}, kernel.Invalidf("request too large")
	}
	in, err := reg.op.Normalize(raw)
	if err != nil {
		return Result{}, classify(err)
	}
	if in.Targets, err = kernel.Targets(in.Targets, reg.def.MaxTargets); err != nil {
		return Result{}, err
	}
	if len(in.Payload) == 0 {
		in.Payload = json.RawMessage(`{}`)
	}
	if !json.Valid(in.Payload) {
		return Result{}, kernel.Internalf("%s: Normalize returned invalid payload JSON", ref)
	}
	if err = reg.op.Authorize(p); err != nil {
		return Result{}, classify(err)
	}

	tx, err := r.pool.Begin(ctx)
	if err != nil {
		return Result{}, classify(err)
	}
	defer func() { _ = tx.Rollback(context.WithoutCancel(ctx)) }()
	if err = bind(ctx, tx, p); err != nil {
		return Result{}, classify(err)
	}
	scope, _ := json.Marshal([]any{ref.Name, ref.Version, p.Tenant, p.Actor, key})
	if _, err = tx.Exec(ctx, `SELECT pg_advisory_xact_lock(hashtextextended($1, 0))`, string(scope)); err != nil {
		return Result{}, classify(err)
	}
	hash := kernel.Fingerprint(ref, in)
	var saved Result
	var savedHash string
	err = tx.QueryRow(ctx, `SELECT request_hash, targets, result FROM kernel.request
	 WHERE tenant_id=$1 AND actor_id=$2 AND operation=$3 AND version=$4 AND key=$5`,
		p.Tenant, p.Actor, ref.Name, ref.Version, key).Scan(&savedHash, &saved.Targets, &saved.Body)
	switch {
	case err == nil && savedHash == hash:
		saved.Replayed = true
		return saved, nil // read-only: rollback is fine
	case err == nil:
		return Result{}, kernel.Conflictf("idempotency key already used for a different request")
	case !errors.Is(err, pgx.ErrNoRows):
		return Result{}, classify(err)
	}

	out, err := reg.op.Execute(ctx, &Session{tx, p}, in)
	if err != nil {
		return Result{}, classify(err)
	}
	if err = checkOutcome(reg.def, in, out); err != nil {
		return Result{}, err
	}
	body, err := json.Marshal(out.Result)
	if err != nil {
		return Result{}, kernel.Internalf("%s: result not serializable: %v", ref, err)
	}
	if _, err = tx.Exec(ctx, `INSERT INTO kernel.request (tenant_id, actor_id, operation, version, key, request_hash, targets, result)
	 VALUES ($1,$2,$3,$4,$5,$6,$7,$8)`, p.Tenant, p.Actor, ref.Name, ref.Version, key, hash, in.Targets, body); err != nil {
		return Result{}, classify(err)
	}
	var auditTargets, auditChanges []string
	for _, a := range out.Audit {
		auditTargets, auditChanges = append(auditTargets, a.Target), append(auditChanges, string(a.Change))
	}
	if _, err = tx.Exec(ctx, `INSERT INTO kernel.audit (tenant_id, actor_id, operation, version, key, target_id, change)
	 SELECT $1,$2,$3,$4,$5,t,c::jsonb FROM unnest($6::text[], $7::text[]) AS u(t,c)`,
		p.Tenant, p.Actor, ref.Name, ref.Version, key, auditTargets, auditChanges); err != nil {
		return Result{}, classify(err)
	}
	if len(out.Events) > 0 {
		var types, targets, payloads []string
		var versions []int32
		for _, e := range out.Events {
			types, versions = append(types, e.Type), append(versions, int32(e.Version))
			targets, payloads = append(targets, e.Target), append(payloads, string(e.Payload))
		}
		if _, err = tx.Exec(ctx, `INSERT INTO kernel.outbox (tenant_id, actor_id, operation, version, key, event_type, event_version, target_id, payload)
		 SELECT $1,$2,$3,$4,$5,t,v,g,p::jsonb FROM unnest($6::text[], $7::int[], $8::text[], $9::text[]) AS u(t,v,g,p)`,
			p.Tenant, p.Actor, ref.Name, ref.Version, key, types, versions, targets, payloads); err != nil {
			return Result{}, classify(err)
		}
	}
	if err = tx.Commit(ctx); err != nil {
		return Result{}, &kernel.Error{Code: kernel.Retryable, Msg: "commit outcome unknown; retry with the same key", Err: err}
	}
	return Result{Targets: in.Targets, Body: body}, nil
}

// checkOutcome verifies the domain's report: the changed set equals the
// requested set (compared by ID), one audit object per target, and only
// declared events on requested targets, at least one per target when the
// operation declares events.
func checkOutcome(def kernel.Definition, in kernel.Input, out kernel.Outcome) error {
	bad := func(format string, a ...any) error {
		return kernel.Internalf("%s: outcome violates contract: %s", def.Ref, fmt.Sprintf(format, a...))
	}
	changed := slices.Clone(out.Targets)
	slices.Sort(changed)
	if !slices.Equal(changed, in.Targets) {
		return bad("changed targets %v, requested %v", changed, in.Targets)
	}
	want := map[string]bool{}
	for _, t := range in.Targets {
		want[t] = true
	}
	audited := map[string]bool{}
	for _, a := range out.Audit {
		if !want[a.Target] || audited[a.Target] || !isObject(a.Change) {
			return bad("audit for %q missing target, duplicated or not a JSON object", a.Target)
		}
		audited[a.Target] = true
	}
	if len(audited) != len(want) {
		return bad("%d of %d targets audited", len(audited), len(want))
	}
	evented := map[string]bool{}
	for _, e := range out.Events {
		if !slices.Contains(def.Events, e.EventType) || !want[e.Target] || !isObject(e.Payload) {
			return bad("event %s on %q undeclared, off-target or payload not a JSON object", e.EventType, e.Target)
		}
		evented[e.Target] = true
	}
	if len(def.Events) > 0 && len(evented) != len(want) {
		return bad("%d of %d targets have an event", len(evented), len(want))
	}
	return nil
}

func isObject(b json.RawMessage) bool {
	b = bytes.TrimSpace(b)
	return len(b) > 1 && b[0] == '{' && json.Valid(b)
}

// classify keeps kernel errors, marks transient PostgreSQL failures
// (serialization 40001, deadlock 40P01) Retryable and everything else Internal.
func classify(err error) error {
	if err == nil {
		return nil
	}
	var ke *kernel.Error
	if errors.As(err, &ke) {
		return err
	}
	var pe *pgconn.PgError
	if errors.As(err, &pe) && (pe.Code == "40001" || pe.Code == "40P01") {
		return &kernel.Error{Code: kernel.Retryable, Msg: "transient conflict; retry with the same key", Err: err}
	}
	return &kernel.Error{Code: kernel.Internal, Msg: "internal error", Err: err}
}
