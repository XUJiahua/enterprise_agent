CREATE TABLE "Purchase" (
  id text PRIMARY KEY,
  "tenantId" text NOT NULL,
  "createdBy" text NOT NULL,
  title text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
  amount integer NOT NULL CHECK (amount > 0),
  status text NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted', 'approved')),
  "internalNote" text NOT NULL DEFAULT 'supplier-bank-account'
);
CREATE INDEX purchase_tenant_idx ON "Purchase" ("tenantId", id);
