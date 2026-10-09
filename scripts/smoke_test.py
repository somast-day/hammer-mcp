"""Exercise the real stdio protocol and optional locally installed Valve tools.

Creates its own maps/addon; never edits an existing user map. Full light baking
and GUI/playtesting are intentionally not part of this smoke test.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mcp import Client, StdioServerParameters
from hammer_mcp.environment import Environment
from hammer_mcp.maps import Document


async def main(live: bool):
    root = Path(__file__).resolve().parents[1]
    report = {"checks": [], "builds": [], "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    report_dir = Environment().state / "verification"
    report_dir.mkdir(parents=True, exist_ok=True)
    params = StdioServerParameters(command=sys.executable, args=[str(root / "server.py")],
                                   env={**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    async with Client(params) as client:
        async def call(tool_name: str, **arguments):
            result = await client.call_tool(tool_name, arguments)
            if result.is_error:
                raise RuntimeError(f"{tool_name}: {result.content}")
            data = result.structured_content
            if data is None:
                data = json.loads(result.content[0].text)
            return data

        listing = await client.list_tools()
        tool_names = [t.name for t in listing.tools] if hasattr(listing, "tools") else [t.name for t in listing]
        assert len(tool_names) == 18, tool_names
        health = await call("hammer_health")
        report["tool_count"] = len(tool_names)
        report["checks"].append("Real stdio initialization, tools/list and tools/call")
        print(f"Connected: {len(tool_names)} tools; games {[g['id'] for g in health['games']]}", flush=True)
        report["detected_games"] = health["games"]
        if not live:
            return
        games = {g["id"] for g in health["games"]}
        run_id = time.strftime("%Y%m%d_%H%M%S")
        if "cs2" in games:
            addon = await call("hammer_create_addon", game_id="cs2", name="hammer_mcp_smoke_" + run_id)
            path = str(Path(addon["maps_dir"]) / "mcp_test.vmap")
            created = await call("hammer_create_map", destination=path, game_id="cs2")
            before = await call("hammer_map_info", path=path, game_id="cs2")
            env = Environment()
            original_doc = Document(Path(path), env, env.game("cs2"))
            original_meshes = {n.scalar("id"): hashlib.sha256(original_doc.text[n.start:n.end].encode()).hexdigest()
                               for n in original_doc.nodes if n.type == "CDmePolygonMesh"}
            added = await call("hammer_add_entity", path=path, game_id="cs2", classname="logic_relay",
                               properties={"targetname": "mcp_relay"}, origin=[0, 0, 64], expected_hash=before["sha256"])
            edited = await call("hammer_set_entity", path=path, game_id="cs2", entity=added["entity"]["id"],
                                properties={"targetname": "mcp_relay_edited"}, origin=[32, 64, 96], expected_hash=added["sha256"])
            connected = await call("hammer_add_connection", path=path, game_id="cs2", entity=added["entity"]["id"],
                                   output="OnTrigger", target="mcp_relay_edited", input_name="Disable", delay=0.5,
                                   expected_hash=edited["sha256"])
            actual = await call("hammer_entities", path=path, game_id="cs2", name="mcp_relay_edited")
            assert actual["entities"][0]["origin"] == "32 64 96"
            assert actual["entities"][0]["connections"][0]["target"] == "mcp_relay_edited"
            modified_doc = Document(Path(path), env, env.game("cs2"))
            actual_meshes = {n.scalar("id"): hashlib.sha256(modified_doc.text[n.start:n.end].encode()).hexdigest()
                             for n in modified_doc.nodes if n.type == "CDmePolygonMesh"}
            assert original_meshes == actual_meshes, "Unrelated polygon mesh data changed"
            validation = await call("hammer_validate", path=path, game_id="cs2")
            assert validation["error_count"] == 0, validation
            report["checks"].append("CS2 binary DMX9: entity add/edit, transforms, I/O, all polygon mesh blocks unchanged")
            restored = await call("hammer_restore_backup", path=path, backup_id=added["backup_id"], expected_hash=connected["sha256"])
            assert restored["sha256"] == created["sha256"], "Restore was not byte-exact"
            report["checks"].append("CS2 byte-exact backup restore")
            # Re-add a relay so the real compiler validates generated DMX nodes and I/O.
            added = await call("hammer_add_entity", path=path, game_id="cs2", classname="logic_relay",
                               properties={"targetname": "mcp_relay"}, origin=[0, 0, 64], expected_hash=restored["sha256"])
            connected = await call("hammer_add_connection", path=path, game_id="cs2", entity=added["entity"]["id"],
                                   output="OnTrigger", target="mcp_relay", input_name="Disable", expected_hash=added["sha256"])
            report["cs2_map"] = path
            print("CS2 binary edits and geometry preservation passed. Starting native fast compile.", flush=True)
            job = await call("hammer_compile", path=path, game_id="cs2", expected_hash=connected["sha256"], timeout_seconds=300)
            while job["status"] in {"queued", "running"}:
                await asyncio.sleep(2)
                job = await call("hammer_compile_status", job_id=job["job_id"], tail_chars=1500)
            report["builds"].append(job)
            print(f"CS2 compile: {job['status']} ({job.get('elapsed_seconds')}s)", flush=True)
        if "css" in games:
            path = str(Environment().state / "maps" / f"mcp_room_{run_id}.vmf")
            created = await call("hammer_create_map", destination=path, game_id="css")
            report["css_map"] = path
            print("Starting native Source 1 VBSP/VVIS/VRAD compile.", flush=True)
            job = await call("hammer_compile", path=path, game_id="css", expected_hash=created["sha256"], timeout_seconds=180)
            while job["status"] in {"queued", "running"}:
                await asyncio.sleep(2)
                job = await call("hammer_compile_status", job_id=job["job_id"], tail_chars=1500)
            report["builds"].append(job)
            print(f"Source 1 compile: {job['status']} ({job.get('elapsed_seconds')}s)", flush=True)
        report_path = report_dir / ("smoke_" + run_id + ".json")
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Report: {report_path}", flush=True)
        if any(job["status"] != "completed" for job in report["builds"]):
            raise RuntimeError("A native compile failed. Inspect the report and compiler log.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Create isolated test maps/addon and run installed native compilers.")
    asyncio.run(main(parser.parse_args().live))
