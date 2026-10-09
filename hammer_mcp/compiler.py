from __future__ import annotations

import atexit
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid

from .environment import Environment
from .keyvalues import MapError
from .locking import atomic_json, file_lock, lock_is_held
from .storage import Store, sha256


_LOG_PREFIX = r"^\s*(?:(?:\[[^\]\r\n]*\]|\d{1,2}:\d{2}:\d{2}(?:\.\d+)?)\s*)*"
_FATAL = re.compile(_LOG_PREFIX + r"(?:\*+\s*)?fatal(?:\s+error)?(?=\s|:|$)", re.IGNORECASE)
_ERROR = re.compile(_LOG_PREFIX + r"error(?=\s|:|$)", re.IGNORECASE)
_WARNING = re.compile(_LOG_PREFIX + r"warning(?=\s|:|$)", re.IGNORECASE)
_LEAK = re.compile(_LOG_PREFIX + r"(?:\*{4}\s*leaked\s*\*{4}|Entity\s+.+\s+leaked[!.]?)\s*$", re.IGNORECASE)


def diagnose_log(text: str) -> dict:
    findings = []
    for number, line in enumerate(text.splitlines(), 1):
        lower = line.lower()
        rule = None
        if "loc tokens are no longer supported" in lower:
            rule = ("error", "source2_localization_mismatch", "The installed compiler rejects an obsolete localization file before map loading. Compare loose game/core/resource localization files with the current core VPK; repair the Workshop Tools installation or stale overrides.")
        elif _LEAK.search(line):
            rule = ("error", "leak", "Load the .lin pointfile and locate the opening to the void; check entity origins too.")
        elif (_FATAL.search(line) or _ERROR.search(line)) and not re.search(r"\b0 errors?\b|\berrors?\s*[:=]\s*0\b", lower):
            rule = ("error", "compiler_error", "Inspect this message and preceding context; later missing outputs can be secondary failures.")
        elif _WARNING.search(line):
            rule = ("warning", "compiler_warning", "Review the named resource or geometry; a warning does not automatically mean build failure.")
        elif "could not" in lower or "unable to" in lower or "failed to" in lower:
            rule = ("warning", "operation_failed", "Check the named path/resource and preceding diagnostics.")
        if rule:
            findings.append({"line": number, "severity": rule[0], "code": rule[1], "message": line[:1200], "hint": rule[2]})
    return {"error_count": sum(f["severity"] == "error" for f in findings),
            "warning_count": sum(f["severity"] == "warning" for f in findings),
            "findings": findings[:100], "truncated": len(findings) > 100,
            "first_error": next((f for f in findings if f["severity"] == "error"), None),
            "note": "Pattern-based triage; exit codes and produced artifacts determine build status."}


class Jobs:
    def __init__(self, env: Environment, store: Store):
        self.env = env
        self.store = store
        self.jobs: dict[str, dict] = {}
        self.processes: dict[str, subprocess.Popen] = {}
        self.cancel_events: dict[str, threading.Event] = {}
        self.workers: dict[str, threading.Thread] = {}
        self.mutex = threading.Lock()
        self.closing = False
        self.owner_id = uuid.uuid4().hex
        self.owner_lease = file_lock(self.env.state / "servers" / (self.owner_id + ".lock"))
        self.owner_lease.__enter__()
        self.lease_released = False
        atexit.register(self.shutdown)

    def plan(self, filename: str, game_id: str, mode: str) -> dict:
        game = self.env.game(game_id)
        path = self.env.resolve_map(filename)
        expected = ".vmap" if game.engine == "source2" else ".vmf"
        if path.suffix.lower() != expected:
            raise MapError(f"{game_id} requires {expected} files.")
        if mode not in {"check", "fast", "full"}:
            raise MapError("mode must be check, fast or full.")
        if game.engine == "source1" and mode == "check":
            raise MapError("Source 1 has no dependency-only check; use hammer_validate or fast mode.")
        required = ["resourcecompiler"] if game.engine == "source2" else ["vbsp", "vvis", "vrad"]
        for tool in required:
            if not Path(game.tools[tool]).is_file():
                raise MapError(f"Required compiler is missing: {game.tools[tool]}")
        if game.engine == "source2":
            # Resourcecompiler maps content paths into the corresponding game
            # directory. Only compile writable project maps, never stock files.
            self.env.resolve_map(filename, write=True)
            content = Path(game.install_dir) / "content"
            if not path.is_relative_to(content):
                raise MapError("Source 2 compilation requires a map under the game's content tree. Create a dedicated addon with hammer_create_addon first.")
            rel = path.relative_to(content)
            if len(rel.parts) < 3 or rel.parts[0] != Path(game.game_dir).name + "_addons":
                raise MapError("Compile Source 2 maps inside a dedicated content/<game>_addons/<addon>/ project.")
            addon = rel.parts[1]
            output = Path(game.install_dir) / "game" / rel.with_suffix(".vpk")
            command = [game.tools["resourcecompiler"], "-retail", "-noassert", "-nop4", "-vulkan",
                       "-game", game.game_dir, "-addon", addon, "-i", str(path)]
            if mode == "check":
                command += ["-dependency_check_only"]
            else:
                command += ["-fshallow", "-world", "-vis", "-phys"]
                command += ["-disableLightingCalculations"] if mode == "fast" else ["-bakelighting"]
            commands = [{"stage": "resourcecompiler", "argv": command}]
            return {"engine": game.engine, "source": str(path), "mode": mode, "commands": commands,
                    "expected_artifact": str(output), "outputs": "Matching game addon folder; existing compiled resources there may be replaced.",
                    "lighting": "Disabled for fast geometry testing." if mode == "fast" else "Compiler default/full lighting."}
        return {"engine": game.engine, "source": str(path), "mode": mode,
                "commands": [{"stage": name, "argv": [game.tools[name], *( ["-fast"] if name == "vvis" and mode == "fast" else []),
                    "-game", game.game_dir, "<isolated-build-map>"]} for name in required],
                "outputs": "Isolated job directory; not copied into the game's maps folder."}

    def start(self, filename: str, game_id: str, mode: str, expected_hash: str, timeout_seconds: int = 600) -> dict:
        if not 10 <= timeout_seconds <= 7200:
            raise MapError("timeout_seconds must be between 10 and 7200.")
        plan = self.plan(filename, game_id, mode)
        path = Path(plan["source"])
        raw = path.read_bytes()
        if sha256(raw) != expected_hash:
            raise MapError("Map hash mismatch. Read the map again before compiling.")
        with self.mutex:
            if self.closing:
                raise MapError("The MCP server is shutting down; reconnect before starting a compile.")
            if any(not j.get("finished", False) for j in self.jobs.values()):
                raise MapError("A compile is already running. Query or cancel it before starting another.")
            job_id = uuid.uuid4().hex
            directory = self.env.state / "builds" / job_id
            directory.mkdir(parents=True)
            if plan["engine"] == "source1":
                source = directory / path.name
                source.write_bytes(raw)
                for command in plan["commands"]:
                    command["argv"][-1] = str(source if command["stage"] == "vbsp" else source.with_suffix(".bsp"))
                plan["expected_artifact"] = str(source.with_suffix(".bsp"))
            job = {"job_id": job_id, "status": "queued", "stage": None, "created_at": datetime.now(timezone.utc).isoformat(),
                   "owner_id": self.owner_id, "owner_pid": os.getpid(),
                   "source_sha256": expected_hash, "log_path": str(directory / "compile.log"),
                   "job_path": str(directory / "job.json"), "plan": plan, "stage_results": [], "finished": False}
            self.jobs[job_id] = job
            self.cancel_events[job_id] = threading.Event()
            self._persist(job)
            worker = threading.Thread(target=self._run, args=(job_id, timeout_seconds), daemon=True)
            self.workers[job_id] = worker
            worker.start()
        return self.status(job_id)

    def _persist(self, job: dict) -> None:
        atomic_json(Path(job["job_path"]), job)

    def _run(self, job_id: str, timeout_seconds: int) -> None:
        job = self.jobs[job_id]
        start = time.monotonic()
        event = self.cancel_events[job_id]
        flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        artifact = Path(job["plan"]["expected_artifact"])
        process = None
        try:
            before_artifact = artifact.stat().st_mtime_ns if artifact.is_file() else None
            job["status"] = "running"
            with self.store.lock(Path(job["plan"]["source"])), open(job["log_path"], "wb") as log:
                if sha256(Path(job["plan"]["source"]).read_bytes()) != job["source_sha256"]:
                    raise MapError("Map changed before compilation began.")
                for entry in job["plan"]["commands"]:
                    if event.is_set():
                        job["status"] = "cancelled"
                        break
                    job["stage"] = entry["stage"]
                    self._persist(job)
                    log.write(f"\n--- {entry['stage']} ---\n".encode())
                    log.flush()
                    with self.mutex:
                        if event.is_set():
                            job["status"] = "cancelled"
                            break
                        process = subprocess.Popen(entry["argv"], cwd=str(Path(entry["argv"][0]).parent),
                                                   stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                                   shell=False, creationflags=flags,
                                                   start_new_session=os.name != "nt")
                        self.processes[job_id] = process
                    while process.poll() is None:
                        if event.wait(0.1) or time.monotonic() - start > timeout_seconds:
                            self._kill(process)
                            job["status"] = "cancelled" if event.is_set() else "timed_out"
                            break
                    code = process.wait()
                    with self.mutex:
                        self.processes.pop(job_id, None)
                    job["stage_results"].append({"stage": entry["stage"], "exit_code": code})
                    self._persist(job)
                    if job["status"] in {"cancelled", "timed_out"}:
                        break
                    if code != 0:
                        job["status"] = "failed"
                        break
                else:
                    job["stage"] = "verifying"
            text = Path(job["log_path"]).read_text(encoding="utf-8", errors="replace")
            job["diagnostics"] = diagnose_log(text)
            after_artifact = artifact.stat().st_mtime_ns if artifact.is_file() else None
            job["artifact_exists"] = artifact.is_file()
            job["artifact_updated"] = after_artifact is not None and after_artifact != before_artifact
            if job["status"] == "running":
                job["status"] = "completed"
            summary_matches = re.findall(r"\bOK:\s*(\d+) compiled,\s*(\d+) failed,\s*(\d+) skipped", text)
            if summary_matches:
                compiled, failed, skipped = map(int, summary_matches[-1])
                job["compiler_summary"] = {"compiled": compiled, "failed": failed, "skipped": skipped}
            if job["status"] == "completed" and job["plan"]["mode"] != "check":
                fatal = any(_FATAL.search(line) or _LEAK.search(line) for line in text.splitlines())
                source2_failed = job["plan"]["engine"] == "source2" and (not summary_matches or int(summary_matches[-1][1]) != 0)
                if not job["artifact_updated"] or fatal or source2_failed:
                    job["status"] = "failed"
            job["has_diagnostics"] = bool(job["diagnostics"]["findings"])
            job["source_changed_during_build"] = sha256(Path(job["plan"]["source"]).read_bytes()) != job["source_sha256"]
            if job["source_changed_during_build"] and job["status"] == "completed":
                job["status"] = "invalidated"
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = str(exc)
        finally:
            if process is not None:
                self._kill(process)
            with self.mutex:
                self.processes.pop(job_id, None)
            job["elapsed_seconds"] = round(time.monotonic() - start, 2)
            job["finished"] = True
            self._persist(job)

    def status(self, job_id: str, tail_chars: int = 6000) -> dict:
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise MapError("Invalid job ID.")
        if not 0 <= tail_chars <= 30000:
            raise MapError("tail_chars must be between 0 and 30000.")
        if job_id in self.jobs:
            result = json.loads(json.dumps(self.jobs[job_id]))
            if not result.get("finished", False) and result["status"] not in {"queued", "running"}:
                result["status"] = "running"
        else:
            path = self.env.state / "builds" / job_id / "job.json"
            if not path.is_file():
                raise MapError("Unknown job ID.")
            result = json.loads(path.read_text(encoding="utf-8"))
            if not result.get("finished", False):
                owner_id = result.get("owner_id", "")
                if re.fullmatch(r"[0-9a-f]{32}", owner_id):
                    result["owner_alive"] = lock_is_held(self.env.state / "servers" / (owner_id + ".lock"))
                    if not result["owner_alive"]:
                        result["status"] = "interrupted"
                        result["finished"] = True
                        result["error"] = "The owning MCP server exited before recording job completion."
                    elif result["status"] not in {"queued", "running"}:
                        result["status"] = "running"
                else:
                    result["status"] = "unknown"
                    result["error"] = "This legacy job has no owner information; its liveness cannot be determined."
        result["owned_by_this_server"] = job_id in self.jobs
        result["can_cancel"] = result["owned_by_this_server"] and not result.get("finished", False)
        if not result["owned_by_this_server"] and result.get("owner_alive"):
            result["control_note"] = "This job is running in another MCP server. Cancel it from the task that started it."
        log = Path(result["log_path"])
        if log.is_file() and tail_chars:
            with log.open("rb") as stream:
                stream.seek(max(0, log.stat().st_size - tail_chars * 4))
                result["log_tail"] = stream.read().decode("utf-8", errors="replace")[-tail_chars:]
        return result

    @staticmethod
    def _kill(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run([str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/taskkill.exe"),
                            "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
        else:
            import signal
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)

    def cancel(self, job_id: str) -> dict:
        if job_id not in self.jobs:
            raise MapError("This server has no running job with that ID.")
        if self.jobs[job_id]["status"] in {"queued", "running"}:
            self.cancel_events[job_id].set()
        return self.status(job_id)

    def shutdown(self) -> None:
        with self.mutex:
            self.closing = True
            for event in self.cancel_events.values():
                event.set()
            workers = list(self.workers.values())
        # Workers own their subprocess and map lock. Let their finally blocks
        # complete before releasing the server's liveness lease.
        for worker in workers:
            if worker is not threading.current_thread():
                worker.join(timeout=25)
        with self.mutex:
            if not any(worker.is_alive() for worker in workers) and not self.lease_released:
                self.owner_lease.__exit__(None, None, None)
                self.lease_released = True
                atexit.unregister(self.shutdown)
