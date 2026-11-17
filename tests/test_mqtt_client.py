import json

import pytest

from mqtt_client import MqttClient


class FakeMqttClient:
    """Records publishes instead of talking to a broker."""

    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append(
            {"topic": topic, "payload": payload, "qos": qos, "retain": retain}
        )

    def username_pw_set(self, *args):
        pass


def make_mqtt_client(topic_prefix="deye-dummycloud", autoconf_interval=0):
    fake = FakeMqttClient()
    client = MqttClient(
        topic_prefix=topic_prefix,
        autoconf_interval=autoconf_interval,
        client=fake,
    )
    return client, fake


def sample_data_event(serial=123456, mppt_count=2, **overrides):
    from protocol import PacketHeader

    payload = {
        "pv": {
            "1": {"v": 230.0, "i": 4.5, "w": 1035.0, "kWh_today": 5.3, "kWh_total": 1234.5},
            "2": {"v": 0.0, "i": 0.0, "w": 0.0, "kWh_today": 0.0, "kWh_total": 0.0},
            "3": {"v": 0.0, "i": 0.0, "w": 0.0, "kWh_today": 0.0, "kWh_total": 0.0},
            "4": {"v": 0.0, "i": 0.0, "w": 0.0, "kWh_today": 0.0, "kWh_total": 0.0},
        },
        "grid": {
            "active_power_w": 2356,
            "kWh_today": 123.45,
            "kWh_total": 98765.4,
            "v": 232.5,
            "i": 8.5,
            "hz": 50.0,
        },
        "inverter": {"radiator_temp_celsius": 45.5},
        "inverter_meta": {
            "rated_power_w": 60.0,
            "mppt_count": mppt_count,
            "phase_count": 1,
            "startup_self_check_time": 300,
            "current_time": None,
            "grid_freq_hz_overfreq_load_reduction_starting_point": 10.5,
            "grid_overfreq_load_reduction_percent": 90,
            "grid_v_limit_upper": 250.0,
            "grid_v_limit_lower": 210.0,
            "grid_freq_hz_limit_upper": 51.5,
            "grid_freq_hz_limit_lower": 49.5,
            "protocol_ver": "V1.0",
            "dc_master_fw_ver": "SUN600",
            "ac_fw_ver": "AC1.2",
        },
    }
    payload.update(overrides)
    return {
        "header": PacketHeader(
            payload_length=256,
            unknown1=0,
            type=0x42,
            msg_id_response=1,
            msg_id_request=1,
            logger_serial=serial,
        ),
        "payload": payload,
        "meta": {"remoteAddress": "192.168.178.42"},
    }


class TestMqttPublish:
    def test_handle_data_publishes_pv_and_grid(self):
        client, fake = make_mqtt_client()
        client.handle_data(sample_data_event())
        topics = [entry["topic"] for entry in fake.published]

        for topic in (
            "deye-dummycloud/123456/pv/1/v",
            "deye-dummycloud/123456/pv/1/i",
            "deye-dummycloud/123456/pv/1/w",
            "deye-dummycloud/123456/pv/1/kWh_today",
            "deye-dummycloud/123456/pv/1/kWh_total",
            "deye-dummycloud/123456/pv/2/v",
            "deye-dummycloud/123456/grid/active_power_w",
            "deye-dummycloud/123456/grid/kWh_today",
            "deye-dummycloud/123456/grid/kWh_total",
            "deye-dummycloud/123456/grid/v",
            "deye-dummycloud/123456/grid/hz",
            "deye-dummycloud/123456/inverter/radiator_temperature",
            "deye-dummycloud/123456/inverter/efficiency",
        ):
            assert topic in topics

        def value_of(topic):
            return next(e["payload"] for e in fake.published if e["topic"] == topic)

        assert value_of("deye-dummycloud/123456/pv/1/v") == "230.0"
        assert value_of("deye-dummycloud/123456/pv/1/w") == "1035.0"
        assert value_of("deye-dummycloud/123456/grid/active_power_w") == "2356"
        assert value_of("deye-dummycloud/123456/grid/kWh_today") == "123.45"
        assert value_of(
            "deye-dummycloud/123456/inverter/radiator_temperature"
        ) == "45.5"

    def test_retain_on_energy_topics(self):
        client, fake = make_mqtt_client()
        client.handle_data(sample_data_event())
        retained = {
            e["topic"] for e in fake.published if e["retain"]
        }
        assert (
            "deye-dummycloud/123456/pv/1/kWh_today" in retained
        )
        assert (
            "deye-dummycloud/123456/pv/1/kWh_total" in retained
        )
        assert (
            "deye-dummycloud/123456/grid/kWh_today" in retained
        )
        assert "deye-dummycloud/123456/pv/1/v" not in retained

    def test_zero_kwh_total_not_published(self):
        client, fake = make_mqtt_client()
        client.handle_data(sample_data_event())
        topics = [entry["topic"] for entry in fake.published]
        assert "deye-dummycloud/123456/pv/2/kWh_total" not in topics

    def test_efficiency(self):
        client, fake = make_mqtt_client()
        client.handle_data(sample_data_event())
        eff = next(
            e["payload"]
            for e in fake.published
            if e["topic"] == "deye-dummycloud/123456/inverter/efficiency"
        )
        # 2356 W AC out of 1035 W DC input would be > 100%, so "null"
        assert eff == "null"

    def test_efficiency_valid(self):
        client, fake = make_mqtt_client()
        event = sample_data_event()
        # Make AC power < total DC power
        event["payload"]["pv"]["1"]["w"] = 5000.0
        event["payload"]["grid"]["active_power_w"] = 4500
        client.handle_data(event)
        eff = next(
            e["payload"]
            for e in fake.published
            if e["topic"] == "deye-dummycloud/123456/inverter/efficiency"
        )
        assert eff == "90.00"

    def test_topic_prefix_configurable(self):
        client, fake = make_mqtt_client(topic_prefix="custom")
        client.handle_data(sample_data_event())
        topics = [entry["topic"] for entry in fake.published]
        assert "custom/123456/pv/1/v" in topics


class TestAutoconf:
    def test_discovery_published(self):
        client, fake = make_mqtt_client(autoconf_interval=0)
        client.handle_data(sample_data_event(mppt_count=2))
        discovery = [
            e for e in fake.published if e["topic"].startswith("homeassistant/")
        ]
        # 4 sensors per pv slot * 2 slots + grid(5) + inverter(2) = 17
        assert len(discovery) == 17
        for entry in discovery:
            assert entry["retain"] is True
            json.loads(entry["payload"])  # must be valid JSON

    def test_discovery_sensor_fields(self):
        client, fake = make_mqtt_client(autoconf_interval=0)
        client.handle_data(sample_data_event(mppt_count=1))
        config = next(
            e
            for e in fake.published
            if e["topic"]
            == "homeassistant/sensor/deye_dummycloud_123456/123456_pv1_v/config"
        )
        data = json.loads(config["payload"])
        assert data["state_topic"] == "deye-dummycloud/123456/pv/1/v"
        assert data["name"] == "PV 1 Voltage"
        assert data["unique_id"] == "deye_dummycloud_123456_pv_1_v"
        assert data["object_id"] == "deye_dummycloud_123456_pv_1_v"
        assert data["unit_of_measurement"] == "V"
        assert data["device_class"] == "voltage"
        assert data["state_class"] == "measurement"
        assert data["expire_after"] == 360
        assert data["device"]["identifiers"] == ["deye_dummycloud_123456"]
        assert data["device"]["manufacturer"] == "Deye"

    def test_grid_kwh_discovery_keeps_node_unique_ids(self):
        client, fake = make_mqtt_client(autoconf_interval=0)
        client.handle_data(sample_data_event(mppt_count=1))
        entry = next(
            e
            for e in fake.published
            if e["topic"]
            == "homeassistant/sensor/deye_dummycloud_123456/123456_grid_kWh_today/config"
        )
        data = json.loads(entry["payload"])
        assert data["unique_id"] == "deye_dummycloud_123456_grid_energy_today"

    def test_disabled_by_default_from_pv3_on(self):
        client, fake = make_mqtt_client(autoconf_interval=0)
        client.handle_data(sample_data_event(mppt_count=4))
        pv1 = next(
            e
            for e in fake.published
            if e["topic"].endswith("123456_pv1_v/config")
        )
        pv3 = next(
            e
            for e in fake.published
            if e["topic"].endswith("123456_pv3_v/config")
        )
        assert json.loads(pv1["payload"])["enabled_by_default"] is True
        assert json.loads(pv3["payload"])["enabled_by_default"] is False

    def test_autoconf_not_republished_within_interval(self):
        client, fake = make_mqtt_client(autoconf_interval=1000)
        client.handle_data(sample_data_event())
        first = len(fake.published)
        client.handle_data(sample_data_event())
        # Second round: no new discovery, only state topics
        new_topics = [
            e["topic"] for e in fake.published[first:]
        ]
        assert not any(t.startswith("homeassistant/") for t in new_topics)

    def test_autoconf_republished_after_interval(self):
        client, fake = make_mqtt_client(autoconf_interval=1000)
        client.handle_data(sample_data_event())
        client.autoconf_timestamps[123456] -= 1001
        client.handle_data(sample_data_event())
        discovery = [
            e for e in fake.published if e["topic"].startswith("homeassistant/")
        ]
        assert len(discovery) == 34

    def test_discovery_uses_mppt_count(self):
        client, fake = make_mqtt_client(autoconf_interval=0)
        client.handle_data(sample_data_event(mppt_count=4))
        # 4 PVs * 5 sensors (v, i, w, kWh_today, kWh_total) + 7 non-PV = 27
        discovery = [
            e for e in fake.published if e["topic"].startswith("homeassistant/")
        ]
        assert len(discovery) == 27

    def test_handshake_is_noop(self):
        client, fake = make_mqtt_client(autoconf_interval=0)
        client.handle_handshake(
            {
                "header": None,
                "payload": {"fw_ver": "x", "ip": "y", "ver": "z", "ssid": "s"},
                "meta": {},
            }
        )
        assert fake.published == []


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
