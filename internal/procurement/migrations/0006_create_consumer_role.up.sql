-- 1. procurement.create@1: the application role may insert new purchases
--    (status, internal_note keep their defaults).
-- 2. Role separation: only the consumer role writes the inbox and
--    notifications. Prerequisite, created once per cluster by the operator: a
--    NOLOGIN group role enterprise_consumer, granted to the consumer login role
--    (non-owner, NOSUPERUSER, NOBYPASSRLS). The application role keeps SELECT
--    on notifications for GET /v1/activity.
GRANT INSERT (tenant_id, id, created_by, title, amount) ON procurement.purchase TO enterprise_runtime;

REVOKE INSERT ON kernel.inbox FROM enterprise_runtime;
REVOKE INSERT ON procurement.notification FROM enterprise_runtime;
REVOKE EXECUTE ON FUNCTION kernel.pending_tenants(text, text[]) FROM enterprise_runtime;

GRANT USAGE ON SCHEMA kernel, procurement TO enterprise_consumer;
GRANT SELECT ON kernel.outbox TO enterprise_consumer;
GRANT SELECT, INSERT ON kernel.inbox TO enterprise_consumer;
GRANT EXECUTE ON FUNCTION kernel.pending_tenants(text, text[]) TO enterprise_consumer;
GRANT SELECT (tenant_id, id, created_by) ON procurement.purchase TO enterprise_consumer;
GRANT INSERT (tenant_id, event_id, recipient, purchase_id, kind) ON procurement.notification TO enterprise_consumer;
