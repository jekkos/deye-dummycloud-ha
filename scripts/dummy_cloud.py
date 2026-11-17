"""TCP server that emulates the Deye/Solarman cloud for a microinverter logger.

Ported from the Node.js implementation in Hypfer/deye-microinverter-cloud-free
(dummycloud/src/DummyCloud.js).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

from protocol import (
    MESSAGE_REQUEST_TYPES,
    Packet,
    ProtocolError,
    build_time_response,
    parse_data_packet_payload,
    parse_logger_packet_payload,
    request_type_name,
    try_parse_packet,
)

logger = logging.getLogger(__name__)

DEFAULT_PORT = 10000
DEFAULT_HOST = "0.0.0.0"

DataCallback = Callable[[dict], None]
HandshakeCallback = Callable[[dict], None]


class DummyCloud:
    def __init__(self, port: int = DEFAULT_PORT, host: str = DEFAULT_HOST):
        self.port = port
        self.host = host
        self._server: asyncio.Server | None = None
        self._on_data: DataCallback | None = None
        self._on_handshake: HandshakeCallback | None = None

    def on_data(self, callback: DataCallback) -> None:
        self._on_data = callback

    def on_handshake(self, callback: HandshakeCallback) -> None:
        self._on_handshake = callback

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_connection, self.host, self.port
        )
        logger.info("Starting deye-dummycloud on port %s", self.port)

    async def serve_forever(self) -> None:
        if self._server is None:
            raise RuntimeError("Server was not started")
        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        peer_str = f"{peer[0]}:{peer[1]}" if peer else "unknown"
        logger.info("New connection from %s", peer_str)
        buffer = bytearray()
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    break
                logger.debug(
                    "Data received from client %s: %s", peer_str, bytes(chunk).hex()
                )
                buffer.extend(chunk)
                while buffer:
                    try:
                        packet, rest = try_parse_packet(bytes(buffer))
                    except ProtocolError as exc:
                        logger.error(
                            "Error while parsing packet from %s: %s", peer_str, exc
                        )
                        buffer = buffer[1:]  # drop the bad byte and resynchronize
                        continue
                    if packet is None:
                        break  # incomplete packet, wait for more data
                    buffer = bytearray(rest)
                    response = self._process_packet(packet, peer_str)
                    if response is not None:
                        logger.debug("Response %s", response.hex())
                        writer.write(response)
                        await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            logger.debug("Connection with %s reset", peer_str)
        finally:
            logger.info("Closing connection with %s", peer_str)
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionResetError, BrokenPipeError, OSError):
                pass

    def _process_packet(self, packet: Packet, peer_str: str) -> bytes | None:
        type_name = request_type_name(packet.header.type)
        if type_name:
            logger.debug('Received packet of type "%s"', type_name)
        else:
            logger.warning(
                'Received packet of unknown type "0x%02x"', packet.header.type
            )

        if packet.header.type == MESSAGE_REQUEST_TYPES["DATA"]:
            try:
                data = parse_data_packet_payload(packet.payload)
            except Exception:
                logger.exception(
                    "Error parsing DATA payload from %s; still replying", peer_str
                )
                data = None
            if data is not None:
                logger.debug("DATA packet data from %s: %s", peer_str, data)
                if self._on_data is not None:
                    self._on_data(
                        {
                            "header": packet.header,
                            "payload": data,
                            "meta": {"remoteAddress": peer_str.split(":")[0]},
                        }
                    )
            else:
                logger.debug("Discarded data packet")
            return build_time_response(packet)

        if packet.header.type == MESSAGE_REQUEST_TYPES["HANDSHAKE"]:
            try:
                data = parse_logger_packet_payload(packet.payload)
            except Exception:
                logger.exception(
                    "Error parsing HANDSHAKE payload from %s; still replying",
                    peer_str,
                )
                data = None
            if data is not None:
                logger.debug("Handshake packet data from %s: %s", peer_str, data)
                if self._on_handshake is not None:
                    self._on_handshake(
                        {"header": packet.header, "payload": data, "meta": {}}
                    )
            return build_time_response(packet)

        # WIFI, HEARTBEAT and anything else all get the time response
        return build_time_response(packet)
