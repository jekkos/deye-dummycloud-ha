"""Deye Dummy Cloud: replaces the Deye/Solarman cloud for microinverter loggers.

The logger (or inverter in "standalone" mode) pushes its data to this server
over plain TCP; the data is republished on MQTT, with Home Assistant
auto-discovery for the common entities.

Ported from the Node.js implementation in Hypfer/deye-microinverter-cloud-free.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dummy_cloud import DEFAULT_HOST, DEFAULT_PORT, DummyCloud
from mqtt_client import DEFAULT_TOPIC_PREFIX, MqttClient

LOG_LEVELS = ("trace", "debug", "info", "warn", "error", "fatal")


def main() -> None:
    listen_port = int(os.environ.get("LISTEN_PORT", DEFAULT_PORT))
    mqtt_host = os.environ.get("MQTT_HOST", "core-mosquitto")
    mqtt_port = int(os.environ.get("MQTT_PORT", 1883))
    mqtt_username = os.environ.get("MQTT_USERNAME") or None
    mqtt_password = os.environ.get("MQTT_PASSWORD") or None
    topic_prefix = os.environ.get("TOPIC_PREFIX", DEFAULT_TOPIC_PREFIX)
    log_level = os.environ.get("LOG_LEVEL", "info")

    if log_level not in LOG_LEVELS:
        print(
            f"Invalid log level: {log_level}, must be one of {', '.join(LOG_LEVELS)}",
            file=sys.stderr,
        )
        sys.exit(1)
    if mqtt_username and not mqtt_password:
        print(
            "MQTT_USERNAME was set, but MQTT_PASSWORD is empty, please set both",
            file=sys.stderr,
        )
        sys.exit(1)

    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    mqtt_client = MqttClient(
        host=mqtt_host,
        port=mqtt_port,
        username=mqtt_username,
        password=mqtt_password,
        topic_prefix=topic_prefix,
    )
    dummy_cloud = DummyCloud(port=listen_port, host=DEFAULT_HOST)

    dummy_cloud.on_data(mqtt_client.handle_data)
    dummy_cloud.on_handshake(mqtt_client.handle_handshake)

    async def run() -> None:
        mqtt_client.initialize()
        await dummy_cloud.start()
        await dummy_cloud.serve_forever()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
