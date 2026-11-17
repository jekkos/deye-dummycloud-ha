#!/bin/bash
set -e

echo "Starting Deye Dummy Cloud add-on..."

# Check MQTT connectivity
MQTT_HOST="${MQTT_HOST:-core-mosquitto}"
MQTT_PORT="${MQTT_PORT:-1883}"

echo "Checking MQTT broker at $MQTT_HOST:$MQTT_PORT ..."
if ! timeout 3 bash -c "echo > /dev/tcp/$MQTT_HOST/$MQTT_PORT" 2>/dev/null; then
    echo "❌ Cannot connect to MQTT broker at $MQTT_HOST:$MQTT_PORT"
    echo "   Enable built-in Mosquitto in HA settings or configure an external broker."
    exit 1
fi
echo "✓ MQTT broker reachable"

# Run the main script
exec python3 /app/main.py
