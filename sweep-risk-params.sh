#!/usr/bin/env bash
# Sweeps RISK_SLOTS / RISK_QUEUE_TIMEOUT combinations against the real k6
# grading script and reports work_score / risk p95 / error rate for each,
# so you can pick values with actual evidence instead of guessing.
#
# Requires main.py to read RISK_SLOTS and RISK_QUEUE_TIMEOUT from env vars
# (see the os.environ.get(...) pattern) -- add that first if it's not there.
#
# Usage: ./sweep-risk-params.sh

set -e

STARTER_DIR="starters/python"
IMAGE_NAME="obsidio-sweep"
CONTAINER_NAME="obsidio-sweep"
RESULTS_FILE="sweep-results.csv"

SLOTS_VALUES=(2 4 8)
TIMEOUT_VALUES=(0.5 0.75 1.0)

cleanup() {
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "Building image once ..."
docker build -t "$IMAGE_NAME" "$STARTER_DIR"

echo "slots,timeout,work_score,risk_p95_ms,error_rate,qualified" > "$RESULTS_FILE"

for slots in "${SLOTS_VALUES[@]}"; do
  for timeout in "${TIMEOUT_VALUES[@]}"; do
    echo ""
    echo "=== RISK_SLOTS=$slots RISK_QUEUE_TIMEOUT=$timeout ==="

    cleanup
    docker run -d --name "$CONTAINER_NAME" --cpus=2 --memory=2g -p 8080:8080 \
      -e RISK_SLOTS="$slots" -e RISK_QUEUE_TIMEOUT="$timeout" \
      "$IMAGE_NAME" >/dev/null

    for i in $(seq 1 20); do
      curl -sf http://localhost:8080/health >/dev/null && break
      if [ "$i" -eq 20 ]; then
        echo "Health check failed for slots=$slots timeout=$timeout, skipping."
        docker logs "$CONTAINER_NAME"
        continue 2
      fi
      sleep 1
    done

    k6 run --summary-export=sweep-summary.json \
      -e TARGET=http://localhost:8080 k6/grading.js || true

    if [ -f sweep-summary.json ]; then
      WORK_SCORE=$(jq -r '.metrics.work_score.values.count // 0' sweep-summary.json)
      RISK_P95=$(jq -r '.metrics["http_req_duration{tier:risk}"].values["p(95)"] // "n/a"' sweep-summary.json)
      ERROR_RATE=$(jq -r '.metrics.http_req_failed.values.rate // "n/a"' sweep-summary.json)
      QUALIFIED=$(jq -r 'if .metrics["http_req_duration{tier:price}"].thresholds["p(95)<200"].ok
                            and .metrics["http_req_duration{tier:stats}"].thresholds["p(95)<500"].ok
                            and .metrics["http_req_duration{tier:risk}"].thresholds["p(95)<1500"].ok
                            and .metrics["http_req_failed"].thresholds["rate<0.01"].ok
                          then "yes" else "no" end' sweep-summary.json 2>/dev/null || echo "unknown")
      echo "$slots,$timeout,$WORK_SCORE,$RISK_P95,$ERROR_RATE,$QUALIFIED" >> "$RESULTS_FILE"
      echo "work_score=$WORK_SCORE risk_p95=$RISK_P95 error_rate=$ERROR_RATE qualified=$QUALIFIED"
      rm -f sweep-summary.json
    else
      echo "$slots,$timeout,ERROR,ERROR,ERROR,ERROR" >> "$RESULTS_FILE"
    fi
  done
done

echo ""
echo "=== All results (also saved to $RESULTS_FILE) ==="
column -t -s, "$RESULTS_FILE"
