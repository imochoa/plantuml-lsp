"""Document symbols: entities, participants and grouping blocks."""

from tree_sitter import Node

from plantuml_lsp.index import element_kind
from plantuml_lsp.parsing import name_text, parse

# (qualified_name, kind, start_line, start_col, end_line, end_col)
SymbolTuple = tuple[str, str, int, int, int, int]

_ENTITY_KINDS = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
}
_GROUP_KINDS = {
    "namespace_block": "namespace",
    "package_block": "package",
}


def collect_symbols(src: bytes) -> list[SymbolTuple]:
    found: list[SymbolTuple] = []
    _walk(parse(src).root_node, src, [], found)
    return found


def _walk(node: Node, src: bytes, ns: list[str], found: list[SymbolTuple]) -> None:
    kind = node.type
    if kind in _GROUP_KINDS:
        name = _name_of(node, src)
        found.append(_entry(".".join([*ns, name]), _GROUP_KINDS[kind], node))
        for child in node.children:
            _walk(child, src, [*ns, name], found)
        return
    if kind in _ENTITY_KINDS:
        found.append(
            _entry(".".join([*ns, _name_of(node, src)]), _ENTITY_KINDS[kind], node)
        )
    elif kind == "participant_declaration":
        found.append(_entry(_id_of(node, src), "participant", node))
    elif kind == "element_declaration":
        # containers (node X { … }) scope their nested elements
        name = _id_of(node, src)
        found.append(_entry(".".join([*ns, name]), element_kind(node, src), node))
        for child in node.children:
            _walk(child, src, [*ns, name], found)
        return
    for child in node.children:
        _walk(child, src, ns, found)


def _name_of(node: Node, src: bytes) -> str:
    name = node.child_by_field_name("name")
    return name_text(src, name) if name is not None else "?"


def _id_of(node: Node, src: bytes) -> str:
    """The alias when there is one: it is what relations reference."""
    alias = node.child_by_field_name("alias")
    return name_text(src, alias) if alias is not None else _name_of(node, src)


def _entry(name: str, kind: str, node: Node) -> SymbolTuple:
    sr, sc = node.start_point
    er, ec = node.end_point
    return (name, kind, sr, sc, er, ec)
