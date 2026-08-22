#!/usr/bin/env bash
# Sweeps RISK_SLOTS / RISK_QUEUE_TIMEOUT / RISK_WAIT_TIMEOUT combinations
# against the real k6 grading script and reports work_score / risk p95 /
# error rate for each, so you can pick values with actual evidence instead
# of guessing.
#
# Requires main.py to read RISK_SLOTS, RISK_QUEUE_TIMEOUT, and
# RISK_WAIT_TIMEOUT from env vars (see the os.environ.get(...) pattern) --
# add that first if it's not there.
#
# Usage: ./sweep-risk-params.sh

set -e

STARTER_DIR="starters/python"
IMAGE_NAME="obsidio-sweep"
CONTAINER_NAME="obsidio-sweep"
K6_IMAGE="grafana/k6"
JQ_IMAGE="ghcr.io/jqlang/jq"
RESULTS_FILE="sweep-results.csv"
LOG_DIR="k6-logs"
mkdir -p "$LOG_DIR"

SLOTS_VALUES=(2)
TIMEOUT_VALUES=(0.8)
WAIT_TIMEOUT_VALUES=(0.8)
ADMIT_BUDGET_VALUES=(1.0 1.2 1.5)

jq() {
  MSYS_NO_PATHCONV=1 docker run --rm -i "$JQ_IMAGE" "$@"
}

cleanup() {
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "Building image once ..."
docker build -t "$IMAGE_NAME" "$STARTER_DIR"

echo "Pulling k6 image once ..."
docker pull "$K6_IMAGE" >/dev/null

echo "Pulling jq image once ..."
docker pull "$JQ_IMAGE" >/dev/null

echo "slots,timeout,wait_timeout,admit_budget,work_score,risk_p95_ms,price_p95_ms,stats_p95_ms,error_rate,qualified" > "$RESULTS_FILE"

for slots in "${SLOTS_VALUES[@]}"; do
  for timeout in "${TIMEOUT_VALUES[@]}"; do
    for wait_timeout in "${WAIT_TIMEOUT_VALUES[@]}"; do
      for admit_budget in "${ADMIT_BUDGET_VALUES[@]}"; do
        echo ""
        echo "=== RISK_SLOTS=$slots RISK_QUEUE_TIMEOUT=$timeout RISK_WAIT_TIMEOUT=$wait_timeout RISK_ADMIT_BUDGET=$admit_budget ==="

        cleanup
        docker run -d --name "$CONTAINER_NAME" --cpus=2 --memory=2g -p 8080:8080 \
          -e RISK_SLOTS="$slots" -e RISK_QUEUE_TIMEOUT="$timeout" -e RISK_WAIT_TIMEOUT="$wait_timeout" \
          -e RISK_ADMIT_BUDGET="$admit_budget" \
          "$IMAGE_NAME" >/dev/null

        for i in $(seq 1 20); do
          curl -sf http://localhost:8080/health >/dev/null && break
          if [ "$i" -eq 20 ]; then
            echo "Health check failed for slots=$slots timeout=$timeout wait_timeout=$wait_timeout admit_budget=$admit_budget, skipping."
            docker logs "$CONTAINER_NAME"
            continue 2
          fi
          sleep 1
        done

        LOG_FILE="$LOG_DIR/slots${slots}_timeout${timeout}_wait${wait_timeout}_admit${admit_budget}.log"
        MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd):/work" -w /work "$K6_IMAGE" run \
          --summary-export=sweep-summary.json \
          -e TARGET=http://host.docker.internal:8080 k6/grading.js 2>&1 | tee "$LOG_FILE"
        true # k6 exits non-zero when thresholds fail; don't let that trip `set -e`

        if [ -f sweep-summary.json ]; then
          WORK_SCORE=$(jq -r '.metrics.work_score.count // 0' < sweep-summary.json)
          RISK_P95=$(jq -r '.metrics["http_req_duration{tier:risk}"]["p(95)"] // "n/a"' < sweep-summary.json)
          PRICE_P95=$(jq -r '.metrics["http_req_duration{tier:price}"]["p(95)"] // "n/a"' < sweep-summary.json)
          STATS_P95=$(jq -r '.metrics["http_req_duration{tier:stats}"]["p(95)"] // "n/a"' < sweep-summary.json)
          ERROR_RATE=$(jq -r '.metrics.http_req_failed.value // "n/a"' < sweep-summary.json)
          # Computed directly from the raw numbers against the known bars, not
          # from the exported JSON's own .thresholds booleans -- those were
          # found to report "true" even when the run's own console output and
          # ERRO log line said the threshold had been crossed. Not trustworthy.
          QUALIFIED=$(jq -r --argjson price_bar 200 --argjson stats_bar 500 \
                            --argjson risk_bar 1500 --argjson err_bar 0.01 '
            if (.metrics["http_req_duration{tier:price}"]["p(95)"] < $price_bar)
               and (.metrics["http_req_duration{tier:stats}"]["p(95)"] < $stats_bar)
               and (.metrics["http_req_duration{tier:risk}"]["p(95)"] < $risk_bar)
               and (.metrics.http_req_failed.value < $err_bar)
            then "yes" else "no" end' < sweep-summary.json 2>/dev/null || echo "unknown")
          echo "$slots,$timeout,$wait_timeout,$admit_budget,$WORK_SCORE,$RISK_P95,$PRICE_P95,$STATS_P95,$ERROR_RATE,$QUALIFIED" >> "$RESULTS_FILE"
          echo "work_score=$WORK_SCORE risk_p95=$RISK_P95 price_p95=$PRICE_P95 stats_p95=$STATS_P95 error_rate=$ERROR_RATE qualified=$QUALIFIED"
          echo "full k6 output saved to $LOG_FILE"
          rm -f sweep-summary.json
        else
          echo "$slots,$timeout,$wait_timeout,$admit_budget,ERROR,ERROR,ERROR,ERROR,ERROR,ERROR" >> "$RESULTS_FILE"
        fi
      done
    done
  done
done

echo ""
echo "=== All results (also saved to $RESULTS_FILE) ==="
column -t -s, "$RESULTS_FILE"
