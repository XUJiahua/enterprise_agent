// Package action implements a typed, domain-specific transactional Action POC.
// It is not a general workflow engine or a sandbox for untrusted extensions.
package action

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"sort"
	"strings"
	"unicode/utf8"

	"github.com/XUJiahua/enterprise_agent/internal/app"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

const Operation = "bulk-approve.v3"

type Result struct {
	ApprovedIDs []string `json:"approvedIds"`
}

type Rejection struct{ Status int }

func (r *Rejection) Error() string { return fmt.Sprintf("action rejected: %d", r.Status) }

type Executor struct {
	pool     *pgxpool.Pool
	mutation string
}

// New requires a guarded Purchase runtime and independently checks the new tables.
func New(ctx context.Context, pool *pgxpool.Pool, mutation string) (*Executor, error) {
	if pool == nil {
		return nil, errors.New("missing pool")
	}
	switch mutation {
	case "":
	case "omit-audit", "commit-before-audit", "skip-replay", "skip-completeness", "reject-all":
		if os.Getenv("POC_ALLOW_MUTATIONS") != "1" {
			return nil, errors.New("controlled mutations require POC_ALLOW_MUTATIONS=1")
		}
	default:
		return nil, errors.New("unknown controlled mutation")
	}
	var guarded bool
	err := pool.QueryRow(ctx, `SELECT c.relrowsecurity AND c.relforcerowsecurity AND NOT
 (r.rolsuper OR r.rolbypassrls OR pg_has_role(current_user,c.relowner,'MEMBER'))
 FROM pg_roles r CROSS JOIN pg_class c WHERE r.rolname=current_user AND c.oid='"Purchase"'::regclass`).Scan(&guarded)
	if err != nil || !guarded {
		return nil, fmt.Errorf("Action requires non-owner guarded Purchase runtime: %v", err)
	}
	var configured bool
	err = pool.QueryRow(ctx, `SELECT count(*)=2 AND bool_and(c.relrowsecurity AND c.relforcerowsecurity
	 AND NOT pg_has_role(current_user,c.relowner,'MEMBER')
	 AND has_table_privilege(current_user,c.oid,'SELECT')
	 AND has_table_privilege(current_user,c.oid,'INSERT')
	 AND NOT has_any_column_privilege(current_user,c.oid,'UPDATE')
	 AND NOT has_table_privilege(current_user,c.oid,'DELETE,TRUNCATE,TRIGGER'))
	 FROM pg_class c WHERE c.oid IN ('"ActionRequest"'::regclass,'"ApprovalAudit"'::regclass)`).Scan(&configured)
	if err != nil || !configured {
		return nil, fmt.Errorf("Action tables require non-owner RLS and append-only grants: %v", err)
	}
	return &Executor{pool: pool, mutation: mutation}, nil
}

func canonical(ids []string) ([]string, error) {
	if len(ids) < 1 || len(ids) > 100 {
		return nil, &Rejection{400}
	}
	result := append([]string(nil), ids...)
	sort.Strings(result)
	for i, id := range result {
		if len(id) == 0 || len(id) > 100 || !utf8.ValidString(id) || (i > 0 && result[i-1] == id) {
			return nil, &Rejection{400}
		}
	}
	return result, nil
}

func validKey(key string) bool {
	if len(key) < 1 || len(key) > 128 {
		return false
	}
	for _, c := range key {
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || strings.ContainsRune("._:-", c)) {
			return false
		}
	}
	return true
}

func fingerprint(ids []string) string {
	h := sha256.New()
	fmt.Fprint(h, Operation, "\n")
	for _, id := range ids {
		fmt.Fprintf(h, "%d:%s", len(id), id)
	}
	return hex.EncodeToString(h.Sum(nil))
}

func bind(ctx context.Context, tx pgx.Tx, u app.Identity) error {
	_, err := tx.Exec(ctx, `SELECT set_config('app.tenant',$1,true),set_config('app.user',$2,true),set_config('app.role',$3,true)`, u.Tenant, u.User, u.Role)
	return err
}

// BulkApprove owns the transaction. Callers cannot commit a partial result.
// A transaction-scoped key lock serializes concurrent replays. Only completed
// responses are inserted; a failed transaction leaves no poisoned reservation.
func (e *Executor) BulkApprove(ctx context.Context, u app.Identity, key string, input []string) (Result, bool, error) {
	if u.Role != "approver" || u.User == "" || u.Tenant == "" {
		return Result{}, false, &Rejection{403}
	}
	ids, err := canonical(input)
	if err != nil {
		return Result{}, false, err
	}
	if !validKey(key) {
		return Result{}, false, &Rejection{400}
	}
	if e.mutation == "reject-all" {
		return Result{}, false, &Rejection{403}
	}
	for _, id := range ids {
		if strings.ContainsRune(id, 0) {
			return Result{}, false, &Rejection{404}
		}
	}
	tx, err := e.pool.Begin(ctx)
	if err != nil {
		return Result{}, false, err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	if err = bind(ctx, tx, u); err != nil {
		return Result{}, false, err
	}
	// JSON framing avoids ambiguous concatenation; hash collisions only serialize
	// unrelated keys, because the exact scoped primary key is checked below.
	lock, _ := json.Marshal([]string{Operation, u.Tenant, u.User, key})
	if _, err = tx.Exec(ctx, `SELECT pg_advisory_xact_lock(hashtextextended($1,0))`, string(lock)); err != nil {
		return Result{}, false, err
	}
	hash := fingerprint(ids)
	if e.mutation != "skip-replay" {
		var savedHash string
		var saved []byte
		err = tx.QueryRow(ctx, `SELECT "requestHash",response FROM "ActionRequest"
		 WHERE "tenantId"=$1 AND "actorId"=$2 AND operation=$3 AND key=$4`, u.Tenant, u.User, Operation, key).Scan(&savedHash, &saved)
		if err == nil {
			if savedHash != hash {
				return Result{}, false, &Rejection{409}
			}
			var result Result
			if err = json.Unmarshal(saved, &result); err != nil {
				return Result{}, false, err
			}
			if err = tx.Commit(ctx); err != nil {
				return Result{}, false, err
			}
			return result, true, nil
		}
		if !errors.Is(err, pgx.ErrNoRows) {
			return Result{}, false, err
		}
	}
	rows, err := tx.Query(ctx, `SELECT id FROM "Purchase" WHERE id=ANY($1::text[])
	 AND "tenantId"=$2 AND "createdBy"<>$3 AND status='submitted' ORDER BY id FOR UPDATE`, ids, u.Tenant, u.User)
	if err != nil {
		return Result{}, false, err
	}
	eligible := []string{}
	for rows.Next() {
		var id string
		if err = rows.Scan(&id); err != nil {
			rows.Close()
			return Result{}, false, err
		}
		eligible = append(eligible, id)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return Result{}, false, err
	}
	if len(eligible) == 0 || e.mutation != "skip-completeness" && len(eligible) != len(ids) {
		return Result{}, false, &Rejection{409}
	}
	tag, err := tx.Exec(ctx, `UPDATE "Purchase" SET status='approved' WHERE id=ANY($1::text[])
	 AND "tenantId"=$2 AND "createdBy"<>$3 AND status='submitted'`, eligible, u.Tenant, u.User)
	if err != nil {
		return Result{}, false, err
	}
	if e.mutation != "skip-completeness" && tag.RowsAffected() != int64(len(ids)) {
		return Result{}, false, &Rejection{409}
	}
	if e.mutation == "commit-before-audit" {
		// Deliberately broken transaction boundary, enabled only for negative control.
		if err = tx.Commit(ctx); err != nil {
			return Result{}, false, err
		}
		tx, err = e.pool.Begin(ctx)
		if err != nil {
			return Result{}, false, err
		}
		if err = bind(ctx, tx, u); err != nil {
			return Result{}, false, err
		}
	}
	if e.mutation != "omit-audit" {
		_, err = tx.Exec(ctx, `INSERT INTO "ApprovalAudit" ("tenantId","purchaseId","approvedBy",operation,"idempotencyKey","fromStatus","toStatus")
		 SELECT $2,id,$3,$4,$5,'submitted','approved' FROM unnest($1::text[]) AS id`, eligible, u.Tenant, u.User, Operation, key)
		if err != nil {
			return Result{}, false, err
		}
	}
	sort.Strings(eligible)
	result := Result{ApprovedIDs: eligible}
	response, err := json.Marshal(result)
	if err != nil {
		return Result{}, false, err
	}
	_, err = tx.Exec(ctx, `INSERT INTO "ActionRequest" ("tenantId","actorId",operation,key,"requestHash",response)
	 VALUES ($1,$2,$3,$4,$5,$6::jsonb)`, u.Tenant, u.User, Operation, key, hash, string(response))
	if err != nil {
		return Result{}, false, err
	}
	if err = tx.Commit(ctx); err != nil {
		return Result{}, false, err
	}
	return result, false, nil
}
