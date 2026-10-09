"""M3 VConsole tests: fake loopback TCP server only. Never port 29000 / live CS2."""
from pathlib import Path
import socket
import struct
import sys
import threading

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hammer_mcp import vconsole
from hammer_mcp.vconsole import (
    DEFAULT_VERSION,
    check_command,
    check_host,
    decode_packets,
    encode_cmnd,
    prnt_text,
    send_command,
)
from hammer_mcp.keyvalues import MapError
import server
from mcp.server.mcpserver.exceptions import ToolError


def start_fake(responses, capture):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    assert port != 29000

    def serve():
        conn, _ = srv.accept()
        with conn:
            conn.settimeout(5)
            header = b""
            while len(header) < 12:
                chunk = conn.recv(12 - len(header))
                if not chunk:
                    break
                header += chunk
            if len(header) == 12:
                msg, ver, length, handle = struct.unpack(">4sIHH", header)
                capture["type"] = msg
                capture["version"] = ver
                capture["length"] = length
                capture["handle"] = handle
                body = b""
                while len(body) < length - 12:
                    chunk = conn.recv(length - 12 - len(body))
                    if not chunk:
                        break
                    body += chunk
                capture["body"] = body
                for kind, text in responses:
                    if kind == "PRNT":
                        payload = struct.pack(">i", 1) + b"\x00" * 24 + text.encode() + b"\x00"
                    else:
                        payload = text.encode()
                    hdr = struct.pack(">4sIHH", kind.encode("ascii"), DEFAULT_VERSION, 12 + len(payload), 0)
                    conn.sendall(hdr + payload)
            try:
                conn.shutdown(socket.SHUT_WR)
            except OSError:
                pass
        srv.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return port, thread


def test_cmnd_header_bytes_exact():
    packet = encode_cmnd("echo hi")
    body = b"echo hi\x00"
    expected = struct.pack(">4sIHH", b"CMND", DEFAULT_VERSION, 12 + len(body), 0) + body
    assert packet == expected


def test_cmnd_received_and_prnt_collected():
    capture = {}
    port, thread = start_fake([("PRNT", "hello-console")], capture)
    result = send_command("echo hi", host="127.0.0.1", port=port, capture_seconds=2.0)
    thread.join(timeout=5)
    assert capture["type"] == b"CMND"
    assert capture["body"] == b"echo hi\x00"
    assert capture["version"] == DEFAULT_VERSION
    assert result["text"] == "hello-console"
    assert result["packet_bytes"] > 12


def test_unknown_packet_type_is_skipped():
    capture = {}
    port, thread = start_fake([("XXXX", "noise"), ("PRNT", "wanted")], capture)
    result = send_command("echo hi", host="127.0.0.1", port=port, capture_seconds=2.0)
    thread.join(timeout=5)
    assert result["text"] == "wanted"
    assert [p["type"] for p in result["packets"]] == ["XXXX", "PRNT"]


def test_expected_text_matching_via_tool():
    capture = {}
    port, thread = start_fake([("PRNT", "map loaded ok")], capture)
    result = server.hammer_console_send(
        "echo probe", port=port, host="127.0.0.1",
        capture_seconds=2.0, expected_text="loaded", dry_run=False,
    )
    thread.join(timeout=5)
    assert result["dry_run"] is False
    assert result["expected_matched"] is True
    capture2 = {}
    port2, thread2 = start_fake([("PRNT", "something else")], capture2)
    result2 = server.hammer_console_send(
        "echo probe", port=port2, host="127.0.0.1",
        capture_seconds=2.0, expected_text="missing-token", dry_run=False,
    )
    thread2.join(timeout=5)
    assert result2["expected_matched"] is False


def test_non_loopback_host_rejected():
    with pytest.raises(MapError):
        check_host("192.168.0.10")
    with pytest.raises((MapError, ToolError)):
        server.hammer_console_send("echo hi", host="192.168.0.10", dry_run=True)


def test_command_injection_rejected():
    for bad in ("echo a\necho b", "echo a;echo b", "echo a\x00b", "", "   "):
        with pytest.raises(MapError):
            check_command(bad)
    with pytest.raises((MapError, ToolError)):
        server.hammer_console_send("echo a;echo b", dry_run=True)


def test_map_path_traversal_rejected():
    for bad in ("../secret.vmap", "/maps/foo.vmap", "C:/maps/foo.vmap", "maps/../foo.vmap", "maps/foo.vmf"):
        with pytest.raises((MapError, ToolError)):
            server.hammer_open_map_in_editor(bad, dry_run=True)


def test_open_map_modes_build_expected_command():
    result = server.hammer_open_map_in_editor("maps/foo.vmap", mode="open_asset", dry_run=True)
    assert result["command"] == "open_asset maps/foo.vmap"
    result = server.hammer_open_map_in_editor("maps/foo.vmap", mode="current_map", dry_run=True)
    assert result["command"] == "open_current_map_in_hammer"
    result = server.hammer_open_map_in_editor("maps/foo.vmap", mode="show_tool_only", dry_run=True)
    assert result["command"] == "show_tool hammer"
    assert "bring it forward" in result["warning"]
    with pytest.raises((MapError, ToolError)):
        server.hammer_open_map_in_editor("maps/foo.vmap", mode="bogus", dry_run=True)


def test_dry_run_opens_no_socket(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("socket must not open on dry_run")

    monkeypatch.setattr(socket, "create_connection", fail)
    result = server.hammer_console_send("echo hi", dry_run=True)
    assert result["dry_run"] is True and "packet_hex" in result
    result = server.hammer_open_map_in_editor("maps/foo.vmap", dry_run=True)
    assert result["dry_run"] is True and "packet_hex" in result


def test_prnt_text_trims_at_nul():
    payload = struct.pack(">i", 1) + b"\x00" * 24 + b"abc\x00def"
    assert prnt_text(payload) == "abc"
    assert decode_packets(b"") == []
