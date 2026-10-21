"""Rename: rewrite every occurrence of an entity id across the closure.

Occurrences come from the index's CST scan (identifier and quoted-name
tokens only), so labels, comments and note bodies are never touched.
Quoted and bracketed occurrences keep their delimiters: the edit
replaces the inner text.
"""

from plantuml_lsp.index import Index

# (path, row, start_col, end_col, new_text)
EditTuple = tuple[str, int, int, int, str]


def edits_from(
    occurrences: list[tuple[str, int, int, int]],
    new_name: str,
    read,
) -> list[EditTuple]:
    edits: list[EditTuple] = []
    for path, row, scol, ecol in occurrences:
        src = read(path)
        if src is None:
            continue
        line = src.decode(errors="replace").splitlines()[row]
        token = line[scol:ecol]
        if (token.startswith('"') and token.endswith('"')) or (
            token.startswith("[") and token.endswith("]")
        ):
            edits.append((path, row, scol + 1, ecol - 1, new_name))
        else:
            edits.append((path, row, scol, ecol, new_name))
    return edits


def rename_edits(entity_id: str, new_name: str, index: Index) -> list[EditTuple]:
    return edits_from(index.occurrences(entity_id), new_name, index.files.get)
