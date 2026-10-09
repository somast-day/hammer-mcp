from __future__ import annotations

from collections import Counter
import fnmatch
import json
from pathlib import Path
import re

from . import __version__
from .compiler import Jobs
from .environment import Environment
from .keyvalues import MapError
from .maps import Document, create_room
from .storage import Store


class Hammer:
    def __init__(self, env: Environment | None = None):
        self.env = env or Environment()
        self.store = Store(self.env.state)
        self.jobs = Jobs(self.env, self.store)

    def health(self) -> dict:
        self.env.refresh_projects()
        return {"version": __version__, "transport": "stdio", "platform": "native processes, no Wine",
                "games": [g.describe() for g in self.env.games],
                "project_roots": [str(p) for p in self.env.project_roots], "state_dir": str(self.env.state),
                "config_path": str(self.env.config_path),
                "limits": {"map_mb": self.env.max_map_bytes // 1024 // 1024, "converted_text_mb": self.env.max_text_bytes // 1024 // 1024},
                "capabilities": ["inspect VMF/VMAP", "edit entity properties and transforms", "create point entities",
                                 "append entity I/O", "create maps from templates", "compile jobs", "FGD search", "backup/restore"],
                "limitations": ["No live Hammer UI or unsaved document access.",
                    "No general Source 2 mesh construction/editing tool in this version.",
                    "Validation covers structure and I/O references, not full geometry or gameplay.",
                    "Binary Source 2 conversion requires the game's dmxconvert.exe.",
                    "Reload maps after external edits; external editor saves are not coordinated atomically."]}

    def document(self, filename: str, game_id: str | None = None, write: bool = False) -> Document:
        path = self.env.resolve_map(filename, write=write)
        game = self.env.game(game_id) if game_id else None
        if game and path.suffix.lower() == ".vmf" and game.engine != "source1":
            raise MapError("VMF editing needs a Source 1 game profile. Convert/import into Source 2 separately.")
        return Document(path, self.env, game)

    def list_maps(self, game_id: str | None, query: str, limit: int) -> dict:
        if not 1 <= limit <= 500:
            raise MapError("limit must be between 1 and 500.")
        self.env.refresh_projects()
        roots = list(self.env.project_roots) + [self.env.state / "maps"]
        if game_id:
            game = self.env.game(game_id)
            if game.content_dir:
                roots += [Path(game.content_dir), Path(game.install_dir) / "content" / (Path(game.game_dir).name + "_addons")]
            else:
                roots += [Path(game.install_dir) / "sdk_content", Path(game.game_dir) / "maps"]
        found = []
        seen = set()
        for root in roots:
            if not root.is_dir():
                continue
            for pattern in ("*.vmf", "*.vmap"):
                for path in root.rglob(pattern):
                    if not path.is_file() or path.resolve() in seen or query.casefold() not in str(path).casefold():
                        continue
                    seen.add(path.resolve())
                    found.append({"path": str(path.resolve()), "bytes": path.stat().st_size,
                                  "within_size_limit": path.stat().st_size <= self.env.max_map_bytes})
                    if len(found) >= limit:
                        return {"maps": found, "limit_reached": True}
        return {"maps": found, "limit_reached": False}

    def entities(self, filename: str, game_id: str | None, classname: str | None, name: str | None,
                 offset: int, limit: int) -> dict:
        if offset < 0 or not 1 <= limit <= 200:
            raise MapError("offset must be nonnegative and limit between 1 and 200.")
        doc = self.document(filename, game_id)
        records = [doc.record(n) for n in doc.entity_nodes()]
        if classname:
            records = [r for r in records if r["classname"].casefold() == classname.casefold()]
        if name:
            records = [r for r in records if name.casefold() in r["targetname"].casefold()]
        return {"path": str(doc.path), "sha256": doc.original_hash, "total": len(records),
                "entities": records[offset:offset + limit],
                "next_offset": offset + limit if offset + limit < len(records) else None}

    def validate(self, filename: str, game_id: str | None) -> dict:
        doc = self.document(filename, game_id)
        records = [doc.record(n) for n in doc.entity_nodes()]
        names = {r["targetname"] for r in records if r["targetname"]}
        findings = []
        if doc.typed:
            all_ids = [("dmx_element", n.scalar("id")) for n in doc.nodes if n.scalar("id")]
        else:
            namespaces = {"world": "entity", "entity": "entity", "solid": "solid", "side": "side", "group": "group"}
            all_ids = [(namespaces[n.type], n.scalar("id")) for n in doc.nodes
                       if n.type in namespaces and n.scalar("id")]
        for (namespace, value), count in Counter(all_ids).items():
            if count > 1:
                findings.append({"severity": "error", "code": "duplicate_id", "id": value, "count": count,
                                 "namespace": namespace})
        for record in records:
            if not record["classname"]:
                findings.append({"severity": "error", "code": "missing_classname", "id": record["id"]})
            for connection in record["connections"]:
                if connection.get("malformed"):
                    findings.append({"severity": "error", "code": "malformed_connection", "id": record["id"], "connection": connection})
                    continue
                target = connection["target"]
                if target and not target.startswith("!") and connection.get("target_type", "7") == "7":
                    if not any(fnmatch.fnmatchcase(n.casefold(), target.casefold()) for n in names):
                        findings.append({"severity": "warning", "code": "unresolved_target", "id": record["id"], "target": target,
                                         "note": "May be a runtime-created entity or an instance target; verify in game."})
        return {"summary": doc.summary(), "findings": findings[:200], "truncated": len(findings) > 200,
                "error_count": sum(f["severity"] == "error" for f in findings),
                "checks": ["parseable structure", "unique DMX element IDs" if doc.typed else "unique IDs within each VMF namespace",
                           "entity classname present", "I/O target references"],
                "not_checked": ["leaks", "mesh validity", "FGD property types", "packed assets", "performance", "gameplay"]}

    def create_addon(self, game_id: str, name: str) -> dict:
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,47}", name):
            raise MapError("Addon name must be 3-48 lowercase letters, digits or underscores, starting with a letter.")
        game = self.env.game(game_id)
        if game.engine != "source2":
            raise MapError("Addon creation is for Source 2.")
        mod = Path(game.game_dir).name
        content = Path(game.install_dir) / "content" / (mod + "_addons") / name
        output = Path(game.install_dir) / "game" / (mod + "_addons") / name
        if content.exists() or output.exists():
            raise MapError("Addon already exists. Register its content directory with hammer_add_project.")
        (content / "maps").mkdir(parents=True)
        (output / "maps").mkdir(parents=True)
        (output / "addoninfo.txt").write_text(
            '<!-- kv3 encoding:text:version{e21c7f3c-8a33-41c5-9977-a76d3a32aa0d} format:generic:version{7412167c-06e9-4698-aff2-e63eb59037e7} -->\n{\n}\n', encoding="utf-8")
        self.env.add_project(str(content))
        return {"addon": name, "content_dir": str(content), "game_dir": str(output), "maps_dir": str(content / "maps")}

    def create_map(self, destination: str, game_id: str, template: str | None,
                   width: int, depth: int, height: int) -> dict:
        path = self.env.resolve_map(destination, write=True, exists=False)
        if path.exists():
            raise MapError("Destination already exists. Choose a new map filename.")
        game = self.env.game(game_id)
        if path.suffix.lower() != (".vmap" if game.engine == "source2" else ".vmf"):
            raise MapError("Destination extension does not match the game's engine.")
        if template:
            source = self.env.resolve_map(template)
            if source.suffix.lower() != path.suffix.lower():
                raise MapError("Template and destination formats must match.")
            Document(source, self.env, game)
            data = source.read_bytes()
        elif game.engine == "source2":
            source = Path(game.content_dir) / "maps/templates/template_defuse.vmap"
            if not source.is_file():
                raise MapError("No default template was found for this game. Supply template explicitly.")
            data = source.read_bytes()
        else:
            data = create_room(width, depth, height)
        return self.store.commit(path, None, data, None)

    def fgd_search(self, game_id: str, query: str, limit: int) -> dict:
        if not query.strip() or not 1 <= limit <= 50:
            raise MapError("Provide a nonempty query and a limit between 1 and 50.")
        game = self.env.game(game_id)
        hits = []
        seen = set()
        for folder in game.fgd_dirs:
            root = Path(folder)
            if not root.is_dir():
                continue
            for path in root.glob("*.fgd"):
                if path.resolve() in seen:
                    continue
                seen.add(path.resolve())
                lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
                for index, line in enumerate(lines):
                    if query.casefold() in line.casefold():
                        hits.append({"path": str(path), "line": index + 1,
                                     "context": "\n".join(lines[max(0, index - 2):index + 9])[:3500]})
                        if len(hits) >= limit:
                            return {"matches": hits, "limit_reached": True, "mode": "Literal search in installed FGD files; inheritance is not expanded."}
        return {"matches": hits, "limit_reached": False, "mode": "Literal search in installed FGD files; inheritance is not expanded."}
