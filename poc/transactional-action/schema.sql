-- Additive disposable POC schema. Not compiled by the Purchase policy compiler.
CREATE UNIQUE INDEX purchase_tenant_id_unique ON "Purchase" ("tenantId", id);
CREATE TABLE "ActionRequest" (
  "tenantId" text NOT NULL,
  "actorId" text NOT NULL,
  operation text NOT NULL CHECK (operation='bulk-approve.v3'),
  key text NOT NULL CHECK (length(key) BETWEEN 1 AND 128),
  "requestHash" text NOT NULL CHECK ("requestHash" ~ '^[0-9a-f]{64}$'),
  response jsonb NOT NULL,
  "createdAt" timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("tenantId","actorId",operation,key)
);
CREATE TABLE "ApprovalAudit" (
  "tenantId" text NOT NULL,
  "purchaseId" text NOT NULL,
  "approvedBy" text NOT NULL,
  operation text NOT NULL CHECK (operation='bulk-approve.v3'),
  "idempotencyKey" text NOT NULL,
  "fromStatus" text NOT NULL CHECK ("fromStatus"='submitted'),
  "toStatus" text NOT NULL CHECK ("toStatus"='approved'),
  "createdAt" timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY ("tenantId","purchaseId"),
  FOREIGN KEY ("tenantId","purchaseId") REFERENCES "Purchase" ("tenantId",id),
  FOREIGN KEY ("tenantId","approvedBy",operation,"idempotencyKey")
    REFERENCES "ActionRequest" ("tenantId","actorId",operation,key) DEFERRABLE INITIALLY DEFERRED
);
ALTER TABLE "ActionRequest" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "ActionRequest" FORCE ROW LEVEL SECURITY;
ALTER TABLE "ApprovalAudit" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "ApprovalAudit" FORCE ROW LEVEL SECURITY;
REVOKE ALL ON "ActionRequest","ApprovalAudit" FROM app_runtime;
GRANT SELECT,INSERT ON "ActionRequest","ApprovalAudit" TO app_runtime;
CREATE POLICY action_request_read ON "ActionRequest" FOR SELECT TO app_runtime USING
 ("tenantId"=current_setting('app.tenant',true) AND "actorId"=current_setting('app.user',true) AND current_setting('app.role',true)='approver');
CREATE POLICY action_request_insert ON "ActionRequest" FOR INSERT TO app_runtime WITH CHECK
 ("tenantId"=current_setting('app.tenant',true) AND "actorId"=current_setting('app.user',true) AND current_setting('app.role',true)='approver');
CREATE POLICY approval_audit_read ON "ApprovalAudit" FOR SELECT TO app_runtime USING
 ("tenantId"=current_setting('app.tenant',true) AND current_setting('app.role',true)='approver');
CREATE POLICY approval_audit_insert ON "ApprovalAudit" FOR INSERT TO app_runtime WITH CHECK
 ("tenantId"=current_setting('app.tenant',true) AND "approvedBy"=current_setting('app.user',true) AND current_setting('app.role',true)='approver');
