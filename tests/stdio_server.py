"""Real entry point with only the external compiler replaced by a test child."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server

folder = Path(os.environ["HAMMER_TEST_DIR"])
source = folder / "source.vmf"
pid_file = folder / "child.pid"
code = f"import os,time;from pathlib import Path;Path({str(pid_file)!r}).write_text(str(os.getpid()));time.sleep(30)"
server.hammer.jobs.plan = lambda *args: {
    "engine": "source2", "source": str(source), "mode": "fast",
    "expected_artifact": str(folder / "output.vpk"),
    "commands": [{"stage": "test_child", "argv": [sys.executable, "-c", code]}],
}
server.mcp.run(transport="stdio")
