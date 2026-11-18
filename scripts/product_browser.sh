#!/usr/bin/env bash
# Browser E2E for the v0.1 product page: a fresh database per run,
# appctl migrate up, seed, bin/product (with its consumer), the production
# Next.js build, then apps/web/tests-product. Needs KERNEL_TEST_ADMIN_URL
# (disposable superuser URL), built bin/product + bin/appctl, `npm run build`
# done in apps/web and Playwright Chromium installed. Database name and both
# ports are unique per run, so concurrent runs (other worktrees) do not collide.
set -euo pipefail
: "${KERNEL_TEST_ADMIN_URL:?}"
root=$(cd "$(dirname "$0")/.." && pwd)
admin_db="${KERNEL_TEST_ADMIN_URL%%\?*}"
db=product_browser_$$
admin_db="${admin_db%/*}/$db"
free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }
port=${PRODUCT_HTTP_PORT:-$(free_port)}
web_port=${PRODUCT_WEB_PORT:-$(free_port)}
out="$root/reports/browser/product-$$"
mkdir -p "$out"; rm -f "$out/results.json"
psql -q "$KERNEL_TEST_ADMIN_URL" -c "CREATE DATABASE $db" \
  -c "DO \$\$ BEGIN CREATE ROLE enterprise_runtime NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END \$\$" \
  -c "DO \$\$ BEGIN CREATE ROLE product_smoke_app LOGIN PASSWORD 'smoke-only' NOSUPERUSER NOBYPASSRLS IN ROLE enterprise_runtime; EXCEPTION WHEN duplicate_object THEN NULL; END \$\$" \
  -c "DO \$\$ BEGIN CREATE ROLE enterprise_consumer NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END \$\$" \
  -c "DO \$\$ BEGIN CREATE ROLE product_smoke_consumer LOGIN PASSWORD 'smoke-only' NOSUPERUSER NOBYPASSRLS IN ROLE enterprise_consumer; EXCEPTION WHEN duplicate_object THEN NULL; END \$\$"
trap '[ -n "${pid:-}" ] && kill "$pid" 2>/dev/null || true; psql -q "$KERNEL_TEST_ADMIN_URL" -c "DROP DATABASE IF EXISTS $db WITH (FORCE)"' EXIT
ADMIN_DATABASE_URL="$admin_db" "$root/bin/appctl" migrate up
psql -q -v ON_ERROR_STOP=1 "$admin_db" -f "$root/scripts/product_seed.sql"
DATABASE_URL="postgres://product_smoke_app:smoke-only@${admin_db#*@}" \
  CONSUMER_DATABASE_URL="postgres://product_smoke_consumer:smoke-only@${admin_db#*@}" POC_HTTP_PORT=$port "$root/bin/product" > "$out/backend.log" 2>&1 &
pid=$!
for _ in $(seq 50); do curl -s "http://127.0.0.1:$port/v1/purchases" >/dev/null && break; sleep 0.1; done
PRODUCT_HTTP_PORT=$port PRODUCT_WEB_PORT=$web_port POC_BROWSER_REPORT_DIR="$out" NEXT_TELEMETRY_DISABLED=1 npm run test:e2e:product --prefix "$root/apps/web"
# Expected count = test(...) declarations, so adding a case needs no edit here.
want=$(cat "$root"/apps/web/tests-product/*.spec.ts | grep -cE '^test\(')
python3 - "$out/results.json" "$want" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))["stats"]
assert s["expected"] == int(sys.argv[2]) and not any(s.get(k, 0) for k in ("unexpected", "flaky", "skipped")), s
print("product browser ok", s)
PY
