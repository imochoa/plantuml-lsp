"""Go-to-definition across the preprocessor include directives.

`!includesub path!NAME` on the cursor line resolves to the `!startsub
NAME` line of the referenced file; `!include path` resolves to the top
of the referenced file. Paths resolve relative to the requesting
document, matching PlantUML's own preprocessor semantics.
"""

import re
from pathlib import Path

from plantuml_lsp.parsing import name_text, node_text, parse

_INCLUDESUB = re.compile(r"^\s*!includesub\s+([^\s!]+)!(\w+)\s*$")
_INCLUDE = re.compile(r"^\s*!include\s+(\S+)\s*$")
_STARTSUB = re.compile(r"^\s*!startsub\s+(\w+)\s*$")

# (target_path, line)
DefinitionTuple = tuple[str, int]

# (path, row, start_col, end_col)
EntityLocation = tuple[str, int, int, int]


_CALL_SHAPE = re.compile(r"^\s*(\w+)\s*\(")


def _label_under_cursor(src: bytes, row: int, col: int):
    """The relation label node at the cursor, or None."""
    node = parse(src).root_node.descendant_for_point_range((row, col), (row, col))
    while node is not None and node.type != "relation":
        if node.type == "label":
            break
        node = node.parent
    if node is None or node.type != "label":
        return None
    relation = node.parent
    if relation is None or relation.type != "relation":
        return None
    return node


def _relation_receiver(src: bytes, relation):
    """The endpoint a message points at, honoring arrow direction."""
    operator = relation.child_by_field_name("operator")
    reversed_ = operator is not None and node_text(src, operator).startswith("<")
    receiver = relation.child_by_field_name("left" if reversed_ else "right")
    if receiver is None:
        return None
    return name_text(src, receiver)


def call_at(src: bytes, row: int, col: int):
    """(receiver_name, method_name) when the cursor sits on the label
    of a message whose text is call-shaped; arguments are free text and
    never matter. None anywhere else."""
    label = _label_under_cursor(src, row, col)
    if label is None:
        return None
    m = _CALL_SHAPE.match(node_text(src, label))
    if m is None:
        return None
    receiver = _relation_receiver(src, label.parent)
    if receiver is None:
        return None
    return receiver, m.group(1)


def action_at(index, row: int):
    """(lane, method) when the row holds a call-shaped activity action."""
    for name, lane, arow, _scol, _ecol in index.actions:
        if arow == row:
            return lane, name
    return None


def _receiver_candidates(receiver: str, index, workspace) -> list:
    """The entities a receiver name may denote: document first, then the
    workspace card (qualified tails included)."""
    from plantuml_lsp.index import resolve_entity

    candidates = []
    resolved = resolve_entity(receiver, index.entities)
    if resolved is not None:
        candidates.append(index.entities[resolved])
    if workspace is not None:
        card = workspace.class_card(receiver)
        if card is None and "." in receiver:
            card = workspace.class_card(receiver.rsplit(".", 1)[-1])
        if card is not None:
            candidates.append(card)
    return candidates


def resolve_call(receiver: str, method: str, index, workspace):
    """Three tiers, as ('declared'|'drift'|'unknown', entity|None,
    location|None):

    - declared: the receiver's class (or, receiver classless, some
      class in the workspace) declares the method — gd/hover target;
    - drift: the receiver correlates with a class that does NOT
      declare it — behaviour drifting from structure (warning);
    - unknown: no receiver class and no class anywhere declares it
      (information)."""
    from plantuml_lsp.workspace import CLASS_KINDS

    candidates = _receiver_candidates(receiver, index, workspace)
    for entity in candidates:
        if method in entity.methods:
            return "declared", entity, entity.methods[method]
    for entity in candidates:
        if entity.kind in CLASS_KINDS:
            return "drift", entity, None
    if workspace is not None:
        for entity in workspace.method_declarations(method):
            return "declared", entity, entity.methods[method]
    return "unknown", None, None


def entity_definitions(name, index, workspace) -> list[EntityLocation]:
    """Declarations a name (qualified tails included) resolves to —
    the document closure first, then the whole workspace."""
    from plantuml_lsp.index import resolve_entity

    names = [name]
    if "." in name:
        names.append(name.rsplit(".", 1)[-1])
    found: list[EntityLocation] = []
    seen: set[EntityLocation] = set()
    resolved = resolve_entity(name, index.entities)
    if resolved is not None:
        e = index.entities[resolved]
        loc = (e.path, e.row, e.scol, e.ecol)
        seen.add(loc)
        found.append(loc)
    if workspace is not None:
        for candidate in names:
            for e in workspace.declarations(candidate):
                loc = (e.path, e.row, e.scol, e.ecol)
                if loc not in seen:
                    seen.add(loc)
                    found.append(loc)
    return found


def resolve_definition(doc_path: str, line_text: str) -> DefinitionTuple | None:
    m = _INCLUDESUB.match(line_text)
    if m:
        target = (Path(doc_path).parent / m.group(1)).resolve()
        return _find_startsub(target, m.group(2))
    m = _INCLUDE.match(line_text)
    if m:
        target = (Path(doc_path).parent / m.group(1)).resolve()
        return (str(target), 0) if target.is_file() else None
    return None


def _find_startsub(target: Path, sub_name: str) -> DefinitionTuple | None:
    if not target.is_file():
        return None
    for lineno, line in enumerate(target.read_text(errors="replace").splitlines()):
        m = _STARTSUB.match(line)
        if m and m.group(1) == sub_name:
            return (str(target), lineno)
    return None
