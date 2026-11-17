#!/bin/bash
# Exact operator loop used for v3 Kiro cohort 002. Sequential, in plan order:
# isolated generation (Seatbelt) -> restore -> private 66-case evaluation.
# Usage: run_kiro_cohort.sh <cohort-dir> <evaluator-checkout>
set -u
W=$1; R=$2; H=$HOME
cd "$R"
mkdir -p "$W/ops"; STATUS=$W/ops/status.log
for t in $(tail -1 "$W/order.txt"); do
  if [ -e "$W/results/$t" ]; then echo "$(date -u +%FT%TZ) $t skip-existing" >> "$STATUS"; continue; fi
  DENY=(--deny "$H/.paseo" --deny "$H/cil/ainative/enterprise_agent" --deny "$H/.kiro" --deny "$H/.codex"
        --deny "$W/private" --deny "$W/eval" --deny "$W/evaluations" --deny "$W/ops")
  for o in $(tail -1 "$W/order.txt"); do
    [ "$o" = "$t" ] && continue
    DENY+=(--deny "$W/kits/$o"); [ -e "$W/results/$o" ] && DENY+=(--deny "$W/results/$o")
  done
  echo "$(date -u +%FT%TZ) $t generate" >> "$STATUS"
  env -u ADMIN_DATABASE_URL -u DATABASE_URL -u POC_DB_RESET \
    perl -e 'alarm 900; exec @ARGV' python3 benchmark/phase3/isolate.py --kit "$W/kits/$t" --output "$W/results/$t" "${DENY[@]}" \
      --allow-write "$H/Library/Caches" --allow-write "$H/go/pkg/mod" \
      --allow-write "$H/Library/Application Support/kiro-cli" --allow-write "$H/.aws/sso/cache" \
      > "$W/ops/$t.txt" 2>&1
  rc=$?; echo "$(date -u +%FT%TZ) $t worker_rc=$rc" >> "$STATUS"
  if [ -f "$W/results/$t/receipt.json" ]; then
    python3 benchmark/phase3/ctl.py restore --plan "$W/plan.json" --trial "$t" --bundle "$W/results/$t/candidate.bundle" \
      --receipt "$W/results/$t/receipt.json" --manifest "$W/kits/$t/manifest.json" --candidate "$W/eval/$t" >> "$W/ops/$t.txt" 2>&1
    perl -e 'alarm 900; exec @ARGV' python3 benchmark/phase3/ctl.py evaluate --plan "$W/plan.json" --trial "$t" \
      --candidate "$W/eval/$t" --manifest "$W/kits/$t/manifest.json" --receipt "$W/results/$t/receipt.json" \
      --fixtures "$W/private/fixtures.json" --output "$W/evaluations/$t" >> "$W/ops/$t.txt" 2>&1
    echo "$(date -u +%FT%TZ) $t eval_rc=$?" >> "$STATUS"
  fi
done
echo "$(date -u +%FT%TZ) DONE" >> "$STATUS"
