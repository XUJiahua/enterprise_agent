-- Goods receipts (append-only) and the closed status. A purchase may be closed
-- once its receipts sum to at least its amount; after that no receipt is added.
-- The module enforces this with the parent-lock protocol (procurement.go).
ALTER TABLE procurement.purchase DROP CONSTRAINT purchase_status_check,
  ADD CONSTRAINT purchase_status_check CHECK (status IN ('submitted', 'approved', 'closed'));

CREATE TABLE procurement.receipt (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id text NOT NULL,
  purchase_id text NOT NULL,
  amount bigint NOT NULL CHECK (amount > 0),
  received_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (tenant_id, purchase_id) REFERENCES procurement.purchase
);
CREATE INDEX receipt_purchase_idx ON procurement.receipt (tenant_id, purchase_id);

ALTER TABLE procurement.receipt ENABLE ROW LEVEL SECURITY, FORCE ROW LEVEL SECURITY;
CREATE POLICY allow ON procurement.receipt USING (true) WITH CHECK (true);
CREATE POLICY tenant_floor ON procurement.receipt AS RESTRICTIVE
  USING (tenant_id = current_setting('app.tenant', true))
  WITH CHECK (tenant_id = current_setting('app.tenant', true));

GRANT SELECT, INSERT (tenant_id, purchase_id, amount, received_by) ON procurement.receipt TO enterprise_runtime;
