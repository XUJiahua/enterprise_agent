"""Human v4 candidate controls, excluded from exports and all model sample counts.

Each implicit-violation control breaks exactly one convention and must still pass the
whole explicit contract; explicit-violation controls must fail the explicit contract.
"""
from pathlib import Path

ROUTE='\treturn protocol.HTTP(protocol.Routes{"/purchases/bulk-approve": approve}, fallback), nil'

# control -> (violated group or None, explicit contract expected to pass)
MODES={
 'go-manual':{'correct':(None,True),'omit-audit':('implicit-audit',True),'ignore-operation':('implicit-operation-scope',True),
              'approve-hash':('implicit-request-hash',True),'distinguish-404':('implicit-uniform-409',True),
              'skip-completeness':(None,False),'unchanged':(None,False)},
 'go-action':{'correct':(None,True),'omit-audit':('implicit-audit',True),'ignore-operation':('implicit-operation-scope',True),
              'approve-hash':('implicit-request-hash',True),'distinguish-404':('implicit-uniform-409',True),
              'wrong-field':(None,False),'unchanged':(None,False)}}

REJECT_GO='''package candidate

import (
	"context"
	"encoding/json"
	"errors"
	"strings"

	"github.com/XUJiahua/enterprise_agent/internal/app"
	"github.com/XUJiahua/enterprise_agent/internal/ledger"
	"github.com/XUJiahua/enterprise_agent/internal/protocol"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

const opReject = "bulk-reject.v4"

// Hand-written reject following the existing approval conventions (human control).
func newReject(pool *pgxpool.Pool) protocol.Handler {
	return func(ctx context.Context, u app.Identity, key string, ids []string) (protocol.Result, bool, error) {
		for _, id := range ids {
			if strings.ContainsRune(id, 0) {
				return protocol.Result{}, false, &protocol.Rejection{Status: 404}
			}
		}
		tx, err := pool.Begin(ctx)
		if err != nil {
			return protocol.Result{}, false, err
		}
		defer tx.Rollback(ctx)
		_, err = tx.Exec(ctx, `SELECT set_config('app.tenant',$1,true),set_config('app.user',$2,true),set_config('app.role',$3,true)`, u.Tenant, u.User, u.Role)
		if err != nil {
			return protocol.Result{}, false, err
		}
		lock, _ := json.Marshal([]string{opReject, u.Tenant, u.User, key})
		if _, err = tx.Exec(ctx, `SELECT pg_advisory_xact_lock(hashtextextended($1,0))`, string(lock)); err != nil {
			return protocol.Result{}, false, err
		}
		hash := ledger.Fingerprint(@HASH_OP@, ids)
		var savedHash string
		var saved []byte
		err = tx.QueryRow(ctx, @LOOKUP@).Scan(&savedHash, &saved)
		if err == nil {
			if savedHash != hash {
				return protocol.Result{}, false, &protocol.Rejection{Status: 409}
			}
			var body map[string][]string
			if err = json.Unmarshal(saved, &body); err != nil {
				return protocol.Result{}, false, err
			}
			if err = tx.Commit(ctx); err != nil {
				return protocol.Result{}, false, err
			}
			return protocol.Result{Field: "rejectedIds", IDs: body["rejectedIds"]}, true, nil
		}
		if !errors.Is(err, pgx.ErrNoRows) {
			return protocol.Result{}, false, err
		}
		rows, err := tx.Query(ctx, `SELECT id FROM "Purchase" WHERE id=ANY($1::text[]) AND "tenantId"=$2 AND "createdBy"<>$3 AND status='submitted' ORDER BY id FOR UPDATE`, ids, u.Tenant, u.User)
		if err != nil {
			return protocol.Result{}, false, err
		}
		eligible := []string{}
		for rows.Next() {
			var id string
			if err = rows.Scan(&id); err != nil {
				rows.Close()
				return protocol.Result{}, false, err
			}
			eligible = append(eligible, id)
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			return protocol.Result{}, false, err
		}
		@COMPLETENESS@
		tag, err := tx.Exec(ctx, `UPDATE "Purchase" SET status='rejected' WHERE id=ANY($1::text[]) AND "tenantId"=$2 AND "createdBy"<>$3 AND status='submitted'`, ids, u.Tenant, u.User)
		if err != nil {
			return protocol.Result{}, false, err
		}
		if tag.RowsAffected() != int64(len(ids)) {
			return protocol.Result{}, false, &protocol.Rejection{Status: 409}
		}
		@AUDIT@
		result := protocol.Result{Field: "rejectedIds", IDs: ids}
		response, err := json.Marshal(result)
		if err != nil {
			return protocol.Result{}, false, err
		}
		_, err = tx.Exec(ctx, `INSERT INTO "ActionRequest" ("tenantId","actorId",operation,key,"requestHash",response)
		 VALUES ($1,$2,$3,$4,$5,$6::jsonb)`, u.Tenant, u.User, opReject, key, hash, string(response))
		if err != nil {
			return protocol.Result{}, false, err
		}
		if err = tx.Commit(ctx); err != nil {
			return protocol.Result{}, false, err
		}
		return result, false, nil
	}
}
'''

AUDIT='''_, err = tx.Exec(ctx, `INSERT INTO "ApprovalAudit" ("tenantId","purchaseId","approvedBy",operation,"idempotencyKey","fromStatus","toStatus")
		 SELECT $2,id,$3,$4,$5,'submitted','rejected' FROM unnest($1::text[]) AS id`, ids, u.Tenant, u.User, opReject, key)
		if err != nil {
			return protocol.Result{}, false, err
		}'''
LOOKUP='`SELECT "requestHash",response FROM "ActionRequest" WHERE "tenantId"=$1 AND "actorId"=$2 AND operation=$3 AND key=$4`, u.Tenant, u.User, opReject, key'
LOOKUP_NO_OP='`SELECT "requestHash",response FROM "ActionRequest" WHERE "tenantId"=$1 AND "actorId"=$2 AND key=$3`, u.Tenant, u.User, key'
COMPLETE='''if len(eligible) != len(ids) {
			return protocol.Result{}, false, &protocol.Rejection{Status: @STATUS@}
		}'''
# Explicit violation: reject the eligible subset of a mixed batch.
PARTIAL='''if len(eligible) == 0 {
			return protocol.Result{}, false, &protocol.Rejection{Status: 409}
		}
		ids = eligible'''

KERNEL_ROUTE='''	reject := func(ctx context.Context, u app.Identity, key string, ids []string) (protocol.Result, bool, error) {
		t := action.Transition{Operation: "bulk-reject.v4", From: "submitted", To: "rejected", Field: "@FIELD@"}
		rejected, replayed, err := executor.Run(ctx, u, t, key, ids)
		var denied *action.Rejection
		if errors.As(err, &denied) {
			return protocol.Result{}, false, &protocol.Rejection{Status: denied.Status}
		}
		if err != nil {
			return protocol.Result{}, false, err
		}
		return protocol.Result{Field: t.Field, IDs: rejected}, replayed, nil
	}
'''


def reject_source(mode):
    return (REJECT_GO.replace('@HASH_OP@','"bulk-approve.v3"' if mode=='approve-hash' else 'opReject')
            .replace('@LOOKUP@',LOOKUP_NO_OP if mode=='ignore-operation' else LOOKUP)
            .replace('@COMPLETENESS@',PARTIAL if mode=='skip-completeness' else COMPLETE.replace('@STATUS@','404' if mode=='distinguish-404' else '409'))
            .replace('@AUDIT@','' if mode=='omit-audit' else AUDIT))


def apply(candidate,cell,mode):
    if mode not in MODES[cell]: raise ValueError('unknown synthetic control')
    if mode=='unchanged': return
    approval=Path(candidate)/'internal/candidate/approval.go';text=approval.read_text()
    assert text.count(ROUTE)==1,'unexpected starting candidate'
    routes='\treturn protocol.HTTP(protocol.Routes{"/purchases/bulk-approve": approve, "/purchases/bulk-reject": reject}, fallback), nil'
    if cell=='go-action' and mode in ('correct','wrong-field'):
        closure=KERNEL_ROUTE.replace('@FIELD@','approvedIds' if mode=='wrong-field' else 'rejectedIds')
    else:
        # Hand-written reject: the reference for go-manual, a kernel bypass in go-action.
        (Path(candidate)/'internal/candidate/reject.go').write_text(reject_source(mode))
        closure='\treject := newReject(pool)\n'
    approval.write_text(text.replace(ROUTE,closure+routes))
