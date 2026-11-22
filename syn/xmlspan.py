"""Byte-exact map of a Salesforce metadata XML file.

The writers in ``syn`` never serialize a tree: they splice new bytes into the
original file, so everything they do not touch stays byte-identical. To do
that they need to know, for the root's direct children (blocks) and their
direct children (fields), where each element starts and ends in the file.
Deeper elements are not mapped: a field that contains elements is marked as
nested and its inner XML is reachable as raw text through ``Document.inner``.
"""

from __future__ import annotations

import re
from codecs import BOM_UTF8
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from xml.parsers import expat

_START_TAG = re.compile(rb"""<[^\s/>]+(?:\s+[^\s=/>]+\s*=\s*(?:"[^"]*"|'[^']*'))*\s*/?>""")
_DEFAULT_INDENT = "    "


@dataclass(slots=True, eq=False)
class Node:
    """One mapped element.

    ``start`` and ``end`` delimit the whole element, ``open_end`` is the end of
    its start tag and ``close_start`` the start of its end tag, so the inner
    content is ``data[open_end:close_start]``. A self-closing element has
    ``open_end == close_start == end``. ``value`` is the decoded text of a leaf
    (no element children) and ``None`` otherwise. ``children`` holds the fields
    of a block; fields themselves never have mapped children.
    """

    name: str
    start: int
    open_end: int
    close_start: int = -1
    end: int = -1
    leaf: bool = True
    value: str | None = None
    children: list[Node] = field(default_factory=list)

    @property
    def self_closing(self) -> bool:
        return self.open_end == self.end


@dataclass(eq=False)
class Document:
    """A parsed file: the original bytes, the root node and the file's conventions."""

    data: bytes
    root: Node
    newline: str
    final_newline: bool
    bom: bool

    @property
    def blocks(self) -> list[Node]:
        return self.root.children

    def inner(self, node: Node) -> str:
        """Raw inner XML of ``node``, undecoded: entities and nested tags stay as written."""
        return self.data[node.open_end : node.close_start].decode("utf-8")

    def line_start(self, pos: int) -> int:
        return self.data.rfind(b"\n", 0, pos) + 1

    def indent_at(self, pos: int) -> str | None:
        """Whitespace before ``pos`` on its line, or ``None`` if something else precedes it."""
        prefix = self.data[self.line_start(pos) : pos]
        return prefix.decode("ascii") if not prefix.strip() else None

    def line_span(self, node: Node) -> tuple[int, int]:
        """Span to delete to remove ``node``: its whole lines when it stands alone on them."""
        start = self.line_start(node.start)
        stop = self.data.find(b"\n", node.end)
        stop = len(self.data) if stop < 0 else stop + 1
        if self.data[start : node.start].strip() or self.data[node.end : stop].strip():
            return node.start, node.end
        return start, stop

    @cached_property
    def indent_unit(self) -> str:
        """One indentation step, measured between a block and its first field."""
        for block in self.root.children:
            if not block.children:
                continue
            outer = self.indent_at(block.start)
            inner = self.indent_at(block.children[0].start)
            if outer is not None and inner is not None and len(inner) > len(outer) and inner.startswith(outer):
                return inner[len(outer) :]
        for block in self.root.children:
            if indent := self.indent_at(block.start):
                return indent
        return _DEFAULT_INDENT

    @cached_property
    def escapes_quotes(self) -> bool:
        """Whether new text should write ``'`` and ``"`` as entities.

        Files retrieved from Salesforce write ``&apos;`` and ``&quot;`` in text,
        so that is the default; a file that already has a raw quote in some text
        content is followed instead. Computed lazily since only values with
        quotes need it.
        """
        for block in self.root.children:
            for node in (block, *block.children):
                text = self.data[node.open_end : node.close_start] if node.leaf else b""
                if b"'" in text or b'"' in text:
                    return False
        return True

    def escape(self, value: str) -> str:
        """Encode ``value`` as element text in this file's entity and newline style."""
        text = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if ("'" in text or '"' in text) and self.escapes_quotes:
            text = text.replace("'", "&apos;").replace('"', "&quot;")
        text = text.replace("\r\n", "\n")
        return text.replace("\n", self.newline) if self.newline != "\n" else text


def parse(data: bytes) -> Document:
    """Map ``data``; raises ``ValueError`` with the line number when it is not well-formed XML."""
    parser = expat.ParserCreate()
    parser.buffer_text = True
    stack: list[Node] = []
    texts: list[list[str]] = []
    depth = 0
    roots: list[Node] = []

    def on_start(name: str, attrs: dict[str, str]) -> None:
        """Depth counts open elements; only depths 1 to 3 (root, block, field) are mapped."""
        nonlocal depth
        depth += 1
        if depth > 3:
            stack[2].leaf = False
            return
        pos = parser.CurrentByteIndex
        match = _START_TAG.match(data, pos)
        open_end = match.end() if match else data.index(b">", pos) + 1
        node = Node(name, pos, open_end)
        (stack[-1].children if stack else roots).append(node)
        stack.append(node)
        texts.append([])

    def on_end(name: str) -> None:
        """A self-closing element reports a misleading offset here, so its start tag decides."""
        nonlocal depth
        depth -= 1
        if depth >= 3:
            return
        node = stack.pop()
        parts = texts.pop()
        if data[node.open_end - 2 : node.open_end] == b"/>":
            node.close_start = node.end = node.open_end
        else:
            node.close_start = parser.CurrentByteIndex
            node.end = data.index(b">", node.close_start) + 1
        if node.children:
            node.leaf = False
        if node.leaf:
            node.value = "".join(parts)

    def on_text(text: str) -> None:
        if 2 <= depth <= 3:
            texts[-1].append(text)

    parser.StartElementHandler = on_start
    parser.EndElementHandler = on_end
    parser.CharacterDataHandler = on_text
    try:
        parser.Parse(data, True)
    except expat.ExpatError as error:
        raise ValueError(f"not well-formed XML at line {error.lineno}: {expat.ErrorString(error.code)}") from None
    first_newline = data.find(b"\n")
    return Document(
        data=data,
        root=roots[0],
        newline="\r\n" if first_newline > 0 and data[first_newline - 1] == 0x0D else "\n",
        final_newline=data.endswith(b"\n"),
        bom=data.startswith(BOM_UTF8),
    )


def load(path: str | Path) -> Document:
    return parse(Path(path).read_bytes())


def well_formed(data: bytes) -> bool:
    try:
        expat.ParserCreate().Parse(data, True)
    except expat.ExpatError:
        return False
    return True


Edit = tuple[int, int, bytes]
"""Replace ``data[start:end]`` with the bytes; a zero-width edit is an insertion."""


def splice(data: bytes, edits: list[Edit]) -> bytes:
    """Apply non-overlapping edits in one pass; insertions at the same offset keep their list order."""
    pieces: list[bytes] = []
    cursor = 0
    for start, end, replacement in sorted(edits, key=lambda edit: (edit[0], edit[1])):
        pieces += (data[cursor:start], replacement)
        cursor = end
    pieces.append(data[cursor:])
    return b"".join(pieces)
