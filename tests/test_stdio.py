import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest
from mcp import Client, StdioServerParameters

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hammer_mcp.locking import process_is_alive


def kill_tree(pid):
    if not process_is_alive(pid):
        return
    if os.name == "nt":
        subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/taskkill.exe"),
                        "/PID", str(pid), "/T", "/F"], capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=10, check=True)
    else:
        os.kill(pid, signal.SIGKILL)


@pytest.mark.parametrize("forced", [False, True], ids=["normal-disconnect", "forced-termination"])
def test_other_client_can_observe_and_resume_after_owner_exits(tmp_path, forced):
    source = tmp_path / "source.vmf"
    source.write_text('world { "id" "1" "classname" "worldspawn" }\n'
                      'entity { "id" "2" "classname" "logic_relay" "targetname" "relay" }')
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"state_dir": str(tmp_path / "state"), "project_roots": [str(tmp_path)]}))
    params = StdioServerParameters(command=sys.executable,
        args=[str(Path(__file__).with_name("stdio_server.py"))],
        env={**os.environ, "HAMMER_TEST_DIR": str(tmp_path), "HAMMER_MCP_CONFIG": str(config),
             "HAMMER_MCP_STATE": str(tmp_path / "state"), "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})

    async def call(client, name, **arguments):
        result = await client.call_tool(name, arguments)
        assert not result.is_error, result.content
        return result.structured_content or json.loads(result.content[0].text)

    async def exercise():
        child_pid = None
        try:
            async with Client(params) as observer:
                async with Client(params) as owner:
                    info = await call(owner, "hammer_map_info", path=str(source))
                    job = await call(owner, "hammer_compile", path=str(source), game_id="test",
                                     expected_hash=info["sha256"])
                    deadline = time.monotonic() + 8
                    while not (tmp_path / "child.pid").exists() and time.monotonic() < deadline:
                        await asyncio.sleep(0.02)
                    child_pid = int((tmp_path / "child.pid").read_text())
                    assert process_is_alive(child_pid)
                    remote = await call(observer, "hammer_compile_status", job_id=job["job_id"])
                    assert remote["status"] == "running" and remote["owner_alive"]
                    assert not remote["can_cancel"] and not remote["owned_by_this_server"]
                    denied = await observer.call_tool("hammer_set_entity", {
                        "path": str(source), "entity": "relay", "expected_hash": info["sha256"],
                        "properties": {"targetname": "must_not_write"}})
                    assert denied.is_error and "locked" in str(denied.content).lower()
                    if forced:
                        kill_tree(job["owner_pid"])
                        if os.name != "nt":
                            kill_tree(child_pid)
                assert not process_is_alive(child_pid)
                final = await call(observer, "hammer_compile_status", job_id=job["job_id"])
                assert final["status"] == ("interrupted" if forced else "cancelled")
                assert final["finished"]
                edited = await call(observer, "hammer_set_entity", path=str(source), entity="relay",
                                    expected_hash=info["sha256"], properties={"targetname": "resumed"})
                assert edited["changed"]
                assert edited["entity"]["targetname"] == "resumed"
        finally:
            if child_pid is not None:
                kill_tree(child_pid)

    asyncio.run(exercise())
