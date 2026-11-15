#!/bin/bash
# run_kiro_cohort.sh with network-restricted generation (isolate_net.py: Seatbelt file rules
# + outbound only via an allowlisting proxy; <results>/<trial>/network.log is the evidence) ->
# restore -> private 66-case evaluation, sequential in plan order.
# Usage: run_kiro_cohort.sh <cohort-dir> <evaluator-checkout> [extra-deny-dir ...]
# The lane's own DATABASE_URL/ADMIN_DATABASE_URL/POC_HTTP_PORT come from the env,
# so parallel lanes need separate disposable databases and ports.
set -u
CTL=${CTL:-benchmark/phase3/ctl.py}  # benchmark/phase4/ctl.py for v4 cohorts
W=$1; R=$2; shift 2; EXTRA=("$@"); H=$HOME
cd "$R"
mkdir -p "$W/ops"; STATUS=$W/ops/status.log
ORDER=$(tail -1 "$W/order.txt")
for t in $ORDER; do
  if [ -e "$W/results/$t" ]; then echo "$(date -u +%FT%TZ) $t skip-existing" >> "$STATUS"; continue; fi
  DENY=(--deny "$H/.paseo" --deny "$H/cil/ainative/enterprise_agent" --deny "$H/.kiro" --deny "$H/.codex"
        --deny "$W/private" --deny "$W/eval" --deny "$W/evaluations" --deny "$W/ops")
  for d in "${EXTRA[@]}"; do DENY+=(--deny "$d"); done
  for o in $ORDER; do
    [ "$o" = "$t" ] && continue
    DENY+=(--deny "$W/kits/$o"); [ -e "$W/results/$o" ] && DENY+=(--deny "$W/results/$o")
  done
  echo "$(date -u +%FT%TZ) $t generate" >> "$STATUS"
  env -u ADMIN_DATABASE_URL -u DATABASE_URL -u POC_DB_RESET \
    perl -e 'alarm 900; exec @ARGV' python3 benchmark/phase3/isolate_net.py --kit "$W/kits/$t" --output "$W/results/$t" "${DENY[@]}" \
      --allow-write "$H/Library/Caches" --allow-write "$H/go/pkg/mod" \
      --allow-write "$H/Library/Application Support/kiro-cli" --allow-write "$H/.aws/sso/cache" \
      > "$W/ops/$t.txt" 2>&1
  rc=$?; echo "$(date -u +%FT%TZ) $t worker_rc=$rc" >> "$STATUS"
  if [ -f "$W/results/$t/receipt.json" ]; then
    python3 "$CTL" restore --plan "$W/plan.json" --trial "$t" --bundle "$W/results/$t/candidate.bundle" \
      --receipt "$W/results/$t/receipt.json" --manifest "$W/kits/$t/manifest.json" --candidate "$W/eval/$t" >> "$W/ops/$t.txt" 2>&1
    perl -e 'alarm 900; exec @ARGV' python3 "$CTL" evaluate --plan "$W/plan.json" --trial "$t" \
      --candidate "$W/eval/$t" --manifest "$W/kits/$t/manifest.json" --receipt "$W/results/$t/receipt.json" \
      --fixtures "$W/private/fixtures.json" --output "$W/evaluations/$t" >> "$W/ops/$t.txt" 2>&1
    rc=$?; echo "$(date -u +%FT%TZ) $t eval_rc=$rc" >> "$STATUS"
  fi
done
echo "$(date -u +%FT%TZ) DONE" >> "$STATUS"
