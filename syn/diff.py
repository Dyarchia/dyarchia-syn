"""``syn diff``: what changed in the meaning of metadata XML, not in its lines.

A line diff of a profile is huge: blocks move, whitespace shifts and a
one-field change sits in a wall of context. Here every metadata file is parsed
and compared block by block: repeated blocks are matched by their key fields
(``syn.blocks``), so a moved block is no change, an added or removed row is one
line and a changed row lists only ``field: old -> new``. The same change in
many files (a field granted to forty profiles) is printed once with the files
it applies to. Plain text files get a line count and nothing else.
"""

from __future__ import annotations

import argparse
import difflib
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
import xml.etree.ElementTree as ET

from syn.blocks import spec_for
from syn.cli import SynError
from syn.find import relative_path
from syn.tables import show_key

NAMESPACE = "http://soap.sforce.com/2006/04/metadata"
IGNORED = frozenset({"locationX", "locationY"})
STATUS_NAMES = {"M": "modified", "A": "added", "D": "deleted"}
LISTED_FILES = 8

Change = tuple[str, str, str]


@dataclass
class FileChange:
    """The outcome for one file. ``kind`` is ``meta`` (``lines`` hold the changes), ``format`` or ``inline``."""

    status: str
    path: str
    kind: str = "inline"
    inline: str = ""
    lines: list[str] = field(default_factory=list)


def leaf_text(el: ET.Element) -> str:
    return (el.text or "").strip()


def child_text(el: ET.Element, tag: str) -> str:
    child = el.find(tag)
    return leaf_text(child) if child is not None else ""


def show(raw: str) -> str:
    return raw if raw else '""'


def canon(el: ET.Element) -> tuple:
    """Structural signature used to skip identical subtrees quickly."""
    return (el.tag, leaf_text(el), tuple(canon(c) for c in el if c.tag not in IGNORED))


def group(parent: ET.Element) -> dict[str, list[ET.Element]]:
    groups: dict[str, list[ET.Element]] = {}
    for child in parent:
        if child.tag not in IGNORED:
            groups.setdefault(child.tag, []).append(child)
    return groups


def keyed(items: list[ET.Element], key_of) -> dict[str, ET.Element]:
    """Index blocks by key; a repeated key gets a ``#n`` suffix so no block is lost."""
    index: dict[str, ET.Element] = {}
    for el in items:
        key = base = key_of(el)
        count = 1
        while key in index:
            count += 1
            key = f"{base}#{count}"
        index[key] = el
    return index


def summary(el: ET.Element, skip: tuple[str, ...] = ()) -> str:
    """Compact ``k=v`` form of the leaf fields of a block, with a count of nested blocks."""
    leaves = [f"{c.tag}={show(leaf_text(c))}" for c in el if c.tag not in IGNORED and c.tag not in skip and not len(c)]
    nested = sum(1 for c in el if c.tag not in IGNORED and len(c))
    if nested:
        leaves.append(f"(+{nested} nested)")
    return " ".join(leaves)


def render(change: Change) -> str:
    sign, path, text = change
    return f"~ {path}: {text}" if sign == "~" else f"{sign} {path} {text}".rstrip()


def row_item(change: Change) -> str:
    sign, path, text = change
    return f"{path}: {text}" if sign == "~" else f"{sign}{path} {text}".rstrip()


def diff_leaves(old: list[ET.Element], new: list[ET.Element], here: str) -> list[Change]:
    before, after = [leaf_text(e) for e in old], [leaf_text(e) for e in new]
    if before == after:
        return []
    if len(before) <= 1 and len(after) <= 1:
        return [("~", here, f"{show(before[0]) if before else '(none)'} -> {show(after[0]) if after else '(none)'}")]
    gained, lost = Counter(after) - Counter(before), Counter(before) - Counter(after)
    if not gained and not lost:
        return [("~", here, "reordered")]
    parts = [f"+{show(v)}" for v in gained.elements()] + [f"-{show(v)}" for v in lost.elements()]
    return [("~", here, ", ".join(parts))]


def pick_key(old: list[ET.Element], new: list[ET.Element]) -> str | None:
    for tag in ("fullName", "name"):
        usable = True
        for side in (old, new):
            values = [child_text(e, tag) for e in side]
            usable = usable and all(values) and len(set(values)) == len(values)
        if usable:
            return tag
    return None


def diff_maps(old: dict, new: dict, label) -> list[Change]:
    out: list[Change] = []
    out += [("+", label(k), summary(e)) for k, e in new.items() if k not in old]
    out += [("-", label(k), "") for k in old if k not in new]
    for k, el in new.items():
        before = old.get(k)
        if before is not None and canon(before) != canon(el):
            out += diff_children(before, el, label(k))
    return out


def diff_list(old: list[ET.Element], new: list[ET.Element], here: str) -> list[Change]:
    if all(not len(e) for e in (*old, *new)):
        return diff_leaves(old, new, here)
    if len(old) == 1 and len(new) == 1:
        return [] if canon(old[0]) == canon(new[0]) else diff_children(old[0], new[0], here)
    if not old and len(new) == 1:
        return [("+", here, summary(new[0]))]
    if not new and len(old) == 1:
        return [("-", here, "")]
    tag = pick_key(old, new)
    if tag:
        return diff_maps(
            {child_text(e, tag): e for e in old},
            {child_text(e, tag): e for e in new},
            lambda k: f"{here}[{k}]",
        )
    return diff_sequence(old, new, here)


def diff_sequence(old: list[ET.Element], new: list[ET.Element], here: str) -> list[Change]:
    """Unkeyed blocks aligned by content, so removing one item is one line and not a cascade of shifts.

    Only a replaced run of the same length is compared item by item; anything
    else is reported as removed and added items, each with its content.
    """
    before, after = [canon(e) for e in old], [canon(e) for e in new]
    out: list[Change] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        if op == "replace" and i2 - i1 == j2 - j1:
            for offset in range(i2 - i1):
                out += diff_children(old[i1 + offset], new[j1 + offset], f"{here}[{j1 + offset}]")
            continue
        out += [("-", f"{here}[{i}]", summary(old[i])) for i in range(i1, i2)]
        out += [("+", f"{here}[{j}]", summary(new[j])) for j in range(j1, j2)]
    return out


def diff_children(old: ET.Element, new: ET.Element, path: str) -> list[Change]:
    before, after = group(old), group(new)
    out: list[Change] = []
    for tag in [*after, *(t for t in before if t not in after)]:
        out += diff_list(before.get(tag, []), after.get(tag, []), f"{path}.{tag}" if path else tag)
    return out


def diff_spec(tag: str, key: tuple[str, ...], ordered: bool, old: list[ET.Element], new: list[ET.Element]) -> list[str]:
    def key_of(el: ET.Element) -> str:
        return show_key(child_text(el, k) for k in key)

    before, after = keyed(old, key_of), keyed(new, key_of)
    out = [f"+ {tag} {k} {summary(e, key)}".rstrip() for k, e in after.items() if k not in before]
    out += [f"- {tag} {k}" for k in before if k not in after]
    for k, el in after.items():
        previous = before.get(k)
        if previous is not None and canon(previous) != canon(el):
            fields = diff_children(previous, el, "")
            if fields:
                out.append(f"~ {tag} {k}  " + "; ".join(row_item(f) for f in fields))
    if ordered and [k for k in before if k in after] != [k for k in after if k in before]:
        out.append(f"~ {tag} reordered")
    return out


def diff_roots(old: ET.Element, new: ET.Element) -> list[str]:
    """Semantic changes between two metadata documents, one self-contained line each."""
    if canon(old) == canon(new):
        return []
    before, after = group(old), group(new)
    out: list[str] = []
    for tag in [*after, *(t for t in before if t not in after)]:
        spec = spec_for(new.tag, tag)
        if spec is not None:
            out += diff_spec(tag, spec.key, spec.ordered, before.get(tag, []), after.get(tag, []))
        else:
            out += [render(c) for c in diff_list(before.get(tag, []), after.get(tag, []), tag)]
    return out


def parse_meta(path: str, data: bytes | None) -> ET.Element | None:
    """The parsed document when ``path`` is Salesforce metadata XML, else ``None``."""
    if data is None or not path.lower().endswith(".xml"):
        return None
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    in_namespace = root.tag.startswith(f"{{{NAMESPACE}}}")
    if not (in_namespace or path.endswith("-meta.xml")):
        return None
    for el in root.iter():
        el.tag = el.tag.rpartition("}")[2]
    return root


def component_name(path: str) -> str:
    return Path(path.replace("\\", "/")).name.partition(".")[0]


def short_name(path: str) -> str:
    name = Path(path.replace("\\", "/")).name
    return name[: -len("-meta.xml")] if name.endswith("-meta.xml") else name


def is_binary(data: bytes) -> bool:
    return b"\0" in data[:8000]


def count_lines(data: bytes) -> int:
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def text_counts(old: bytes | None, new: bytes | None) -> tuple[int, int]:
    if old is None or new is None:
        return (count_lines(new), 0) if old is None else (0, count_lines(old))
    a, b = old.decode("utf-8", "replace").splitlines(), new.decode("utf-8", "replace").splitlines()
    added = removed = 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op != "equal":
            removed += i2 - i1
            added += j2 - j1
    return added, removed


def xml_error(path: str, data: bytes | None) -> str | None:
    """Why the new side of an ``.xml`` file does not parse, so a broken file is never reported as a line count."""
    if data is None or not path.lower().endswith(".xml"):
        return None
    try:
        ET.fromstring(data)
    except ET.ParseError as error:
        return f"malformed XML at line {error.position[0]}"
    return None


def analyse(status: str, path: str, old: bytes | None, new: bytes | None, counts: dict[str, tuple[int, int] | None]) -> FileChange:
    if problem := xml_error(path, new):
        return FileChange(status, path, inline=problem)
    old_root, new_root = parse_meta(path, old), parse_meta(path, new)
    if old is None or new is None:
        root = new_root if new is not None else old_root
        if root is not None:
            blocks = Counter(c.tag for c in root if len(c))
            listing = ", ".join(f"{tag} x{n}" for tag, n in blocks.items())
            detail = f" ({listing})" if status == "A" and listing else ""
            return FileChange(status, path, inline=f"{root.tag} {component_name(path)}{detail}")
    elif old_root is not None and new_root is not None:
        lines = diff_roots(old_root, new_root)
        if lines:
            return FileChange(status, path, kind="meta", lines=lines)
        return FileChange(status, path, kind="format", inline="formatting only")
    if any(d is not None and is_binary(d) for d in (old, new)):
        return FileChange(status, path, inline="binary")
    added, removed = counts[path] if counts.get(path) is not None else text_counts(old, new)
    shown = {"A": f"+{added}", "D": f"-{removed}"}.get(status, f"+{added} -{removed}")
    return FileChange(status, path, inline=shown)


def git(cwd: Path, *args: str, stdin: bytes | None = None) -> bytes:
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, input=stdin, capture_output=True)
    except FileNotFoundError as error:
        raise SynError("git is not installed or not on PATH") from error
    if proc.returncode != 0:
        raise SynError(proc.stderr.decode("utf-8", "replace").strip() or "git failed")
    return proc.stdout


def decode(raw: bytes) -> str:
    return raw.decode("utf-8", "replace")


def is_revision(cwd: Path, item: str) -> bool:
    if item.startswith("-"):
        return False
    try:
        proc = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{item}^{{commit}}"], cwd=cwd, capture_output=True)
    except FileNotFoundError as error:
        raise SynError("git is not installed or not on PATH") from error
    return proc.returncode == 0


def split_items(cwd: Path, items: list[str]) -> tuple[list[str], list[str]]:
    """Leading items that name commits are revisions (at most two); the rest are paths.

    A path must exist on disk or in one of the revisions: otherwise a mistyped
    branch name would silently become a path that matches nothing and the
    answer would be "no changes".
    """
    rest, revs = list(items), []
    while rest and len(revs) < 2 and is_revision(cwd, rest[0]):
        revs.append(rest.pop(0))
    for item in rest:
        if any(char in item for char in "*?[:") or (cwd / item).exists():
            continue
        known = (
            subprocess.run(["git", "cat-file", "-e", f"{rev}:./{item}"], cwd=cwd, capture_output=True).returncode == 0
            for rev in revs or ["HEAD"]
        )
        if not any(known):
            raise SynError(f"{item} is neither a revision nor a path")
    return revs, rest


def read_blobs(cwd: Path, specs: list[str]) -> list[bytes | None]:
    if not specs:
        return []
    out = git(cwd, "cat-file", "--batch", stdin=("\n".join(specs) + "\n").encode())
    blobs: list[bytes | None] = []
    pos = 0
    for _ in specs:
        end = out.index(b"\n", pos)
        header, pos = out[pos:end], end + 1
        if header.endswith(b" missing"):
            blobs.append(None)
            continue
        size = int(header.rsplit(b" ", 1)[1])
        blobs.append(out[pos : pos + size])
        pos += size + 1
    return blobs


def collect_git(items: list[str]) -> list[FileChange]:
    """Changes in the whole repository, or under the given paths, wherever in the repository syn runs.

    Git reports paths from the repository root; they are shown relative to
    the current folder, so every printed path can be passed back to syn.
    """
    cwd = Path.cwd()
    try:
        git(cwd, "rev-parse", "--is-inside-work-tree")
    except SynError as error:
        if "not a git repository" in str(error):
            raise SynError("not a git repository; use `syn diff --files A B` to compare two files") from error
        raise
    revs, paths = split_items(cwd, items)
    working_tree = len(revs) < 2
    revs = revs or ["HEAD"]
    pathspec = ["--", *(paths or [":/"])]
    base = ["diff", "--no-ext-diff", "--no-renames", *revs]
    tokens = decode(git(cwd, *base, "--name-status", "-z", *pathspec)).split("\0")
    entries = [(tokens[i][:1].replace("T", "M").replace("U", "M"), tokens[i + 1]) for i in range(0, len(tokens) - 1, 2)]
    counts: dict[str, tuple[int, int] | None] = {}
    for record in decode(git(cwd, *base, "--numstat", "-z", *pathspec)).split("\0"):
        parts = record.split("\t", 2)
        if len(parts) == 3:
            counts[parts[2]] = None if parts[0] == "-" else (int(parts[0]), int(parts[1]))
    untracked: list[str] = []
    if working_tree:
        listed = decode(git(cwd, "ls-files", "--others", "--exclude-standard", "--full-name", "-z", *pathspec)).split("\0")
        untracked = [p for p in listed if p]
        entries += [("A", p) for p in untracked]
    prefix = decode(git(cwd, "rev-parse", "--show-prefix")).strip()
    top = cwd / decode(git(cwd, "rev-parse", "--show-cdup")).strip()
    tracked = [(s, p) for s, p in entries if p not in untracked]
    olds = dict(zip((p for s, p in tracked), read_blobs(cwd, [f"{revs[0]}:{p}" for s, p in tracked])))
    if working_tree:
        news = {p: (top / p).read_bytes() if (top / p).is_file() else None for _, p in entries}
    else:
        news = dict(zip((p for _, p in entries), read_blobs(cwd, [f"{revs[1]}:{p}" for _, p in entries])))
    results = []
    for status, path in sorted(entries, key=lambda e: e[1]):
        change = analyse(status, path, olds.get(path), news[path], counts)
        change.path = relative_path(path, prefix)
        results.append(change)
    return results


def collect_files(left: str, right: str) -> list[FileChange]:
    for name in (left, right):
        if not Path(name).is_file():
            raise SynError(f"no such file: {name}")
    old, new = Path(left).read_bytes(), Path(right).read_bytes()
    if old == new:
        return []
    return [analyse("M", f"{left} -> {right}", old, new, {})]


def file_list(paths: list[str]) -> str:
    if len(paths) == 1:
        return paths[0]
    names = [short_name(p) for p in paths]
    if len(set(names)) != len(names):
        names = paths
    listed = ", ".join(names[:LISTED_FILES])
    more = f", +{len(names) - LISTED_FILES} more" if len(names) > LISTED_FILES else ""
    return f"{len(paths)} files: {listed}{more}"


def format_output(results: list[FileChange], stat: bool) -> list[str]:
    lines: list[str] = []
    if stat:
        for r in results:
            lines.append(f"{r.status} {r.path}  {r.inline or f'{len(r.lines)} changes'}")
    else:
        meta = [r for r in results if r.kind == "meta"]
        owners: dict[str, list[int]] = {}
        for index, r in enumerate(meta):
            for line in dict.fromkeys(r.lines):
                owners.setdefault(line, []).append(index)
        groups: dict[tuple[int, ...], list[str]] = {}
        for line, indexes in owners.items():
            groups.setdefault(tuple(indexes), []).append(line)
        for indexes, group_lines in groups.items():
            lines.append(f"M {file_list([meta[i].path for i in indexes])}")
            lines += [f"  {line}" for line in group_lines]
        lines += [f"{r.status} {r.path}  {r.inline}" for r in results if r.kind == "inline"]
        formatting = [r.path for r in results if r.kind == "format"]
        if len(formatting) == 1:
            lines.append(f"M {formatting[0]}  formatting only")
        elif formatting:
            lines.append(f"M formatting only, {file_list(formatting)}")
    if not results:
        return ["total: no changes"]
    counts = Counter(r.status for r in results)
    kinds = ", ".join(f"{counts[s]} {name}" for s, name in STATUS_NAMES.items() if counts[s])
    changes = sum(len(r.lines) for r in results)
    extra = f", {changes} changes" if changes else ""
    lines.append(f"total: {len(results)} files ({kinds}){extra}")
    return lines


def run(args: argparse.Namespace) -> int:
    if args.files:
        results = collect_files(*args.files)
    else:
        results = collect_git(args.items)
    print("\n".join(format_output(results, args.stat)))
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "diff",
        help="show what changed in the meaning of metadata files",
        description=(
            "Compare a git revision (default HEAD) with the working tree, or two revisions, or two files "
            "(--files A B). Leading arguments that name commits are revisions; paths may follow after `--`. "
            "Without paths the whole repository is compared, from any folder; paths print relative to the current folder."
        ),
    )
    parser.add_argument("items", nargs="*", metavar="REV_OR_PATH", help="up to two revisions, then paths")
    parser.add_argument("--files", nargs=2, metavar=("A", "B"), help="compare two files on disk, no git")
    parser.add_argument("--stat", action="store_true", help="one line per file")
    parser.set_defaults(run=run)
