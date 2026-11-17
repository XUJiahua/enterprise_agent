-- v4 additive disposable POC schema, applied after the v3 Action schema.
-- A purchase can now be rejected; the Action metadata tables accept the reject operation.
ALTER TABLE "Purchase" DROP CONSTRAINT "Purchase_status_check";
ALTER TABLE "Purchase" ADD CONSTRAINT "Purchase_status_check" CHECK (status IN ('submitted','approved','rejected'));
ALTER TABLE "ActionRequest" DROP CONSTRAINT "ActionRequest_operation_check";
ALTER TABLE "ActionRequest" ADD CONSTRAINT "ActionRequest_operation_check" CHECK (operation IN ('bulk-approve.v3','bulk-reject.v4'));
ALTER TABLE "ApprovalAudit" DROP CONSTRAINT "ApprovalAudit_operation_check";
ALTER TABLE "ApprovalAudit" ADD CONSTRAINT "ApprovalAudit_operation_check" CHECK (operation IN ('bulk-approve.v3','bulk-reject.v4'));
ALTER TABLE "ApprovalAudit" DROP CONSTRAINT "ApprovalAudit_toStatus_check";
ALTER TABLE "ApprovalAudit" ADD CONSTRAINT "ApprovalAudit_toStatus_check" CHECK ("toStatus" IN ('approved','rejected'));
-- Replaces the single UPDATE policy (the guarded runtime requires exactly three Purchase policies).
DROP POLICY purchase_approve ON "Purchase";
CREATE POLICY purchase_decide ON "Purchase" FOR UPDATE TO app_runtime
 USING ("tenantId" = current_setting('app.tenant', true) AND current_setting('app.role', true) IN ('approver')
 AND "createdBy" <> current_setting('app.user', true) AND status = 'submitted')
 WITH CHECK ("tenantId" = current_setting('app.tenant', true) AND status IN ('approved','rejected'));
