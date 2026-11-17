-- Kernel-owned tables. Apply as part of the host's migrations, then grant the
-- runtime role (non-owner, no SUPERUSER/BYPASSRLS):
--   GRANT USAGE ON SCHEMA kernel TO <runtime>;
--   GRANT SELECT, INSERT ON kernel.request, kernel.audit, kernel.outbox TO <runtime>;
-- Rows are append-only; the tenant floor is a RESTRICTIVE policy, so a later
-- permissive policy cannot widen it. app.tenant is set transaction-locally by
-- the runner; without it no row is visible or insertable.
CREATE SCHEMA kernel;

CREATE TABLE kernel.request (
  tenant_id text NOT NULL,
  actor_id text NOT NULL,
  operation text NOT NULL,
  version integer NOT NULL,
  key text NOT NULL,
  request_hash text NOT NULL,
  targets text[] NOT NULL,
  result json NOT NULL, -- json, not jsonb: a replay returns the exact bytes
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, actor_id, operation, version, key)
);

CREATE TABLE kernel.audit (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id text NOT NULL,
  actor_id text NOT NULL,
  operation text NOT NULL,
  version integer NOT NULL,
  key text NOT NULL,
  target_id text NOT NULL,
  change jsonb NOT NULL CHECK (jsonb_typeof(change) = 'object'),
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (tenant_id, actor_id, operation, version, key) REFERENCES kernel.request
);

CREATE TABLE kernel.outbox (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id text NOT NULL,
  actor_id text NOT NULL,
  operation text NOT NULL,
  version integer NOT NULL,
  key text NOT NULL,
  event_type text NOT NULL,
  event_version integer NOT NULL,
  target_id text NOT NULL,
  payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (tenant_id, actor_id, operation, version, key) REFERENCES kernel.request
);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['request', 'audit', 'outbox'] LOOP
    EXECUTE format('ALTER TABLE kernel.%I ENABLE ROW LEVEL SECURITY, FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY allow ON kernel.%I USING (true) WITH CHECK (true)', t);
    EXECUTE format($p$CREATE POLICY tenant_floor ON kernel.%I AS RESTRICTIVE
      USING (tenant_id = current_setting('app.tenant', true))
      WITH CHECK (tenant_id = current_setting('app.tenant', true))$p$, t);
  END LOOP;
END $$;
