"""Install the isolated Python runtime and write local MCP launch configuration."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import venv


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-install", action="store_true", help="Use an already provisioned runtime.")
    parser.add_argument("--dev", action="store_true", help="Also install pytest.")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    state = Path(os.environ.get("HAMMER_MCP_STATE", str(Path.home() / ".local/share/hammer-mcp"))).resolve()
    runtime = state / "venv"
    python = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        venv.create(runtime, with_pip=True)
    if not args.skip_install:
        requirements = root / ("requirements-dev.txt" if args.dev else "requirements.txt")
        subprocess.run([str(python), "-m", "pip", "install", "-r", str(requirements)], check=True)
    subprocess.run([str(python), "-c", "import mcp, srctools"], check=True)
    config = {"mcpServers": {"hammer": {
        "command": str(python), "args": [str(root / "server.py")],
        "env": {"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", "HAMMER_MCP_STATE": str(state)},
        "startup_timeout_sec": 30, "tool_timeout_sec": 180,
    }}}
    (root / ".mcp.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Runtime: {python}\nMCP config: {root / '.mcp.json'}")
    print("The generated config uses absolute local paths. Re-run setup after moving the source folder.")


if __name__ == "__main__":
    main()
