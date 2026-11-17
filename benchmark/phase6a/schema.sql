-- v6a additive disposable POC schema, applied after the v5 schema.
-- Purchases above 50000 need two different approvers: the first approval only moves them to pending-second.
ALTER TABLE "Purchase" DROP CONSTRAINT "Purchase_status_check";
ALTER TABLE "Purchase" ADD CONSTRAINT "Purchase_status_check" CHECK (status IN ('submitted','approved','rejected','pending-second'));
ALTER TABLE "ActionRequest" DROP CONSTRAINT "ActionRequest_operation_check";
ALTER TABLE "ActionRequest" ADD CONSTRAINT "ActionRequest_operation_check" CHECK (operation IN ('bulk-approve.v3','bulk-reject.v4','reopen.v5','countersign.v6'));
ALTER TABLE "ApprovalAudit" DROP CONSTRAINT "ApprovalAudit_operation_check";
ALTER TABLE "ApprovalAudit" ADD CONSTRAINT "ApprovalAudit_operation_check" CHECK (operation IN ('bulk-approve.v3','bulk-reject.v4','reopen.v5','countersign.v6'));
ALTER TABLE "ApprovalAudit" DROP CONSTRAINT "ApprovalAudit_fromStatus_check";
ALTER TABLE "ApprovalAudit" ADD CONSTRAINT "ApprovalAudit_fromStatus_check" CHECK ("fromStatus" IN ('submitted','rejected','pending-second'));
ALTER TABLE "ApprovalAudit" DROP CONSTRAINT "ApprovalAudit_toStatus_check";
ALTER TABLE "ApprovalAudit" ADD CONSTRAINT "ApprovalAudit_toStatus_check" CHECK ("toStatus" IN ('approved','rejected','submitted','pending-second'));
-- Replaces the v5 transition policy (the guarded runtime requires exactly three Purchase policies).
DROP POLICY purchase_transition ON "Purchase";
CREATE POLICY purchase_transition ON "Purchase" FOR UPDATE TO app_runtime
 USING ("tenantId" = current_setting('app.tenant', true) AND (
   (current_setting('app.role', true) = 'approver' AND "createdBy" <> current_setting('app.user', true) AND status IN ('submitted','pending-second'))
   OR (current_setting('app.role', true) = 'requester' AND "createdBy" = current_setting('app.user', true) AND status = 'rejected')))
 WITH CHECK ("tenantId" = current_setting('app.tenant', true) AND (
   (current_setting('app.role', true) = 'approver' AND status IN ('approved','rejected','pending-second'))
   OR (current_setting('app.role', true) = 'requester' AND "createdBy" = current_setting('app.user', true) AND status = 'submitted')));
-- Platform guard for every writer, including the legacy per-purchase approve: the tier and the
-- second approver are enforced on the row transition, so a mistaken handler fails instead of approving.
CREATE OR REPLACE FUNCTION purchase_tier_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.status = OLD.status THEN RETURN NEW; END IF;
 IF (OLD.status = 'submitted' AND NEW.status = 'approved' AND NEW.amount > 50000)
  OR (NEW.status = 'pending-second' AND (OLD.status <> 'submitted' OR NEW.amount <= 50000))
  OR (OLD.status = 'pending-second' AND (NEW.status <> 'approved' OR current_setting('app.user', true) IS NOT DISTINCT FROM
      (SELECT a."approvedBy" FROM "ApprovalAudit" a WHERE a."tenantId" = OLD."tenantId" AND a."purchaseId" = OLD.id
        AND a."toStatus" = 'pending-second' ORDER BY a."createdAt" DESC LIMIT 1))) THEN
  RAISE EXCEPTION 'two-level approval transition refused' USING ERRCODE = 'P0001';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER purchase_tier_guard BEFORE UPDATE OF status ON "Purchase" FOR EACH ROW EXECUTE FUNCTION purchase_tier_guard();
