from __future__ import annotations

from collections import Counter
import math
from pathlib import Path
import re
import subprocess
import tempfile
import uuid

from .environment import Environment, Game
from .keyvalues import Array, MapError, Node, Parser, Token, quote, set_scalars, splice, tokens, walk
from .storage import Store, sha256


def run_converter(executable: str, source: Path, destination: Path, encoding: str) -> None:
    if not Path(executable).is_file():
        raise MapError("Source 2 binary maps require dmxconvert.exe from the installed Workshop Tools.")
    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    try:
        proc = subprocess.run([executable, "-i", str(source), "-o", str(destination), "-oe", encoding],
                              capture_output=True, timeout=120, creationflags=flags)
    except subprocess.TimeoutExpired:
        raise MapError("dmxconvert timed out after 120 seconds; original map was not modified.") from None
    if proc.returncode or not destination.is_file():
        log = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")[-4000:]
        raise MapError(f"dmxconvert failed ({proc.returncode}): {log}")


class Document:
    def __init__(self, path: Path, env: Environment, game: Game | None = None):
        self.path = path
        self.original = path.read_bytes()
        self.original_hash = sha256(self.original)
        self.binary = self.original.startswith(b"<!-- dmx encoding binary ")
        self.typed = path.suffix.lower() == ".vmap"
        self.env = env
        self.converter = None
        self.codec = "utf-8"
        if self.typed:
            if game is not None and game.engine != "source2":
                raise MapError("A .vmap requires a Source 2 game.")
            if game is None:
                options = [g for g in env.games if g.engine == "source2" and Path(g.tools["dmxconvert"]).is_file()]
                matching = [g for g in options if path.is_relative_to(Path(g.install_dir))]
                if len(matching) == 1:
                    game = matching[0]
                elif len(options) == 1:
                    game = options[0]
                elif self.binary:
                    raise MapError("Specify game_id to choose a Source 2 DMX converter.")
            self.converter = game.tools["dmxconvert"] if game else None
        if self.binary:
            with tempfile.TemporaryDirectory(prefix="hammer-dmx-") as folder:
                source = Path(folder) / "input.vmap"
                target = Path(folder) / "text.vmap"
                source.write_bytes(self.original)
                run_converter(self.converter or "", source, target, "keyvalues2")
                if target.stat().st_size > env.max_text_bytes:
                    raise MapError("Converted map exceeds max_text_mb. Increase the local limit if RAM permits.")
                data = target.read_bytes()
        else:
            data = self.original
        if b"\0" in data:
            raise MapError("Unsupported binary map encoding; this file will not be treated as text.")
        try:
            self.text = data.decode("utf-8-sig")
            self.codec = "utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8"
        except UnicodeDecodeError:
            if self.typed:
                raise MapError("VMAP text is not valid UTF-8.") from None
            self.codec = "cp1252"
            self.text = data.decode(self.codec)
        self.original_text = self.text
        self.reparse(self.text)

    def reparse(self, text: str) -> None:
        self.text = text
        self.roots = Parser(text, typed=self.typed).parse()
        self.nodes = list(walk(self.roots))
        self.hidden_entities = {id(n) for root in self.roots if not self.typed and root.type == "hidden"
                                for n in walk([root]) if n.type == "entity"}
        self.ids = {node.scalar("id"): node for node in self.nodes if node.scalar("id")}
        if self.typed:
            if not any(n.type == "CMapRootElement" for n in self.roots):
                raise MapError("VMAP has no CMapRootElement.")
            root = next(n for n in self.roots if n.type == "CMapRootElement")
            self.world = self.resolve_attr(root, "world")
        else:
            self.world = next((n for n in self.roots if n.type == "world"), None)
        if self.world is None:
            raise MapError("Map has no world node.")

    def resolve_attr(self, node: Node, key: str) -> Node | None:
        attr = node.get(key)
        if attr is None:
            return None
        if isinstance(attr.value, Node):
            return attr.value
        if isinstance(attr.value, Token) and attr.type == "element":
            return self.ids.get(attr.value.text)
        return None

    def array_nodes(self, node: Node, key: str) -> list[Node]:
        attr = node.get(key)
        if attr is None or not isinstance(attr.value, Array):
            return []
        result = list(attr.value.nodes)
        for ref in attr.value.refs:
            if ref and ref not in self.ids:
                raise MapError(f"Unresolved DMX element reference {ref} in {key}.")
            if ref:
                result.append(self.ids[ref])
        return result

    def entity_nodes(self) -> list[Node]:
        if not self.typed:
            roots = {id(n) for n in self.roots}
            return [n for n in self.nodes if n.type == "entity" and
                    (id(n) in roots or id(n) in self.hidden_entities)]
        result = []
        visited = set()
        pending = self.array_nodes(self.world, "children")
        while pending:
            node = pending.pop()
            identity = id(node)
            if identity in visited:
                continue
            visited.add(identity)
            if node.type == "CMapEntity":
                result.append(node)
            pending.extend(self.array_nodes(node, "children"))
        return sorted(result, key=lambda n: n.start)

    def properties(self, node: Node) -> Node:
        if not self.typed:
            return node
        props = self.resolve_attr(node, "entity_properties")
        if props is None:
            raise MapError(f"Entity {node.scalar('id')} has no entity_properties.")
        return props

    def find_entity(self, selector: str) -> Node:
        entities = self.entity_nodes()
        matches = [n for n in entities if selector == n.scalar("id") or (self.typed and selector == n.scalar("nodeID"))]
        if not matches:
            matches = [n for n in entities if self.properties(n).scalar("targetname") == selector and selector]
        if len(matches) != 1:
            raise MapError(f"Entity selector {selector!r} matched {len(matches)} entities. Use the unique ID from hammer_entities.")
        return matches[0]

    def connections(self, node: Node) -> list[dict]:
        if self.typed:
            result = []
            for index, connection in enumerate(self.array_nodes(node, "connectionsData")):
                result.append({"index": index, "id": connection.scalar("id"),
                    "output": connection.scalar("outputName"), "target": connection.scalar("targetName"),
                    "input": connection.scalar("inputName"), "parameter": connection.scalar("overrideParam"),
                    "delay": connection.scalar("delay", "0"), "times_to_fire": connection.scalar("timesToFire", "-1"),
                    "target_type": connection.scalar("targetType", "7")})
            return result
        conn = self.resolve_attr(node, "connections")
        if conn is None:
            return []
        result = []
        for index, attr in enumerate(conn.attributes):
            if not isinstance(attr.value, Token):
                continue
            raw = attr.value.text
            pieces = raw.split("\x1b" if "\x1b" in raw else ",")
            if len(pieces) != 5:
                result.append({"index": index, "output": attr.name, "malformed": True, "raw": raw})
            else:
                result.append(dict(zip(["target", "input", "parameter", "delay", "times_to_fire"], pieces),
                                   index=index, output=attr.name))
        return result

    def record(self, node: Node, full: bool = True) -> dict:
        props = self.properties(node).scalars()
        result = {"id": node.scalar("id"), "node_id": node.scalar("nodeID") if self.typed else node.scalar("id"),
                  "classname": props.get("classname", ""), "targetname": props.get("targetname", ""),
                  "origin": node.scalar("origin", "0 0 0"), "angles": node.scalar("angles", "0 0 0")}
        if full:
            result["properties"] = props
            result["connections"] = self.connections(node)
            if self.typed:
                result["scales"] = node.scalar("scales", "1 1 1")
        if not self.typed:
            result["hidden"] = id(node) in self.hidden_entities
        return result

    def summary(self) -> dict:
        entities = self.entity_nodes()
        return {"path": str(self.path), "sha256": self.original_hash,
                "engine": "source2" if self.typed else "source1",
                "encoding": "binary-dmx" if self.binary else "text", "bytes": len(self.original),
                "entities": len(entities), "entity_classes": dict(Counter(self.properties(n).scalar("classname") for n in entities)),
                "hidden_entities": len(self.hidden_entities),
                "geometry": {"meshes": sum(n.type == "CMapMesh" for n in self.nodes),
                             "solids": sum(n.type == "solid" for n in self.nodes)},
                "connections": sum(len(self.connections(n)) for n in entities),
                "scope": "Saved map file; unsaved Hammer changes are not visible."}

    def set_entity(self, selector: str, properties: dict[str, str], origin: list[float] | None = None,
                   angles: list[float] | None = None, scales: list[float] | None = None,
                   remove_properties: list[str] | None = None) -> dict:
        node = self.find_entity(selector)
        protected = {"id", "nodeid", "referenceid", "origin", "angles", "scales"}
        if any(k.casefold() in protected for k in properties):
            raise MapError("IDs cannot be edited. Use origin/angles/scales arguments for transforms.")
        remove_protected = protected | {"classname"}
        props_node = self.properties(node)
        removal_edits: list[tuple[int, int, str]] = []
        if remove_properties is not None:
            if not isinstance(remove_properties, list):
                raise MapError("remove_properties must be a list of property names or None.")
            for key in remove_properties:
                if not isinstance(key, str) or key == "":
                    raise MapError("remove_properties names must be non-empty strings.")
            if len({k.casefold() for k in remove_properties}) != len(remove_properties):
                raise MapError("remove_properties names must be unique ignoring case.")
            if any(k.casefold() in remove_protected for k in remove_properties):
                raise MapError("remove_properties contains a protected key.")
            if {k.casefold() for k in properties} & {k.casefold() for k in remove_properties}:
                raise MapError("A property cannot be set and removed in the same call.")
            removal_attrs = []
            for key in remove_properties:
                matches = [a for a in props_node.attributes if a.name.casefold() == key.casefold()]
                if len(matches) != 1:
                    raise MapError(f"Property to remove matched {len(matches)} rows: {key!r}. No changes were made.")
                attr = matches[0]
                if not isinstance(attr.value, Token):
                    raise MapError(f"{key!r} is structured data, not a removable scalar.")
                removal_attrs.append(attr)
            removal_edits = [(attr.start, attr.end, "") for attr in removal_attrs]
        edits = removal_edits + set_scalars(self.text, props_node, properties, self.typed)
        transforms = {}
        for key, value in [("origin", origin), ("angles", angles), ("scales", scales)]:
            if value is not None:
                transforms[key] = vector(value)
        if scales is not None and not self.typed:
            raise MapError("Source 1 entities do not have the Source 2 scales transform.")
        if transforms:
            edits += set_scalars(self.text, node, transforms, self.typed,
                                 {"origin": "vector3", "angles": "qangle", "scales": "vector3"})
        entity_id = node.scalar("id")
        self.reparse(splice(self.text, edits))
        return self.record(self.find_entity(entity_id))

    def add_entity(self, classname: str, properties: dict[str, str], origin: list[float]) -> dict:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", classname):
            raise MapError("Invalid entity classname.")
        if any(k.casefold() in {"id", "nodeid", "referenceid", "classname", "origin", "angles", "scales"} for k in properties):
            raise MapError("Use the named classname/origin arguments; IDs and transforms are managed by the server.")
        props = {"classname": classname, **properties}
        if self.typed:
            entity_id = str(uuid.uuid4())
            node_id = max((int(n.scalar("nodeID", "0")) for n in self.nodes), default=0) + 1
            prop_lines = "\n".join(f"{quote(k)} \"string\" {quote(v)}" for k, v in props.items())
            data = f'''"CMapEntity"
{{
"id" "elementid" "{entity_id}"
"nodeID" "int" "{node_id}"
"referenceID" "uint64" "0x0"
"children" "element_array" [ ]
"connectionsData" "element_array" [ ]
"entity_properties" "EditGameClassProps"
{{
"id" "elementid" "{uuid.uuid4()}"
{prop_lines}
}}
"origin" "vector3" "{vector(origin)}"
"angles" "qangle" "0 0 0"
"scales" "vector3" "1 1 1"
"hitNormal" "vector3" "0 0 1"
"isProceduralEntity" "bool" "0"
"transformLocked" "bool" "0"
"force_hidden" "bool" "0"
"editorOnly" "bool" "0"
}}'''
            children = self.world.get("children")
            if children is None or not isinstance(children.value, Array):
                raise MapError("World has no children array.")
            self.reparse(append_array(self.text, children.value, data))
        else:
            entity_id = str(max((int(n.scalar("id", "0")) for n in self.nodes), default=0) + 1)
            prop_lines = "\n".join(f"{quote(k)} {quote(v)}" for k, v in {"id": entity_id, **props, "origin": vector(origin)}.items())
            self.reparse(self.text + "\nentity\n{\n" + prop_lines + "\n}\n")
        return self.record(self.find_entity(entity_id))

    def add_connection(self, selector: str, output: str, target: str, input_name: str,
                       parameter: str, delay: float, times_to_fire: int,
                       index: int | None = None, target_type: int = 7) -> dict:
        if not output or not target or not input_name or not math.isfinite(delay) or delay < 0:
            raise MapError("Output, target and input are required; delay must be finite and nonnegative.")
        if times_to_fire not in (-1, 1):
            raise MapError("Use -1 for unlimited firing or 1 for once.")
        if isinstance(index, bool) or (index is not None and not isinstance(index, int)):
            raise MapError("Array index must be an integer or None.")
        if isinstance(target_type, bool) or not isinstance(target_type, int) or target_type < 0:
            raise MapError("target_type must be a nonnegative integer.")
        if index is not None and not self.typed:
            raise MapError("Indexed insertion is only supported for Source 2 maps.")
        if target_type != 7 and not self.typed:
            raise MapError("target_type is only supported for Source 2 maps.")
        node = self.find_entity(selector)
        entity_id = node.scalar("id")
        if self._find_duplicate(node, output, target, input_name, parameter, delay, times_to_fire):
            raise MapError("Duplicate connection: the same output, target, input, parameter, delay and fire count already exists.")
        if self.typed:
            data = f'''"DmeConnectionData"
{{
"id" "elementid" "{uuid.uuid4()}"
"outputName" "string" {quote(output)}
"targetType" "int" "{target_type}"
"targetName" "string" {quote(target)}
"inputName" "string" {quote(input_name)}
"overrideParam" "string" {quote(parameter)}
"delay" "float" "{delay:g}"
"timesToFire" "int" "{times_to_fire}"
}}'''
            attr = node.get("connectionsData")
            if attr is None:
                if index is not None and index != 0:
                    raise MapError(f"Array index {index} out of range for 0 items.")
                text = splice(self.text, [(node.end - 1, node.end - 1, '\n"connectionsData" "element_array" [\n' + data + '\n]\n')])
            elif isinstance(attr.value, Array):
                text = insert_array_item(self.text, attr.value, data, index)
            else:
                raise MapError("Unsupported connectionsData representation.")
        else:
            fields = [target, input_name, parameter, f"{delay:g}", str(times_to_fire)]
            if any("," in f or "\x1b" in f for f in fields):
                raise MapError("Source 1 connection fields cannot contain comma or ESC delimiters in this version.")
            line = quote(output) + " " + quote(",".join(fields)) + "\n"
            conn = self.resolve_attr(node, "connections")
            text = splice(self.text, [(conn.end - 1, conn.end - 1, line)] if conn else
                          [(node.end - 1, node.end - 1, "\nconnections\n{\n" + line + "}\n")])
        self.reparse(text)
        return {"entity_id": entity_id, "connections": self.connections(self.find_entity(entity_id))}

    @staticmethod
    def _connection_key(connection: dict) -> tuple[str, str, str, str, str]:
        return (connection.get("output", ""), connection.get("target", ""),
                connection.get("input", ""), connection.get("parameter", ""),
                str(connection.get("times_to_fire", "")))

    def _find_duplicate(self, node: Node, output: str, target: str, input_name: str,
                        parameter: str, delay: float, times_to_fire: int) -> bool:
        candidate = (output, target, input_name, parameter, str(times_to_fire))
        for existing in self.connections(node):
            if "malformed" in existing:
                continue
            if self._connection_key(existing) != candidate:
                continue
            try:
                existing_delay = float(existing.get("delay", "0"))
            except ValueError:
                continue
            if abs(existing_delay - delay) <= 1e-5:
                return True
        return False

    def set_connection(self, selector: str, connection_id: str, output: str | None = None,
                       target: str | None = None, input_name: str | None = None,
                       parameter: str | None = None, delay: float | None = None,
                       times_to_fire: int | None = None, target_type: int | None = None) -> dict:
        if not self.typed:
            raise MapError("In-place connection editing is only supported for Source 2 maps.")
        if times_to_fire is not None and times_to_fire not in (-1, 1):
            raise MapError("Use -1 for unlimited firing or 1 for once.")
        if delay is not None and (not math.isfinite(delay) or delay < 0):
            raise MapError("Delay must be finite and nonnegative.")
        for field in (output, target, input_name):
            if field is not None and not field:
                raise MapError("Output, target and input cannot be empty when specified.")
        node = self.find_entity(selector)
        entity_id = node.scalar("id")
        matches = [c for c in self.array_nodes(node, "connectionsData") if c.scalar("id") == connection_id]
        if len(matches) != 1:
            raise MapError(f"Connection {connection_id!r} matched {len(matches)} rows on this entity. Use the unique connection id.")
        connection = matches[0]
        values: dict[str, str] = {}
        if output is not None:
            values["outputName"] = output
        if target is not None:
            values["targetName"] = target
        if input_name is not None:
            values["inputName"] = input_name
        if parameter is not None:
            values["overrideParam"] = parameter
        if delay is not None:
            values["delay"] = f"{delay:g}"
        if times_to_fire is not None:
            values["timesToFire"] = str(times_to_fire)
        if target_type is not None:
            values["targetType"] = str(target_type)
        if not values:
            raise MapError("No connection fields were specified.")
        edits = set_scalars(self.text, connection, values, True,
                            {"outputName": "string", "targetName": "string", "inputName": "string",
                             "overrideParam": "string", "delay": "float", "timesToFire": "int",
                             "targetType": "int"})
        if not edits:
            raise MapError("Requested connection values are already set.")
        self.reparse(splice(self.text, edits))
        return {"entity_id": entity_id, "connections": self.connections(self.find_entity(entity_id))}

    def replace_material(self, from_material: str, to_material: str, expected_count: int,
                         include_asset_list: bool = True) -> dict:
        for label, value in (("from_material", from_material), ("to_material", to_material)):
            if not isinstance(value, str) or not value:
                raise MapError(f"{label} must be a non-empty string.")
            if not value.startswith("materials/"):
                raise MapError(f"{label} must start with 'materials/'.")
        if from_material == to_material:
            raise MapError("from_material and to_material must differ.")
        if isinstance(expected_count, bool) or not isinstance(expected_count, int) or expected_count < 0:
            raise MapError("expected_count must be a nonnegative integer.")
        mesh_hits: list[tuple[int, int]] = []
        mesh_nodes = 0
        for node in self.nodes:
            if node.type not in ("CMapMesh", "CMapStaticOverlay"):
                continue
            mesh_data = node.get("meshData")
            if mesh_data is None or not isinstance(mesh_data.value, Node):
                continue
            materials = mesh_data.value.get("materials")
            if materials is None or not isinstance(materials.value, Array):
                continue
            mesh_nodes += 1
            mesh_hits += self._string_array_hits(materials.value, from_material)
        if len(mesh_hits) != expected_count:
            raise MapError(f"Expected {expected_count} mesh material items for {from_material!r}, found {len(mesh_hits)}. No changes were made.")
        asset_hits: list[tuple[int, int]] = []
        if include_asset_list:
            for root in self.roots:
                asset_attr = root.get("map_asset_references")
                if asset_attr is None or not isinstance(asset_attr.value, Array):
                    continue
                asset_hits = self._string_array_hits(asset_attr.value, from_material)
                break
        edits = [(start, end, quote(to_material)) for start, end in mesh_hits + asset_hits]
        if edits:
            self.reparse(splice(self.text, edits))
        asset_sorted_after = self._asset_list_sorted() if include_asset_list else None
        return {"from_material": from_material, "to_material": to_material,
                "mesh_items": len(mesh_hits), "asset_list_items": len(asset_hits),
                "asset_list_sorted_after": asset_sorted_after, "meshes": mesh_nodes}

    def _string_array_hits(self, array: Array, wanted: str) -> list[tuple[int, int]]:
        # Local retokenization (R1 design (b)): string_array bodies hold no braces,
        # so lexing the interior slice and shifting by array.start + 1 is safe.
        hits = []
        for token in tokens(self.text[array.start + 1:array.end - 1]):
            if token.kind != "string" or token.text != wanted:
                continue
            hits.append((array.start + 1 + token.start, array.start + 1 + token.end))
        return hits

    def _asset_list_sorted(self) -> bool | None:
        for root in self.roots:
            asset_attr = root.get("map_asset_references")
            if asset_attr is None or not isinstance(asset_attr.value, Array):
                continue
            items = [token.text for token in tokens(self.text[asset_attr.value.start + 1:asset_attr.value.end - 1])
                     if token.kind == "string"]
            return items == sorted(items)
        return None

    def replace_property(self, replacements: list[dict], classname: str | None = None) -> dict:
        if not isinstance(replacements, list) or not replacements:
            raise MapError("Replacements must be a non-empty list.")
        if classname is not None and (not isinstance(classname, str) or not classname):
            raise MapError("Classname filter must be a non-empty string or None.")
        protected = {"id", "nodeid", "referenceid", "origin", "angles", "scales", "classname"}
        parsed = []
        for item in replacements:
            if not isinstance(item, dict):
                raise MapError("Each replacement must be a dict with key/from_value/to_value/expected_count.")
            try:
                key = item["key"]
                from_value = item["from_value"]
                to_value = item["to_value"]
                expected = item["expected_count"]
            except KeyError:
                raise MapError("Each replacement needs key, from_value, to_value and expected_count.") from None
            if not isinstance(key, str) or not key:
                raise MapError("Replacement key must be a non-empty string.")
            if key.casefold() in protected:
                raise MapError(f"Key {key!r} is protected and cannot be batch-replaced.")
            if not isinstance(from_value, str) or not isinstance(to_value, str):
                raise MapError("from_value and to_value must be strings.")
            if from_value == to_value:
                raise MapError("from_value and to_value must differ.")
            if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
                raise MapError("expected_count must be a nonnegative integer.")
            parsed.append((key, from_value, to_value, expected))
        entities = self.entity_nodes()
        if classname is not None:
            entities = [n for n in entities if self.properties(n).scalar("classname", "") == classname]
        matches_per_item = []
        for key, from_value, _, expected in parsed:
            matched = []
            for node in entities:
                props = self.properties(node)
                attr = props.get(key)
                if attr is not None and isinstance(attr.value, Token) and attr.value.text == from_value:
                    matched.append(node)
            if len(matched) != expected:
                raise MapError(f"Expected {expected} rows for {key!r}={from_value!r}, found {len(matched)}. No changes were made.")
            matches_per_item.append(matched)
        edits = []
        report = []
        for (key, from_value, to_value, _), matched in zip(parsed, matches_per_item):
            ids = []
            for node in matched:
                edits += set_scalars(self.text, self.properties(node), {key: to_value}, self.typed)
                ids.append(node.scalar("id"))
            report.append({"key": key, "from_value": from_value, "to_value": to_value,
                           "count": len(ids), "entity_ids": ids})
        if edits:
            self.reparse(splice(self.text, edits))
        return {"classname": classname, "replacements": report}

    def encoded(self) -> bytes:
        if not self.binary:
            return self.text.encode(self.codec)
        with tempfile.TemporaryDirectory(prefix="hammer-dmx-") as folder:
            source = Path(folder) / "text.vmap"
            target = Path(folder) / "binary.vmap"
            source.write_text(self.text, encoding="utf-8", newline="")
            run_converter(self.converter or "", source, target, "binary")
            data = target.read_bytes()
            if not data.startswith(b"<!-- dmx encoding binary "):
                raise MapError("Converter did not produce a binary DMX map.")
            return data

    def save(self, store: Store, expected_hash: str, dry_run: bool = False) -> dict:
        if expected_hash != self.original_hash:
            raise MapError("Map hash mismatch. Re-read before editing.")
        if dry_run:
            return {"dry_run": True, "path": str(self.path), "source_sha256": self.original_hash,
                    "summary": self.summary(), "written": False}
        return store.commit(self.path, self.original, self.original if self.text == self.original_text else self.encoded(), expected_hash)


def append_array(text: str, array: Array, data: str) -> str:
    interior = text[array.start + 1:array.end - 1].rstrip()
    separator = "," if array.count and not interior.endswith(",") else ""
    return splice(text, [(array.end - 1, array.end - 1, separator + "\n" + data + "\n")])


def insert_array_item(text: str, array: Array, data: str, index: int | None) -> str:
    if index is None:
        return append_array(text, array, data)
    if isinstance(index, bool) or not isinstance(index, int):
        raise MapError("Array index must be an integer or None.")
    if index < 0 or index > array.count:
        raise MapError(f"Array index {index} out of range for {array.count} items.")
    if index == array.count:
        return append_array(text, array, data)
    if array.refs:
        raise MapError("Indexed insertion into reference arrays is not supported.")
    if index >= len(array.nodes):
        raise MapError(f"Array index {index} does not map to a stored node.")
    target = array.nodes[index]
    return splice(text, [(target.start, target.start, data + ",\n")])


def vector(value: list[float]) -> str:
    if len(value) != 3 or any(not math.isfinite(x) for x in value):
        raise MapError("Transforms must contain exactly three finite numbers.")
    return " ".join(f"{number:g}" for number in value)


def create_room(width: int, depth: int, height: int) -> bytes:
    from srctools import VMF, Vec
    if any(v < 128 or v > 8192 or v % 16 for v in (width, depth, height)):
        raise MapError("Room dimensions must be multiples of 16 between 128 and 8192.")
    vmf = VMF()
    vmf.add_brushes(vmf.make_hollow(Vec(-width / 2, -depth / 2, 0), Vec(width / 2, depth / 2, height),
                                    thick=16, mat="tools/toolsnodraw", inner_mat="dev/dev_measuregeneric01"))
    vmf.create_ent("info_player_start", origin="0 0 32")
    vmf.create_ent("info_player_counterterrorist", origin="-64 0 32")
    vmf.create_ent("info_player_terrorist", origin="64 0 32")
    vmf.create_ent("light", origin=f"0 0 {height - 48}", _light="255 244 220 500")
    return vmf.export().encode("utf-8")
