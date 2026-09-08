#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is not available in this environment" >&2
  exit 1
fi

if [ -f .env ]; then
  # shellcheck disable=SC2046
  set -a; . ./.env; set +a
else
  echo ".env not found. Copy .env.example to .env and edit paths first." >&2
  exit 1
fi

: "${INV_DEV:?Set INV_DEV in .env}"
: "${SHINE_DEV:=/dev/serial/by-id/shine}"
HOTPLUG_DEVICES="${HOTPLUG_DEVICES:-1}"

echo "Checking devices:"
ls -l "$INV_DEV" || true
ls -l "$SHINE_DEV" || true

# Resolve to concrete device nodes for the explicit-device deployment.
INV_DEV_NODE=$(readlink -f "$INV_DEV" || echo "")
SHINE_DEV_NODE=$(readlink -f "$SHINE_DEV" || echo "")
if [ -z "$INV_DEV_NODE" ] || [ ! -e "$INV_DEV_NODE" ]; then
  echo "Warning: could not resolve INV_DEV to a device node; using path as-is" >&2
  INV_DEV_NODE="$INV_DEV"
fi
if [ -z "$SHINE_DEV_NODE" ] || [ ! -e "$SHINE_DEV_NODE" ]; then
  echo "Warning: could not resolve SHINE_DEV to a device node; using path as-is" >&2
  SHINE_DEV_NODE="$SHINE_DEV"
fi

IMAGE_TAG=growatt-rtu-broker:local
echo "Building image $IMAGE_TAG ..."
docker build -t "$IMAGE_TAG" .

echo "(Re)starting container growatt-rtu-broker ..."
docker rm -f growatt-rtu-broker >/dev/null 2>&1 || true

DOCKER_ARGS=(
  run -d
  --name growatt-rtu-broker
  --restart unless-stopped
  --network host
  --privileged
  -v /var/log:/var/log
)
if [ "${HOTPLUG_DEVICES}" = "1" ]; then
  DOCKER_ARGS+=(-v /dev:/dev)
  INV_ARG="${INV_DEV}"
  SHINE_ARG="${SHINE_DEV}"
else
  DOCKER_ARGS+=(--device "${INV_DEV_NODE}:${INV_DEV_NODE}" --device "${SHINE_DEV_NODE}:${SHINE_DEV_NODE}")
  INV_ARG=/dev/inverter
  SHINE_ARG=/dev/shine
fi

DOCKER_ARGS+=("${IMAGE_TAG}"
  growatt-broker
  --inverter "${INV_ARG}"
  --shine "${SHINE_ARG}"
  --baud "${BAUD:-9600}"
  --bytes "${BYTES:-8E1}"
  --tcp "${TCP_BIND:-0.0.0.0:5020}"
  --min-period "${MIN_PERIOD:-1.0}"
  --rtimeout "${RTIMEOUT:-1.5}"
  --log "${LOG_PATH:-/var/log/growatt_broker.jsonl}"
  --mode "${BROKER_MODE:-legacy}")
if [ -n "${INV_BAUD:-}" ]; then DOCKER_ARGS+=(--inv-baud "${INV_BAUD}"); fi
if [ -n "${INV_BYTES:-}" ]; then DOCKER_ARGS+=(--inv-bytes "${INV_BYTES}"); fi
if [ -n "${SHINE_BAUD:-}" ]; then DOCKER_ARGS+=(--shine-baud "${SHINE_BAUD}"); fi
if [ -n "${SHINE_BYTES:-}" ]; then DOCKER_ARGS+=(--shine-bytes "${SHINE_BYTES}"); fi

docker "${DOCKER_ARGS[@]}"

echo "Container started. Tail logs with: docker logs -f growatt-rtu-broker"
