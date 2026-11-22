"""The map of a huge Salesforce repo: ``syn map``, ``syn find`` and ``syn index``.

The index is one SQLite file at ``<repo root>/.syn/index.db``. Every file gets a row with its
component (metadata type and API name, derived from the SFDX layout) and the identifier-like
tokens it mentions, lowered, with their line numbers. Each query first refreshes the index with a
cheap stat walk, so it is never stale: only files whose size or mtime changed are re-read.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sqlite3
import time
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from syn.cli import SynError

SCHEMA_VERSION = 2
SKIP_DIRS = frozenset({".git", "node_modules", ".sfdx", ".sf", ".syn"})
BINARY_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".bmp", ".webp", ".svg", ".zip", ".jar", ".gz", ".tar",
    ".7z", ".pdf", ".woff", ".woff2", ".ttf", ".eot", ".otf", ".mp3", ".mp4", ".mov", ".avi", ".exe",
    ".dll", ".so", ".dylib", ".class", ".pyc", ".bin", ".xlsx", ".xls", ".docx", ".doc", ".pptx",
    ".resource", ".snapshot",
)
METADATA_SUFFIXES = (
    ".xml", ".cls", ".trigger", ".page", ".component", ".cmp", ".app", ".evt", ".intf", ".design",
    ".auradoc", ".json", ".soql", ".apex",
)
METADATA_CAP = 16 * 1024 * 1024
OTHER_CAP = 1024 * 1024
CHUNK_ROWS = 300_000

CODE_TYPES = frozenset({
    "ApexClass", "ApexTrigger", "LightningComponentBundle", "AuraDefinitionBundle", "Flow", "ApexPage",
    "ApexComponent",
})
SUMMARY_TYPES = frozenset({"Profile", "PermissionSet", "PermissionSetGroup", "Layout"})
TYPE_ALIASES = {
    "lwc": "lightningcomponentbundle", "aura": "auradefinitionbundle", "apex": "apexclass",
    "class": "apexclass", "trigger": "apextrigger", "page": "apexpage", "field": "customfield",
    "object": "customobject", "profile": "profile", "permset": "permissionset",
}

FOLDER_TYPES = {
    "classes": "ApexClass", "triggers": "ApexTrigger", "lwc": "LightningComponentBundle",
    "aura": "AuraDefinitionBundle", "flows": "Flow", "profiles": "Profile",
    "permissionsets": "PermissionSet", "permissionsetgroups": "PermissionSetGroup",
    "layouts": "Layout", "pages": "ApexPage", "components": "ApexComponent", "labels": "CustomLabels",
    "flexipages": "FlexiPage", "staticresources": "StaticResource", "tabs": "CustomTab",
    "applications": "CustomApplication", "email": "EmailTemplate", "reports": "Report",
    "dashboards": "Dashboard", "documents": "Document", "quickActions": "QuickAction",
    "customMetadata": "CustomMetadata", "globalValueSets": "GlobalValueSet",
    "standardValueSets": "StandardValueSet", "workflows": "Workflow", "sharingRules": "SharingRules",
    "roles": "Role", "groups": "Group", "queues": "Queue", "assignmentRules": "AssignmentRules",
    "remoteSiteSettings": "RemoteSiteSetting", "customPermissions": "CustomPermission",
    "connectedApps": "ConnectedApp", "namedCredentials": "NamedCredential", "settings": "Settings",
    "objectTranslations": "CustomObjectTranslation", "translations": "Translations",
    "duplicateRules": "DuplicateRule", "matchingRules": "MatchingRules",
    "approvalProcesses": "ApprovalProcess", "cspTrustedSites": "CspTrustedSite",
    "externalServiceRegistrations": "ExternalServiceRegistration", "sites": "CustomSite",
    "networks": "Network", "contentassets": "ContentAsset", "messageChannels": "LightningMessageChannel",
}
BUNDLE_FOLDERS = frozenset({"lwc", "aura", "staticresources"})
CHILD_TYPES = {
    "fields": "CustomField", "validationRules": "ValidationRule", "recordTypes": "RecordType",
    "listViews": "ListView", "compactLayouts": "CompactLayout", "webLinks": "WebLink",
    "businessProcesses": "BusinessProcess", "fieldSets": "FieldSet", "indexes": "Index",
    "sharingReasons": "SharingReason",
}
SUFFIX_TYPES = {
    "cls": "ApexClass", "trigger": "ApexTrigger", "flow": "Flow", "profile": "Profile",
    "permissionset": "PermissionSet", "layout": "Layout", "page": "ApexPage",
    "component": "ApexComponent", "flexipage": "FlexiPage", "app": "CustomApplication",
    "tab": "CustomTab", "object": "CustomObject", "field": "CustomField", "labels": "CustomLabels",
    "resource": "StaticResource", "md": "CustomMetadata", "role": "Role", "group": "Group",
    "queue": "Queue",
}

STOP = frozenset("""
true false null void public private protected global static final class interface enum extends
implements new return else for while try catch finally throw this super virtual abstract override
with without sharing instanceof break continue switch when and the not any all
fullname label description name value type version encoding xml xmlns http https www w3 org com
salesforce soap sforce metadata default fieldpermissions editable readable allowcreate allowdelete
allowedit allowread modifyallrecords viewallrecords visible custom standard object field
apiversion
""".split())

WORD = re.compile(r"[a-z0-9_]+(?:\.[a-z0-9_]+)*")
XML_TAG = re.compile(r"</?[a-z_][\w:.\-]*")
LWC_TAG = re.compile(r"<c-([a-z0-9]+(?:-[a-z0-9]+)*)")
"""A Lightning web component used in markup: ``<c-invoice-summary>`` is the bundle ``invoiceSummary``."""
QUERY_OK = re.compile(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*$")


@dataclass(slots=True)
class RefreshStats:
    scanned: int = 0
    added: int = 0
    changed: int = 0
    removed: int = 0


def find_root(start: Path) -> Path:
    """Nearest ancestor with ``sfdx-project.json``, else with ``.git``.

    Without either marker there is no project to index: falling back to the
    current folder would write ``.syn`` there and index whatever tree it is,
    a whole home folder included.
    """
    start = start.resolve()
    chain = [start, *start.parents]
    for marker in ("sfdx-project.json", ".git"):
        for folder in chain:
            if (folder / marker).exists():
                return folder
    raise SynError(f"no sfdx-project.json or .git in {start} or above; run syn inside a project")


def shown_path(root: Path, rel: str) -> str:
    """A root-relative path as seen from the current folder, so it can be passed back to syn as is."""
    try:
        folder = Path.cwd().resolve().relative_to(root).as_posix()
    except ValueError:
        return rel
    return relative_path(rel, "" if folder == "." else folder)


def relative_path(path: str, folder: str) -> str:
    """``path`` relative to ``folder``, both relative to the same root and written with ``/``."""
    here = [part for part in folder.split("/") if part]
    parts = path.split("/")
    common = 0
    while common < len(here) and common < len(parts) - 1 and here[common] == parts[common]:
        common += 1
    return "/".join([".."] * (len(here) - common) + parts[common:])


def read_project(root: Path) -> tuple[list[str], str | None]:
    """Package directory paths and the source API version from ``sfdx-project.json``, if any."""
    try:
        data = json.loads((root / "sfdx-project.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return [], None
    if not isinstance(data, dict):
        return [], None
    packages = [
        entry["path"] for entry in data.get("packageDirectories") or []
        if isinstance(entry, dict) and isinstance(entry.get("path"), str)
    ]
    version = data.get("sourceApiVersion")
    return packages, version if isinstance(version, str) else None


def strip_suffix(name: str) -> str:
    """Drop ``-meta.xml`` and then the last extension of the final path segment."""
    name = name.removesuffix("-meta.xml")
    head, _, leaf = name.rpartition("/")
    if "." in leaf:
        leaf = leaf.rsplit(".", 1)[0]
    return f"{head}/{leaf}" if head else leaf


def suffix_type(filename: str) -> str | None:
    match = re.match(r"^.+\.([A-Za-z]+)-meta\.xml$", filename)
    if not match:
        return None
    suffix = match.group(1)
    return SUFFIX_TYPES.get(suffix.lower(), suffix.capitalize())


def classify(rel: str) -> tuple[str, str]:
    """Metadata type and API name of a repo-relative path, from the SFDX source layout."""
    parts = rel.split("/")
    for index, part in enumerate(parts[:-1]):
        rest = parts[index + 1:]
        if part == "objects" and len(rest) >= 2:
            return classify_object(rest)
        kind = FOLDER_TYPES.get(part)
        if kind is None:
            continue
        if part in BUNDLE_FOLDERS:
            if len(rest) >= 2:
                return kind, rest[0]
            if part != "staticresources":
                return "other", strip_suffix(rest[0])
        return kind, strip_suffix("/".join(rest))
    kind = suffix_type(parts[-1])
    return (kind, strip_suffix(parts[-1])) if kind else ("other", strip_suffix(parts[-1]))


def classify_object(rest: list[str]) -> tuple[str, str]:
    owner = rest[0]
    if len(rest) == 2:
        if rest[1].endswith((".object-meta.xml", ".object")):
            return "CustomObject", owner
        return "other", f"{owner}/{strip_suffix(rest[1])}"
    child = strip_suffix("/".join(rest[2:]))
    kind = CHILD_TYPES.get(rest[1]) or suffix_type(rest[-1]) or "other"
    return kind, f"{owner}.{child}"


def keep(token: str) -> bool:
    return len(token) >= 3 and not all(part in STOP or part.isdigit() for part in token.split("."))


def expand(raw: str) -> tuple[str, ...]:
    """The dotted token, each adjacent dotted pair and each single part, filtered of noise."""
    parts = raw.split(".")
    found = {raw}
    found.update(".".join(parts[i:i + 2]) for i in range(len(parts) - 1))
    found.update(parts)
    return tuple(token for token in found if keep(token))


def extract(path: str, rel: str, size: int, cache: dict[str, tuple[str, ...]]) -> tuple[bool, set[tuple[str, int]]]:
    """Whether the file is an Apex test class and its set of (token, line) pairs; empty when skipped."""
    lowered = rel.lower()
    if lowered.endswith(BINARY_EXTENSIONS):
        return False, set()
    if size > (METADATA_CAP if lowered.endswith(METADATA_SUFFIXES) else OTHER_CAP):
        return False, set()
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return False, set()
    if b"\0" in data[:4096]:
        return False, set()
    text = data.decode("utf-8", errors="replace").lower()
    is_test = lowered.endswith(".cls") and "@istest" in text
    if lowered.endswith(".xml"):
        text = XML_TAG.sub("<", text)
    rows: set[tuple[str, int]] = set()
    for number, line in enumerate(text.split("\n"), 1):
        for raw in WORD.findall(line):
            tokens = cache.get(raw)
            if tokens is None:
                tokens = cache[raw] = expand(raw)
            for token in tokens:
                rows.add((token, number))
        for tag in LWC_TAG.findall(line):
            rows.add((tag.replace("-", ""), number))
    return is_test, rows


def scan(root: Path, packages: list[str]) -> dict[str, tuple[int, int]]:
    """Relative path to (size, mtime_ns) of every file under the package directories."""
    base = str(root).rstrip(os.sep)
    cut = len(base) + 1
    bases = [str(root / package) for package in packages if (root / package).is_dir()] or [base]
    found: dict[str, tuple[int, int]] = {}
    stack = list(bases)
    while stack:
        folder = stack.pop()
        try:
            iterator = os.scandir(folder)
        except OSError:
            continue
        with iterator:
            for entry in iterator:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name not in SKIP_DIRS:
                            stack.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        stat = entry.stat(follow_symlinks=False)
                        found[entry.path[cut:].replace(os.sep, "/")] = (stat.st_size, stat.st_mtime_ns)
                except OSError:
                    continue
    return found


SCHEMA = """
CREATE TABLE files(
    id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, size INTEGER NOT NULL, mtime INTEGER NOT NULL,
    type TEXT NOT NULL, name TEXT NOT NULL, name_lc TEXT NOT NULL, is_test INTEGER NOT NULL
);
CREATE INDEX files_name ON files(name_lc);
CREATE INDEX files_type ON files(type);
CREATE TABLE tokens(
    file_id INTEGER NOT NULL, tok TEXT NOT NULL, line INTEGER NOT NULL, PRIMARY KEY(file_id, tok, line)
) WITHOUT ROWID;
"""


def open_index(root: Path, rebuild: bool = False) -> sqlite3.Connection:
    """Open the index, creating or resetting its tables under the write lock.

    A rebuild empties the tables instead of deleting the file, which another
    syn process may have open (Windows refuses to delete an open file).
    """
    folder = root / ".syn"
    try:
        folder.mkdir(exist_ok=True)
        ignore = folder / ".gitignore"
        if not ignore.exists():
            ignore.write_text("*\n", encoding="utf-8")
    except OSError as error:
        raise SynError(f"cannot create {folder}: {error}") from error
    conn = sqlite3.connect(folder / "index.db", isolation_level=None, timeout=60)
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA journal_mode=MEMORY")
    conn.execute("BEGIN IMMEDIATE")
    try:
        if rebuild or conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            for statement in ["DROP TABLE IF EXISTS tokens", "DROP TABLE IF EXISTS files", *SCHEMA.split(";")]:
                if statement.strip():
                    conn.execute(statement)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return conn


def refresh(conn: sqlite3.Connection, root: Path) -> RefreshStats:
    """Bring the index in line with the disk, re-reading only new or changed files.

    The write lock is taken before the index is read: two syn processes
    refreshing at once (agents run commands in parallel) would otherwise
    both plan the same new rows and the second would fail on their ids.
    """
    packages, _ = read_project(root)
    current = scan(root, packages)
    conn.execute("BEGIN IMMEDIATE")
    known = {
        path: (file_id, size, mtime)
        for file_id, path, size, mtime in conn.execute("SELECT id, path, size, mtime FROM files")
    }
    todo = [path for path, stat in current.items() if path not in known or known[path][1:] != stat]
    gone = [known[path][0] for path in known if path not in current]
    stale = gone + [known[path][0] for path in todo if path in known]
    stats = RefreshStats(
        scanned=len(current), added=sum(path not in known for path in todo),
        changed=sum(path in known for path in todo), removed=len(gone),
    )
    if not todo and not stale:
        conn.execute("COMMIT")
        return stats
    base = str(root).rstrip(os.sep)
    cache: dict[str, tuple[str, ...]] = {}
    next_id = (conn.execute("SELECT COALESCE(MAX(id), 0) FROM files").fetchone()[0]) + 1
    file_rows: list[tuple] = []
    token_rows: list[tuple[int, str, int]] = []

    def flush() -> None:
        conn.executemany("INSERT INTO files VALUES (?,?,?,?,?,?,?,?)", file_rows)
        conn.executemany("INSERT INTO tokens VALUES (?,?,?)", token_rows)
        file_rows.clear()
        token_rows.clear()

    try:
        conn.executemany("DELETE FROM tokens WHERE file_id=?", [(i,) for i in stale])
        conn.executemany("DELETE FROM files WHERE id=?", [(i,) for i in stale])
        for path in todo:
            size, mtime = current[path]
            kind, name = classify(path)
            is_test, rows = extract(f"{base}{os.sep}{path}".replace("/", os.sep), path, size, cache)
            file_rows.append((next_id, path, size, mtime, kind, name, name.lower(), int(is_test)))
            token_rows.extend((next_id, token, line) for token, line in rows)
            next_id += 1
            if len(token_rows) >= CHUNK_ROWS:
                flush()
        flush()
        conn.execute("CREATE INDEX IF NOT EXISTS tokens_tok ON tokens(tok)")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return stats


def like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def compact_lines(lines: list[int], cap: int = 12) -> str:
    shown = ",".join(map(str, lines[:cap]))
    return shown if len(lines) <= cap else f"{shown},+{len(lines) - cap}"


def definitions(conn: sqlite3.Connection, query: str) -> list[tuple[str, str, str]]:
    """Components named exactly like the query, or whose dotted name ends with it."""
    rows = conn.execute(
        "SELECT type, name, path FROM files WHERE name_lc = ? OR name_lc LIKE ? ESCAPE '\\'",
        (query, "%." + like_escape(query)),
    ).fetchall()
    best: dict[tuple[str, str], str] = {}
    for kind, name, path in rows:
        current = best.get((kind, name))
        rank = (path.endswith("-meta.xml"), len(path))
        if current is None or rank < (current.endswith("-meta.xml"), len(current)):
            best[(kind, name)] = path
    ordered = sorted(best.items(), key=lambda item: (item[0][1].lower() != query, item[0][1], item[0][0]))
    return [
        (kind, name, path.rsplit("/", 1)[0] if kind in ("LightningComponentBundle", "AuraDefinitionBundle") else path)
        for (kind, name), path in ordered
    ]


def suggestions(conn: sqlite3.Connection, query: str) -> list[str]:
    escaped = like_escape(query)
    found: list[str] = []
    for pattern in (escaped + "%", "%" + escaped + "%", "%." + escaped):
        rows = conn.execute(
            "SELECT DISTINCT name FROM files WHERE name_lc LIKE ? ESCAPE '\\' ORDER BY length(name), name LIMIT 5",
            (pattern,),
        ).fetchall()
        found.extend(name for (name,) in rows if name not in found)
    if len(found) < 5:
        rows = conn.execute(
            "SELECT DISTINCT tok FROM tokens WHERE tok LIKE ? ESCAPE '\\' ORDER BY length(tok), tok LIMIT 5",
            (escaped + "%",),
        ).fetchall()
        seen = {name.lower() for name in found}
        found.extend(token for (token,) in rows if token not in seen)
    return found[:5]


def resolve_types(values: list[str] | None) -> set[str]:
    """``--type`` values, comma separated like ``rows --cols``, with their short aliases resolved."""
    return {TYPE_ALIASES.get(value.lower(), value.lower()) for item in values or [] for value in item.split(",") if value}


def line_text(root: Path, rel: str, numbers: list[int], cap: int = 8) -> list[str]:
    try:
        lines = (root / rel).read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        return []
    out = []
    for number in numbers[:cap]:
        if 0 < number <= len(lines):
            out.append(f"    {number}: {lines[number - 1].strip()[:160]}")
    if len(numbers) > cap:
        out.append(f"    +{len(numbers) - cap} more lines")
    return out


def member_mentions(conn: sqlite3.Connection, query: str) -> list[tuple[str, str, str, int, int]]:
    """Mentions of ``Owner.Member`` written without the owner, in files that are about the owner.

    Code rarely spells a field as ``Invoice__c.Amount__c``: Apex writes
    ``inv.Amount__c``, a flow ``$Record.Amount__c``, a layout or validation
    rule just ``Amount__c``. So for a two-part query the bare member also
    counts in a file that belongs to the owner (its name starts with
    ``Owner.`` or ``Owner-``), which is certain, or that mentions the owner
    somewhere, which is a guess (the last column is 0): a class that touches
    two objects may use another object's field of the same name. Access
    files (profiles, permission sets) always spell the full name, and their
    bare tokens come from other owners' fields, so they are left out.
    """
    owner, _, member = query.partition(".")
    if not member or "." in member or not keep(owner) or not keep(member):
        return []
    return conn.execute(
        "SELECT f.path, f.type, f.name, t.line, "
        "(f.name_lc = ? OR f.name_lc LIKE ? ESCAPE '\\' OR f.name_lc LIKE ? ESCAPE '\\') AS certain "
        "FROM tokens t JOIN files f ON f.id = t.file_id "
        "WHERE t.tok = ? AND f.type NOT IN ('Profile', 'PermissionSet', 'PermissionSetGroup', 'MutingPermissionSet') "
        "AND (certain OR EXISTS (SELECT 1 FROM tokens o WHERE o.file_id = f.id AND o.tok = ?))",
        (owner, like_escape(owner) + ".%", like_escape(owner) + "-%", member, owner),
    ).fetchall()


def run_find(args: argparse.Namespace) -> int:
    query = args.name.strip().lower()
    if not QUERY_OK.match(query):
        raise SynError(f"expected an identifier such as Account.Region__c or InvoiceService, got {args.name!r}")
    wanted = resolve_types(args.type)
    root = find_root(Path.cwd())
    with closing(open_index(root)) as conn:
        refresh(conn, root)
        defined = definitions(conn, query)
        owners = {(kind, name) for kind, name, _ in defined}
        groups: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
        names: dict[str, str] = {}
        rows = conn.execute(
            "SELECT f.path, f.type, f.name, t.line, 1 FROM tokens t JOIN files f ON f.id = t.file_id WHERE t.tok = ?",
            (query,),
        ).fetchall()
        certain: set[str] = set()
        for path, kind, name, line, sure in sorted(set(rows + member_mentions(conn, query)), key=lambda row: (row[1], row[0], row[3])):
            if (kind, name) in owners:
                continue
            if wanted and kind.lower() not in wanted:
                continue
            if args.glob and not (fnmatch.fnmatchcase(path, args.glob) or fnmatch.fnmatchcase(shown_path(root, path), args.glob)):
                continue
            if line not in groups[kind][path]:
                groups[kind][path].append(line)
            names[path] = name
            if sure:
                certain.add(path)
        misses = suggestions(conn, query) if not groups and not defined else []
    shown = 0
    for kind, name, path in defined[:5]:
        print(f"defined: {kind} {name}  {shown_path(root, path)}")
        shown += 1
    if len(defined) > shown:
        print(f"defined: +{len(defined) - shown} more components")
    if not groups:
        print(f"no mentions of {args.name}" + (" with these filters" if wanted or args.glob else ""))
        if misses:
            print("close names: " + ", ".join(misses))
        return 0
    order = sorted(groups, key=lambda k: (k in SUMMARY_TYPES, k not in CODE_TYPES, -len(groups[k]), k))
    guessed = False
    for kind in order:
        files = groups[kind]
        hits = sum(len(lines) for lines in files.values())
        head = f"{kind}: {len(files)} files, {hits} mentions"
        if kind in SUMMARY_TYPES:
            listed = list(files)[:3]
            guessed = guessed or any(path not in certain for path in listed)
            examples = ", ".join(names[path] + ("" if path in certain else " ?") for path in listed)
            print(f"{head}  e.g. {examples}")
            continue
        print(head)
        for path in list(files)[:args.limit]:
            mark = "" if path in certain else " ?"
            guessed = guessed or bool(mark)
            print(f"  {shown_path(root, path)}:{compact_lines(files[path])}{mark}")
            if args.lines and kind in CODE_TYPES:
                for text in line_text(root, path, files[path]):
                    print(text)
        if len(files) > args.limit:
            print(f"  +{len(files) - args.limit} more")
    if guessed:
        owner, _, member = args.name.strip().partition(".")
        print(f"? = bare {member} in a file that also mentions {owner}: may be another object's field")
    return 0


def run_index(args: argparse.Namespace) -> int:
    root = find_root(Path.cwd())
    started = time.perf_counter()
    with closing(open_index(root, rebuild=True)) as conn:
        stats = refresh(conn, root)
        components = conn.execute("SELECT COUNT(DISTINCT type || ':' || name) FROM files").fetchone()[0]
        tokens = conn.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]
    elapsed = time.perf_counter() - started
    print(f"indexed {stats.scanned} files, {components} components, {tokens} token rows in {elapsed:.2f}s")
    print(f"index: {(root / '.syn' / 'index.db').relative_to(root).as_posix()}")
    return 0


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def run_map(args: argparse.Namespace) -> int:
    start = Path(args.path).resolve() if args.path else Path.cwd()
    if start.is_file():
        start = start.parent
    if not start.exists():
        raise SynError(f"path not found: {args.path}")
    root = find_root(start)
    packages, version = read_project(root)
    with closing(open_index(root)) as conn:
        refresh(conn, root)
        total = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        kinds = conn.execute(
            "SELECT type, COUNT(DISTINCT name) AS c FROM files GROUP BY type ORDER BY c DESC, type"
        ).fetchall()
        largest = conn.execute("SELECT path, size FROM files ORDER BY size DESC, path LIMIT 10").fetchall()
        objects = conn.execute(
            "SELECT substr(name, 1, instr(name, '.') - 1) AS owner, COUNT(*) AS c FROM files "
            "WHERE type = 'CustomField' AND instr(name, '.') > 0 GROUP BY owner ORDER BY c DESC, owner LIMIT 5"
        ).fetchall()
        classes, tests = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(is_test), 0) FROM files WHERE type = 'ApexClass' AND path LIKE '%.cls'"
        ).fetchone()
        triggers = conn.execute(
            "SELECT COUNT(*) FROM files WHERE type = 'ApexTrigger' AND path LIKE '%.trigger'"
        ).fetchone()[0]
    print(f"repo {root.name}: {total} files" + (f", source API {version}" if version else ""))
    print("packages: " + (", ".join(packages) if packages else "(no sfdx-project.json, whole repo)"))
    print(f"apex: {classes} classes ({tests} tests), {triggers} triggers")
    if objects:
        print("objects by fields: " + ", ".join(f"{owner} {count}" for owner, count in objects))
    print(f"components by type ({sum(count for _, count in kinds)} in {len(kinds)} types):")
    for index in range(0, min(len(kinds), 18), 3):
        print("  " + ", ".join(f"{kind} {count}" for kind, count in kinds[index:index + 3]))
    if len(kinds) > 18:
        print(f"  +{len(kinds) - 18} more types")
    print("largest files:")
    for path, size in largest:
        print(f"  {human_size(size):>9}  {shown_path(root, path)}")
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    finder = subparsers.add_parser("find", help="where a name is defined and who mentions it, grouped by type")
    finder.add_argument(
        "name",
        help="Account.Region__c, Region__c, InvoiceService, ...; Object.Field also counts a bare Field "
        "in files of Object and, marked ?, in files that mention Object (inv.Region__c, $Record.Region__c)",
    )
    finder.add_argument("--type", action="append", metavar="T,T", help="only these metadata types (lwc, apex, flow, ApexTrigger, ...)")
    finder.add_argument("--in", dest="glob", metavar="GLOB", help="only paths matching this glob (* crosses folders)")
    finder.add_argument("--limit", type=int, default=15, help="files listed per type (default 15)")
    finder.add_argument("--lines", action="store_true", help="show the mentioned lines of code files")
    finder.set_defaults(run=run_find)

    mapper = subparsers.add_parser("map", help="one-screen overview of the repo")
    mapper.add_argument("path", nargs="?", help="a path inside the repo (default: current folder)")
    mapper.set_defaults(run=run_map)

    indexer = subparsers.add_parser("index", help="rebuild the index from scratch")
    indexer.set_defaults(run=run_index)
