#!/usr/bin/env bash
# Run CryptoSwarm on a local Kubernetes cluster (minikube) with KEDA autoscaling.
#   ./scripts/k8s-up.sh          create/start the cluster, build + load images, deploy
#   ./scripts/k8s-down.sh        delete the cluster
# Stops the Docker Compose stack first: this laptop can't comfortably run both.
set -euo pipefail
cd "$(dirname "$0")/.."

PROFILE=cryptoswarm
KEDA_VERSION=2.21.1
NS=cryptoswarm
IMAGES=(coordinator worker worker-rust oracle dashboard)
kc() { kubectl --context "$PROFILE" "$@"; }
step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

step "Stopping the Docker Compose stack (if running)"
docker compose down >/dev/null 2>&1 || true

step "Starting minikube profile '$PROFILE' (2 CPUs, 3 GB RAM)"
if ! minikube -p "$PROFILE" status >/dev/null 2>&1; then
  minikube start -p "$PROFILE" --driver=docker --cpus=2 --memory=3072 --kubernetes-version=stable
fi

step "Building images"
docker compose build -q

step "Loading images into the cluster"
for img in "${IMAGES[@]}"; do
  echo "  cryptoswarm/$img:local"
  minikube -p "$PROFILE" image load "cryptoswarm/$img:local" --overwrite=true
done

step "Installing KEDA $KEDA_VERSION (autoscaler)"
kc apply --server-side -f "https://github.com/kedacore/keda/releases/download/v${KEDA_VERSION}/keda-${KEDA_VERSION}.yaml" >/dev/null
# Wait until every KEDA deployment is available. (Not `rollout status`: on a slow network the first image
# pull can outlast the deployment's 10-minute progress deadline even though it then starts fine.)
kc -n keda wait --for=condition=Available deployment --all --timeout=900s

step "Deploying CryptoSwarm"
kc apply -k k8s/
for d in redis oracle coordinator dashboard; do kc -n "$NS" rollout status "deployment/$d" --timeout=180s; done
# pick up freshly loaded images on re-runs
kc -n "$NS" rollout restart deployment/coordinator deployment/dashboard deployment/oracle >/dev/null
for d in coordinator dashboard oracle; do kc -n "$NS" rollout status "deployment/$d" --timeout=180s; done

URL="http://$(minikube -p "$PROFILE" ip):30080"
step "Ready"
cat <<MSG
Dashboard:  $URL
Workers:    0 until you start an attack; KEDA then scales them up to 8 (watch the Workers panel).
Watch pods: kubectl --context $PROFILE -n $NS get pods -w
Rust:       kubectl --context $PROFILE -n $NS scale deployment worker-rust --replicas=2
Smoke test: python3 scripts/smoke_test.py $URL
Remove:     ./scripts/k8s-down.sh
MSG
