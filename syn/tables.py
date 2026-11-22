"""Repeated metadata blocks as tables: ``rows`` reads them; ``set``, ``add`` and ``del`` change one row.

A profile is thousands of lines of one block shape, so ``rows`` prints it as
tab-separated rows keyed by the field that identifies each block, which
costs a fraction of the XML. The writers splice bytes into the original file
(see ``syn.xmlspan``): only the touched row changes, and new text copies the
indentation, newline style and field order the file already uses.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import shutil
import tempfile
from bisect import bisect_right
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from syn.blocks import spec_for
from syn.cli import SynError
from syn.xmlspan import Document, Edit, Node, load, splice, well_formed

KEY_JOIN = "+"
NESTED = "{...}"
INFERRED = " (inferred key)"
_GLOB_CHARS = "*?["
_PATH_CHARS = "./\\:*?["

Plan = Callable[[Document], tuple[list[Edit], str]]


@dataclass(frozen=True, slots=True)
class Table:
    """The blocks named ``block`` in one file, with the key that identifies them."""

    block: str
    rows: list[Node]
    key: tuple[str, ...]
    ordered: bool
    inferred: bool

    def key_of(self, row: Node) -> tuple[str, ...]:
        return tuple(leaf_value(row, name) for name in self.key)


def table(doc: Document, block: str) -> Table:
    """Collect the ``block`` rows of ``doc``; a type missing from ``syn.blocks`` gets an inferred key."""
    rows = [node for node in doc.blocks if node.name == block and not node.leaf]
    if spec := spec_for(doc.root.name, block):
        return Table(block, rows, spec.key, spec.ordered, False)
    return Table(block, rows, _infer_key(rows), True, True)


def leaf_value(row: Node, name: str) -> str:
    """Value of the first leaf field ``name`` of ``row``, or ``""`` when absent."""
    return next((child.value for child in row.children if child.name == name and child.value is not None), "")


def _infer_key(rows: list[Node]) -> tuple[str, ...]:
    """The first leaf field, in the first row's order, that every row has once and with a unique value."""
    if not rows:
        return ()
    for name in dict.fromkeys(child.name for child in rows[0].children if child.leaf):
        seen: set[str] = set()
        for row in rows:
            values = [child.value for child in row.children if child.name == name]
            if len(values) != 1 or values[0] is None or values[0] in seen:
                break
            seen.add(values[0])
        else:
            return (name,)
    return ()


def parse_key(tab: Table, text: str) -> tuple[str, ...]:
    """Turn a KEY argument into a key tuple; only composite keys split on ``+``."""
    if not tab.key:
        raise SynError(f"{tab.block} has no field with unique values, so rows cannot be addressed")
    if len(tab.key) == 1:
        return (text,)
    parts = text.split(KEY_JOIN)
    if len(parts) > len(tab.key):
        raise SynError(f"{tab.block} key is {KEY_JOIN.join(tab.key)}, got {len(parts)} parts")
    return tuple(parts) + ("",) * (len(tab.key) - len(parts))


def show_key(parts: Iterable[str]) -> str:
    items = list(parts)
    while len(items) > 1 and not items[-1]:
        items.pop()
    return KEY_JOIN.join(items)


def find_row(tab: Table, text: str) -> Node:
    wanted = parse_key(tab, text)
    matches = [row for row in tab.rows if tab.key_of(row) == wanted]
    if not matches:
        raise SynError(f"{tab.block} {text} not found")
    if len(matches) > 1:
        raise SynError(f"{tab.block} {text} matches {len(matches)} rows")
    return matches[0]


def _insert_index(names: list[str], new: str, siblings: Iterable[Node]) -> int:
    """Where field ``new`` goes among ``names``: after the fields a sibling row puts before it, else alphabetical."""
    for sibling in siblings:
        order = [child.name for child in sibling.children]
        if new in order:
            before = set(order[: order.index(new)])
            return max((index + 1 for index, name in enumerate(names) if name in before), default=0)
    return next((index for index, name in enumerate(names) if name > new), len(names))


def _order_names(names: Iterable[str], siblings: list[Node]) -> list[str]:
    ordered: list[str] = []
    for name in names:
        ordered.insert(_insert_index(ordered, name, siblings), name)
    return ordered


def _field(doc: Document, name: str, value: str) -> bytes:
    return f"<{name}>{doc.escape(value)}</{name}>".encode()


def _layout(doc: Document, node: Node) -> tuple[bytes, bytes, bytes] | None:
    """Whitespace of ``node`` before its first field, between fields and after its last field."""
    kids = node.children
    first = doc.data[node.open_end : kids[0].start]
    between = doc.data[kids[0].end : kids[1].start] if len(kids) > 1 else first
    last = doc.data[kids[-1].end : node.close_start]
    return None if (first + between + last).strip() else (first, between, last)


def _block_indent(doc: Document) -> str:
    indent = doc.indent_at(doc.blocks[0].start) if doc.blocks else None
    return doc.indent_unit if indent is None else indent


def _block_sep(doc: Document) -> bytes:
    """Whitespace between two sibling blocks, copied from the file; nothing when blocks share a line."""
    for left, right in zip(doc.blocks, doc.blocks[1:]):
        gap = doc.data[left.end : right.start]
        if not gap.strip():
            return gap
    if doc.blocks and doc.indent_at(doc.blocks[0].start) is None:
        return b""
    return (doc.newline + _block_indent(doc)).encode()


def plan_set(doc: Document, block: str, key: str, assignments: list[tuple[str, str]]) -> tuple[list[Edit], str]:
    """Edits that give the ``block`` row ``key`` the assigned values, and the result message."""
    tab = table(doc, block)
    row = find_row(tab, key)
    values = dict(assignments)
    if renamed := [name for name in tab.key if name in values]:
        new_key = tuple(values.get(name, leaf_value(row, name)) for name in tab.key)
        if any(tab.key_of(other) == new_key for other in tab.rows if other is not row):
            raise SynError(f"{block} {show_key(new_key)} already exists ({', '.join(renamed)})")
    edits: list[Edit] = []
    changes: list[str] = []
    inserts: dict[int, list[str]] = {}
    names = [child.name for child in row.children]
    for name, value in values.items():
        found = [child for child in row.children if child.name == name]
        if len(found) > 1:
            raise SynError(f"{name} appears {len(found)} times in this row")
        if not found:
            if not row.children:
                raise SynError(f"{block} {key} is empty")
            inserts.setdefault(_insert_index(names, name, tab.rows), []).append(name)
            changes.append(f"{name} ->{_cell(value)}")
            continue
        node = found[0]
        if node.value is None:
            raise SynError(f"{name} holds nested XML")
        if node.value == value.replace("\r\n", "\n"):
            continue
        if node.self_closing:
            edits.append((node.start, node.end, _field(doc, name, value)))
        else:
            edits.append((node.open_end, node.close_start, doc.escape(value).encode()))
        changes.append(f"{name} {_cell(node.value)}->{_cell(value)}")
    if inserts:
        sep = _field_sep(doc, row)
        for index, group in inserts.items():
            fields = [_field(doc, name, values[name]) for name in _order_names(group, tab.rows)]
            if index:
                edits.append((row.children[index - 1].end, row.children[index - 1].end, b"".join(sep + f for f in fields)))
            else:
                edits.append((row.children[0].start, row.children[0].start, b"".join(f + sep for f in fields)))
    return edits, f"{block} {key} {', '.join(changes) if changes else 'unchanged'}{_mark(tab)}"


def _field_sep(doc: Document, row: Node) -> bytes:
    layout = _layout(doc, row)
    if layout:
        return layout[1]
    return (doc.newline + (doc.indent_at(row.start) or "") + doc.indent_unit).encode()


def plan_add(
    doc: Document, block: str, assignments: list[tuple[str, str]], before: str | None = None, after: str | None = None
) -> tuple[list[Edit], str]:
    """Edits that insert a new ``block`` row built from the assignments, and the result message."""
    tab = table(doc, block)
    values = dict(assignments)
    new_key: tuple[str, ...] = ()
    if tab.key:
        if not values.get(tab.key[0]):
            raise SynError(f"{block} needs its key field {tab.key[0]}")
        new_key = tuple(values.get(name, "") for name in tab.key)
        if any(tab.key_of(row) == new_key for row in tab.rows):
            raise SynError(f"{block} {show_key(new_key)} already exists")
    label = show_key(new_key) if new_key else next(iter(values.values()))
    text = _render_block(doc, tab, [_field(doc, name, values[name]) for name in _order_names(values, tab.rows)])
    anchor, place_before = _anchor(doc, tab, new_key, before, after)
    if anchor is None:
        edit = (doc.root.open_end, doc.root.open_end, (doc.newline + doc.indent_unit).encode() + text)
    elif place_before:
        edit = (anchor.start, anchor.start, text + _block_sep(doc))
    else:
        edit = (anchor.end, anchor.end, _block_sep(doc) + text)
    return [edit], f"{block} +{label}{_mark(tab)}"


def _render_block(doc: Document, tab: Table, fields: list[bytes]) -> bytes:
    """A new block shaped like an existing one: same type first, else any block with fields."""
    template = next((row for row in tab.rows if row.children), None) or next(
        (node for node in doc.blocks if node.children), None
    )
    layout = _layout(doc, template) if template else None
    if layout is None:
        indent = _block_indent(doc)
        inner = (doc.newline + indent + doc.indent_unit).encode()
        layout = (inner, inner, (doc.newline + indent).encode())
    first, between, last = layout
    name = tab.block.encode()
    return b"<" + name + b">" + first + between.join(fields) + last + b"</" + name + b">"


def _anchor(
    doc: Document, tab: Table, new_key: tuple[str, ...], before: str | None, after: str | None
) -> tuple[Node | None, bool]:
    """The node the new block goes next to, and whether it goes before that node."""
    if before or after:
        return find_row(tab, before or after or ""), bool(before)
    rows = tab.rows
    if rows:
        if not tab.ordered and new_key:
            keys = [tab.key_of(row) for row in rows]
            for fold in (_same, _casefold):
                folded = [fold(key) for key in keys]
                if all(left <= right for left, right in zip(folded, folded[1:])):
                    index = bisect_right(folded, fold(new_key))
                    return (rows[index], True) if index < len(rows) else (rows[-1], False)
        return rows[-1], False
    following = next((node for node in doc.blocks if node.name > tab.block), None)
    if following:
        return following, True
    return (doc.blocks[-1], False) if doc.blocks else (None, True)


def _mark(tab: Table) -> str:
    return INFERRED if tab.inferred else ""


def _same(key: tuple[str, ...]) -> tuple[str, ...]:
    return key


def _casefold(key: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(part.casefold() for part in key)


def plan_del(doc: Document, block: str, key: str) -> tuple[list[Edit], str]:
    """Edit that removes the ``block`` row ``key`` with its line, and the result message."""
    tab = table(doc, block)
    start, end = doc.line_span(find_row(tab, key))
    return [(start, end, b"")], f"{block} -{key}{_mark(tab)}"


def expand(patterns: Iterable[str]) -> list[Path]:
    """Expand glob patterns (Windows shells do not), keep literal paths, drop duplicates."""
    found: dict[str, Path] = {}
    for pattern in patterns:
        if any(char in pattern for char in _GLOB_CHARS):
            matches = [Path(match) for match in sorted(glob.glob(pattern, recursive=True))]
            if not matches:
                raise SynError(f"no file matches {pattern}")
        else:
            matches = [Path(pattern)]
        for match in matches:
            found.setdefault(os.path.normcase(os.path.abspath(match)), match)
    return list(found.values())


def short_names(paths: list[Path]) -> list[str]:
    """The shortest labels that tell the files apart: name before the first dot, file name, then parent folders."""
    full = [Path(os.path.abspath(path)) for path in paths]
    for level in range(max(len(path.parts) for path in full) + 1):
        names = [path.name.split(".", 1)[0] if level == 0 else "/".join(path.parts[-level:]) for path in full]
        if len(set(names)) == len(names):
            return names
    return [str(path) for path in full]


def _split(items: list[str]) -> tuple[list[str], list[str]]:
    """Leading items that look like paths are files; the first that does not is the block."""
    for index, item in enumerate(items):
        if not any(char in item for char in _PATH_CHARS) and not os.path.isfile(item):
            if not index:
                raise SynError(f"expected a FILE before {item}")
            return items[:index], items[index:]
    return items, []


def _assignments(items: list[str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for item in items:
        name, sep, value = item.partition("=")
        if not sep or not name:
            raise SynError(f"expected FIELD=VALUE, got {item}")
        if any(name == seen for seen, _ in pairs):
            raise SynError(f"{name} given twice")
        pairs.append((name, value))
    return pairs


def _cell(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\n", "\\n").replace("\t", "\\t")


def _glob_regex(pattern: str) -> re.Pattern[str]:
    return re.compile("".join(".*" if char == "*" else re.escape(char) for char in pattern), re.S)


def _field_text(doc: Document, row: Node, name: str, show_nested: bool) -> str:
    """One cell: repeated fields join with ``|``; nested XML shows as ``{...}`` unless asked for."""
    if row.leaf:
        return row.value or ""
    parts = [
        child.value if child.value is not None else " ".join(doc.inner(child).split()) if show_nested else NESTED
        for child in row.children
        if child.name == name
    ]
    return _cell("|".join(parts))


def _load_all(paths: list[Path], labels: list[str]) -> tuple[list[tuple[str, Document]], bool]:
    docs: list[tuple[str, Document]] = []
    failed = False
    for path, label in zip(paths, labels):
        try:
            docs.append((label, load(path)))
        except (OSError, ValueError) as error:
            failed = True
            print(f"{label}: error: {_reason(error)}")
    return docs, failed


def run_rows(args: argparse.Namespace) -> int:
    files, rest = _split(args.items)
    if len(rest) > 1:
        raise SynError(f"rows takes one BLOCK, got {' '.join(rest)}")
    paths = expand(files)
    docs, failed = _load_all(paths, short_names(paths))
    if not rest:
        for label, doc in docs:
            if len(docs) > 1:
                print(f"[{label}]")
            print("\n".join(summary(doc)))
        return int(failed)
    print("\n".join(rows_lines(docs, rest[0], args.where or [], args.cols)))
    return int(failed)


def summary(doc: Document) -> list[str]:
    """Root name, single values as ``name=value``, then one line per repeated block type."""
    groups: dict[str, list[Node]] = {}
    for node in doc.blocks:
        groups.setdefault(node.name, []).append(node)
    singles = [f"{name}={_cell(nodes[0].value or '')}" for name, nodes in groups.items() if len(nodes) == 1 and nodes[0].leaf]
    lines = [doc.root.name, *(["\t".join(singles)] if singles else []), "block\tn\tkey"]
    for name, nodes in groups.items():
        if all(node.leaf for node in nodes):
            if len(nodes) > 1:
                lines.append(f"{name}\t{len(nodes)}\t(values)")
            continue
        tab = table(doc, name)
        flag = INFERRED if tab.inferred else " ordered" if tab.ordered else ""
        lines.append(f"{name}\t{len(tab.rows)}\t{KEY_JOIN.join(tab.key) or '(none)'}{flag}")
    return lines


def rows_lines(docs: list[tuple[str, Document]], block: str, where: list[str], cols: str | None) -> list[str]:
    """The table of ``block`` across ``docs``: header once, key columns first, one row per line."""
    filters = []
    for item in where:
        name, sep, pattern = item.partition("=")
        if not sep or not name:
            raise SynError(f"--where expects FIELD=VALUE, got {item}")
        filters.append((name, _glob_regex(pattern)))
    tables: list[tuple[str, Document, Table]] = []
    for label, doc in docs:
        tab = table(doc, block)
        if not tab.rows:
            scalars = [node for node in doc.blocks if node.name == block and node.leaf]
            tab = Table(block, scalars, (block,) if scalars else (), True, False)
        tables.append((label, doc, tab))
    if docs and not any(tab.rows for _, _, tab in tables):
        names = sorted({node.name for _, doc in docs for node in doc.blocks})
        raise SynError(f"no {block} blocks; blocks here: {', '.join(names)}")
    picked: list[tuple[str, Document, Node]] = []
    for label, doc, tab in tables:
        for row in tab.rows:
            if all(regex.fullmatch(_field_text(doc, row, name, True)) for name, regex in filters):
                picked.append((label, doc, row))
    head = next((tab for _, _, tab in tables if tab.rows), tables[0][2] if tables else None)
    key = list(head.key) if head else []
    seen = dict.fromkeys(child.name for _, _, row in picked for child in row.children)
    if cols:
        wanted = [name for name in cols.split(",") if name]
        known = dict.fromkeys(child.name for _, _, tab in tables for row in tab.rows for child in row.children)
        if unknown := [name for name in wanted if name not in known and name not in key]:
            raise SynError(f"no field {', '.join(unknown)}; fields: {', '.join(known)}")
        columns = key + [name for name in wanted if name not in key]
    else:
        columns = key + [name for name in seen if name not in key]
    columns = columns or [block]
    explicit = set(wanted) if cols else set()
    lines = []
    if head and head.inferred:
        lines.append(f"# key {KEY_JOIN.join(head.key)}{INFERRED}" if head.key else "# no unique key field")
    cells = [(label, "\t".join(_field_text(doc, row, name, name in explicit) for name in columns)) for label, doc, row in picked]
    header = "\t".join(columns)
    if len(docs) < 2:
        return lines + [header] + [text for _, text in cells]
    with_column = [f"file\t{header}"] + [f"{label}\t{text}" for label, text in cells]
    grouped = [header]
    for index, (label, text) in enumerate(cells):
        if not index or cells[index - 1][0] != label:
            grouped.append(f"[{label}]")
        grouped.append(text)
    return lines + min(with_column, grouped, key=lambda block_lines: sum(len(line) + 1 for line in block_lines))


def _apply(args: argparse.Namespace, files: list[str], plan: Plan) -> int:
    """Run ``plan`` on every file; a failing file is reported and does not stop the others.

    Files with the same result share one line (``Admin, Sales: message``):
    the same change applied to forty profiles is one fact, not forty lines.
    """
    paths = expand(files)
    failed = False
    done: dict[str, list[str]] = {}
    for path, label in zip(paths, short_names(paths)):
        try:
            doc = load(path)
            edits, message = plan(doc)
            if edits:
                new = splice(doc.data, edits)
                if not well_formed(new):
                    raise SynError("the edit would break the XML; file left unchanged")
                if not args.dry_run:
                    _atomic_write(path, new)
        except (SynError, OSError, ValueError) as error:
            failed = True
            print(f"{label}: error: {_reason(error)}")
            continue
        if args.dry_run and edits:
            print(f"{label}: {message}")
            print("\n".join(unified_diff(path.as_posix(), doc.data, new)))
        else:
            done.setdefault(message, []).append(label)
    for message, labels in done.items():
        print(f"{', '.join(labels)}: {message}")
    return int(failed)


def _reason(error: Exception) -> str:
    """One wording for a file that cannot be read, whatever the command."""
    if isinstance(error, FileNotFoundError):
        return "no such file"
    if isinstance(error, IsADirectoryError | PermissionError) and Path(error.filename or "").is_dir():
        return "is a folder, not a file"
    if isinstance(error, OSError):
        return error.strerror or str(error)
    return str(error)


def _atomic_write(path: Path, data: bytes) -> None:
    """Write next to the target, then replace it, so a crash never leaves half a file."""
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        shutil.copymode(path, temp)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def unified_diff(label: str, old: bytes, new: bytes, context: int = 1) -> list[str]:
    """A unified diff that only runs ``difflib`` on the changed window, so 10 MB files stay fast."""
    before = old.decode("utf-8").splitlines()
    after = new.decode("utf-8").splitlines()
    limit = min(len(before), len(after))
    head = 0
    while head < limit and before[head] == after[head]:
        head += 1
    tail = 0
    while tail < limit - head and before[-1 - tail] == after[-1 - tail]:
        tail += 1
    low = max(0, head - context)
    window_a = before[low : min(len(before), len(before) - tail + context)]
    window_b = after[low : min(len(after), len(after) - tail + context)]
    lines = [f"--- {label}", f"+++ {label}"]
    for group in SequenceMatcher(None, window_a, window_b, autojunk=False).get_grouped_opcodes(context):
        a1, a2, b1, b2 = group[0][1], group[-1][2], group[0][3], group[-1][4]
        lines.append(f"@@ -{_hunk_range(low + a1, a2 - a1)} +{_hunk_range(low + b1, b2 - b1)} @@")
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                lines += [" " + line for line in window_a[i1:i2]]
            else:
                lines += ["-" + line for line in window_a[i1:i2]] + ["+" + line for line in window_b[j1:j2]]
    return lines


def _hunk_range(start: int, length: int) -> str:
    return f"{start + 1},{length}" if length else f"{start},0"


def run_set(args: argparse.Namespace) -> int:
    files, rest = _split(args.items)
    if len(rest) < 3:
        raise SynError("usage: syn set FILE... BLOCK KEY FIELD=VALUE...")
    block, key, pairs = rest[0], rest[1], _assignments(rest[2:])
    return _apply(args, files, lambda doc: plan_set(doc, block, key, pairs))


def run_add(args: argparse.Namespace) -> int:
    files, rest = _split(args.items)
    if len(rest) < 2:
        raise SynError("usage: syn add FILE... BLOCK FIELD=VALUE...")
    if args.before and args.after:
        raise SynError("give --before or --after, not both")
    block, pairs = rest[0], _assignments(rest[1:])
    return _apply(args, files, lambda doc: plan_add(doc, block, pairs, args.before, args.after))


def run_del(args: argparse.Namespace) -> int:
    files, rest = _split(args.items)
    if len(rest) != 2:
        raise SynError("usage: syn del FILE... BLOCK KEY")
    block, key = rest
    return _apply(args, files, lambda doc: plan_del(doc, block, key))


_FILES = """FILE is a path or glob pattern (syn expands it, quote it); the first argument that is not a
path is BLOCK. Run "syn rows FILE" to see the blocks and their key fields."""

_KEY = """KEY is the value of the block's key field. A composite key joins its parts with + in key order:
layoutAssignments takes LAYOUT+RECORDTYPE, or LAYOUT alone for the row without a recordType."""

_MULTI = """With several files the change is applied to each; files with the same result share one line
(Admin, Sales: result). A file that fails gets its own error line and the others are still
changed (exit 1 if any failed). Only the bytes of the touched row change; --dry-run prints
a unified diff per file and writes nothing."""

_HELP = {
    "rows": (
        "show repeated blocks as a table",
        "syn rows FILE... [BLOCK] [--where FIELD=VALUE]... [--cols A,B]",
        f"""Show the repeated blocks of Salesforce metadata XML as a compact table.

Without BLOCK: the root, a line of single values (name=value), then one line per block
type: name, row count, key fields ("(inferred key)" when syn guessed it, "ordered" when
file order matters).
With BLOCK: tab-separated rows, header first, key columns first. An empty cell is an
absent field, {NESTED} is nested XML (name the field in --cols to see it), repeated
fields join with |.
{_FILES}
With several files each row names its file: a file column or [file] group lines.

examples:
  syn rows Admin.profile-meta.xml
  syn rows "profiles/*.profile-meta.xml" fieldPermissions --where field=Account.* --where editable=false
  syn rows Admin.profile-meta.xml objectPermissions --cols allowRead,allowEdit""",
    ),
    "set": (
        "change fields of one row",
        "syn set FILE... BLOCK KEY FIELD=VALUE... [--dry-run]",
        f"""Change fields of one row. A FIELD the row lacks is inserted where sibling rows put it.
{_KEY}
{_FILES}
{_MULTI}

examples:
  syn set Admin.profile-meta.xml fieldPermissions Account.Foo__c editable=false readable=true
  syn set "profiles/*.profile-meta.xml" userPermissions ViewSetup enabled=true --dry-run
  syn set Admin.profile-meta.xml layoutAssignments "Account-Account Layout+Account.Partner" layout=Account-Partner""",
    ),
    "add": (
        "add a row",
        "syn add FILE... BLOCK FIELD=VALUE... [--before KEY | --after KEY] [--dry-run]",
        f"""Add a row built from FIELD=VALUE pairs; the key fields are required and must be new.
Placement: an unordered type goes where the file's sort order of keys puts it (after the
last row of the type if the file is not sorted); an ordered type goes after its last row;
--before/--after KEY put it next to that row; the first block of a type goes where
alphabetical order of block names puts it. Fields follow the order sibling rows use
(alphabetical without siblings), in the file's indentation and newline style.
{_KEY}
{_FILES}
{_MULTI}

examples:
  syn add Admin.profile-meta.xml fieldPermissions field=Account.Bar__c editable=false readable=true
  syn add Picklist.globalValueSet-meta.xml customValue fullName=Gold default=false label=Gold --after Silver""",
    ),
    "del": (
        "remove a row",
        "syn del FILE... BLOCK KEY [--dry-run]",
        f"""Remove one row and the line(s) it stands on.
{_KEY}
{_FILES}
{_MULTI}

example:
  syn del "profiles/*.profile-meta.xml" fieldPermissions Account.Old__c""",
    ),
}


def register(subparsers: argparse._SubParsersAction) -> None:
    runners = {"rows": run_rows, "set": run_set, "add": run_add, "del": run_del}
    for name, run in runners.items():
        summary_text, usage, description = _HELP[name]
        parser = subparsers.add_parser(
            name,
            help=summary_text,
            usage=usage,
            description=description,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        parser.add_argument("items", nargs="+", metavar="ARG", help="FILE... then BLOCK and the rest, as in usage")
        if name == "rows":
            parser.add_argument("--where", action="append", metavar="FIELD=VALUE", help="keep rows whose FIELD matches; * is a wildcard")
            parser.add_argument("--cols", metavar="A,B", help="show only these fields after the key columns")
        else:
            parser.add_argument("--dry-run", action="store_true", help="print a unified diff instead of writing")
        if name == "add":
            parser.add_argument("--before", metavar="KEY", help="insert before the row with this key")
            parser.add_argument("--after", metavar="KEY", help="insert after the row with this key")
        parser.set_defaults(run=run)
