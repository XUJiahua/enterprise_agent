#!/bin/bash
# v6 confirmatory driver: 12 lanes (task x model), at most $PAR in parallel.
# Each lane gets its own disposable PostgreSQL and HTTP port and denies every other lane.
# Usage: run_v6_lanes.sh <lanes-root> <evaluator-checkout> [PAR]
set -u
L=$1; R=$2; PAR=${3:-6}; H=$HOME
LANES=$(cd "$L" && ls -d T*/* | sort)
run_lane() {
  rel=$1; W=$L/$rel; read -r ph n < "$W/lane.txt"
  name=ea-v6-lane-$n; port=$((55700+n)); http=$((18300+n))
  docker rm -f $name >/dev/null 2>&1
  docker run -d --rm --name $name -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=enterprise_agent_poc \
    -p 127.0.0.1:$port:5432 postgres:17-alpine >/dev/null
  for _ in $(seq 60); do docker exec $name pg_isready -q 2>/dev/null && break; sleep 1; done
  OTHERS=()
  for o in $LANES; do [ "$o" != "$rel" ] && OTHERS+=("$L/$o"); done
  CTL=benchmark/$ph/ctl.py \
  ADMIN_DATABASE_URL=postgres://postgres:postgres@127.0.0.1:$port/enterprise_agent_poc \
  DATABASE_URL=postgres://app_runtime:poc-only@127.0.0.1:$port/enterprise_agent_poc \
  POC_DB_RESET=1 POC_HTTP_PORT=$http \
    bash "$R/benchmark/phase3/run_kiro_cohort_net.sh" "$W" "$R" "${OTHERS[@]}" \
      $H/poc-v5b-lanes $H/poc-v5-lanes $H/poc-v4b-lanes $H/poc-v4-lanes $H/poc-v4-diagnostic \
      $H/poc-v3-lanes $H/poc-phase3-kiro $H/poc-phase3-kiro-002 > "$W/driver.out" 2>&1
  docker stop $name >/dev/null 2>&1
}
export -f run_lane; export L R H LANES
printf '%s\n' $LANES | xargs -P "$PAR" -I{} bash -c 'run_lane "$@"' _ {}
echo "$(date -u +%FT%TZ) ALL DONE" >> "$L/all.status"
