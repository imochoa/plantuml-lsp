"""The symbol index: every entity visible from a document — its own
declarations plus the transitive `!include`/`!includesub` closure.

The index is the substrate the v1 features share: completion offers its
ids, hover reads its members and relations, references and rename walk
its occurrence scan. Occurrences come from the CST (identifier/string
nodes whose text matches), never from regex over text, so renames can't
touch labels, comments or note bodies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter import Node

from plantuml_lsp.parsing import NAME_TYPES, name_text, node_text, parse

_INCLUDE_ANY = re.compile(r"^\s*!include(?:sub)?\s+([^\s!]+)")

_DECLARATIONS = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
}

_REFERENCE_FIELDS = ("left", "right", "target")

_LABEL_CALL = re.compile(r"^\s*(\w+)\s*\(")
_ACTION_CALL = re.compile(r"^:\s*(\w+)\s*\(")
_LANE_NAME = re.compile(r"^\|(?:#[^|]*\|)?([^|]+)\|")


@dataclass
class Entity:
    id: str
    name: str
    kind: str
    path: str
    row: int
    scol: int
    ecol: int
    members: list[str] = field(default_factory=list)
    # method name -> (row, scol, ecol, member line text)
    methods: dict[str, tuple[int, int, int, str]] = field(default_factory=dict)


@dataclass
class Index:
    doc_path: str
    entities: dict[str, Entity] = field(default_factory=dict)
    files: dict[str, bytes] = field(default_factory=dict)
    # duplicate declarations kept aside for the lints
    duplicates: list[Entity] = field(default_factory=list)
    # relation lines by entity id, for hover
    relations: dict[str, list[str]] = field(default_factory=dict)
    # call sites by method name: spans of the NAME inside labels and
    # activity actions (path, row, scol, ecol)
    calls: dict[str, list[tuple[str, int, int, int]]] = field(default_factory=dict)
    # activity actions with their swimlane: (name, lane, row, scol, ecol)
    actions: list[tuple[str, str | None, int, int, int]] = field(default_factory=list)

    def occurrences(self, entity_id: str) -> list[tuple[str, int, int, int]]:
        """CST-backed (path, row, start_col, end_col) of every identifier
        or quoted-string token spelling the id, declaration included."""
        found: list[tuple[str, int, int, int]] = []
        for path, src in self.files.items():
            _scan_occurrences(parse(src).root_node, src, entity_id, path, found)
        return found


def build_index(doc_path: str, src: bytes) -> Index:
    index = Index(doc_path=doc_path)
    _index_file(doc_path, src, index, seen=set())
    return index


def identifier_at(src: bytes, row: int, col: int) -> str | None:
    """The identifier (or quoted name) token under the cursor, falling
    back to the identifier-shaped word at the cursor when the CST node
    is opaque (parameter payloads, member types, labels)."""
    node = parse(src).root_node.descendant_for_point_range((row, col), (row, col))
    if node is not None and node.type in NAME_TYPES:
        return name_text(src, node)
    return word_at(src, row, col)


_WORD = re.compile(r"[A-Za-z_][\w]*(?:[.-][A-Za-z_]\w*)*")


def word_at(src: bytes, row: int, col: int) -> str | None:
    """The identifier-shaped word covering the cursor column."""
    lines = src.decode(errors="replace").splitlines()
    if row >= len(lines):
        return None
    for match in _WORD.finditer(lines[row]):
        if match.start() <= col < match.end():
            return match.group(0)
    return None


def _index_file(path: str, src: bytes, index: Index, seen: set[str]) -> None:
    resolved = str(Path(path).resolve())
    if resolved in seen:
        return
    seen.add(resolved)
    index.files[resolved] = src
    _walk(parse(src).root_node, src, resolved, index)
    for line in src.decode(errors="replace").splitlines():
        m = _INCLUDE_ANY.match(line)
        if m:
            target = (Path(path).parent / m.group(1)).resolve()
            if target.is_file():
                _index_file(str(target), target.read_bytes(), index, seen)


def _register_call(index: Index, path: str, node: Node, name: str, offset: int) -> None:
    row, scol = node.start_point
    index.calls.setdefault(name, []).append(
        (path, row, scol + offset, scol + offset + len(name))
    )


def _visit_relation(node: Node, src: bytes, path: str, index: Index) -> None:
    _record_relation(node, src, index)
    label = node.child_by_field_name("label")
    if label is None:
        return
    m = _LABEL_CALL.match(node_text(src, label))
    if m is not None:
        _register_call(index, path, label, m.group(1), m.start(1))


def _visit_swimlane(node: Node, src: bytes, lane: list) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    m = _LANE_NAME.match(node_text(src, name_node))
    lane[0] = m.group(1).strip() if m else None


def _visit_action(node: Node, src: bytes, path: str, index: Index, lane: list) -> None:
    text_node = node.child_by_field_name("text")
    if text_node is None:
        return
    m = _ACTION_CALL.match(node_text(src, text_node))
    if m is None:
        return
    row, scol = text_node.start_point
    index.actions.append(
        (
            m.group(1),
            lane[0],
            row,
            scol + m.start(1),
            scol + m.start(1) + len(m.group(1)),
        )
    )
    _register_call(index, path, text_node, m.group(1), m.start(1))


def _walk(node: Node, src: bytes, path: str, index: Index, lane: list = None) -> None:
    if lane is None:
        lane = [None]
    kind = _declared_kind(node, src)
    if kind is not None:
        _add(index, node, src, path, kind)
    elif node.type == "relation":
        _visit_relation(node, src, path, index)
    elif node.type == "swimlane":
        _visit_swimlane(node, src, lane)
    elif node.type == "activity_action":
        _visit_action(node, src, path, index, lane)
    for child in node.children:
        _walk(child, src, path, index, lane)


def _declared_kind(node: Node, src: bytes) -> str | None:
    if node.type in _DECLARATIONS:
        return _DECLARATIONS[node.type]
    if node.type in ("entity_declaration", "participant_declaration"):
        kind_node = node.child_by_field_name("kind")
        if kind_node is not None:
            return node_text(src, kind_node)
        return "entity" if node.type == "entity_declaration" else "participant"
    if node.type == "element_declaration":
        return element_kind(node, src)
    return None


def element_kind(node: Node, src: bytes) -> str:
    """The kind an element declares: its keyword, `interface` for the
    `()` lollipop, `component` for the bare `[X]` shorthand."""
    kind_node = node.child_by_field_name("kind")
    if kind_node is None:
        return "component"
    kind = node_text(src, kind_node)
    return "interface" if kind == "()" else kind


def _record_relation(node: Node, src: bytes, index: Index) -> None:
    line = node_text(src, node).strip()
    for field_name in ("left", "right"):
        end = node.child_by_field_name(field_name)
        if end is not None and end.type in NAME_TYPES:
            index.relations.setdefault(name_text(src, end), []).append(line)


def _add(index: Index, node: Node, src: bytes, path: str, kind: str) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    alias_node = node.child_by_field_name("alias")
    id_node = alias_node if alias_node is not None else name_node
    entity_id = name_text(src, id_node)
    entity = Entity(
        id=entity_id,
        name=name_text(src, name_node),
        kind=kind,
        path=path,
        row=id_node.start_point[0],
        scol=id_node.start_point[1],
        ecol=id_node.end_point[1],
        members=_members(node, src),
        methods=_methods(node, src),
    )
    if entity_id in index.entities:
        index.duplicates.append(entity)
    else:
        index.entities[entity_id] = entity


def _methods(node: Node, src: bytes) -> dict[str, tuple[int, int, int, str]]:
    """Method name -> declaration location and line, for call-level
    correlation (sequence labels are invocations; arguments are free
    text, so matching is by name only)."""
    body = node.child_by_field_name("body")
    if body is None:
        return {}
    methods: dict[str, tuple[int, int, int, str]] = {}
    for member in body.named_children:
        if member.type != "member":
            continue
        for child in member.named_children:
            if child.type == "method":
                name_node = child.child_by_field_name("name")
                if name_node is not None:
                    methods[node_text(src, name_node)] = (
                        name_node.start_point[0],
                        name_node.start_point[1],
                        name_node.end_point[1],
                        node_text(src, member).strip(),
                    )
    return methods


def _members(node: Node, src: bytes) -> list[str]:
    body = node.child_by_field_name("body")
    if body is None:
        return []
    return [
        node_text(src, member).strip()
        for member in body.named_children
        if member.type == "member"
    ]


def resolve_entity(name: str, entities: dict[str, "Entity"]) -> str | None:
    """The id a cursor token resolves to: exact, or the tail of a
    qualified name (maquina.Cafetera -> Cafetera)."""
    if name in entities:
        return name
    if "." in name:
        tail = name.rsplit(".", 1)[-1]
        if tail in entities:
            return tail
    return None


def _scan_occurrences(
    node: Node,
    src: bytes,
    entity_id: str,
    path: str,
    found: list[tuple[str, int, int, int]],
) -> None:
    if node.type in NAME_TYPES:
        text = name_text(src, node)
        if text == entity_id:
            found.append(
                (path, node.start_point[0], node.start_point[1], node.end_point[1])
            )
        elif node.type != "bracket_name" and text.endswith("." + entity_id):
            # qualified use: report the span of the tail segment only,
            # so references highlight and renames rewrite just the name
            offset = (node.end_point[1] - node.start_point[1]) - len(entity_id)
            found.append(
                (
                    path,
                    node.start_point[0],
                    node.start_point[1] + offset,
                    node.end_point[1],
                )
            )
        return
    for child in node.children:
        _scan_occurrences(child, src, entity_id, path, found)
