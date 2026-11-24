#!/usr/bin/env bash
# Delete the CryptoSwarm minikube cluster (frees ~3 GB of RAM). Docker Compose is unaffected.
set -euo pipefail
minikube delete -p cryptoswarm
