"""Local VConsole2 client for loopback-only console commands.

No live CS2 connection happens at import time. Every live send requires an
explicit caller opt-in (see server.py ``dry_run`` defaults).
"""
from __future__ import annotations

from dataclasses import dataclass
import socket
import struct

from .keyvalues import MapError

HEADER_FORMAT = ">4sIHH"
HEADER_SIZE = 12
DEFAULT_VERSION = 0x00D40000
DEFAULT_PORT = 29000
CONNECT_TIMEOUT = 2.0
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(slots=True)
class Packet:
    msg_type: str
    version: int
    handle: int
    payload: bytes


def check_host(host: str) -> str:
    if host not in LOOPBACK_HOSTS:
        raise MapError("VConsole host must be a loopback address (127.0.0.1, localhost or ::1).")
    return host


def check_command(command: str) -> str:
    if not command or not command.strip():
        raise MapError("Console command must be a non-empty single command.")
    if any(part in command for part in ("\n", "\r", "\0", ";")):
        raise MapError("Console command must be one command without newline, NUL or ';'.")
    return command


def encode_cmnd(command: str, version: int = DEFAULT_VERSION, handle: int = 0) -> bytes:
    payload = check_command(command).encode("utf-8") + b"\x00"
    length = HEADER_SIZE + len(payload)
    if length > 0xFFFF:
        raise MapError("Console command is too long for one VConsole packet.")
    return struct.pack(HEADER_FORMAT, b"CMND", version, length, handle) + payload


def decode_packets(data: bytes) -> list[Packet]:
    packets: list[Packet] = []
    offset = 0
    while offset + HEADER_SIZE <= len(data):
        msg_type, version, length, handle = struct.unpack_from(HEADER_FORMAT, data, offset)
        if length < HEADER_SIZE:
            break
        end = offset + length
        if end > len(data):
            break
        try:
            name = msg_type.decode("ascii")
        except UnicodeDecodeError:
            name = repr(msg_type)
        packets.append(Packet(name, version, handle, data[offset + HEADER_SIZE:end]))
        offset = end
    return packets


def prnt_text(payload: bytes) -> str:
    text = payload[28:] if len(payload) > 28 else b""
    return text.split(b"\x00", 1)[0].decode("utf-8", errors="replace")


def send_command(command: str, host: str = "127.0.0.1", port: int = DEFAULT_PORT,
                 capture_seconds: float = 1.0, version: int = DEFAULT_VERSION) -> dict:
    check_host(host)
    payload = encode_cmnd(command, version)
    if capture_seconds < 0 or capture_seconds > 30:
        raise MapError("capture_seconds must be between 0 and 30.")
    family = socket.AF_INET6 if host == "::1" else socket.AF_INET
    received = bytearray()
    with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT) as stream:
        stream.sendall(payload)
        stream.settimeout(max(capture_seconds, 0.1))
        try:
            while True:
                chunk = stream.recv(65536)
                if not chunk:
                    break
                received += chunk
        except (socket.timeout, TimeoutError):
            pass
    packets = decode_packets(bytes(received))
    texts = [prnt_text(packet.payload) for packet in packets if packet.msg_type == "PRNT"]
    return {"command": command, "host": host, "port": port,
            "packet_bytes": len(received),
            "packets": [{"type": packet.msg_type, "length": HEADER_SIZE + len(packet.payload)}
                        for packet in packets],
            "text": "".join(texts)}
