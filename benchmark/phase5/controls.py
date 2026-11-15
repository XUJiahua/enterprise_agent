"""Human v5 candidate controls, excluded from exports and all model sample counts.

Each implicit-violation control breaks exactly one convention and must still pass the
whole explicit contract; explicit-violation controls must fail the explicit contract.
"""
from pathlib import Path

ROUTES={'go-manual':'\treturn protocol.HTTP(protocol.Routes{"/purchases/bulk-approve": approve, "/purchases/bulk-reject": newReject(pool)}, fallback), nil',
        'go-action':'\treturn protocol.HTTP(protocol.Routes{"/purchases/bulk-approve": batch(executor, approveOp), "/purchases/bulk-reject": batch(executor, rejectOp)}, fallback), nil'}

# control -> (violated group or None, explicit contract expected to pass)
MODES={
 'go-manual':{'correct':(None,True),'omit-audit':('implicit-audit',True),'reason-hash':('implicit-request-hash',True),
              'omit-event':('implicit-outbox',True),'distinguish':('implicit-uniform-denial',True),
              'no-replay':(None,False),'unchanged':(None,False)},
 'go-action':{'correct':(None,True),'omit-audit':('implicit-audit',True),'reason-hash':('implicit-request-hash',True),
              'omit-event':('implicit-outbox',True),'distinguish':('implicit-uniform-denial',True),
              'own-false':(None,False),'unchanged':(None,False)}}
# Controls that use the kernel in go-action; the others bypass it with the hand-written reopen.
KERNEL={'correct','omit-event','own-false'}

REOPEN_GO='''package candidate

import (
	"context"
	"encoding/json"
	"errors"

	"github.com/XUJiahua/enterprise_agent/internal/app"
	"github.com/XUJiahua/enterprise_agent/internal/ledger"
	"github.com/XUJiahua/enterprise_agent/internal/protocol"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

const opReopen = "reopen.v5"

// Hand-written reopen following the scattered existing conventions (human control).
func newReopen(pool *pgxpool.Pool) protocol.ItemHandler {
	return func(ctx context.Context, u app.Identity, key, id, reason string) (protocol.ItemResult, bool, error) {
		tx, err := pool.Begin(ctx)
		if err != nil {
			return protocol.ItemResult{}, false, err
		}
		defer tx.Rollback(ctx)
		if _, err = tx.Exec(ctx, `SELECT set_config('app.tenant',$1,true),set_config('app.user',$2,true),set_config('app.role',$3,true)`, u.Tenant, u.User, u.Role); err != nil {
			return protocol.ItemResult{}, false, err
		}
		lock, _ := json.Marshal([]string{opReopen, u.Tenant, u.User, key})
		if _, err = tx.Exec(ctx, `SELECT pg_advisory_xact_lock(hashtextextended($1,0))`, string(lock)); err != nil {
			return protocol.ItemResult{}, false, err
		}
		hash := ledger.Fingerprint(opReopen, @SUBJECT@)
		@REPLAY@
		var creator, status string
		err = tx.QueryRow(ctx, `SELECT "createdBy",status FROM "Purchase" WHERE id=$1 AND "tenantId"=$2@LOCK@`, id, u.Tenant).Scan(&creator, &status)
		@ELIGIBILITY@
		tag, err := tx.Exec(ctx, `UPDATE "Purchase" SET status='submitted' WHERE id=$1 AND "tenantId"=$2 AND "createdBy"=$3 AND status='rejected'`, id, u.Tenant, u.User)
		if err != nil {
			return protocol.ItemResult{}, false, err
		}
		if tag.RowsAffected() != 1 {
			return protocol.ItemResult{}, false, &protocol.Rejection{Status: 409}
		}
		@AUDIT@
		@EVENT@
		result := protocol.ItemResult{ID: id, Status: "submitted"}
		response, err := json.Marshal(result)
		if err != nil {
			return protocol.ItemResult{}, false, err
		}
		if _, err = tx.Exec(ctx, `INSERT INTO "ActionRequest" ("tenantId","actorId",operation,key,"requestHash",response) VALUES ($1,$2,$3,$4,$5,$6::jsonb)`,
			u.Tenant, u.User, opReopen, key, hash, string(response)); err != nil {
			return protocol.ItemResult{}, false, err
		}
		if err = tx.Commit(ctx); err != nil {
			return protocol.ItemResult{}, false, err
		}
		return result, false, nil
	}
}
'''

REPLAY='''var savedHash string
		var saved []byte
		err = tx.QueryRow(ctx, `SELECT "requestHash",response FROM "ActionRequest" WHERE "tenantId"=$1 AND "actorId"=$2 AND operation=$3 AND key=$4`,
			u.Tenant, u.User, opReopen, key).Scan(&savedHash, &saved)
		if err == nil {
			if savedHash != hash {
				return protocol.ItemResult{}, false, &protocol.Rejection{Status: 409}
			}
			var stored protocol.ItemResult
			if err = json.Unmarshal(saved, &stored); err != nil {
				return protocol.ItemResult{}, false, err
			}
			return stored, true, tx.Commit(ctx)
		}
		if !errors.Is(err, pgx.ErrNoRows) {
			return protocol.ItemResult{}, false, err
		}'''
# Explicit violation keeps the errors import used.
NO_REPLAY='_ = errors.Is'
UNIFORM='''if errors.Is(err, pgx.ErrNoRows) || (err == nil && (creator != u.User || status != "rejected")) {
			return protocol.ItemResult{}, false, &protocol.Rejection{Status: 409}
		}
		if err != nil {
			return protocol.ItemResult{}, false, err
		}'''
DISTINGUISH='''switch {
		case errors.Is(err, pgx.ErrNoRows):
			return protocol.ItemResult{}, false, &protocol.Rejection{Status: 404}
		case err != nil:
			return protocol.ItemResult{}, false, err
		case creator != u.User:
			return protocol.ItemResult{}, false, &protocol.Rejection{Status: 403}
		case status != "rejected":
			return protocol.ItemResult{}, false, &protocol.Rejection{Status: 409}
		}'''
AUDIT='''if _, err = tx.Exec(ctx, `INSERT INTO "ApprovalAudit" ("tenantId","purchaseId","approvedBy",operation,"idempotencyKey","fromStatus","toStatus",reason) VALUES ($1,$2,$3,$4,$5,'rejected','submitted',$6)`,
			u.Tenant, id, u.User, opReopen, key, reason); err != nil {
			return protocol.ItemResult{}, false, err
		}'''
EVENT='''if _, err = tx.Exec(ctx, `INSERT INTO "PurchaseEvent" ("tenantId","purchaseId",type,"actorId","idempotencyKey") VALUES ($1,$2,'purchase.reopened',$3,$4)`,
			u.Tenant, id, u.User, key); err != nil {
			return protocol.ItemResult{}, false, err
		}'''

KERNEL_ROUTE='''	reopen := func(ctx context.Context, u app.Identity, key, id, reason string) (protocol.ItemResult, bool, error) {
		done, replayed, err := executor.Run(ctx, u, reopenOp, key, []string{id}, reason)
		if err != nil {
			return protocol.ItemResult{}, false, adapt(err)
		}
		return protocol.ItemResult{ID: done[0], Status: reopenOp.To}, replayed, nil
	}
'''
KERNEL_OP='\nvar reopenOp = action.Operation{Name: "reopen.v5", Role: "requester", Own: @OWN@, From: "rejected", To: "submitted"@EVENT@}\n'


def reopen_source(mode):
    return (REOPEN_GO.replace('@SUBJECT@','[]string{id, reason}' if mode=='reason-hash' else '[]string{id}')
            .replace('@REPLAY@',NO_REPLAY if mode=='no-replay' else REPLAY)
            .replace('@ELIGIBILITY@',DISTINGUISH if mode=='distinguish' else UNIFORM)
            # Under guarded RLS a FOR UPDATE read only sees rows the UPDATE policy admits, which already
            # hides other creators' and non-rejected rows; the natural distinguishing variant reads without it.
            .replace('@LOCK@','' if mode=='distinguish' else ' FOR UPDATE')
            .replace('@AUDIT@','' if mode=='omit-audit' else AUDIT)
            .replace('@EVENT@','' if mode=='omit-event' else EVENT))


def apply(candidate,cell,mode):
    if mode not in MODES[cell]: raise ValueError('unknown synthetic control')
    if mode=='unchanged': return
    root=Path(candidate)/'internal/candidate';approval=root/'approval.go';text=approval.read_text()
    route=ROUTES[cell];assert text.count(route)==1,'unexpected starting candidate'
    item='\treturn protocol.Item("reopen", "requester", reopen, '+route.removeprefix('\treturn ').removesuffix(', nil')+'), nil'
    if cell=='go-action' and mode in KERNEL:
        operations=root/'operations.go'
        operations.write_text(operations.read_text()+KERNEL_OP.replace('@OWN@','false' if mode=='own-false' else 'true')
                              .replace('@EVENT@','' if mode=='omit-event' else ', Event: "purchase.reopened"'))
        closure=KERNEL_ROUTE
    else:
        # Hand-written reopen: the reference for go-manual, a kernel bypass in go-action.
        (root/'reopen.go').write_text(reopen_source(mode))
        closure='\treopen := newReopen(pool)\n'
    approval.write_text(text.replace(route,closure+item))
