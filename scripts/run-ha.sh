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
: "${SHINE_DEV:?Set SHINE_DEV in .env}"

echo "Checking devices:"
ls -l "$INV_DEV" || true
ls -l "$SHINE_DEV" || true

IMAGE_TAG=growatt-rtu-broker:local
echo "Building image $IMAGE_TAG ..."
docker build -t "$IMAGE_TAG" .

echo "(Re)starting container growatt-rtu-broker ..."
docker rm -f growatt-rtu-broker >/dev/null 2>&1 || true

docker run -d \
  --name growatt-rtu-broker \
  --restart unless-stopped \
  --network host \
  --privileged \
  -v /var/log:/var/log \
  --device /dev/serial/by-path:/dev/serial/by-path \
  "$IMAGE_TAG" \
  growatt-broker \
  --inverter "${INV_DEV}" \
  --shine "${SHINE_DEV}" \
  --baud "${BAUD:-9600}" \
  --bytes "${BYTES:-8E1}" \
  --tcp "${TCP_BIND:-0.0.0.0:5020}" \
  --min-period "${MIN_PERIOD:-1.0}" \
  --rtimeout "${RTIMEOUT:-1.5}" \
  --log "${LOG_PATH:-/var/log/growatt_broker.jsonl}"

echo "Container started. Tail logs with: docker logs -f growatt-rtu-broker"
