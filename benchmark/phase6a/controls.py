"""Human v6a candidate controls, excluded from exports and all model sample counts.

Each implicit-violation control breaks exactly one convention and must still pass the
whole explicit contract; explicit-violation controls must fail the explicit contract.
"""
from pathlib import Path

ROUTES={'go-manual':'\treturn protocol.Item("reopen", "requester", newReopen(pool), protocol.HTTP(protocol.Routes{"/purchases/bulk-approve": approve, "/purchases/bulk-reject": newReject(pool)}, fallback)), nil',
        'go-action':'\treturn protocol.Item("reopen", "requester", reopen, protocol.HTTP(protocol.Routes{"/purchases/bulk-approve": batch(executor, approveOp), "/purchases/bulk-reject": batch(executor, rejectOp)}, fallback)), nil'}

# control -> (violated group or None, explicit contract expected to pass)
MODES={
 'go-manual':{'correct':(None,True),'omit-audit':('implicit-audit',True),'amount-hash':('implicit-request-hash',True),
              'omit-event':('implicit-outbox',True),'distinguish':('implicit-uniform-denial',True),
              'no-separation':(None,False),'unchanged':(None,False)},
 'go-action':{'correct':(None,True),'omit-audit':('implicit-audit',True),'amount-hash':('implicit-request-hash',True),
              'no-event':('implicit-outbox',True),'distinguish':('implicit-uniform-denial',True),
              'empty-event':(None,False),'no-separate':(None,False),'unchanged':(None,False)}}
# Controls that use the kernel in go-action; the others bypass it with the hand-written countersign.
KERNEL={'correct','no-event','empty-event','no-separate'}

COUNTERSIGN_GO='''package candidate

import (
	"context"
	"encoding/json"
	"errors"
	"strconv"

	"github.com/XUJiahua/enterprise_agent/internal/app"
	"github.com/XUJiahua/enterprise_agent/internal/ledger"
	"github.com/XUJiahua/enterprise_agent/internal/protocol"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

const opCountersign = "countersign.v6"

var _ = strconv.Itoa

func newCountersign(pool *pgxpool.Pool) protocol.ConfirmHandler {
	return func(ctx context.Context, u app.Identity, key, id string, amount int) (protocol.ItemResult, bool, error) {
		tx, err := pool.Begin(ctx)
		if err != nil {
			return protocol.ItemResult{}, false, err
		}
		defer tx.Rollback(ctx)
		if _, err = tx.Exec(ctx, `SELECT set_config('app.tenant',$1,true),set_config('app.user',$2,true),set_config('app.role',$3,true)`, u.Tenant, u.User, u.Role); err != nil {
			return protocol.ItemResult{}, false, err
		}
		lock, _ := json.Marshal([]string{opCountersign, u.Tenant, u.User, key})
		if _, err = tx.Exec(ctx, `SELECT pg_advisory_xact_lock(hashtextextended($1,0))`, string(lock)); err != nil {
			return protocol.ItemResult{}, false, err
		}
		hash := ledger.Fingerprint(opCountersign, @SUBJECT@)
		var savedHash string
		var saved []byte
		err = tx.QueryRow(ctx, `SELECT "requestHash",response FROM "ActionRequest" WHERE "tenantId"=$1 AND "actorId"=$2 AND operation=$3 AND key=$4`,
			u.Tenant, u.User, opCountersign, key).Scan(&savedHash, &saved)
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
		}
		var creator, status, first string
		var current int
		err = tx.QueryRow(ctx, `SELECT p."createdBy",p.status,p.amount,COALESCE((SELECT a."approvedBy" FROM "ApprovalAudit" a
		 WHERE a."tenantId"=p."tenantId" AND a."purchaseId"=p.id AND a."toStatus"='pending-second' ORDER BY a."createdAt" DESC LIMIT 1),'')
		 FROM "Purchase" p WHERE p.id=$1 AND p."tenantId"=$2@LOCK@`, id, u.Tenant).Scan(&creator, &status, &current, &first)
		@ELIGIBILITY@
		tag, err := tx.Exec(ctx, `UPDATE "Purchase" SET status='approved' WHERE id=$1 AND "tenantId"=$2 AND "createdBy"<>$3 AND status='pending-second'`, id, u.Tenant, u.User)
		if err != nil {
			return protocol.ItemResult{}, false, err
		}
		if tag.RowsAffected() != 1 {
			return protocol.ItemResult{}, false, &protocol.Rejection{Status: 409}
		}
		@AUDIT@
		@EVENT@
		result := protocol.ItemResult{ID: id, Status: "approved"}
		response, err := json.Marshal(result)
		if err != nil {
			return protocol.ItemResult{}, false, err
		}
		if _, err = tx.Exec(ctx, `INSERT INTO "ActionRequest" ("tenantId","actorId",operation,key,"requestHash",response) VALUES ($1,$2,$3,$4,$5,$6::jsonb)`,
			u.Tenant, u.User, opCountersign, key, hash, string(response)); err != nil {
			return protocol.ItemResult{}, false, err
		}
		if err = tx.Commit(ctx); err != nil {
			return protocol.ItemResult{}, false, err
		}
		return result, false, nil
	}
}
'''

UNIFORM='''if errors.Is(err, pgx.ErrNoRows) || (err == nil && (creator == u.User || status != "pending-second" || first == u.User@SEPARATION@ || current != amount)) {
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
		case creator == u.User || first == u.User:
			return protocol.ItemResult{}, false, &protocol.Rejection{Status: 403}
		case status != "pending-second" || current != amount:
			return protocol.ItemResult{}, false, &protocol.Rejection{Status: 409}
		}'''
AUDIT='''if _, err = tx.Exec(ctx, `INSERT INTO "ApprovalAudit" ("tenantId","purchaseId","approvedBy",operation,"idempotencyKey","fromStatus","toStatus") VALUES ($1,$2,$3,$4,$5,'pending-second','approved')`,
			u.Tenant, id, u.User, opCountersign, key); err != nil {
			return protocol.ItemResult{}, false, err
		}'''
EVENT='''if _, err = tx.Exec(ctx, `INSERT INTO "PurchaseEvent" ("tenantId","purchaseId",type,"actorId","idempotencyKey") VALUES ($1,$2,'purchase.countersigned',$3,$4)`,
			u.Tenant, id, u.User, key); err != nil {
			return protocol.ItemResult{}, false, err
		}'''

KERNEL_ROUTE='''	countersign := func(ctx context.Context, u app.Identity, key, id string, amount int) (protocol.ItemResult, bool, error) {
		done, replayed, err := executor.Run(ctx, u, countersignOp, key, action.Request{IDs: []string{id}, Amount: amount})
		if err != nil {
			return protocol.ItemResult{}, false, adapt(err)
		}
		return protocol.ItemResult{ID: done[0], Status: countersignOp.To}, replayed, nil
	}
'''
KERNEL_OP='''
var countersignOp = action.Operation{Name: "countersign.v6", Role: "approver", Own: false,
	From: "pending-second", To: "approved"@SEPARATE@, Confirm: true@EVENT@}
'''


def countersign_source(mode):
    return (COUNTERSIGN_GO.replace('@SUBJECT@','[]string{id, strconv.Itoa(amount)}' if mode=='amount-hash' else '[]string{id}')
            .replace('@ELIGIBILITY@',DISTINGUISH if mode=='distinguish' else UNIFORM)
            # Without SoD the guard trigger refuses the row transition (503): an explicit failure, never an approval.
            .replace('@SEPARATION@',' && false' if mode=='no-separation' else '')
            # A locked read sees only rows the UPDATE policy admits; the natural distinguishing variant reads without it.
            .replace('@LOCK@','' if mode=='distinguish' else ' FOR UPDATE OF p')
            .replace('@AUDIT@','' if mode=='omit-audit' else AUDIT)
            .replace('@EVENT@','' if mode=='omit-event' else EVENT))


def kernel_op(mode):
    event={'no-event':', Event: action.NoEvent','empty-event':''}.get(mode,', Event: "purchase.countersigned"')
    return KERNEL_OP.replace('@SEPARATE@','' if mode=='no-separate' else ', Separate: true').replace('@EVENT@',event)


def apply(candidate,cell,mode):
    if mode not in MODES[cell]: raise ValueError('unknown synthetic control')
    if mode=='unchanged': return
    root=Path(candidate)/'internal/candidate';approval=root/'approval.go';text=approval.read_text()
    route=ROUTES[cell];assert text.count(route)==1,'unexpected starting candidate'
    item='\treturn protocol.Confirm("countersign", "approver", countersign, '+route.removeprefix('\treturn ').removesuffix(', nil')+'), nil'
    if cell=='go-action' and mode in KERNEL:
        operations=root/'operations.go'
        operations.write_text(operations.read_text()+kernel_op(mode))
        closure=KERNEL_ROUTE
    else:
        # Hand-written countersign: the reference for go-manual, a kernel bypass in go-action.
        (root/'countersign.go').write_text(countersign_source(mode))
        closure='\tcountersign := newCountersign(pool)\n'
    approval.write_text(text.replace(route,closure+item))
