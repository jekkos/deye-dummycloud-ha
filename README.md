# Deye Dummy Cloud - Home Assistant Add-on

Replaces the Deye/Solarman cloud for Deye microinverter loggers. The logger
pushes its data to this add-on over plain TCP; the add-on republishes it on
MQTT with automatic Home Assistant sensor discovery. No cloud, no TLS, no
account — your inverter data stays on your LAN.

This is a Python port of [Hypfer/deye-microinverter-cloud-free](https://github.com/Hypfer/deye-microinverter-cloud-free),
packaged as a Home Assistant add-on.

## Features

- Emulates the Deye/Solarman cloud on TCP port 10000 (configurable)
- Publishes PV, grid and inverter data to MQTT
- Automatic Home Assistant sensor discovery (re-published every 4 hours)
- Works with Deye/Solarman loggers (e.g. SUN600G3-EU) and microinverters in "standalone" mode
- Optional MQTT authentication
- Self-healing: waits for the MQTT broker and reconnects automatically

## Requirements

- Home Assistant OS / Supervised / Docker (add-on store)
- MQTT broker (the built-in Mosquitto is recommended)
- A Deye/Solarman logger or compatible microinverter
- The inverter's firmware must still be able to report to a "Server A"
  (some new firmwares no longer offer plain-HTTP cloud endpoints — this
  add-on only works with firmware that can point at a custom server)

## Installation

### HACS (Recommended)

1. Open HACS → Add-ons → Repositories
2. Click **Add Repository**
3. Paste: `https://github.com/jekkos/deye-dummycloud`
4. Click **Add**
5. Find **Deye Dummy Cloud** and click **Install**

### Custom Repository

1. Go to: **Settings → Apps → App Store → Repositories**
2. Click **Add Repository**
3. Paste: `https://github.com/jekkos/deye-dummycloud`
4. Click **Add**
5. Find **Deye Dummy Cloud** and click **Install**

## Inverter setup

1. Install and start the add-on.
2. Open the logger's web interface (its IP is visible in the add-on log on
   the first handshake, or find it on your LAN).
3. In the cloud/server settings (often `config_hide.html`), point
   **Server A** (or the "optional server") at the IP address of your
   Home Assistant host.
4. Leave the port at `10000` unless you changed `listen_port`.
5. Make sure the logger can actually reach the add-on (no router NAT in
   between; the inverter must be on the same LAN as Home Assistant).

## Configuration

After installation, go to the add-on settings:

| Option | Description | Default |
|--------|-------------|---------|
| `listen_port` | TCP port the inverter connects to | `10000` |
| `mqtt_host` | MQTT broker hostname (leave empty for built-in) | `core-mosquitto` |
| `mqtt_port` | MQTT broker port | `1883` |
| `mqtt_username` | MQTT username (leave empty for anonymous) | |
| `mqtt_password` | MQTT password (leave empty for anonymous) | |
| `topic_prefix` | MQTT topic prefix | `deye-dummycloud` |
| `log_level` | `trace`, `debug`, `info`, `warn`, `error` or `fatal` | `info` |

**Note:** The add-on uses the host network so the inverter can reach it on
its configured port. Only configure `mqtt_host`, `mqtt_username`, and
`mqtt_password` if you want to use an external broker.

## MQTT Topics

Topics are published under `<topic_prefix>/<logger_serial>/...`, for example
with the default prefix and a serial of `123456`:

| Topic | Description |
|-------|-------------|
| `deye-dummycloud/123456/pv/1/v` | PV 1 voltage (V) |
| `deye-dummycloud/123456/pv/1/i` | PV 1 current (A) |
| `deye-dummycloud/123456/pv/1/w` | PV 1 power (W) |
| `deye-dummycloud/123456/pv/1/kWh_today` | PV 1 energy today (kWh, retained) |
| `deye-dummycloud/123456/pv/1/kWh_total` | PV 1 energy total (kWh, retained) |
| `deye-dummycloud/123456/pv/2/...` | PV 2 (same structure) |
| `deye-dummycloud/123456/grid/active_power_w` | Grid active power (W) |
| `deye-dummycloud/123456/grid/kWh_today` | Grid energy today (kWh, retained) |
| `deye-dummycloud/123456/grid/kWh_total` | Grid energy total (kWh, retained) |
| `deye-dummycloud/123456/grid/v` | Grid voltage (V) |
| `deye-dummycloud/123456/grid/hz` | Grid frequency (Hz) |
| `deye-dummycloud/123456/inverter/radiator_temperature` | Radiator temperature (°C) |
| `deye-dummycloud/123456/inverter/efficiency` | Inverter efficiency (%) |

Sensors for PV 1–4, grid and inverter are discovered automatically by Home
Assistant via MQTT Discovery (re-published every 4 hours). PV slots that are
not in use on your inverter are discovered disabled by default.

## Updating

1. Go to **Settings → Apps**
2. Click on **Deye Dummy Cloud**
3. Click **Update** in the top-right corner

Or use HACS → Add-ons → Updates.

## Troubleshooting

### "Cannot connect to MQTT broker"
- Enable the built-in Mosquitto: **Settings → System → Services → MQTT**
- Or configure an external broker in the add-on settings

### No data from the inverter
- Check the add-on log: the logger sends a handshake on connect; you should
  see `New connection from <inverter IP>` followed by a handshake line.
- Make sure the inverter's "Server A" points at the Home Assistant host IP
  (not `localhost`) and at port `10000`.
- Verify connectivity from another device: `nc <ha-host> 10000`.
- Some newer firmwares refuse to report to non-Deye servers; an older
  firmware is required.

### Entities disappear from Home Assistant
- Discovery config is re-published every 4 hours; entities with `expire_after`
  drop out 6 minutes after the last data. This is expected when the inverter
  is offline — data resumes when it reconnects.

## Development

### Set up a local environment
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt pytest
```

### Run the tests
```bash
.venv/bin/python -m pytest tests/
```

`tests/test_compare_with_node.py` is a byte-level parity test against the
reference Node.js implementation. It is skipped automatically when Node.js or
the reference clone is not available. To enable it, clone the original project
and point `DEYE_NODE_DIR` at it (a `tcp_harness.js` must be present there):
```bash
git clone https://github.com/Hypfer/deye-microinverter-cloud-free /tmp/deye-cloud
DEYE_NODE_DIR=/tmp/deye-cloud/dummycloud .venv/bin/python -m pytest tests/test_compare_with_node.py -v
```

### Build locally
```bash
docker build -t deye-dummycloud .
```

### Run locally
```bash
docker run -d --name deye-dummycloud \
  --network host \
  -e MQTT_HOST=your-broker \
  -e MQTT_USERNAME=your-username \
  -e MQTT_PASSWORD=your-password \
  -e LISTEN_PORT=10000 \
  deye-dummycloud
```

## License

Apache-2.0, based on the work of Hypfer (see
[Hypfer/deye-microinverter-cloud-free](https://github.com/Hypfer/deye-microinverter-cloud-free)).
