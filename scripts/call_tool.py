"""Call a Hammer MCP tool over stdio using JSON arguments; useful for diagnostics.

python scripts/call_tool.py hammer_health
python scripts/call_tool.py hammer_map_info --arguments-file args.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from mcp import Client, StdioServerParameters


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("tool")
    parser.add_argument("--arguments-file", type=Path)
    args = parser.parse_args()
    arguments = json.loads(args.arguments_file.read_text(encoding="utf-8")) if args.arguments_file else {}
    root = Path(__file__).resolve().parents[1]
    params = StdioServerParameters(command=sys.executable, args=[str(root / "server.py")],
                                   env={**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    async with Client(params) as client:
        result = await client.call_tool(args.tool, arguments)
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
        failed = result.is_error
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
