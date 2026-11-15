-- v6b additive disposable POC schema, applied after the v3 Action, v4 reject and v5 reopen schemas.
-- Tenant members, and an assignee per purchase that approvers may change while it is submitted.
CREATE TABLE "TenantMember" (
  "tenantId" text NOT NULL,
  "userId" text NOT NULL,
  role text NOT NULL CHECK (role IN ('approver','requester')),
  active boolean NOT NULL,
  PRIMARY KEY ("tenantId","userId")
);
ALTER TABLE "TenantMember" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "TenantMember" FORCE ROW LEVEL SECURITY;
REVOKE ALL ON "TenantMember" FROM app_runtime;
GRANT SELECT ON "TenantMember" TO app_runtime;
CREATE POLICY tenant_member_read ON "TenantMember" FOR SELECT TO app_runtime USING
 ("tenantId"=current_setting('app.tenant',true) AND current_setting('app.role',true) IN ('approver','requester'));
INSERT INTO "TenantMember" ("tenantId","userId",role,active) VALUES
 ('tenant-a','alice','requester',true),('tenant-a','bob','approver',true),('tenant-a','carol','approver',true),
 ('tenant-a','hana','approver',true),('tenant-a','frank','approver',false),
 ('tenant-b','dave','requester',true),('tenant-b','erin','approver',true),('tenant-b','ivan','approver',true);
ALTER TABLE "Purchase" ADD COLUMN "assigneeId" text;
ALTER TABLE "Purchase" ADD FOREIGN KEY ("tenantId","assigneeId") REFERENCES "TenantMember" ("tenantId","userId");
GRANT SELECT ("assigneeId") ON "Purchase" TO app_runtime;
GRANT UPDATE ("assigneeId") ON "Purchase" TO app_runtime;
CREATE TABLE "AssignmentHistory" (
  "tenantId" text NOT NULL,
  "purchaseId" text NOT NULL,
  "fromAssignee" text,
  "toAssignee" text NOT NULL,
  "changedBy" text NOT NULL,
  operation text NOT NULL,
  "idempotencyKey" text NOT NULL,
  "createdAt" timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY ("tenantId","purchaseId","changedBy",operation,"idempotencyKey"),
  FOREIGN KEY ("tenantId","purchaseId") REFERENCES "Purchase" ("tenantId",id)
);
ALTER TABLE "AssignmentHistory" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "AssignmentHistory" FORCE ROW LEVEL SECURITY;
REVOKE ALL ON "AssignmentHistory" FROM app_runtime;
GRANT SELECT,INSERT ON "AssignmentHistory" TO app_runtime;
CREATE POLICY assignment_history_read ON "AssignmentHistory" FOR SELECT TO app_runtime USING
 ("tenantId"=current_setting('app.tenant',true) AND current_setting('app.role',true) IN ('approver','requester'));
CREATE POLICY assignment_history_insert ON "AssignmentHistory" FOR INSERT TO app_runtime WITH CHECK
 ("tenantId"=current_setting('app.tenant',true) AND "changedBy"=current_setting('app.user',true) AND current_setting('app.role',true)='approver');
ALTER TABLE "ActionRequest" DROP CONSTRAINT "ActionRequest_operation_check";
ALTER TABLE "ActionRequest" ADD CONSTRAINT "ActionRequest_operation_check" CHECK (operation IN ('bulk-approve.v3','bulk-reject.v4','reopen.v5','bulk-reassign.v6'));
-- Replaces the v5 transition policy (the guarded runtime requires exactly three Purchase policies):
-- an approver may also keep a submitted purchase submitted while changing its assignee.
DROP POLICY purchase_transition ON "Purchase";
CREATE POLICY purchase_transition ON "Purchase" FOR UPDATE TO app_runtime
 USING ("tenantId" = current_setting('app.tenant', true) AND (
   (current_setting('app.role', true) = 'approver' AND "createdBy" <> current_setting('app.user', true) AND status = 'submitted')
   OR (current_setting('app.role', true) = 'requester' AND "createdBy" = current_setting('app.user', true) AND status = 'rejected')))
 WITH CHECK ("tenantId" = current_setting('app.tenant', true) AND (
   (current_setting('app.role', true) = 'approver' AND status IN ('approved','rejected','submitted'))
   OR (current_setting('app.role', true) = 'requester' AND "createdBy" = current_setting('app.user', true) AND status = 'submitted')));
