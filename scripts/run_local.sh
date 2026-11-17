#!/bin/bash
# Run the deye-dummycloud server locally (outside of HA) with default settings.
#
# Example:
#   MQTT_HOST=localhost LISTEN_PORT=10000 ./scripts/run_local.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

LISTEN_PORT="${LISTEN_PORT:-10000}"
MQTT_HOST="${MQTT_HOST:-core-mosquitto}"
MQTT_PORT="${MQTT_PORT:-1883}"
TOPIC_PREFIX="${TOPIC_PREFIX:-deye-dummycloud}"
LOG_LEVEL="${LOG_LEVEL:-info}"

cd "$PROJECT_DIR"

export LISTEN_PORT MQTT_HOST MQTT_PORT TOPIC_PREFIX LOG_LEVEL
export MQTT_USERNAME="${MQTT_USERNAME:-}"
export MQTT_PASSWORD="${MQTT_PASSWORD:-}"

exec "$PROJECT_DIR/.venv/bin/python" "$SCRIPT_DIR/main.py"
