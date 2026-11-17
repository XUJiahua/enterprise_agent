#!/usr/bin/env bash
# End-to-end smoke for the v0.1 product: empty database -> appctl migrate up
# (twice) -> bin/product -> receive, close, replay, list over HTTP.
# Needs a disposable PostgreSQL superuser URL in KERNEL_TEST_ADMIN_URL and
# built bin/product and bin/appctl. Creates and drops database product_smoke.
set -euo pipefail
: "${KERNEL_TEST_ADMIN_URL:?}"
root=$(cd "$(dirname "$0")/.." && pwd)
admin_db="${KERNEL_TEST_ADMIN_URL%%\?*}"
admin_db="${admin_db%/*}/product_smoke"
port=${PRODUCT_SMOKE_PORT:-18090}
psql -q "$KERNEL_TEST_ADMIN_URL" -c 'DROP DATABASE IF EXISTS product_smoke' -c 'CREATE DATABASE product_smoke' \
  -c "DO \$\$ BEGIN CREATE ROLE enterprise_runtime NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END \$\$" \
  -c "DO \$\$ BEGIN CREATE ROLE product_smoke_app LOGIN PASSWORD 'smoke-only' NOSUPERUSER NOBYPASSRLS IN ROLE enterprise_runtime; EXCEPTION WHEN duplicate_object THEN NULL; END \$\$"
trap 'kill ${pid:-0} 2>/dev/null || true; psql -q "$KERNEL_TEST_ADMIN_URL" -c "DROP DATABASE IF EXISTS product_smoke WITH (FORCE)"' EXIT

ADMIN_DATABASE_URL="$admin_db" "$root/bin/appctl" migrate up | tee /dev/stderr | grep -q '"applied":true,"ok":true,"version":3'
ADMIN_DATABASE_URL="$admin_db" "$root/bin/appctl" migrate up | tee /dev/stderr | grep -q '"applied":false,"ok":true,"version":3'
psql -q "$admin_db" -c "INSERT INTO procurement.purchase (tenant_id,id,created_by,title,amount,status,internal_note)
  VALUES ('tenant-a','po-1','alice','laptop',900,'approved','bank-1'), ('tenant-b','po-9','dave','server',50,'approved','secret')"

app_url="postgres://product_smoke_app:smoke-only@${admin_db#*@}"
DATABASE_URL="$app_url" POC_HTTP_PORT=$port "$root/bin/product" &
pid=$!
for _ in $(seq 50); do curl -s "http://127.0.0.1:$port/v1/purchases" >/dev/null && break; sleep 0.1; done

call() { # user key path body -> "status body"
  curl -s -o /tmp/product_smoke.body -w '%{http_code}' -X POST -H "Authorization: Bearer $1" -H "Idempotency-Key: $2" \
    "http://127.0.0.1:$port/v1/operations/$3" -d "$4"; echo " $(cat /tmp/product_smoke.body)"
}
expect() { echo "$2" >&2; [[ "$2" == "$1"* ]] || { echo "FAIL: want prefix '$1'" >&2; exit 1; }; }

expect '403' "$(call demo_bob c1 procurement.close/1 '{"ids":["po-1"]}')"
expect '200 {"amount":900,"purchaseId":"po-1"}' "$(call demo_alice r1 procurement.receive/1 '{"purchaseId":"po-1","amount":900}')"
expect '403' "$(call demo_erin c2 procurement.close/1 '{"ids":["po-1"]}')"
expect '200 {"closedIds":["po-1"]}' "$(call demo_bob c3 procurement.close/1 '{"ids":["po-1"]}')"
expect '200 {"closedIds":["po-1"]}' "$(call demo_bob c3 procurement.close/1 '{"ids":["po-1"]}')"
expect '409' "$(call demo_bob c3 procurement.close/1 '{"ids":["po-1"],"reason":"x"}')"
expect '403' "$(call demo_alice r2 procurement.receive/1 '{"purchaseId":"po-1","amount":1}')"
list=$(curl -s -H 'Authorization: Bearer demo_bob' "http://127.0.0.1:$port/v1/purchases")
expect '{"purchases":[{"id":"po-1","title":"laptop","amount":900,"status":"closed","createdBy":"alice","received":900}]}' "$list"
psql -Atq "$admin_db" -c "SELECT event_type||'@'||event_version||' '||target_id FROM kernel.outbox" | grep -qx 'procurement.purchase.closed@1 po-1'
echo "product smoke ok" >&2
