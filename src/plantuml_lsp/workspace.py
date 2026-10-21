"""The workspace index: every entity of every diagram under the root.

Where the per-document index (index.py) answers questions inside one
document and its include closure, this one correlates across diagrams:
the class a sequence participant choreographs, every sequence a class
appears in, and the participants no class in the workspace explains.
Correlation is by name — an entity matches through its id (alias) or
its display name.

Incremental by construction: each file is cached under an
(mtime_ns, size) signature and only new or changed files reparse on
refresh(); a per-file token-name set keeps occurrence scans away from
files that cannot contain the queried name.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

from plantuml_lsp.index import Entity, Index, _scan_occurrences, _walk
from plantuml_lsp.parsing import NAME_TYPES, name_text, parse

_EXTENSIONS = {".puml", ".plantuml", ".iuml", ".wsd"}
# Hidden directories (relative to the root) are never part of the
# workspace: .git, .venv, .argos federation mirrors, .claude worktree
# copies of the whole repo… — plus the usual vendored dirs.
_SKIP_DIRS = {"node_modules", "build", "__pycache__"}
_REFRESH_TTL = 10.0

CLASS_KINDS = {
    "class",
    "interface",
    "enum",
    "annotation",
    "exception",
    "metaclass",
    "protocol",
    "struct",
    "record",
    "dataclass",
}


class _FileEntry:
    __slots__ = ("signature", "src", "entities", "names", "calls")

    def __init__(
        self,
        signature: tuple[int, int],
        src: bytes,
        entities: list[Entity],
        names: set[str],
        calls: dict,
    ) -> None:
        self.signature = signature
        self.src = src
        self.entities = entities
        self.names = names
        self.calls = calls


class WorkspaceIndex:
    def __init__(self, root: str) -> None:
        self.root = Path(root)
        self._files: dict[str, _FileEntry] = {}
        self._last_refresh = 0.0
        self.refresh()

    # ── Maintenance ─────────────────────────────────────────────────

    def _iter_files(self) -> Iterator[Path]:
        import os

        for dirpath, dirnames, filenames in os.walk(self.root):
            # prune excluded subtrees instead of filtering afterwards —
            # walking a .venv to discard it costs more than the scan
            dirnames[:] = [
                d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")
            ]
            for name in filenames:
                if Path(name).suffix in _EXTENSIONS:
                    yield Path(dirpath) / name

    def refresh(self) -> None:
        seen: set[str] = set()
        for path in self._iter_files():
            key = str(path.resolve())
            seen.add(key)
            stat = path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            entry = self._files.get(key)
            if entry is None or entry.signature != signature:
                self._files[key] = self._scan(key, path.read_bytes(), signature)
        for gone in set(self._files) - seen:
            del self._files[gone]
        self._last_refresh = time.monotonic()

    def refresh_if_stale(self) -> None:
        if time.monotonic() - self._last_refresh > _REFRESH_TTL:
            self.refresh()

    def _scan(self, key: str, src: bytes, signature: tuple[int, int]) -> _FileEntry:
        scratch = Index(doc_path=key)
        root = parse(src).root_node
        _walk(root, src, key, scratch)
        entities = list(scratch.entities.values()) + scratch.duplicates
        names: set[str] = set()
        _collect_names(root, src, names)
        return _FileEntry(signature, src, entities, names, scratch.calls)

    # ── Queries ─────────────────────────────────────────────────────

    def entities(self) -> list[Entity]:
        return [e for entry in self._files.values() for e in entry.entities]

    def declarations(self, name: str) -> list[Entity]:
        """Every declaration the name correlates with, id or display name."""
        return [e for e in self.entities() if e.id == name or e.name == name]

    def symbols(self, query: str) -> list[Entity]:
        needle = query.lower()
        return [
            e
            for e in self.entities()
            if needle in e.id.lower() or needle in e.name.lower()
        ]

    def occurrences(self, name: str) -> list[tuple[str, int, int, int]]:
        found: list[tuple[str, int, int, int]] = []
        suffix = "." + name
        for key, entry in self._files.items():
            if name not in entry.names and not any(
                n.endswith(suffix) for n in entry.names
            ):
                continue
            _scan_occurrences(parse(entry.src).root_node, entry.src, name, key, found)
        return found

    def file_bytes(self, path: str) -> bytes | None:
        entry = self._files.get(str(Path(path).resolve()))
        return entry.src if entry is not None else None

    def call_sites(self, method: str) -> list[tuple[str, int, int, int]]:
        """Every call-shaped use of the method name across the
        workspace: sequence labels and activity actions."""
        found: list[tuple[str, int, int, int]] = []
        for entry in self._files.values():
            found.extend(entry.calls.get(method, []))
        return found

    def method_declarations(self, method: str) -> list[Entity]:
        """Every class of the workspace declaring the method — the
        fallback when a call's receiver has no class of its own."""
        return [
            e for e in self.entities() if e.kind in CLASS_KINDS and method in e.methods
        ]

    def class_card(self, name: str) -> Entity | None:
        """The structural declaration a name correlates with, if any."""
        for entity in self.declarations(name):
            if entity.kind in CLASS_KINDS:
                return entity
        return None

    def unmatched_participants(self, doc_path: str) -> list[Entity]:
        """Declared plain participants of one document with no structural
        declaration anywhere in the workspace. Actors and the other
        participant kinds are legitimately abstract and stay exempt."""
        entry = self._files.get(str(Path(doc_path).resolve()))
        if entry is None:
            return []
        return [
            e
            for e in entry.entities
            if e.kind == "participant"
            # class-cased names only: lowercase participants (cli,
            # daemon…) are conceptual actors, not missing classes
            and e.name[:1].isupper()
            and self.class_card(e.id) is None
            and self.class_card(e.name) is None
        ]


def _collect_names(node, src: bytes, names: set[str]) -> None:
    if node.type in NAME_TYPES:
        names.add(name_text(src, node))
        return
    for child in node.children:
        _collect_names(child, src, names)
