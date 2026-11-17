-- Consumer inbox, applied after schema.sql. A consumer handles an outbox event
-- and inserts (consumer, event_id) here in one transaction, so a crash before
-- commit leaves the event pending and a repeat delivery is skipped. Grant the
-- runtime role:
--   GRANT SELECT, INSERT ON kernel.inbox TO <runtime>;
--   GRANT EXECUTE ON FUNCTION kernel.pending_tenants(text, text[]) TO <runtime>;
-- kernel.pending_tenants is SECURITY DEFINER so the runtime role can find which
-- tenants have pending events without reading other tenants' rows. Its owner
-- (the migration role) must be superuser or BYPASSRLS, because the kernel tables
-- FORCE row level security even for their owner.
CREATE TABLE kernel.inbox (
  consumer text NOT NULL,
  event_id bigint NOT NULL REFERENCES kernel.outbox,
  tenant_id text NOT NULL,
  handled_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (consumer, event_id)
);
ALTER TABLE kernel.inbox ENABLE ROW LEVEL SECURITY, FORCE ROW LEVEL SECURITY;
CREATE POLICY allow ON kernel.inbox USING (true) WITH CHECK (true);
CREATE POLICY tenant_floor ON kernel.inbox AS RESTRICTIVE
  USING (tenant_id = current_setting('app.tenant', true))
  WITH CHECK (tenant_id = current_setting('app.tenant', true));

CREATE FUNCTION kernel.pending_tenants(text, text[]) RETURNS SETOF text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
  SELECT DISTINCT o.tenant_id FROM kernel.outbox o
  WHERE o.event_type = ANY ($2)
    AND NOT EXISTS (SELECT 1 FROM kernel.inbox i WHERE i.consumer = $1 AND i.event_id = o.id)
$$;
REVOKE ALL ON FUNCTION kernel.pending_tenants(text, text[]) FROM PUBLIC;
