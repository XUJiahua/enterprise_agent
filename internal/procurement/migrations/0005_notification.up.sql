-- Notification projection written by the procurement.notify consumer in the
-- same transaction as its kernel.inbox row. event_id is unique, so a repeated
-- delivery can never add a second notification.
CREATE TABLE procurement.notification (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id text NOT NULL,
  event_id bigint NOT NULL UNIQUE REFERENCES kernel.outbox,
  recipient text NOT NULL,
  purchase_id text NOT NULL,
  kind text NOT NULL CHECK (kind IN ('closed')),
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (tenant_id, purchase_id) REFERENCES procurement.purchase
);
ALTER TABLE procurement.notification ENABLE ROW LEVEL SECURITY, FORCE ROW LEVEL SECURITY;
CREATE POLICY allow ON procurement.notification USING (true) WITH CHECK (true);
CREATE POLICY tenant_floor ON procurement.notification AS RESTRICTIVE
  USING (tenant_id = current_setting('app.tenant', true))
  WITH CHECK (tenant_id = current_setting('app.tenant', true));

GRANT SELECT, INSERT ON kernel.inbox TO enterprise_runtime;
GRANT EXECUTE ON FUNCTION kernel.pending_tenants(text, text[]) TO enterprise_runtime;
GRANT SELECT, INSERT (tenant_id, event_id, recipient, purchase_id, kind) ON procurement.notification TO enterprise_runtime;
