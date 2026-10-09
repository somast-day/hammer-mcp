from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid

from .keyvalues import MapError
from .locking import LockBusy, file_lock, process_is_alive


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Store:
    def __init__(self, state: Path):
        self.state = state
        (state / "backups").mkdir(parents=True, exist_ok=True)
        (state / "locks").mkdir(parents=True, exist_ok=True)

    @contextmanager
    def lock(self, path: Path):
        key = hashlib.sha256(str(path.resolve()).casefold().encode()).hexdigest()
        lockfile = self.state / "locks" / (key + ".lock")
        try:
            with file_lock(lockfile) as stream:
                raw = stream.read()
                if raw:
                    try:
                        metadata = json.loads(raw)
                    except (ValueError, UnicodeError):
                        raise MapError(f"Unrecognized legacy lock; inspect {lockfile} before recovering it.") from None
                    if metadata.get("version") != 2:
                        pid = metadata.get("pid")
                        if not isinstance(pid, int) or process_is_alive(pid):
                            raise MapError("Map is locked by a legacy MCP server. Finish its operation and reconnect that server.")
                stream.seek(0)
                stream.truncate()
                stream.write(json.dumps({"version": 2, "pid": os.getpid(), "path": str(path)}).encode())
                stream.flush()
                yield
        except LockBusy:
            raise MapError("Map is locked by another MCP operation. Query or cancel the active operation before retrying.") from None

    def commit(self, path: Path, original: bytes | None, updated: bytes, expected_hash: str | None) -> dict:
        if original is not None and (not expected_hash or sha256(original) != expected_hash):
            raise MapError("Map hash mismatch. Read the map again before editing.")
        with self.lock(path):
            current = path.read_bytes() if path.exists() else None
            if current != original:
                raise MapError("The map changed on disk during this operation. No changes were written.")
            if original == updated:
                return {"path": str(path), "changed": False, "sha256": sha256(updated)}
            backup_id = None
            if original is not None:
                backup_id = uuid.uuid4().hex
                backup = self.state / "backups" / backup_id
                backup.with_suffix(".bin").write_bytes(original)
                backup.with_suffix(".json").write_text(json.dumps({
                    "path": str(path), "sha256": sha256(original), "bytes": len(original),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }), encoding="utf-8")
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=".hammer-mcp-", suffix=path.suffix, dir=path.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(updated)
                    stream.flush()
                    os.fsync(stream.fileno())
                # Check once more after writing the temporary file. Editor saves
                # are not under our lock, so callers still need to reload Hammer.
                if (path.read_bytes() if path.exists() else None) != original:
                    raise MapError("An external save was detected. No changes were written.")
                if original is None:
                    # Hard-link installation is atomic and fails if a creator
                    # races us. Both files reside on the same filesystem.
                    os.link(name, path)
                else:
                    os.replace(name, path)
            finally:
                Path(name).unlink(missing_ok=True)
            return {"path": str(path), "changed": True, "sha256": sha256(updated),
                    "backup_id": backup_id, "bytes": len(updated),
                    "editor_action": "Reload this map in Hammer before saving it from the editor."}

    def restore(self, path: Path, backup_id: str, expected_hash: str) -> dict:
        if not re_backup_id(backup_id):
            raise MapError("Invalid backup ID.")
        base = self.state / "backups" / backup_id
        metadata = json.loads(base.with_suffix(".json").read_text(encoding="utf-8"))
        if Path(metadata["path"]).resolve() != path.resolve():
            raise MapError("This backup belongs to another map.")
        data = base.with_suffix(".bin").read_bytes()
        if sha256(data) != metadata["sha256"]:
            raise MapError("Backup checksum mismatch.")
        return self.commit(path, path.read_bytes(), data, expected_hash)


def re_backup_id(value: str) -> bool:
    return len(value) == 32 and all(c in "0123456789abcdef" for c in value)
