"""Publish Deye logger data to MQTT and provide Home Assistant discovery.

Ported from the Node.js implementation in Hypfer/deye-microinverter-cloud-free
(dummycloud/src/MqttClient.js).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

try:
    from paho.mqtt import client as mqtt
except ModuleNotFoundError as exc:
    raise SystemExit(
        f"Missing dependency: {exc.name}. Install it with: python3 -m pip install paho-mqtt"
    ) from exc

logger = logging.getLogger(__name__)

DEFAULT_TOPIC_PREFIX = "deye-dummycloud"
# (Re-)publish discovery config every 4 hours
DEFAULT_AUTOCONF_INTERVAL = 4 * 60 * 60


class MqttClient:
    def __init__(
        self,
        host: str = "core-mosquitto",
        port: int = 1883,
        username: str | None = None,
        password: str | None = None,
        topic_prefix: str = DEFAULT_TOPIC_PREFIX,
        autoconf_interval: int = DEFAULT_AUTOCONF_INTERVAL,
        client: Any | None = None,
    ):
        self.host = host
        self.port = port
        self.topic_prefix = topic_prefix
        self.autoconf_interval = autoconf_interval
        self.autoconf_timestamps: dict[int, float] = {}
        self._owns_client = client is None
        self.client = client if client is not None else self._build_client(username, password)

    def _build_client(self, username: str | None, password: str | None) -> Any:
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"deye_dummycloud_{uuid.uuid4().hex[:7]}",  # 23 characters max
            protocol=mqtt.MQTTv311,
        )
        if username:
            client.username_pw_set(username, password)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.reconnect_delay_set(min_delay=1, max_delay=120)
        return client

    def initialize(self) -> None:
        if not self._owns_client:
            return
        logger.info("Connecting to MQTT broker %s:%s ...", self.host, self.port)
        self.client.connect_async(self.host, self.port, keepalive=60)
        self.client.loop_start()

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            logger.error("MQTT connect failed: %s", reason_code)
            return
        logger.info("Connected to MQTT broker")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code is not None and reason_code.is_failure:
            logger.info("Attempting to reconnect to MQTT broker")

    def publish(self, topic: str, payload: str, retain: bool = False) -> None:
        self.client.publish(topic, payload, qos=0, retain=retain)

    def handle_handshake(self, data: dict) -> None:
        # Nothing to see here
        logger.debug("Received handshake from %s", data["payload"])

    def handle_data(self, data: dict) -> None:
        payload = data["payload"]
        meta = data.get("meta") or {}
        serial = data["header"].logger_serial
        mppt_count = payload["inverter_meta"]["mppt_count"]

        self.ensure_autoconf(meta, serial, mppt_count)
        base_topic = f"{self.topic_prefix}/{serial}"
        pv = payload["pv"]

        for index in range(1, mppt_count + 1):
            slot = pv[str(index)]
            self.publish(f"{base_topic}/pv/{index}/v", str(slot["v"]))
            self.publish(f"{base_topic}/pv/{index}/i", str(slot["i"]))
            self.publish(f"{base_topic}/pv/{index}/w", str(slot["w"]))
            self.publish(
                f"{base_topic}/pv/{index}/kWh_today",
                str(slot["kWh_today"]),
                retain=True,
            )
            if slot["kWh_total"] > 0:
                self.publish(
                    f"{base_topic}/pv/{index}/kWh_total",
                    str(slot["kWh_total"]),
                    retain=True,
                )

        grid = payload["grid"]
        self.publish(f"{base_topic}/grid/active_power_w", str(grid["active_power_w"]))
        self.publish(
            f"{base_topic}/grid/kWh_today", str(grid["kWh_today"]), retain=True
        )
        if grid["kWh_total"] > 0:
            self.publish(
                f"{base_topic}/grid/kWh_total",
                str(grid["kWh_total"]),
                retain=True,
            )
        self.publish(f"{base_topic}/grid/v", str(grid["v"]))
        self.publish(f"{base_topic}/grid/hz", str(grid["hz"]))

        self.publish(
            f"{base_topic}/inverter/radiator_temperature",
            str(payload["inverter"]["radiator_temp_celsius"]),
        )

        total_dc_power = sum(slot["w"] for slot in pv.values())
        ac_power = abs(grid["active_power_w"])
        efficiency = (
            f"{(ac_power / total_dc_power) * 100:.2f}"
            if total_dc_power > 0 and total_dc_power > ac_power
            else "null"
        )
        self.publish(f"{base_topic}/inverter/efficiency", efficiency)

    def ensure_autoconf(self, meta: dict, serial: int, mppt_count: int) -> None:
        now = time.time()
        last = self.autoconf_timestamps.get(serial, 0)
        if now - last <= self.autoconf_interval:
            return

        # The discovery topics and unique ids are kept identical to the
        # original Node.js dummycloud so entities carry over between versions.
        logger_serial = str(serial)
        ident = f"deye_dummycloud_{logger_serial}"
        base_topic = f"{self.topic_prefix}/{logger_serial}"
        device = {
            "manufacturer": "Deye",
            "model": "Microinverter",
            "name": f"Deye Microinverter {logger_serial}",
            "configuration_url": f"http://{meta.get('remoteAddress', 'unknown')}/index_cn.html",
            "identifiers": [ident],
        }

        s = logger_serial

        def sensor(
            state_topic: str, suffix: str, unique_id: str, **fields: Any
        ) -> None:
            self._publish_sensor(
                logger_serial, state_topic, suffix=suffix, unique_id=unique_id,
                device=device, **fields,
            )

        for index in range(1, mppt_count + 1):
            enabled = index < 3
            sensor(
                f"{base_topic}/pv/{index}/v",
                f"{s}_pv{index}_v",
                f"deye_dummycloud_{s}_pv_{index}_v",
                name=f"PV {index} Voltage",
                unit_of_measurement="V",
                device_class="voltage",
                state_class="measurement",
                expire_after=360,
                enabled_by_default=enabled,
            )
            sensor(
                f"{base_topic}/pv/{index}/i",
                f"{s}_pv{index}_i",
                f"deye_dummycloud_{s}_pv_{index}_i",
                name=f"PV {index} Current",
                unit_of_measurement="A",
                device_class="current",
                state_class="measurement",
                expire_after=360,
                enabled_by_default=enabled,
            )
            sensor(
                f"{base_topic}/pv/{index}/w",
                f"{s}_pv{index}_w",
                f"deye_dummycloud_{s}_pv_{index}_w",
                name=f"PV {index} Power",
                unit_of_measurement="W",
                device_class="power",
                state_class="measurement",
                expire_after=360,
                enabled_by_default=enabled,
            )
            sensor(
                f"{base_topic}/pv/{index}/kWh_today",
                f"{s}_pv{index}_kWh_today",
                f"deye_dummycloud_{s}_pv_{index}_kWh_today",
                name=f"PV {index} Energy Today",
                unit_of_measurement="kWh",
                device_class="energy",
                state_class="total_increasing",
                enabled_by_default=enabled,
            )
            sensor(
                f"{base_topic}/pv/{index}/kWh_total",
                f"{s}_pv{index}_kWh_total",
                f"deye_dummycloud_{s}_pv_{index}_kWh_total",
                name=f"PV {index} Energy Total",
                unit_of_measurement="kWh",
                device_class="energy",
                state_class="total_increasing",
                enabled_by_default=enabled,
            )

        sensor(
            f"{base_topic}/grid/active_power_w",
            f"{s}_grid_active_power_w",
            f"deye_dummycloud_{s}_grid_active_power_w",
            name="Grid Power (Active)",
            unit_of_measurement="W",
            device_class="power",
            state_class="measurement",
            expire_after=360,
        )
        sensor(
            f"{base_topic}/grid/kWh_today",
            f"{s}_grid_kWh_today",
            f"deye_dummycloud_{s}_grid_energy_today",
            name="Grid Energy Today",
            unit_of_measurement="kWh",
            device_class="energy",
            state_class="total_increasing",
        )
        sensor(
            f"{base_topic}/grid/kWh_total",
            f"{s}_grid_kWh_total",
            f"deye_dummycloud_{s}_grid_energy_total",
            name="Grid Energy Total",
            unit_of_measurement="kWh",
            device_class="energy",
            state_class="total_increasing",
        )
        sensor(
            f"{base_topic}/grid/v",
            f"{s}_grid_v",
            f"deye_dummycloud_{s}_grid_v",
            name="Grid Voltage",
            unit_of_measurement="V",
            device_class="voltage",
            state_class="measurement",
            expire_after=360,
        )
        sensor(
            f"{base_topic}/grid/hz",
            f"{s}_grid_hz",
            f"deye_dummycloud_{s}_grid_hz",
            name="Grid Frequency",
            unit_of_measurement="Hz",
            device_class="frequency",
            state_class="measurement",
            expire_after=360,
        )
        sensor(
            f"{base_topic}/inverter/radiator_temperature",
            f"{s}_inverter_radiator_temperature",
            f"deye_dummycloud_{s}_inverter_radiator_temperature",
            name="Radiator Temperature",
            unit_of_measurement="\u00b0C",
            device_class="temperature",
            state_class="measurement",
            expire_after=360,
        )
        sensor(
            f"{base_topic}/inverter/efficiency",
            f"{s}_inverter_efficiency",
            f"deye_dummycloud_{s}_inverter_efficiency",
            name="Inverter Efficiency",
            unit_of_measurement="%",
            entity_category="diagnostic",
            icon="mdi:cog-transfer",
            state_class="measurement",
            expire_after=360,
            value_template="{{ value_json }}",
        )

        self.autoconf_timestamps[serial] = now

    def _publish_sensor(
        self,
        logger_serial: str,
        state_topic: str,
        *,
        suffix: str,
        unique_id: str,
        device: dict,
        **fields: Any,
    ) -> None:
        config: dict[str, Any] = {
            "state_topic": state_topic,
        }
        config.update(fields)
        config.update(
            {
                "object_id": unique_id,
                "unique_id": unique_id,
                "device": device,
            }
        )
        self.publish(
            f"homeassistant/sensor/deye_dummycloud_{logger_serial}/{suffix}/config",
            json.dumps(config),
            retain=True,
        )
