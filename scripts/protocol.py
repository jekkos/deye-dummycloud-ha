"""Deye logger cloud protocol: framing, parsing and response building.

Ported from the Node.js implementation in Hypfer/deye-microinverter-cloud-free
(dummycloud/src/Protocol.js). Field offsets were cross-checked against the
Kaitai spec in assets/deye_sun600.ksy of the same repository.

Packet layout:
    +--------+----------+----+----+-----+-----+--------+----+----------+----+
    | 0xa5   | len (u16) | u1 |typ | mR  | mQ  | sn (u32)|...| chk  | 0x15 |
    | magic  |  LE       |    |    |     |     |   LE    |...|      | magic |
    +--------+----------+----+----+-----+-----+--------+----+----------+----+
    |<------------- HEADER_LEN = 11 ------------------->|<payload>|<FOOTER>|
"""

from __future__ import annotations

import datetime
import struct
import time
from dataclasses import dataclass

HEADER_LEN = 11
FOOTER_LEN = 2
HEADER_MAGIC = 0xA5
FOOTER_MAGIC = 0x15

# Minimum size of a microinverter DATA payload (ends with the 6-byte time at
# offset 245).
MICROINVERTER_PAYLOAD_LEN = 256

MESSAGE_REQUEST_TYPES = {
    "HANDSHAKE": 0x41,
    "DATA": 0x42,
    "WIFI": 0x43,
    "HEARTBEAT": 0x47,
}

MESSAGE_RESPONSE_TYPES = {
    "HANDSHAKE": 0x11,
    "DATA": 0x12,
    # wifi info reply is 0x13?
    "WIFI": 0x13,
    "HEARTBEAT": 0x17,
}

DATA_PACKET_SCHEMAS = {
    "MICROINVERTER": 0x08,
    "HYBRIDINVERTER": 0x11,
    "RELAYMODULE": 0x13,
}


class ProtocolError(Exception):
    """Raised when a packet does not conform to the protocol."""


@dataclass(frozen=True)
class PacketHeader:
    payload_length: int
    unknown1: int
    type: int
    msg_id_response: int
    msg_id_request: int
    logger_serial: int


@dataclass(frozen=True)
class Packet:
    header: PacketHeader
    payload: bytes


def request_type_name(type_: int) -> str | None:
    return next(
        (name for name, value in MESSAGE_REQUEST_TYPES.items() if value == type_),
        None,
    )


def parse_header(buf: bytes) -> PacketHeader:
    if len(buf) < HEADER_LEN:
        raise ProtocolError("Packet too short to contain a header")
    magic = buf[0]
    if magic != HEADER_MAGIC:
        raise ProtocolError(f"Invalid header magic: {magic}")
    payload_length = struct.unpack_from("<H", buf, 1)[0]
    if payload_length + HEADER_LEN + FOOTER_LEN != len(buf):
        raise ProtocolError(
            "Payload length from header doesn't match packet length. Truncated?"
        )
    unknown1, type_, msg_id_response, msg_id_request = struct.unpack_from(
        "<BBBB", buf, 3
    )
    (logger_serial,) = struct.unpack_from("<I", buf, 7)
    return PacketHeader(
        payload_length=payload_length,
        unknown1=unknown1,
        type=type_,
        msg_id_response=msg_id_response,
        msg_id_request=msg_id_request,
        logger_serial=logger_serial,
    )


def parse_footer(buf: bytes) -> int:
    checksum = buf[-2]
    magic = buf[-1]
    if magic != FOOTER_MAGIC:
        raise ProtocolError(f"Invalid footer magic: {magic}")
    return checksum


def checksum(body: bytes) -> int:
    """Checksum over ``body``.

    ``body`` is the message *without* the trailing checksum byte and 0x15
    magic (the leading 0xa5 magic may be included; it is excluded from the
    sum). Matches the Node.js ``Protocol.checksum`` which sums ``buf[1:-2]``
    of the complete message.
    """
    return sum(body[1:]) & 0xFF


def parse_packet(buf: bytes) -> Packet:
    """Parse a complete, exactly-sized packet."""
    header = parse_header(buf)
    parse_footer(buf)
    return Packet(header=header, payload=bytes(buf[HEADER_LEN:-FOOTER_LEN]))


def try_parse_packet(buf: bytes) -> tuple[Packet | None, bytes]:
    """Parse the first complete packet from a stream buffer.

    Returns (packet, remaining). If the buffer does not yet hold a complete
    packet, packet is None and the full buffer is returned as remaining.
    Raises ProtocolError if the buffer starts with an invalid magic byte.
    """
    if not buf:
        return None, buf
    if buf[0] != HEADER_MAGIC:
        raise ProtocolError(f"Invalid header magic: {buf[0]}")
    if len(buf) < HEADER_LEN:
        return None, buf
    (payload_length,) = struct.unpack_from("<H", buf, 1)
    total = HEADER_LEN + payload_length + FOOTER_LEN
    if len(buf) < total:
        return None, buf
    return parse_packet(buf[:total]), buf[total:]


def parse_time(buf: bytes) -> datetime.datetime:
    return datetime.datetime(
        2000 + buf[0],
        buf[1],
        buf[2],
        buf[3],
        buf[4],
        buf[5],
        tzinfo=datetime.timezone.utc,
    )


def _u16le(payload: bytes, offset: int) -> int:
    return struct.unpack_from("<H", payload, offset)[0]


def _u16be(payload: bytes, offset: int) -> int:
    return struct.unpack_from(">H", payload, offset)[0]


def _u32le(payload: bytes, offset: int) -> int:
    return struct.unpack_from("<I", payload, offset)[0]


def _i16le(payload: bytes, offset: int) -> int:
    return struct.unpack_from("<h", payload, offset)[0]


def _i8(payload: bytes, offset: int) -> int:
    return struct.unpack_from("<b", payload, offset)[0]


def _ascii(payload: bytes, start: int, end: int) -> str:
    return payload[start:end].decode("ascii", "replace")


def _ascii_truncated(payload: bytes, start: int, end: int) -> str:
    raw = payload[start:end]
    null_index = raw.find(b"\x00")
    if null_index != -1:
        raw = raw[:null_index]
    return raw.decode("ascii", "replace")


def parse_data_packet_payload(payload: bytes) -> dict | None:
    if len(payload) < 2:
        return None
    if payload[1] != DATA_PACKET_SCHEMAS["MICROINVERTER"]:
        return None  # TODO: parse hybrid inverter and relay module packets
    if payload[0] & 0b10000000:
        # Seems to be one of these weird historic data packets from the SUN-M
        # series. Ignoring for now.
        return None
    if len(payload) < MICROINVERTER_PAYLOAD_LEN:
        return None
    return parse_data_packet_microinverter_payload(payload)


def parse_data_packet_microinverter_payload(payload: bytes) -> dict:
    def pv(index: int) -> dict:
        v = _u16le(payload, 85 + (index - 1) * 4) / 10
        i = _u16le(payload, 87 + (index - 1) * 4) / 10
        return {
            "v": v,
            "i": i,
            "w": round(v * i, 2),
            "kWh_today": _u16le(payload, 136 + (index - 1) * 2) / 10,
            "kWh_total": _u16be(payload, 145 + (index - 1) * 4) / 10,
        }

    return {
        "pv": {str(index): pv(index) for index in range(1, 5)},
        "grid": {
            "active_power_w": _u32le(payload, 59),
            "kWh_today": _u32le(payload, 33) / 100,
            "kWh_total": _u32le(payload, 37) / 10,
            "v": _u16le(payload, 45) / 10,
            "i": _u16le(payload, 51) / 10,
            "hz": _u16le(payload, 57) / 100,
        },
        "inverter": {
            "radiator_temp_celsius": _i16le(payload, 63) / 100,
        },
        "inverter_meta": {
            "rated_power_w": _u16be(payload, 129) / 10,
            "mppt_count": _i8(payload, 131),
            "phase_count": _i8(payload, 132),
            "startup_self_check_time": _u16be(payload, 243),
            "current_time": parse_time(payload[245:251]),
            "grid_freq_hz_overfreq_load_reduction_starting_point": _u16be(
                payload, 223
            )
            / 100,
            "grid_overfreq_load_reduction_percent": _u16be(payload, 225),
            "grid_v_limit_upper": _u16be(payload, 235) / 10,
            "grid_v_limit_lower": _u16be(payload, 237) / 10,
            "grid_freq_hz_limit_upper": _u16be(payload, 239) / 100,
            "grid_freq_hz_limit_lower": _u16be(payload, 241) / 100,
            "protocol_ver": _ascii(payload, 101, 109),
            "dc_master_fw_ver": _ascii(payload, 109, 117),
            "ac_fw_ver": _ascii(payload, 117, 125),
        },
    }


def parse_logger_packet_payload(payload: bytes) -> dict:
    return {
        "fw_ver": _ascii_truncated(payload, 19, 60),
        "ip": _ascii_truncated(payload, 65, 82),
        "ver": _ascii_truncated(payload, 89, 130),
        "ssid": _ascii_truncated(payload, 172, 210),
    }


def build_time_response(packet: Packet, timestamp: int | None = None) -> bytes:
    """Build the standard 23-byte response carrying the current unix time."""
    if timestamp is None:
        timestamp = int(time.time())
    header = packet.header
    response = bytearray(23)
    response[0] = HEADER_MAGIC
    struct.pack_into("<H", response, 1, 10)
    response[3] = header.unknown1
    response[4] = (header.type - 0x30) & 0xFF  # set type to response for that type
    response[5] = (header.msg_id_response + 1) & 0xFF
    response[6] = header.msg_id_request
    struct.pack_into("<I", response, 7, header.logger_serial)
    response[11] = packet.payload[0] if packet.payload else 0
    response[12] = 0x01
    struct.pack_into("<I", response, 13, timestamp & 0xFFFFFFFF)
    struct.pack_into("<I", response, 17, 0)
    response[21] = checksum(bytes(response[:-2]))
    response[22] = FOOTER_MAGIC
    return bytes(response)
