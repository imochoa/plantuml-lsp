"""Shared parser access: one grammar instance per process."""

import tree_sitter_plantuml
from tree_sitter import Language, Node, Parser, Tree

_parser: Parser | None = None


def get_parser() -> Parser:
    global _parser
    if _parser is None:
        _parser = Parser(Language(tree_sitter_plantuml.language()))
    return _parser


def parse(src: bytes) -> Tree:
    return get_parser().parse(src)


def node_text(src: bytes, node: Node) -> str:
    return src[node.start_byte : node.end_byte].decode(errors="replace")


# CST tokens that spell an entity name: bare, "quoted" or [bracketed]
NAME_TYPES = ("identifier", "string", "bracket_name")


def name_text(src: bytes, node: Node) -> str:
    """The name a name token spells, without its quotes or brackets."""
    text = node_text(src, node)
    if node.type == "bracket_name":
        return text[1:-1]
    return text.strip('"')
