from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import subprocess
import sys
import threading
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hammer_mcp.environment import Environment
from hammer_mcp.keyvalues import MapError
from hammer_mcp.storage import Store, sha256


def spawn(code, *arguments):
    return subprocess.Popen([sys.executable, "-c", code, *map(str, arguments)],
        cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def wait_for_file(path):
    deadline = time.monotonic() + 8
    while not path.is_file() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert path.is_file(), f"Child did not reach checkpoint: {path}"


def test_project_additions_from_concurrent_servers_are_merged(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"state_dir": str(tmp_path / "state"), "max_map_mb": 96, "custom": "preserve"}))
    observer = Environment(config, games=[])
    projects = [tmp_path / f"project-{i}" for i in range(4)]
    gate = tmp_path / "go"
    code = '''import sys,time
from pathlib import Path
from hammer_mcp.environment import Environment
config, root, ready, gate = map(Path, sys.argv[1:])
env = Environment(config, games=[])
ready.touch()
while not gate.exists(): time.sleep(0.01)
env.add_project(str(root))
'''
    children = []
    try:
        for i, project in enumerate(projects):
            project.mkdir()
            children.append(spawn(code, config, project, tmp_path / f"ready-{i}", gate))
        for i in range(len(children)):
            wait_for_file(tmp_path / f"ready-{i}")
        gate.touch()
        for child in children:
            stdout, stderr = child.communicate(timeout=10)
            assert child.returncode == 0, (stdout, stderr)
        saved = json.loads(config.read_text())
        assert set(saved["project_roots"]) == set(map(str, projects))
        assert saved["max_map_mb"] == 96 and saved["custom"] == "preserve"
        path = projects[0] / "new.vmf"
        assert observer.resolve_map(str(path), write=True, exists=False) == path
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)


def test_map_lock_survives_contention_but_not_process_death(tmp_path):
    state, path, ready = tmp_path / "state", tmp_path / "map.vmf", tmp_path / "ready"
    path.write_bytes(b"original")
    code = '''import sys,time
from pathlib import Path
from hammer_mcp.storage import Store
state, path, ready = map(Path, sys.argv[1:])
with Store(state).lock(path):
    ready.touch()
    time.sleep(30)
'''
    child = spawn(code, state, path, ready)
    store = Store(state)
    try:
        wait_for_file(ready)
        with pytest.raises(MapError, match="locked"):
            store.commit(path, b"original", b"changed", sha256(b"original"))
        assert path.read_bytes() == b"original"
        child.kill()
        child.communicate(timeout=5)
        store.commit(path, b"original", b"changed", sha256(b"original"))
        assert path.read_bytes() == b"changed"
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


def test_legacy_lock_is_recovered_only_after_owner_exits(tmp_path):
    path = tmp_path / "map.vmf"
    path.write_bytes(b"original")
    store = Store(tmp_path / "state")
    key = hashlib.sha256(str(path.resolve()).casefold().encode()).hexdigest()
    lock = store.state / "locks" / (key + ".lock")
    child = spawn("import time;time.sleep(30)")
    try:
        lock.write_text(json.dumps({"pid": child.pid, "path": str(path)}))
        with pytest.raises(MapError, match="legacy"):
            with store.lock(path):
                pass
        child.kill()
        child.communicate(timeout=5)
        store.commit(path, b"original", b"recovered", sha256(b"original"))
        assert path.read_bytes() == b"recovered"
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


def test_map_lock_also_excludes_another_thread_in_the_same_server(tmp_path):
    path = tmp_path / "map.vmf"
    path.write_bytes(b"original")
    store = Store(tmp_path / "state")
    ready, release = threading.Event(), threading.Event()

    def hold():
        with store.lock(path):
            ready.set()
            assert release.wait(5)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(hold)
        try:
            assert ready.wait(5)
            with pytest.raises(MapError, match="locked"):
                store.commit(path, b"original", b"changed", sha256(b"original"))
        finally:
            release.set()
            future.result(timeout=5)
