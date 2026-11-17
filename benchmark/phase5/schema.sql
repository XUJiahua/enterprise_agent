-- v5 additive disposable POC schema, applied after the v3 Action schema and the v4 reject schema.
-- A requester may reopen a rejected purchase; audit rows may repeat per purchase.
ALTER TABLE "ActionRequest" DROP CONSTRAINT "ActionRequest_operation_check";
ALTER TABLE "ActionRequest" ADD CONSTRAINT "ActionRequest_operation_check" CHECK (operation IN ('bulk-approve.v3','bulk-reject.v4','reopen.v5'));
ALTER TABLE "ApprovalAudit" DROP CONSTRAINT "ApprovalAudit_operation_check";
ALTER TABLE "ApprovalAudit" ADD CONSTRAINT "ApprovalAudit_operation_check" CHECK (operation IN ('bulk-approve.v3','bulk-reject.v4','reopen.v5'));
ALTER TABLE "ApprovalAudit" DROP CONSTRAINT "ApprovalAudit_fromStatus_check";
ALTER TABLE "ApprovalAudit" ADD CONSTRAINT "ApprovalAudit_fromStatus_check" CHECK ("fromStatus" IN ('submitted','rejected'));
ALTER TABLE "ApprovalAudit" DROP CONSTRAINT "ApprovalAudit_toStatus_check";
ALTER TABLE "ApprovalAudit" ADD CONSTRAINT "ApprovalAudit_toStatus_check" CHECK ("toStatus" IN ('approved','rejected','submitted'));
ALTER TABLE "ApprovalAudit" ADD COLUMN reason text CHECK (reason IS NULL OR length(reason) BETWEEN 1 AND 200);
ALTER TABLE "ApprovalAudit" DROP CONSTRAINT "ApprovalAudit_pkey";
ALTER TABLE "ApprovalAudit" ADD PRIMARY KEY ("tenantId","purchaseId","approvedBy",operation,"idempotencyKey");
CREATE TABLE "PurchaseEvent" (
  "tenantId" text NOT NULL,
  "purchaseId" text NOT NULL,
  type text NOT NULL,
  "actorId" text NOT NULL,
  "idempotencyKey" text NOT NULL,
  "createdAt" timestamptz NOT NULL DEFAULT now(),
  "deliveredAt" timestamptz,
  PRIMARY KEY ("tenantId","purchaseId",type,"actorId","idempotencyKey"),
  FOREIGN KEY ("tenantId","purchaseId") REFERENCES "Purchase" ("tenantId",id)
);
ALTER TABLE "PurchaseEvent" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "PurchaseEvent" FORCE ROW LEVEL SECURITY;
REVOKE ALL ON "PurchaseEvent" FROM app_runtime;
GRANT SELECT,INSERT ON "PurchaseEvent" TO app_runtime;
CREATE POLICY purchase_event_read ON "PurchaseEvent" FOR SELECT TO app_runtime USING
 ("tenantId"=current_setting('app.tenant',true) AND current_setting('app.role',true) IN ('approver','requester'));
CREATE POLICY purchase_event_insert ON "PurchaseEvent" FOR INSERT TO app_runtime WITH CHECK
 ("tenantId"=current_setting('app.tenant',true) AND "actorId"=current_setting('app.user',true) AND current_setting('app.role',true) IN ('approver','requester'));
DROP POLICY action_request_read ON "ActionRequest";
DROP POLICY action_request_insert ON "ActionRequest";
CREATE POLICY action_request_read ON "ActionRequest" FOR SELECT TO app_runtime USING
 ("tenantId"=current_setting('app.tenant',true) AND "actorId"=current_setting('app.user',true) AND current_setting('app.role',true) IN ('approver','requester'));
CREATE POLICY action_request_insert ON "ActionRequest" FOR INSERT TO app_runtime WITH CHECK
 ("tenantId"=current_setting('app.tenant',true) AND "actorId"=current_setting('app.user',true) AND current_setting('app.role',true) IN ('approver','requester'));
DROP POLICY approval_audit_insert ON "ApprovalAudit";
CREATE POLICY approval_audit_insert ON "ApprovalAudit" FOR INSERT TO app_runtime WITH CHECK
 ("tenantId"=current_setting('app.tenant',true) AND "approvedBy"=current_setting('app.user',true) AND current_setting('app.role',true) IN ('approver','requester'));
-- Replaces the v4 decide policy (the guarded runtime requires exactly three Purchase policies).
DROP POLICY purchase_decide ON "Purchase";
CREATE POLICY purchase_transition ON "Purchase" FOR UPDATE TO app_runtime
 USING ("tenantId" = current_setting('app.tenant', true) AND (
   (current_setting('app.role', true) = 'approver' AND "createdBy" <> current_setting('app.user', true) AND status = 'submitted')
   OR (current_setting('app.role', true) = 'requester' AND "createdBy" = current_setting('app.user', true) AND status = 'rejected')))
 WITH CHECK ("tenantId" = current_setting('app.tenant', true) AND (
   (current_setting('app.role', true) = 'approver' AND status IN ('approved','rejected'))
   OR (current_setting('app.role', true) = 'requester' AND "createdBy" = current_setting('app.user', true) AND status = 'submitted')));
