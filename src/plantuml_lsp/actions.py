"""Quick fixes over the semantic lints — reconciliation with one key:

- an endpoint nothing declares gets its declaration;
- a broken include path gets the closest existing file;
- a drifting call gets the method skeleton declared in the receiver's
  class, wherever its file lives.

Each builder returns (title, path, row, scol, ecol, new_text) — a
replacement span, zero-width for insertions — so the server maps them
1:1 onto workspace edits.
"""

from __future__ import annotations

import difflib
import os
import re
from pathlib import Path

from plantuml_lsp.index import Entity
from plantuml_lsp.parsing import name_text, parse
from plantuml_lsp.workspace import WorkspaceIndex

# (title, path, row, scol, ecol, new_text)
FixTuple = tuple[str, str, int, int, int, str]

_DIRECTIVE = re.compile(r"^(\s*!include(?:sub)?\s+)([^\s!]+)")


def declare_entity(name: str, doc_path: str, src: bytes) -> FixTuple | None:
    lines = src.decode(errors="replace").splitlines()
    for row in range(len(lines) - 1, -1, -1):
        if lines[row].strip().startswith("@enduml"):
            return (
                f"Crear declaración: class {name}",
                doc_path,
                row,
                0,
                0,
                f"class {name}\n",
            )
    return None


def fix_include_path(
    row: int,
    line: str,
    doc_path: str,
    workspace: WorkspaceIndex,
) -> FixTuple | None:
    m = _DIRECTIVE.match(line)
    if m is None:
        return None
    broken = m.group(2)
    by_name = {Path(p).name: p for p in workspace._files}
    close = difflib.get_close_matches(Path(broken).name, by_name, 1, 0.6)
    if not close:
        return None
    target = by_name[close[0]]
    rel = os.path.relpath(target, Path(doc_path).parent)
    if not rel.startswith("."):
        rel = "./" + rel
    scol = m.start(2)
    return (
        f"Corregir ruta: {rel}",
        doc_path,
        row,
        scol,
        scol + len(broken),
        rel,
    )


def declare_method(
    method: str,
    entity: Entity,
    workspace: WorkspaceIndex,
) -> FixTuple | None:
    src = workspace.file_bytes(entity.path)
    if src is None:
        return None
    root = parse(src).root_node
    body = _body_of_declaration_at(root, src, entity)
    if body is None:
        return None
    brace_row = None
    for child in body.children:
        if child.type == "}":
            brace_row = child.start_point[0]
    if brace_row is None:
        return None
    indent = "  "
    if entity.methods:
        first_row = next(iter(entity.methods.values()))[0]
        line = src.decode(errors="replace").splitlines()[first_row]
        indent = line[: len(line) - len(line.lstrip())]
    return (
        f"Declarar '+{method}()' en {entity.id}",
        entity.path,
        brace_row,
        0,
        0,
        f"{indent}+{method}() : None\n",
    )


def _body_of_declaration_at(root, src: bytes, entity: Entity):
    stack = [root]
    while stack:
        node = stack.pop()
        name_node = node.child_by_field_name("name")
        if (
            name_node is not None
            and name_node.start_point[0] == entity.row
            and name_text(src, name_node) == entity.name
        ):
            body = node.child_by_field_name("body")
            if body is not None:
                return body
        stack.extend(node.children)
    return None
