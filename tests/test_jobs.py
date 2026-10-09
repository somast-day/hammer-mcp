from pathlib import Path
import json
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hammer_mcp.compiler import Jobs
from hammer_mcp.environment import Environment
from hammer_mcp.keyvalues import MapError
from hammer_mcp.storage import Store, sha256


@pytest.fixture
def job_fixture(tmp_path, monkeypatch):
    source = tmp_path / "source.vmf"
    source.write_bytes(b"test source")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"state_dir": str(tmp_path / "state")}))
    jobs = Jobs(Environment(config, games=[]), Store(tmp_path / "state"))
    artifact = tmp_path / "output.vmap_c"

    def start(code, timeout=10):
        plan = {"engine": "source2", "source": str(source), "mode": "fast",
                "expected_artifact": str(artifact),
                "commands": [{"stage": "probe", "argv": [sys.executable, "-c", code]}]}
        monkeypatch.setattr(jobs, "plan", lambda *args: plan)
        return jobs.start(str(source), "fake", "fast", sha256(source.read_bytes()), timeout)

    yield jobs, source, artifact, start
    jobs.shutdown()


def finish(jobs, job_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = jobs.status(job_id)
        if status["status"] not in {"queued", "running"}:
            return status
        time.sleep(0.03)
    pytest.fail("Job failed to reach a terminal status")


def test_nonzero_compiler_is_failure(job_fixture):
    jobs, _, _, start = job_fixture
    job = start("import sys;print('FATAL ERROR: intentional test');sys.exit(3)")
    result = finish(jobs, job["job_id"])
    assert result["status"] == "failed"
    assert result["stage_results"][0]["exit_code"] == 3
    assert result["diagnostics"]["first_error"]["message"].endswith("intentional test")


def test_missing_artifact_is_failure_even_with_exit_zero(job_fixture):
    jobs, _, _, start = job_fixture
    job = start("print('Process exited without creating a map')")
    result = finish(jobs, job["job_id"])
    assert result["status"] == "failed"
    assert not result["artifact_exists"]


def test_real_output_and_exit_zero_are_required(job_fixture):
    jobs, _, artifact, start = job_fixture
    job = start(f"from pathlib import Path;Path({str(artifact)!r}).write_bytes(b'compiled');print('OK: 1 compiled, 0 failed, 0 skipped')")
    result = finish(jobs, job["job_id"])
    assert result["status"] == "completed"
    assert result["artifact_updated"]
    assert result["elapsed_seconds"] >= 0


def test_cancellation_stops_own_child(job_fixture):
    jobs, _, _, start = job_fixture
    job = start("import time;time.sleep(60)")
    jobs.cancel(job["job_id"])
    assert finish(jobs, job["job_id"])["status"] == "cancelled"
    assert not jobs.processes


def test_filename_cannot_fail_a_successful_compile(job_fixture):
    jobs, _, artifact, start = job_fixture
    job = start(f"from pathlib import Path;Path({str(artifact)!r}).write_bytes(b'compiled');print('Loading C:/maps/fatal.vmap');print('OK: 1 compiled, 0 failed, 0 skipped')")
    result = finish(jobs, job["job_id"])
    assert result["status"] == "completed"
    assert result["diagnostics"]["error_count"] == 0


@pytest.mark.parametrize("message", ["FATAL ERROR: broken world", "[Compiler] FATAL: broken world", "**** leaked ****"])
def test_fatal_diagnostic_fails_even_with_output_and_exit_zero(job_fixture, message):
    jobs, _, artifact, start = job_fixture
    job = start(f"from pathlib import Path;Path({str(artifact)!r}).write_bytes(b'compiled');print({message!r});print('OK: 1 compiled, 0 failed, 0 skipped')")
    assert finish(jobs, job["job_id"])["status"] == "failed"


def test_shutdown_finishes_worker_and_releases_map(job_fixture):
    jobs, source, _, start = job_fixture
    job = start("import time;time.sleep(30)")
    deadline = time.monotonic() + 5
    while not jobs.processes and time.monotonic() < deadline:
        time.sleep(0.01)
    assert jobs.processes
    jobs.shutdown()
    assert all(not worker.is_alive() for worker in jobs.workers.values())
    assert not jobs.processes
    assert jobs.status(job["job_id"])["status"] == "cancelled"
    with jobs.store.lock(source):
        pass
    with pytest.raises(MapError, match="shutting down"):
        start("print('must not run')")
