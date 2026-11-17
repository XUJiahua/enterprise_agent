-- Procurement tables for the v0.1 product. Runs after 0001_kernel (a verbatim
-- copy of kernel/pg/schema.sql). Prerequisite, created once per cluster by the
-- operator: a NOLOGIN group role enterprise_runtime, granted to the application
-- login role (non-owner, NOSUPERUSER, NOBYPASSRLS).
CREATE SCHEMA procurement;

CREATE TABLE procurement.purchase (
  tenant_id text NOT NULL,
  id text NOT NULL CHECK (length(id) BETWEEN 1 AND 100),
  created_by text NOT NULL,
  title text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
  amount bigint NOT NULL CHECK (amount > 0),
  status text NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted', 'approved')),
  internal_note text NOT NULL DEFAULT '',
  PRIMARY KEY (tenant_id, id)
);

ALTER TABLE procurement.purchase ENABLE ROW LEVEL SECURITY, FORCE ROW LEVEL SECURITY;
CREATE POLICY allow ON procurement.purchase USING (true) WITH CHECK (true);
-- RESTRICTIVE: ANDed with every permissive policy, so adding one cannot widen it.
CREATE POLICY tenant_floor ON procurement.purchase AS RESTRICTIVE
  USING (tenant_id = current_setting('app.tenant', true))
  WITH CHECK (tenant_id = current_setting('app.tenant', true));

GRANT USAGE ON SCHEMA kernel, procurement TO enterprise_runtime;
GRANT SELECT, INSERT ON kernel.request, kernel.audit, kernel.outbox TO enterprise_runtime;
-- internal_note is deliberately not readable; only status is writable.
GRANT SELECT (tenant_id, id, created_by, title, amount, status) ON procurement.purchase TO enterprise_runtime;
GRANT UPDATE (status) ON procurement.purchase TO enterprise_runtime;
