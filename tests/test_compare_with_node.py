"""Byte-level parity test between the Node.js reference and the Python port.

Starts the reference Node.js DummyCloud TCP server (no MQTT) on port 10000 and
the Python DummyCloud on port 10001, feeds both the *same* packets, and asserts
the time responses are identical except for the (intentionally varying) unix
timestamp at bytes 13..16 and the checksum at byte 21 (which is derived from
that timestamp). Each response's checksum is verified to be self-consistent.

This is an integration test: it is skipped when Node.js or the reference clone
is not available.
"""

import asyncio
import os
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from dummy_cloud import DummyCloud  # noqa: E402

NODE_DIR = os.environ.get(
    "DEYE_NODE_DIR", "/tmp/opencode/deye-cloud/dummycloud"
)
NODE_HARNESS = os.path.join(NODE_DIR, "tcp_harness.js")
NODE_PORT = 10000
PY_PORT = 10001

HEARTBEAT = 0x47
HANDSHAKE = 0x41
DATA = 0x42
SERIAL = 4151234567

HARNESS_SOURCE = """\
// Standalone TCP harness: starts only the DummyCloud TCP server (no MQTT),
// so it can be compared byte-for-byte against the Python port.
const DummyCloud = require("./src/DummyCloud");

const cloud = new DummyCloud();
cloud.initialize();
console.log("tcp_harness ready on " + DummyCloud.PORT);
// Keep the event loop alive.
setInterval(() => {}, 3600 * 1000);
"""

pytestmark = pytest.mark.skipif(
    not (shutil.which("node") and os.path.isdir(os.path.join(NODE_DIR, "src"))),
    reason="Node.js or the reference clone is not available",
)


def ensure_harness() -> None:
    if not os.path.exists(NODE_HARNESS):
        with open(NODE_HARNESS, "w", encoding="utf-8") as fh:
            fh.write(HARNESS_SOURCE)


def make_packet(
    type_: int,
    payload: bytes,
    msg_id_response: int = 0x00,
    msg_id_request: int = 0x00,
) -> bytes:
    buf = bytearray([0xA5])
    buf += struct.pack("<H", len(payload))
    buf += bytes([0x00, type_, msg_id_response, msg_id_request])
    buf += struct.pack("<I", SERIAL)
    buf += payload
    buf += bytes([(sum(buf[1:]) & 0xFF), 0x15])
    return bytes(buf)


def wait_for_port(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=0.5)
            s.close()
            return True
        except OSError:
            time.sleep(0.15)
    return False


def send_and_read(port: int, data: bytes, expected_len: int = 23) -> bytes:
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        sock.sendall(data)
        chunks = []
        while sum(len(c) for c in chunks) < expected_len:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        sock.close()


def checksum_ok(resp: bytes) -> bool:
    return (sum(resp[1:21]) & 0xFF) == resp[21]


def assert_time_responses_parity(node_resp: bytes, py_resp: bytes, label: str) -> None:
    assert len(node_resp) == 23, f"{label}: node response length {len(node_resp)}"
    assert len(py_resp) == 23, f"{label}: python response length {len(py_resp)}"
    # Identical except the timestamp (13..16) and the checksum (21).
    assert node_resp[:13] == py_resp[:13], (
        f"{label}: header mismatch\nnode: {node_resp.hex()}\npy:   {py_resp.hex()}"
    )
    assert node_resp[17:21] == py_resp[17:21], (
        f"{label}: trailing-zero mismatch\nnode: {node_resp.hex()}\npy:   {py_resp.hex()}"
    )
    assert node_resp[22] == 0x15 and py_resp[22] == 0x15, f"{label}: footer magic"
    assert checksum_ok(node_resp), f"{label}: node checksum invalid"
    assert checksum_ok(py_resp), f"{label}: python checksum invalid"


@pytest.fixture(scope="module")
def node_server():
    ensure_harness()
    proc = subprocess.Popen(
        ["node", NODE_HARNESS],
        cwd=NODE_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if not wait_for_port(NODE_PORT):
        proc.terminate()
        out = b""
        if proc.stdout:
            try:
                out = proc.stdout.read()
            finally:
                proc.kill()
        pytest.fail("Node server did not start:\n" + out.decode(errors="replace"))
    yield
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="module")
def py_server():
    loop = asyncio.new_event_loop()
    cloud = DummyCloud(port=PY_PORT, host="127.0.0.1")
    ready = threading.Event()

    def run() -> None:
        asyncio.set_event_loop(loop)

        async def main() -> None:
            await cloud.start()
            ready.set()
            await cloud.serve_forever()

        try:
            loop.run_until_complete(main())
        except (asyncio.CancelledError, Exception):
            pass

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert ready.wait(20), "Python server did not start"
    yield
    try:
        asyncio.run_coroutine_threadsafe(cloud.stop(), loop).result(timeout=5)
    except Exception:
        pass
    thread.join(timeout=2)


def test_heartbeat_parity(node_server, py_server):
    packet = make_packet(HEARTBEAT, b"\x00")
    assert_time_responses_parity(
        send_and_read(NODE_PORT, packet), send_and_read(PY_PORT, packet), "heartbeat"
    )


def test_handshake_parity(node_server, py_server):
    packet = make_packet(HANDSHAKE, b"\x00" * 210)
    assert_time_responses_parity(
        send_and_read(NODE_PORT, packet), send_and_read(PY_PORT, packet), "handshake"
    )


def test_data_parity(node_server, py_server):
    payload = bytearray(256)
    payload[1] = 0x08  # microinverter schema
    payload[245:251] = bytes([30, 11, 17, 15, 30, 0])  # valid current_time
    packet = make_packet(DATA, bytes(payload))
    assert_time_responses_parity(
        send_and_read(NODE_PORT, packet), send_and_read(PY_PORT, packet), "data"
    )


def test_data_parity_zeroed(node_server, py_server):
    # All-zero payload (invalid date): both must still reply with a time response.
    payload = bytearray(256)
    payload[1] = 0x08
    packet = make_packet(DATA, bytes(payload))
    assert_time_responses_parity(
        send_and_read(NODE_PORT, packet), send_and_read(PY_PORT, packet), "data-zeroed"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
