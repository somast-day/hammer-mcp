from __future__ import annotations

from dataclasses import dataclass, asdict
import json
import os
from pathlib import Path
import re

from .keyvalues import MapError
from .locking import atomic_json, file_lock


@dataclass
class Game:
    id: str
    name: str
    engine: str
    install_dir: str
    game_dir: str
    content_dir: str | None
    tools: dict[str, str]
    fgd_dirs: list[str]

    def describe(self) -> dict:
        result = asdict(self)
        result["tools_available"] = {name: Path(path).is_file() for name, path in self.tools.items()}
        return result


PROFILES = {
    "730": ("cs2", "Counter-Strike 2", "source2", "csgo"),
    "240": ("css", "Counter-Strike: Source", "source1", "cstrike"),
    "4000": ("gmod", "Garry's Mod", "source1", "garrysmod"),
    "440": ("tf2", "Team Fortress 2", "source1", "tf"),
    "220": ("hl2", "Half-Life 2", "source1", "hl2"),
    "550": ("l4d2", "Left 4 Dead 2", "source1", "left4dead2"),
    "620": ("portal2", "Portal 2", "source1", "portal2"),
    "546560": ("alyx", "Half-Life: Alyx", "source2", "hlvr"),
}


def discover_games(steam_roots: list[str] | None = None) -> list[Game]:
    candidates = [Path(p) for p in steam_roots or []]
    if os.name == "nt":
        import winreg
        for hive, key, name in [(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                                 (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")]:
            try:
                with winreg.OpenKey(hive, key) as registry:
                    candidates.append(Path(winreg.QueryValueEx(registry, name)[0]))
            except OSError:
                pass
    else:
        candidates.extend([Path.home() / ".steam/steam", Path.home() / ".local/share/Steam"])
    libraries: set[Path] = set()
    for steam in candidates:
        libraries.add(steam.resolve())
        vdf = steam / "steamapps/libraryfolders.vdf"
        if vdf.is_file():
            text = vdf.read_text(encoding="utf-8", errors="replace")
            libraries.update(Path(p.replace("\\\\", "\\")).resolve() for p in re.findall(r'"path"\s+"([^"]+)"', text))
    games = []
    for library in sorted(libraries):
        for appid, (game_id, name, engine, mod) in PROFILES.items():
            manifest = library / f"steamapps/appmanifest_{appid}.acf"
            if not manifest.is_file():
                continue
            match = re.search(r'"installdir"\s+"([^"]+)"', manifest.read_text(encoding="utf-8", errors="replace"))
            if not match:
                continue
            install = library / "steamapps/common" / match[1]
            if engine == "source2":
                bin_dir = install / "game/bin/win64"
                game_dir = install / "game" / mod
                content_dir = install / "content" / mod
                tools = {n: str(bin_dir / f"{n}.exe") for n in ["resourcecompiler", "dmxconvert"]}
                fgd_dirs = [str(game_dir), str(install / "game/core"), str(install / f"game/{mod}_core")]
            else:
                bin_dir = install / "bin/x64"
                if not (bin_dir / "vbsp.exe").is_file():
                    bin_dir = install / "bin"
                game_dir = install / mod
                content_dir = None
                tools = {n: str(bin_dir / f"{n}.exe") for n in ["vbsp", "vvis", "vrad", "hammer"]}
                fgd_dirs = [str(install / "bin"), str(bin_dir), str(game_dir)]
            if game_dir.is_dir():
                games.append(Game(game_id, name, engine, str(install), str(game_dir),
                                  str(content_dir) if content_dir else None, tools, fgd_dirs))
    return games


def default_state() -> Path:
    return Path(os.environ.get("HAMMER_MCP_STATE", str(Path.home() / ".local/share/hammer-mcp"))).resolve()


class Environment:
    def __init__(self, config_path: Path | None = None, games: list[Game] | None = None):
        self.state = default_state()
        self.config_path = (config_path or Path(os.environ.get("HAMMER_MCP_CONFIG", str(self.state / "config.json")))).resolve()
        self.config = json.loads(self.config_path.read_text(encoding="utf-8")) if self.config_path.is_file() else {}
        self.state = Path(self.config.get("state_dir", self.state)).resolve()
        self.state.mkdir(parents=True, exist_ok=True)
        self.games = games if games is not None else discover_games(self.config.get("steam_roots"))
        self.project_roots = [Path(p).resolve() for p in self.config.get("project_roots", [])]
        self.max_map_bytes = int(self.config.get("max_map_mb", 64)) * 1024 * 1024
        self.max_text_bytes = int(self.config.get("max_text_mb", 512)) * 1024 * 1024

    def game(self, game_id: str) -> Game:
        matches = [g for g in self.games if g.id == game_id]
        if len(matches) != 1:
            raise MapError(f"Game {game_id!r} is not uniquely installed. Use hammer_health to see detected games.")
        return matches[0]

    def resolve_map(self, filename: str, *, write: bool = False, exists: bool = True) -> Path:
        self.refresh_projects()
        path = Path(filename).expanduser().resolve()
        if path.suffix.lower() not in {".vmf", ".vmap"}:
            raise MapError("Expected a .vmf or .vmap source map, not a compiled map.")
        roots = self.project_roots + [self.state / "maps", self.state / "builds"]
        if not write:
            roots += [Path(g.install_dir) for g in self.games]
        if not any(path.is_relative_to(root.resolve()) for root in roots):
            raise MapError("Map is outside configured project roots. Register its project with hammer_add_project.")
        if exists and not path.is_file():
            raise MapError(f"Map does not exist: {path}")
        if exists and path.stat().st_size > self.max_map_bytes:
            raise MapError(f"Map exceeds the configured {self.max_map_bytes // 1024 // 1024} MiB limit. Increase max_map_mb in the local config if sufficient RAM is available.")
        return path

    def add_project(self, folder: str) -> dict:
        root = Path(folder).expanduser().resolve()
        if not root.is_dir():
            raise MapError("Project must be an existing directory.")
        if root == Path(root.anchor) or root == Path.home().resolve():
            raise MapError("Choose a specific mapping project, not a drive root or home directory.")
        with file_lock(self.config_path.with_name(self.config_path.name + ".lock"), timeout=5):
            latest = json.loads(self.config_path.read_text(encoding="utf-8")) if self.config_path.is_file() else {}
            roots = [Path(p).resolve() for p in latest.get("project_roots", [])]
            if root not in roots:
                roots.append(root)
                latest["project_roots"] = [str(p) for p in roots]
                atomic_json(self.config_path, latest)
            self.config = latest
            self.project_roots = roots
        return {"project_roots": [str(p) for p in self.project_roots]}

    def refresh_projects(self) -> None:
        if self.config_path.is_file():
            latest = json.loads(self.config_path.read_text(encoding="utf-8"))
            self.project_roots = [Path(p).resolve() for p in latest.get("project_roots", [])]
