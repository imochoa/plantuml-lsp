"""Component and deployment elements: declarations, bracket names, nesting."""

from pathlib import Path

from plantuml_lsp.index import build_index, identifier_at
from plantuml_lsp.rename import rename_edits
from plantuml_lsp.symbols import collect_symbols

SRC = b"""@startuml
component Foo
component "Payment Service" as PS
[Billing] as B
[Inventory]
() "REST API" as API
node Server {
  component Web
}
[Inventory] --> PS : reserve
Web --> [Billing]
@enduml
"""


def make_doc(tmp_path: Path) -> Path:
    doc = tmp_path / "components.puml"
    doc.write_bytes(SRC)
    return doc


def test_elements_index_by_kind_and_alias(tmp_path):
    index = build_index(str(make_doc(tmp_path)), SRC)
    assert index.entities["Foo"].kind == "component"
    assert index.entities["PS"].name == "Payment Service"
    assert index.entities["B"].kind == "component"
    assert index.entities["B"].name == "Billing"
    assert index.entities["API"].kind == "interface"
    assert index.entities["Server"].kind == "node"


def test_bracket_names_drop_their_brackets(tmp_path):
    index = build_index(str(make_doc(tmp_path)), SRC)
    assert "Inventory" in index.entities
    assert "[Inventory]" not in index.entities
    assert identifier_at(SRC, 9, 3) == "Inventory"


def test_nested_elements_are_indexed(tmp_path):
    index = build_index(str(make_doc(tmp_path)), SRC)
    assert index.entities["Web"].row == 7


def test_bracket_references_are_occurrences(tmp_path):
    index = build_index(str(make_doc(tmp_path)), SRC)
    rows = [row for _p, row, _s, _e in index.occurrences("Inventory")]
    assert rows == [4, 9]
    assert index.relations["Inventory"] == ["[Inventory] --> PS : reserve"]


def test_rename_keeps_the_brackets(tmp_path):
    index = build_index(str(make_doc(tmp_path)), SRC)
    edits = rename_edits("Inventory", "Stock", index)
    # the edit replaces the inner text, so `[Inventory]` becomes `[Stock]`
    assert [(row, scol, ecol) for _p, row, scol, ecol, _n in edits] == [
        (4, 1, 10),
        (9, 1, 10),
    ]


def test_elements_are_document_symbols():
    names = {(name, kind) for name, kind, *_ in collect_symbols(SRC)}
    assert ("Foo", "component") in names
    assert ("PS", "component") in names
    assert ("Server", "node") in names
    assert ("Server.Web", "component") in names
