#!/usr/bin/env bash
# Browser E2E for the v0.1 product page: fresh database product_browser,
# appctl migrate up, seed, bin/product (with its consumer), the production
# Next.js build, then apps/web/tests-product. Needs KERNEL_TEST_ADMIN_URL
# (disposable superuser URL), built bin/product + bin/appctl, `npm run build`
# done in apps/web and Playwright Chromium installed.
set -euo pipefail
: "${KERNEL_TEST_ADMIN_URL:?}"
root=$(cd "$(dirname "$0")/.." && pwd)
admin_db="${KERNEL_TEST_ADMIN_URL%%\?*}"
admin_db="${admin_db%/*}/product_browser"
port=${PRODUCT_HTTP_PORT:-18091}
out="$root/reports/browser/product"
mkdir -p "$out"; rm -f "$out/results.json"
psql -q "$KERNEL_TEST_ADMIN_URL" -c 'DROP DATABASE IF EXISTS product_browser WITH (FORCE)' -c 'CREATE DATABASE product_browser' \
  -c "DO \$\$ BEGIN CREATE ROLE enterprise_runtime NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END \$\$" \
  -c "DO \$\$ BEGIN CREATE ROLE product_smoke_app LOGIN PASSWORD 'smoke-only' NOSUPERUSER NOBYPASSRLS IN ROLE enterprise_runtime; EXCEPTION WHEN duplicate_object THEN NULL; END \$\$" \
  -c "DO \$\$ BEGIN CREATE ROLE enterprise_consumer NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END \$\$" \
  -c "DO \$\$ BEGIN CREATE ROLE product_smoke_consumer LOGIN PASSWORD 'smoke-only' NOSUPERUSER NOBYPASSRLS IN ROLE enterprise_consumer; EXCEPTION WHEN duplicate_object THEN NULL; END \$\$"
trap '[ -n "${pid:-}" ] && kill "$pid" 2>/dev/null || true; psql -q "$KERNEL_TEST_ADMIN_URL" -c "DROP DATABASE IF EXISTS product_browser WITH (FORCE)"' EXIT
ADMIN_DATABASE_URL="$admin_db" "$root/bin/appctl" migrate up
psql -q -v ON_ERROR_STOP=1 "$admin_db" -f "$root/scripts/product_seed.sql"
DATABASE_URL="postgres://product_smoke_app:smoke-only@${admin_db#*@}" \
  CONSUMER_DATABASE_URL="postgres://product_smoke_consumer:smoke-only@${admin_db#*@}" POC_HTTP_PORT=$port "$root/bin/product" > "$out/backend.log" 2>&1 &
pid=$!
for _ in $(seq 50); do curl -s "http://127.0.0.1:$port/v1/purchases" >/dev/null && break; sleep 0.1; done
PRODUCT_HTTP_PORT=$port POC_BROWSER_REPORT_DIR="$out" NEXT_TELEMETRY_DISABLED=1 npm run test:e2e:product --prefix "$root/apps/web"
python3 - "$out/results.json" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))["stats"]
assert s["expected"] == 6 and not any(s.get(k, 0) for k in ("unexpected", "flaky", "skipped")), s
print("product browser ok", s)
PY
