import datetime
import struct
import time

import pytest

from protocol import (
    FOOTER_MAGIC,
    HEADER_MAGIC,
    MESSAGE_REQUEST_TYPES,
    ProtocolError,
    build_time_response,
    checksum,
    parse_data_packet_microinverter_payload,
    parse_logger_packet_payload,
    parse_packet,
    parse_time,
    try_parse_packet,
)


def make_packet(
    type_: int,
    payload: bytes,
    msg_id_response: int = 0x00,
    msg_id_request: int = 0x00,
    unknown1: int = 0x00,
    logger_serial: int = 4151234567,
) -> bytes:
    """Build a valid packet using the same rules as the Node implementation."""
    buf = bytearray()
    buf.append(HEADER_MAGIC)
    buf += struct.pack("<H", len(payload))
    buf.append(unknown1)
    buf.append(type_)
    buf.append(msg_id_response)
    buf.append(msg_id_request)
    buf += struct.pack("<I", logger_serial)
    buf += payload
    buf.append(checksum(bytes(buf)))
    buf.append(FOOTER_MAGIC)
    return bytes(buf)


class TestParsing:
    def test_parse_valid_heartbeat(self):
        packet = make_packet(MESSAGE_REQUEST_TYPES["HEARTBEAT"], b"\x00")
        parsed = parse_packet(packet)
        assert parsed.header.type == MESSAGE_REQUEST_TYPES["HEARTBEAT"]
        assert parsed.header.logger_serial == 4151234567
        assert parsed.header.payload_length == 1
        assert parsed.payload == b"\x00"

    def test_parse_invalid_header_magic(self):
        packet = make_packet(MESSAGE_REQUEST_TYPES["HEARTBEAT"], b"\x00")
        bad = b"\x00" + packet[1:]
        with pytest.raises(ProtocolError, match="Invalid header magic"):
            parse_packet(bad)

    def test_parse_wrong_payload_length(self):
        packet = make_packet(MESSAGE_REQUEST_TYPES["HEARTBEAT"], b"\x00")
        bad = bytearray(packet)
        bad[1] += 1
        with pytest.raises(ProtocolError, match="Payload length"):
            parse_packet(bytes(bad))

    def test_parse_invalid_footer_magic(self):
        packet = make_packet(MESSAGE_REQUEST_TYPES["HEARTBEAT"], b"\x00")
        bad = packet[:-1] + b"\x00"
        with pytest.raises(ProtocolError, match="Invalid footer magic"):
            parse_packet(bad)

    def test_parse_short_buffer(self):
        packet = make_packet(MESSAGE_REQUEST_TYPES["HEARTBEAT"], b"\x00")
        with pytest.raises(ProtocolError, match="too short"):
            parse_packet(packet[:8])

    def test_checksum(self):
        packet = make_packet(MESSAGE_REQUEST_TYPES["HEARTBEAT"], b"\x00")
        assert checksum(packet[:-2]) == packet[-2]

    def test_try_parse_incomplete(self):
        packet = make_packet(MESSAGE_REQUEST_TYPES["HEARTBEAT"], b"\x00")
        for cut in range(1, len(packet)):
            result, rest = try_parse_packet(packet[:cut])
            assert result is None
            assert rest == packet[:cut]

    def test_try_parse_complete(self):
        packet = make_packet(MESSAGE_REQUEST_TYPES["HEARTBEAT"], b"\x00")
        result, rest = try_parse_packet(packet)
        assert result is not None
        assert result.header.type == MESSAGE_REQUEST_TYPES["HEARTBEAT"]
        assert rest == b""

    def test_try_parse_two_packets_in_stream(self):
        first = make_packet(MESSAGE_REQUEST_TYPES["HEARTBEAT"], b"\x00")
        second = make_packet(MESSAGE_REQUEST_TYPES["HEARTBEAT"], b"\x01")
        result, rest = try_parse_packet(first + second)
        assert result is not None
        assert result.payload == b"\x00"
        result2, rest = try_parse_packet(rest)
        assert result2 is not None
        assert result2.payload == b"\x01"
        assert rest == b""

    def test_try_parse_invalid_magic_raises(self):
        with pytest.raises(ProtocolError):
            try_parse_packet(b"\xde\xad\xbe\xef")

    def test_parse_time(self):
        parsed = parse_time(bytes([30, 11, 17, 15, 30, 0]))
        assert parsed == datetime.datetime(
            2030, 11, 17, 15, 30, 0, tzinfo=datetime.timezone.utc
        )


class TestTimeResponse:
    def test_structure(self):
        packet = parse_packet(
            make_packet(
                MESSAGE_REQUEST_TYPES["DATA"],
                b"\x00\x08" + b"\x00" * 254,
                msg_id_response=0x02,
                msg_id_request=0x03,
                unknown1=0x01,
            )
        )
        response = build_time_response(packet, timestamp=0x01020304)
        assert len(response) == 23
        assert response[0] == HEADER_MAGIC
        assert struct.unpack_from("<H", response, 1)[0] == 10
        assert response[3] == 0x01
        # Response type = request type - 0x30
        assert response[4] == (MESSAGE_REQUEST_TYPES["DATA"] - 0x30) & 0xFF
        assert response[5] == 0x03  # msg_id_response + 1
        assert response[6] == 0x03  # msg_id_request
        assert struct.unpack_from("<I", response, 7)[0] == 4151234567
        assert response[11] == 0x00  # carried-over payload magic
        assert response[12] == 0x01
        assert struct.unpack_from("<I", response, 13)[0] == 0x01020304
        assert response[-2:] == bytes([response[-2], FOOTER_MAGIC])
        assert checksum(response[:-2]) == response[-2]

    def test_msg_id_wraps(self):
        packet = parse_packet(
            make_packet(
                MESSAGE_REQUEST_TYPES["HEARTBEAT"], b"\x00", msg_id_response=0xFF
            )
        )
        response = build_time_response(packet)
        assert response[5] == 0x00

    def test_timestamp_defaults_to_now(self):
        before = int(time.time())
        packet = parse_packet(make_packet(MESSAGE_REQUEST_TYPES["HEARTBEAT"], b"\x00"))
        response = build_time_response(packet)
        after = int(time.time())
        ts = struct.unpack_from("<I", response, 13)[0]
        assert before <= ts <= after


class TestDataPayload:
    @staticmethod
    def sample_payload() -> bytes:
        p = bytearray(256)
        p[0] = 0x00
        p[1] = 0x08

        def u16le(off, val):
            p[off : off + 2] = val.to_bytes(2, "little")

        def u16be(off, val):
            p[off : off + 2] = val.to_bytes(2, "big")

        def u32le(off, val):
            p[off : off + 4] = val.to_bytes(4, "little")

        u32le(33, 12345)  # grid kWh_today 123.45
        u32le(37, 987654)  # grid kWh_total 98765.4
        u16le(45, 2325)  # grid v 232.5
        u16le(51, 85)  # grid i 8.5
        u16le(57, 5000)  # grid hz 50.00
        u32le(59, 2356)  # grid active power W
        p[63:65] = (4550).to_bytes(2, "little", signed=True)  # 45.5 C

        # PV1
        u16le(85, 2300)  # v 230.0
        u16le(87, 45)  # i 4.5
        u16le(136, 53)  # kWh_today 5.3
        u16be(145, 12345)  # kWh_total 1234.5
        # PV2
        u16le(89, 0)
        u16le(91, 0)
        u16le(138, 0)
        u16be(149, 0)

        u16be(129, 600)  # rated power 60.0 W
        p[131] = 2  # mppt count
        p[132] = 1  # phase count
        p[101:109] = b"V1.0\0\0\0\0"
        p[109:117] = b"SUN600\0\0"
        p[117:125] = b"AC1.2\0\0\0"
        u16be(223, 1050)  # 10.50
        u16be(225, 90)
        u16be(235, 2500)  # 250.0
        u16be(237, 2100)  # 210.0
        u16be(239, 5150)  # 51.50
        u16be(241, 4950)  # 49.50
        u16be(243, 300)
        p[245:251] = bytes([30, 11, 17, 15, 30, 0])
        return bytes(p)

    def test_parse(self):
        data = parse_data_packet_microinverter_payload(self.sample_payload())
        assert data["pv"]["1"] == {
            "v": 230.0,
            "i": 4.5,
            "w": 1035.0,
            "kWh_today": 5.3,
            "kWh_total": 1234.5,
        }
        assert data["grid"] == {
            "active_power_w": 2356,
            "kWh_today": 123.45,
            "kWh_total": 98765.4,
            "v": 232.5,
            "i": 8.5,
            "hz": 50.0,
        }
        assert data["inverter"]["radiator_temp_celsius"] == 45.5
        meta = data["inverter_meta"]
        assert meta["rated_power_w"] == 60.0
        assert meta["mppt_count"] == 2
        assert meta["phase_count"] == 1
        assert meta["protocol_ver"] == "V1.0\x00\x00\x00\x00"
        assert meta["dc_master_fw_ver"] == "SUN600\x00\x00"
        assert meta["ac_fw_ver"] == "AC1.2\x00\x00\x00"
        assert meta["current_time"] == datetime.datetime(
            2030, 11, 17, 15, 30, 0, tzinfo=datetime.timezone.utc
        )
        assert meta["grid_v_limit_upper"] == 250.0
        assert meta["grid_freq_hz_limit_lower"] == 49.5

    def test_negative_temperature(self):
        p = bytearray(self.sample_payload())
        p[63:65] = (-1250).to_bytes(2, "little", signed=True)
        data = parse_data_packet_microinverter_payload(bytes(p))
        assert data["inverter"]["radiator_temp_celsius"] == -12.5


class TestLoggerPayload:
    def test_parse(self):
        p = bytearray(210)
        p[19 : 19 + 18] = b"MW3_16U_5406_1.53"
        p[65 : 65 + 14] = b"192.168.178.12"
        p[89 : 89 + 5] = b"v1.0"
        p[172 : 172 + 6] = b"mynet"
        data = parse_logger_packet_payload(bytes(p))
        assert data == {
            "fw_ver": "MW3_16U_5406_1.53",
            "ip": "192.168.178.12",
            "ver": "v1.0",
            "ssid": "mynet",
        }
