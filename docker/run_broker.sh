#!/usr/bin/env bash
# Helper to run growatt-rtu-broker:local with devices only if present
# Usage: copy .env to /share/growatt-rtu-broker/.env and run this script
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${SCRIPT_DIR}/.."

ENV_FILE="/share/growatt-rtu-broker/.env"
if [ -f "${ENV_FILE}" ]; then
  # shellcheck disable=SC1090
  set -a; . "${ENV_FILE}"; set +a
fi

DOCKER_ARGS=(--name growatt-broker --restart unless-stopped)

# Add device mounts only if the paths exist. If a requested device path is missing,
# fall back to starting the container in privileged mode and bind-mounting /dev so
# hot-plugged devices become visible inside the container. This preserves the
# "start now, plug later" workflow while warning about the elevated privileges.
MISSING_DEVICE=0
if [ -n "${INV_DEV:-}" ] && [ -e "${INV_DEV}" ]; then
  DOCKER_ARGS+=(--device="${INV_DEV}:/dev/inverter:rw")
else
  echo "Warning: inverter device '${INV_DEV:-}' not present; container will start without explicit device binding"
  MISSING_DEVICE=1
fi

if [ -n "${SHINE_DEV:-}" ] && [ -e "${SHINE_DEV}" ]; then
  DOCKER_ARGS+=(--device="${SHINE_DEV}:/dev/shine:rw")
else
  echo "Info: shine device '${SHINE_DEV:-}' not present; broker can be started hot-pluggable"
  MISSING_DEVICE=1
fi

if [ "${MISSING_DEVICE}" -eq 1 ]; then
  echo "Info: one or more requested devices missing. Starting container in hot-plug mode"
  echo "      (bind-mounting /dev/serial and any existing /dev/ttyUSB* devices)."
  echo "      This is a limited fallback compared to mounting /dev entirely. It still"
  echo "      grants broader access to those device nodes; prefer explicit --device flags"
  echo "      when possible."
  # Bind the serial-by-id tree so newly-created by-id nodes appear inside container
  DOCKER_ARGS+=(-v /dev/serial:/dev/serial -v /dev/serial/by-id:/dev/serial/by-id)

  # If any /dev/ttyUSB* exist right now, bind-mount them explicitly so tools
  # that reference /dev/ttyUSB* will also work. New ttyUSB devices created after
  # container start will still be visible via /dev/serial/by-id.
  for f in /dev/ttyUSB*; do
    if [ -e "$f" ]; then
      echo "Info: binding existing device $f into container"
      DOCKER_ARGS+=(-v "$f":"$f")
    fi
  done
fi

# Port mappings
TCP_HOST_PORT="${TCP_BIND##*:}"
TCP_ALT_HOST_PORT="${TCP_ALT_BIND##*:}"
SNIFF_HOST_PORT="${SNIFF_BIND##*:}"
DOCKER_ARGS+=(-p "${TCP_HOST_PORT}:${TCP_HOST_PORT}" -p "${TCP_ALT_HOST_PORT}:${TCP_ALT_HOST_PORT}" -p "${SNIFF_HOST_PORT}:${SNIFF_HOST_PORT}")

CONTAINER_NAME=growatt-broker

# Decide whether we need to run (container missing or removed) or exit (already running)
NEED_RUN=1
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
  echo "Found existing container ${CONTAINER_NAME}; updating restart policy to 'unless-stopped'"
  docker update --restart unless-stopped "${CONTAINER_NAME}" || true
  RUNNING=$(docker inspect -f '{{.State.Running}}' ${CONTAINER_NAME} 2>/dev/null || echo "false")
  if [ "${RUNNING}" = "true" ]; then
    echo "Container ${CONTAINER_NAME} is already running; will not re-run docker create"
    NEED_RUN=0
  else
    echo "Container ${CONTAINER_NAME} is present but stopped; removing to recreate with current device configuration"
    docker rm "${CONTAINER_NAME}" || true
    NEED_RUN=1
  fi
fi

# Build the full docker run command (for visibility)
DOCKER_CMD=(docker run -d "${DOCKER_ARGS[@]}" growatt-rtu-broker:local \
  growatt-broker --inverter /dev/inverter --shine /dev/shine \
    --baud "${INV_BAUD:-${BAUD:-115200}}" --bytes "${INV_BYTES:-${BYTES:-8N1}}" \
    --tcp "${TCP_BIND:-0.0.0.0:5020}" --tcp-alt "${TCP_ALT_BIND:-0.0.0.0:5021}" --sniff "${SNIFF_BIND:-0.0.0.0:5700}" \
    --min-period "${MIN_PERIOD:-1.0}" --rtimeout "${RTIMEOUT:-1.5}" --log "${LOG_PATH:--}")

echo "Prepared docker run command:"
printf ' %s' "${DOCKER_CMD[@]}"
echo

if [ "${NEED_RUN}" -eq 1 ]; then
  echo "Starting container ${CONTAINER_NAME}"
  "${DOCKER_CMD[@]}"
  # short pause to let docker register the container
  sleep 1
else
  echo "Skipping docker run; container already running"
fi

# Show the current restart policy to confirm --restart is set
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
  RPOL=$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' ${CONTAINER_NAME} 2>/dev/null || echo "(unknown)")
  echo "Container ${CONTAINER_NAME} restart policy: ${RPOL}"
fi
