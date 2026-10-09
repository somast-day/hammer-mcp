"""Span-preserving readers for VMF KeyValues and text DMX KeyValues2.

Unknown scalar types and mesh arrays are retained verbatim. This is deliberately
not a DMX serializer: editing a property must not rebuild unrelated geometry.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Iterator


class MapError(ValueError):
    pass


@dataclass(slots=True)
class Token:
    text: str
    start: int
    end: int
    kind: str = "string"


@dataclass(slots=True)
class Array:
    start: int
    end: int
    nodes: list[Node] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)
    count: int = 0


@dataclass(slots=True)
class Attribute:
    name: str
    type: str
    value: Token | Node | Array
    start: int
    end: int


@dataclass(slots=True)
class Node:
    type: str
    start: int
    brace: int
    end: int
    attributes: list[Attribute] = field(default_factory=list)

    def get(self, name: str) -> Attribute | None:
        return next((a for a in self.attributes if a.name.casefold() == name.casefold()), None)

    def scalar(self, name: str, default: str = "") -> str:
        attr = self.get(name)
        return attr.value.text if attr is not None and isinstance(attr.value, Token) else default

    def scalars(self) -> dict[str, str]:
        return {a.name: a.value.text for a in self.attributes if isinstance(a.value, Token)}


_TOKEN = re.compile(
    r'\s+|<!--.*?-->|//[^\r\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|[{}\[\],]|[^\s{}\[\],"]+',
    re.DOTALL,
)
_UNESCAPE = re.compile(r'\\([\\"nrt])')
_ESCAPES = {"\\": "\\", '"': '"', "n": "\n", "r": "\r", "t": "\t"}


def quote(value: str) -> str:
    if "\0" in value:
        raise MapError("NUL is not allowed in map properties.")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t") + '"'


def tokens(text: str) -> Iterator[Token]:
    end = 0
    for match in _TOKEN.finditer(text):
        if match.start() != end:
            raise MapError(f"Unterminated string or unsupported syntax at offset {end}.")
        end = match.end()
        raw = match.group()
        if raw.isspace() or raw.startswith(("<!--", "//", "/*")):
            continue
        if raw.startswith('"'):
            yield Token(_UNESCAPE.sub(lambda m: _ESCAPES[m[1]], raw[1:-1]), match.start(), end)
        elif raw in "{}[],":
            yield Token(raw, match.start(), end, "symbol")
        else:
            yield Token(raw, match.start(), end)
    if end != len(text):
        raise MapError(f"Unterminated string at offset {end}.")


class Parser:
    def __init__(self, text: str, typed: bool):
        self.text = text
        self.typed = typed
        self.stream = iter(tokens(text))
        self.current = next(self.stream, None)
        self.depth = 0

    def pop(self, symbol: str | None = None) -> Token:
        tok = self.current
        if tok is None:
            raise MapError("Unexpected end of map.")
        if symbol is not None and (tok.kind != "symbol" or tok.text != symbol):
            raise MapError(f"Expected {symbol!r} at offset {tok.start}.")
        self.current = next(self.stream, None)
        return tok

    def at(self, symbol: str) -> bool:
        return self.current is not None and self.current.kind == "symbol" and self.current.text == symbol

    def string(self) -> Token:
        tok = self.pop()
        if tok.kind != "string":
            raise MapError(f"Expected a value at offset {tok.start}.")
        return tok

    def node(self, label: Token) -> Node:
        self.depth += 1
        if self.depth > 128:
            raise MapError("Map nesting exceeds 128 levels.")
        brace = self.pop("{")
        node = Node(label.text, label.start, brace.start, 0)
        while not self.at("}"):
            key = self.string()
            if self.typed:
                typ = self.string()
                if self.at("{"):
                    value = self.node(typ)
                elif self.at("["):
                    value = self.array()
                else:
                    value = self.string()
                attr_type = typ.text
            else:
                value = self.node(key) if self.at("{") else self.string()
                attr_type = "block" if isinstance(value, Node) else "string"
            node.attributes.append(Attribute(key.text, attr_type, value, key.start, value.end))
        node.end = self.pop("}").end
        self.depth -= 1
        return node

    def array(self) -> Array:
        arr = Array(self.pop("[").start, 0)
        while not self.at("]"):
            if self.at(","):
                self.pop(",")
                continue
            tok = self.string()
            if self.at("{"):
                arr.nodes.append(self.node(tok))
            elif tok.text == "element" and self.current is not None and self.current.kind == "string":
                arr.refs.append(self.string().text)
            arr.count += 1
        arr.end = self.pop("]").end
        return arr

    def parse(self) -> list[Node]:
        roots = []
        while self.current is not None:
            roots.append(self.node(self.string()))
        return roots


def walk(nodes: list[Node]) -> Iterator[Node]:
    for node in nodes:
        yield node
        for attr in node.attributes:
            if isinstance(attr.value, Node):
                yield from walk([attr.value])
            elif isinstance(attr.value, Array):
                yield from walk(attr.value.nodes)


def splice(text: str, edits: list[tuple[int, int, str]]) -> str:
    boundary = len(text) + 1
    for start, end, value in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        if start < 0 or end < start or end > len(text) or end > boundary:
            raise MapError("Overlapping or invalid edits.")
        text = text[:start] + value + text[end:]
        boundary = start
    return text


def set_scalars(text: str, node: Node, values: dict[str, str], typed: bool,
                types: dict[str, str] | None = None) -> list[tuple[int, int, str]]:
    edits = []
    additions = []
    newline = "\r\n" if "\r\n" in text else "\n"
    if len({k.casefold() for k in values}) != len(values):
        raise MapError("Property names must be unique ignoring case.")
    for key, value in values.items():
        matches = [a for a in node.attributes if a.name.casefold() == key.casefold()]
        if len(matches) > 1:
            raise MapError(f"Ambiguous duplicate property: {key}")
        attr = matches[0] if matches else None
        if attr:
            if not isinstance(attr.value, Token):
                raise MapError(f"{key} is structured data, not an editable scalar.")
            if attr.value.text != value:
                edits.append((attr.value.start, attr.value.end, quote(value)))
        else:
            line = f"\t{quote(key)} "
            if typed:
                line += quote((types or {}).get(key, "string")) + " "
            additions.append(line + quote(value))
    if additions:
        edits.append((node.end - 1, node.end - 1, newline + newline.join(additions) + newline))
    return edits
