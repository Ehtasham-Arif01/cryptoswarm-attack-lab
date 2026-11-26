#!/usr/bin/env bash
# Get CryptoSwarm ready for a class demo (Docker Compose).
#
#   ./scripts/demo.sh            start fresh: build, 1 Python worker, open the dashboard
#   ./scripts/demo.sh --check    same, then run every attack once end-to-end first (~2 min)
#   ./scripts/demo.sh --stop     stop everything
#
# The dashboard opens in presenter mode (large text for the projector).
# PRESENTER=0 opens it in normal mode, NO_BROWSER=1 doesn't open a browser at all.
#
# Run it 10 minutes before presenting. See DEMO.md for the run sheet.
set -euo pipefail
cd "$(dirname "$0")/.."

URL="http://localhost:${DASHBOARD_PORT:-8090}"
if [[ "${PRESENTER:-1}" == "0" ]]; then OPEN_URL="$URL/?presenter=0#bruteforce"; else OPEN_URL="$URL/?presenter=1#bruteforce"; fi
step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

if [[ "${1:-}" == "--stop" ]]; then
  docker compose down
  exit 0
fi

if minikube -p cryptoswarm status >/dev/null 2>&1; then
  step "Stopping the Kubernetes cluster to free CPU and RAM (start it again with ./scripts/k8s-up.sh)"
  minikube -p cryptoswarm stop >/dev/null
fi

step "Building and starting the stack"
docker compose up -d --build --scale worker=1 --scale worker-rust=0

step "Waiting for the coordinator"
for _ in $(seq 1 60); do
  curl -fs "$URL/api/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -fs "$URL/api/health" >/dev/null || { echo "Coordinator did not come up. Check: docker compose logs coordinator"; exit 1; }

if [[ "${1:-}" == "--check" ]]; then
  step "Running every attack once (smoke test)"
  docker compose up -d --scale worker=4 >/dev/null 2>&1
  python3 scripts/smoke_test.py "$URL"
  docker compose up -d --scale worker=1 >/dev/null 2>&1
fi

step "Cancelling any running job and warming up the speed benchmark"
curl -fs -X POST "$URL/api/jobs/cancel" >/dev/null || true
curl -fs "$URL/api/benchmark?refresh=true" >/dev/null || true

step "Ready"
cat <<MSG
Dashboard:      $OPEN_URL
                (P or the Presenter button toggles presenter mode; F11 for full screen)
Scale Python:   docker compose up -d --scale worker=8
Add Rust:       docker compose up -d --scale worker-rust=4
Kill a worker:  docker kill \$(docker compose ps -q worker | head -1)
Back to 1:      docker compose up -d --scale worker=1 --scale worker-rust=0
Run sheet:      DEMO.md
MSG

[[ -z "${NO_BROWSER:-}" ]] && command -v xdg-open >/dev/null && xdg-open "$OPEN_URL" >/dev/null 2>&1 || true
