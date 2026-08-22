#!/usr/bin/env bash
# Local equivalent of the CI k6 run: build, run under the real grading caps,
# wait for health, run the k6 script, print the result, clean up.
#
# Usage: ./test-local.sh [starter-dir]
#   ./test-local.sh                # defaults to starters/python
#   ./test-local.sh starters/go    # test a different starter

set -e

STARTER_DIR="${1:-starters/python}"
IMAGE_NAME="obsidio-local-test"
CONTAINER_NAME="obsidio-local-test"

cleanup() {
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "Building $STARTER_DIR ..."
docker build -t "$IMAGE_NAME" "$STARTER_DIR"

cleanup  # remove any leftover container from a previous run

echo "Starting container under grading caps (2 CPU / 2 GB) ..."
docker run -d --name "$CONTAINER_NAME" --cpus=2 --memory=2g -p 8080:8080 "$IMAGE_NAME" >/dev/null

echo "Waiting for /health ..."
for i in $(seq 1 20); do
  if curl -sf http://localhost:8080/health >/dev/null; then
    echo "Healthy."
    break
  fi
  if [ "$i" -eq 20 ]; then
    echo "Health check failed. Container logs:"
    docker logs "$CONTAINER_NAME"
    exit 1
  fi
  sleep 1
done

echo "Running k6 grading script ..."
k6 run -e TARGET=http://localhost:8080 k6/grading.js

# chmod +x test-local.sh  
# ./test-local.sh         
