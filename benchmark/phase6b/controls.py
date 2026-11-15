"""Human v6b candidate controls, excluded from exports and all model sample counts.

Each implicit-violation control breaks exactly one convention and must still pass the
whole explicit contract; explicit-violation controls must fail the explicit contract.
"""
from pathlib import Path

ROUTES={'go-manual':'\treturn protocol.Item("reopen", "requester", newReopen(pool), batches), nil',
        'go-action':'\treturn protocol.Item("reopen", "requester", reopen, batches), nil'}

# control -> (violated group or None, explicit contract expected to pass)
MODES={
 'go-manual':{'correct':(None,True),'omit-history':('implicit-history',True),'param-hash':('implicit-request-hash',True),
              'omit-event':('implicit-outbox',True),'distinguish':('implicit-uniform-denial',True),
              'no-replay':(None,False),'unchanged':(None,False)},
 'go-action':{'correct':(None,True),'no-event':('implicit-outbox',True),'omit-history':('implicit-history',True),
              'param-hash':('implicit-request-hash',True),'distinguish':('implicit-uniform-denial',True),
              'omit-event':(None,False),'own-true':(None,False),'unchanged':(None,False)}}
# Controls that use the kernel in go-action; the others bypass it with the hand-written reassign.
KERNEL={'correct','no-event','omit-event','own-true'}

REASSIGN_GO='''package candidate

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

const opReassign = "bulk-reassign.v6"

// Hand-written bulk reassign following the scattered existing conventions (human control).
func newReassign(pool *pgxpool.Pool) protocol.AssignHandler {
	return func(ctx context.Context, u app.Identity, key string, ids []string, assignee string) (protocol.AssignResult, bool, error) {
		for _, id := range append([]string{assignee}, ids...) {
			if strings.ContainsRune(id, 0) {
				return protocol.AssignResult{}, false, &protocol.Rejection{Status: 404}
			}
		}
		tx, err := pool.Begin(ctx)
		if err != nil {
			return protocol.AssignResult{}, false, err
		}
		defer tx.Rollback(ctx)
		if _, err = tx.Exec(ctx, `SELECT set_config('app.tenant',$1,true),set_config('app.user',$2,true),set_config('app.role',$3,true)`, u.Tenant, u.User, u.Role); err != nil {
			return protocol.AssignResult{}, false, err
		}
		lock, _ := json.Marshal([]string{opReassign, u.Tenant, u.User, key})
		if _, err = tx.Exec(ctx, `SELECT pg_advisory_xact_lock(hashtextextended($1,0))`, string(lock)); err != nil {
			return protocol.AssignResult{}, false, err
		}
		hash := @HASH@
		@REPLAY@
		@MEMBER@
		rows, err := tx.Query(ctx, `SELECT id,"assigneeId" FROM "Purchase" WHERE id=ANY($1::text[]) AND "tenantId"=$2 AND "createdBy"<>$3 AND status='submitted' ORDER BY id FOR UPDATE`, ids, u.Tenant, u.User)
		if err != nil {
			return protocol.AssignResult{}, false, err
		}
		previous := map[string]*string{}
		for rows.Next() {
			var id string
			var old *string
			if err = rows.Scan(&id, &old); err != nil {
				rows.Close()
				return protocol.AssignResult{}, false, err
			}
			previous[id] = old
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			return protocol.AssignResult{}, false, err
		}
		if len(previous) != len(ids) {
			return protocol.AssignResult{}, false, &protocol.Rejection{Status: 409}
		}
		tag, err := tx.Exec(ctx, `UPDATE "Purchase" SET "assigneeId"=$4 WHERE id=ANY($1::text[]) AND "tenantId"=$2 AND "createdBy"<>$3 AND status='submitted'`, ids, u.Tenant, u.User, assignee)
		if err != nil {
			return protocol.AssignResult{}, false, err
		}
		if tag.RowsAffected() != int64(len(ids)) {
			return protocol.AssignResult{}, false, &protocol.Rejection{Status: 409}
		}
		batch := &pgx.Batch{}
		for _, id := range ids {
			_ = id
			@HISTORY@
			@EVENT@
		}
		_ = previous
		result := protocol.AssignResult{Field: "reassignedIds", IDs: ids, Assignee: assignee}
		response, err := json.Marshal(result)
		if err != nil {
			return protocol.AssignResult{}, false, err
		}
		batch.Queue(`INSERT INTO "ActionRequest" ("tenantId","actorId",operation,key,"requestHash",response) VALUES ($1,$2,$3,$4,$5,$6::jsonb)`,
			u.Tenant, u.User, opReassign, key, hash, string(response))
		if err = tx.SendBatch(ctx, batch).Close(); err != nil {
			return protocol.AssignResult{}, false, err
		}
		if err = tx.Commit(ctx); err != nil {
			return protocol.AssignResult{}, false, err
		}
		return result, false, nil
	}
}
'''

HASH='ledger.Fingerprint(opReassign+"\\n"+assignee, ids)'
# Natural alternative: the parameter treated as one more subject element. Still distinguishes assignees.
PARAM_HASH='ledger.Fingerprint(opReassign, append(append([]string(nil), ids...), "assignee="+assignee))'
REPLAY='''var savedHash string
		var saved []byte
		err = tx.QueryRow(ctx, `SELECT "requestHash",response FROM "ActionRequest" WHERE "tenantId"=$1 AND "actorId"=$2 AND operation=$3 AND key=$4`,
			u.Tenant, u.User, opReassign, key).Scan(&savedHash, &saved)
		switch {
		case err == nil && savedHash != hash:
			return protocol.AssignResult{}, false, &protocol.Rejection{Status: 409}
		case err == nil:
			var body struct {
				IDs      []string `json:"reassignedIds"`
				Assignee string   `json:"assignee"`
			}
			if err = json.Unmarshal(saved, &body); err != nil {
				return protocol.AssignResult{}, false, err
			}
			return protocol.AssignResult{Field: "reassignedIds", IDs: body.IDs, Assignee: body.Assignee}, true, tx.Commit(ctx)
		case !errors.Is(err, pgx.ErrNoRows):
			return protocol.AssignResult{}, false, err
		}'''
NO_REPLAY='_ = errors.Is'
MEMBER='''var one int
		err = tx.QueryRow(ctx, `SELECT 1 FROM "TenantMember" WHERE "tenantId"=$1 AND "userId"=$2 AND role='approver' AND active`, u.Tenant, assignee).Scan(&one)
		if errors.Is(err, pgx.ErrNoRows) {
			return protocol.AssignResult{}, false, &protocol.Rejection{Status: 409}
		}
		if err != nil {
			return protocol.AssignResult{}, false, err
		}'''
DISTINGUISH='''var role string
		var active bool
		err = tx.QueryRow(ctx, `SELECT role,active FROM "TenantMember" WHERE "tenantId"=$1 AND "userId"=$2`, u.Tenant, assignee).Scan(&role, &active)
		switch {
		case errors.Is(err, pgx.ErrNoRows):
			return protocol.AssignResult{}, false, &protocol.Rejection{Status: 404}
		case err != nil:
			return protocol.AssignResult{}, false, err
		case role != "approver":
			return protocol.AssignResult{}, false, &protocol.Rejection{Status: 422}
		case !active:
			return protocol.AssignResult{}, false, &protocol.Rejection{Status: 409}
		}'''
HISTORY='''batch.Queue(`INSERT INTO "AssignmentHistory" ("tenantId","purchaseId","fromAssignee","toAssignee","changedBy",operation,"idempotencyKey") VALUES ($1,$2,$3,$4,$5,$6,$7)`,
				u.Tenant, id, previous[id], assignee, u.User, opReassign, key)'''
EVENT='''batch.Queue(`INSERT INTO "PurchaseEvent" ("tenantId","purchaseId",type,"actorId","idempotencyKey") VALUES ($1,$2,'purchase.reassigned',$3,$4)`,
				u.Tenant, id, u.User, key)'''

KERNEL_ROUTE='''	reassign := func(ctx context.Context, u app.Identity, key string, ids []string, assignee string) (protocol.AssignResult, bool, error) {
		done, replayed, err := executor.Set(ctx, u, reassignOp, key, ids, assignee)
		if err != nil {
			return protocol.AssignResult{}, false, adapt(err)
		}
		return protocol.AssignResult{Field: reassignOp.Field, IDs: done, Assignee: assignee}, replayed, nil
	}
'''
KERNEL_OP='''
var reassignOp = action.Update{Name: "bulk-reassign.v6", Role: "approver", Own: @OWN@,
	Status: "submitted", Column: "assigneeId", Field: "reassignedIds"@EVENT@}
'''
KERNEL_EVENT={'correct':', Event: "purchase.reassigned"','no-event':', Event: action.NoEvent','omit-event':'','own-true':', Event: "purchase.reassigned"'}


def reassign_source(mode):
    return (REASSIGN_GO.replace('@HASH@',PARAM_HASH if mode=='param-hash' else HASH)
            .replace('@REPLAY@',NO_REPLAY if mode=='no-replay' else REPLAY)
            .replace('@MEMBER@',DISTINGUISH if mode=='distinguish' else MEMBER)
            .replace('@HISTORY@','' if mode=='omit-history' else HISTORY)
            .replace('@EVENT@','' if mode=='omit-event' else EVENT))


def apply(candidate,cell,mode):
    if mode not in MODES[cell]: raise ValueError('unknown synthetic control')
    if mode=='unchanged': return
    root=Path(candidate)/'internal/candidate';approval=root/'approval.go';text=approval.read_text()
    route=ROUTES[cell];assert text.count(route)==1,'unexpected starting candidate'
    wrapped='\treturn protocol.Assign("/purchases/bulk-reassign", reassign, '+route.removeprefix('\treturn ').removesuffix(', nil')+'), nil'
    if cell=='go-action' and mode in KERNEL:
        operations=root/'operations.go'
        operations.write_text(operations.read_text()+KERNEL_OP.replace('@OWN@','true' if mode=='own-true' else 'false')
                              .replace('@EVENT@',KERNEL_EVENT[mode]))
        closure=KERNEL_ROUTE
    else:
        # Hand-written reassign: the reference for go-manual, a kernel bypass in go-action.
        (root/'reassign.go').write_text(reassign_source(mode))
        closure='\treassign := newReassign(pool)\n'
    approval.write_text(text.replace(route,closure+wrapped))
