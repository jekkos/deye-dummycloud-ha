import asyncio
import socket
import struct
import time

from dummy_cloud import DummyCloud
from protocol import (
    FOOTER_MAGIC,
    HEADER_MAGIC,
    MESSAGE_REQUEST_TYPES,
    checksum,
)

SERIAL = 4151234567


def make_packet(
    type_: int,
    payload: bytes,
    msg_id_response: int = 0x00,
    msg_id_request: int = 0x00,
) -> bytes:
    buf = bytearray()
    buf.append(HEADER_MAGIC)
    buf += struct.pack("<H", len(payload))
    buf.append(0x00)
    buf.append(type_)
    buf.append(msg_id_response)
    buf.append(msg_id_request)
    buf += struct.pack("<I", SERIAL)
    buf += payload
    buf.append(checksum(bytes(buf)))
    buf.append(FOOTER_MAGIC)
    return bytes(buf)


def run_with_server(exercise):
    async def main():
        server = await asyncio.start_server(
            DummyCloud(host="127.0.0.1")._handle_connection, "127.0.0.1", 0
        )
        port = server.sockets[0].getsockname()[1]
        try:
            await exercise(port)
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(main())


def connect_and_send(port: int, data: bytes) -> bytes:
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    sock.sendall(data)
    chunks = []
    try:
        while sum(len(c) for c in chunks) < 23:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        sock.close()
    return b"".join(chunks)


def test_heartbeat_gets_time_response():
    async def exercise(port):
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            connect_and_send,
            port,
            make_packet(MESSAGE_REQUEST_TYPES["HEARTBEAT"], b"\x00"),
        )
        assert len(response) == 23
        assert response[0] == HEADER_MAGIC
        assert struct.unpack_from("<H", response, 1)[0] == 10
        assert response[4] == (MESSAGE_REQUEST_TYPES["HEARTBEAT"] - 0x30) & 0xFF
        assert response[-1] == FOOTER_MAGIC
        assert checksum(response[:-2]) == response[-2]
        ts = struct.unpack_from("<I", response, 13)[0]
        assert abs(ts - time.time()) < 60

    run_with_server(exercise)


def test_handshake_and_data():
    async def exercise(port):
        loop = asyncio.get_event_loop()
        handshake_packet = make_packet(
            MESSAGE_REQUEST_TYPES["HANDSHAKE"], b"\x00" * 210
        )
        response = await loop.run_in_executor(
            None, connect_and_send, port, handshake_packet
        )
        assert len(response) == 23

        data_payload = bytearray(256)
        data_payload[1] = 0x08
        response = await loop.run_in_executor(
            None,
            connect_and_send,
            port,
            make_packet(MESSAGE_REQUEST_TYPES["DATA"], bytes(data_payload)),
        )
        assert len(response) == 23
        assert response[11] == 0x00  # carried-over payload magic

    run_with_server(exercise)


def test_two_packets_in_one_send():
    async def exercise(port):
        loop = asyncio.get_event_loop()
        first = make_packet(MESSAGE_REQUEST_TYPES["HEARTBEAT"], b"\x00")
        second = make_packet(MESSAGE_REQUEST_TYPES["HEARTBEAT"], b"\x01")
        response = await loop.run_in_executor(
            None, connect_and_send, port, first + second
        )
        assert len(response) == 46  # two time responses
        assert response[23] == HEADER_MAGIC  # second response starts here

    run_with_server(exercise)


def test_packet_split_across_sends():
    async def exercise(port):
        loop = asyncio.get_event_loop()
        packet = make_packet(MESSAGE_REQUEST_TYPES["HEARTBEAT"], b"\x00")
        sock = await loop.run_in_executor(
            None, lambda: socket.create_connection(("127.0.0.1", port), timeout=5)
        )
        try:
            for part in (packet[:5], packet[5:12], packet[12:]):
                sock.sendall(part)
                await asyncio.sleep(0.05)
            sock.settimeout(5)
            chunks = []
            while sum(len(c) for c in chunks) < 23:
                chunk = await loop.run_in_executor(None, sock.recv, 4096)
                if not chunk:
                    break
                chunks.append(chunk)
            assert len(b"".join(chunks)) == 23
        finally:
            sock.close()

    run_with_server(exercise)


def test_callback_events():
    data_events = []
    handshake_events = []
    cloud = DummyCloud(host="127.0.0.1")
    cloud.on_data(data_events.append)
    cloud.on_handshake(handshake_events.append)

    async def exercise(port):
        loop = asyncio.get_event_loop()
        data_payload = bytearray(256)
        data_payload[1] = 0x08
        # Valid current_time (2030-11-17 15:30:00 UTC) so the payload parses
        data_payload[245:251] = bytes([30, 11, 17, 15, 30, 0])
        for packet in (
            make_packet(MESSAGE_REQUEST_TYPES["HANDSHAKE"], b"\x00" * 210),
            make_packet(MESSAGE_REQUEST_TYPES["DATA"], bytes(data_payload)),
        ):
            await loop.run_in_executor(None, connect_and_send, port, packet)

    async def main():
        server = await asyncio.start_server(
            cloud._handle_connection, "127.0.0.1", 0
        )
        port = server.sockets[0].getsockname()[1]
        try:
            await exercise(port)
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(main())

    assert len(handshake_events) == 1
    assert handshake_events[0]["payload"] == {
        "fw_ver": "",
        "ip": "",
        "ver": "",
        "ssid": "",
    }
    assert len(data_events) == 1
    assert data_events[0]["header"].logger_serial == SERIAL
    assert data_events[0]["payload"]["pv"]["1"]["v"] == 0.0
    assert "remoteAddress" in data_events[0]["meta"]
