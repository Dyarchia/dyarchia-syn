"""Condensers for what the org sends back: ``syn log``, ``syn tests``, ``syn errors`` and ``syn org``.

Each command reads a file path or ``-`` for stdin and prints the few lines that matter. The JSON
parsers are tolerant on purpose: a missing key is simply not printed, and only input of the wrong
overall shape raises a one-line ``SynError``.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from syn.cli import SynError

EVENT = re.compile(r"^\d{1,2}:\d\d:\d\d(?:\.\d+)? \((\d+)\)\|([A-Za-z0-9_]+)(?:\|(.*))?$")
LIMIT_LINE = re.compile(r"^\s*(.+?):\s*(\d+) out of (\d+)")
ALWAYS_LIMITS = ("soql queries", "dml statements", "cpu time")
LOOP_THRESHOLD = 20
DIGITS = re.compile(r"\d+")


def read_source(source: str) -> str:
    if source == "-":
        buffer = getattr(sys.stdin, "buffer", None)
        text = buffer.read().decode("utf-8", errors="replace") if buffer else sys.stdin.read()
    else:
        try:
            text = Path(source).read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError as error:
            raise SynError(f"no such file: {source}") from error
        except OSError as error:
            raise SynError(f"cannot read {source}: {error.strerror or error}") from error
    return text.lstrip("\ufeff")


def load_json(text: str, expected: str) -> Any:
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except ValueError:
                pass
    raise SynError(f"expected {expected}, but the input is not JSON")


def unwrap(data: Any, expected: str, keys: tuple[str, ...]) -> dict[str, Any]:
    """The ``result`` payload of an ``sf --json`` envelope, or the envelope itself if it already fits."""
    if not isinstance(data, dict):
        raise SynError(f"expected {expected} (a JSON object), got a JSON {type(data).__name__}")
    for candidate in (data.get("result"), data.get("data"), data):
        if isinstance(candidate, dict) and any(key in candidate for key in keys):
            return candidate
    message = data.get("message")
    if isinstance(message, str) and message:
        raise SynError(f"sf reported an error: {message.strip().splitlines()[0]}")
    raise SynError(f"expected {expected}: a JSON object with {' or '.join(keys)}")


def as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def as_int(value: Any) -> int | None:
    try:
        return int(float(str(value).strip().rstrip("%")))
    except (TypeError, ValueError):
        return None


def text_of(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[:width - 3] + "..."


@dataclass(slots=True)
class LogState:
    fatal: tuple[str, list[str]] | None = None
    exceptions: list[list] = field(default_factory=list)
    debugs: list[list] = field(default_factory=list)
    """Runs of USER_DEBUG entries from one line that differ at most in their numbers (ids, counters).

    Each run is ``[(line, first message), count, shape, varied]``: a debug in a
    loop prints one line with its count instead of one line per record.
    """
    soql_stack: list[str] = field(default_factory=list)
    soql_total: int = 0
    soql: dict[str, list] = field(default_factory=dict)
    dml: dict[tuple[str, str], list[int]] = field(default_factory=dict)
    limits: list[str] = field(default_factory=list)
    last_nanos: int = 0


def strip_line_ref(detail: str) -> tuple[str, str]:
    match = re.match(r"^\[([^\]]*)\]\|?(.*)$", detail, re.S)
    return (match.group(1), match.group(2)) if match else ("", detail)


def handle_event(state: LogState, name: str, detail: str, extra: list[str]) -> None:
    if name == "FATAL_ERROR":
        if state.fatal is None:
            stack = [line.strip() for line in extra if line.strip()][:6]
            state.fatal = (text_of(detail), stack)
    elif name == "EXCEPTION_THROWN":
        line, message = strip_line_ref(detail)
        entry = [f"[{line}] {text_of(message)}" if line else text_of(message), 1]
        if state.exceptions and state.exceptions[-1][0] == entry[0]:
            state.exceptions[-1][1] += 1
        else:
            state.exceptions.append(entry)
    elif name == "USER_DEBUG":
        parts = detail.split("|", 2)
        line = parts[0].strip("[]") if len(parts) == 3 else "?"
        message = parts[2] if len(parts) == 3 else detail
        message = " / ".join([message, *extra]).strip() if extra else message
        key = (line, message)
        shape = (line, DIGITS.sub("#", message))
        last = state.debugs[-1] if state.debugs else None
        if last and last[2] == shape:
            last[1] += 1
            last[3] = last[3] or last[0] != key
        else:
            state.debugs.append([key, 1, shape, False])
    elif name == "SOQL_EXECUTE_BEGIN":
        parts = detail.split("|", 2)
        query = text_of(" ".join([parts[2] if len(parts) == 3 else detail, *extra]))
        line = parts[0].strip("[]")
        state.soql_total += 1
        state.soql_stack.append(query)
        entry = state.soql.setdefault(query, [0, 0, line])
        entry[0] += 1
    elif name == "SOQL_EXECUTE_END":
        rows = re.search(r"Rows:(\d+)", detail)
        if state.soql_stack:
            query = state.soql_stack.pop()
            if rows:
                state.soql[query][1] += int(rows.group(1))
    elif name == "DML_BEGIN":
        operation = re.search(r"Op:([\w ]+?)(?:\||$)", detail)
        kind = re.search(r"Type:([^|]+)", detail)
        rows = re.search(r"Rows:(\d+)", detail)
        entry = state.dml.setdefault(
            (operation.group(1) if operation else "?", kind.group(1).strip() if kind else "?"), [0, 0]
        )
        entry[0] += 1
        entry[1] += int(rows.group(1)) if rows else 0
    elif name == "LIMIT_USAGE_FOR_NS":
        state.limits = list(extra)


def parse_log(text: str) -> LogState:
    """Stream the log: a line that is not a timestamped event continues the previous event."""
    state = LogState()
    current: tuple[str, str] | None = None
    extra: list[str] = []
    for raw in text.split("\n"):
        line = raw.rstrip("\r")
        match = EVENT.match(line)
        if match:
            if current:
                handle_event(state, current[0], current[1], extra)
            current, extra = (match.group(2), match.group(3) or ""), []
            state.last_nanos = max(state.last_nanos, int(match.group(1)))
        elif current:
            extra.append(line)
    if current:
        handle_event(state, current[0], current[1], extra)
    return state


def format_duration(nanos: int) -> str:
    millis = nanos / 1_000_000
    return f"{millis:.0f} ms" if millis < 1000 else f"{millis / 1000:.2f} s"


def condense_log(state: LogState, debug_cap: int) -> list[str]:
    out: list[str] = []
    if state.fatal:
        message, stack = state.fatal
        out.append(f"FATAL: {clip(message, 400)}")
        out.extend(f"  {clip(line, 200)}" for line in stack)
    if state.exceptions:
        out.append(f"EXCEPTION_THROWN ({sum(count for _, count in state.exceptions)}):")
        out.extend(
            f"  {clip(text, 300)}" + (f" (x{count})" if count > 1 else "") for text, count in state.exceptions
        )
    if state.debugs:
        out.append(f"USER_DEBUG ({sum(entry[1] for entry in state.debugs)}):")
        for (line, message), count, _, varied in state.debugs[:debug_cap]:
            repeat = f" (x{count}, numbers vary)" if varied else f" (x{count})" if count > 1 else ""
            out.append(f"  line {line}: {clip(message, 300)}{repeat}")
        if len(state.debugs) > debug_cap:
            out.append(f"  +{len(state.debugs) - debug_cap} more entries")
    if state.soql_total:
        ranked = sorted(state.soql.items(), key=lambda item: (-item[1][0], item[0]))
        out.append(f"SOQL: {state.soql_total} queries, {len(ranked)} distinct")
        for query, (runs, rows, line) in ranked[:10]:
            flag = "  <- likely query in a loop" if runs > LOOP_THRESHOLD else ""
            out.append(f"  {runs}x, {rows} rows, line {line}: {clip(query, 150)}{flag}")
        if len(ranked) > 10:
            out.append(f"  +{len(ranked) - 10} more distinct queries")
    if state.dml:
        out.append(f"DML: {sum(count for count, _ in state.dml.values())} statements")
        for (operation, kind), (count, rows) in sorted(state.dml.items(), key=lambda item: -item[1][0]):
            out.append(f"  {operation} {kind}: {count}x, {rows} rows")
    limits = []
    for line in state.limits:
        match = LIMIT_LINE.match(line)
        if not match:
            continue
        name, used, cap = match.group(1), int(match.group(2)), int(match.group(3))
        percent = used * 100 // cap if cap else 0
        if percent > 50 or any(key in name.lower() for key in ALWAYS_LIMITS):
            limits.append(f"  {name}: {used}/{cap} ({percent}%)")
    if limits:
        out.append("LIMITS (last block, above 50% plus SOQL, DML, CPU):")
        out.extend(limits)
    if state.last_nanos:
        out.append(f"duration: {format_duration(state.last_nanos)}")
    return out


def run_log(args: argparse.Namespace) -> int:
    state = parse_log(read_source(args.file))
    lines = condense_log(state, max(args.debug, 0))
    print("\n".join(lines) if lines else "no recognizable debug log lines")
    return 0


def ranges(numbers: Iterable[int]) -> str:
    """Compress sorted line numbers into ``12-18,40``."""
    ordered = sorted(set(numbers))
    out: list[str] = []
    index = 0
    while index < len(ordered):
        end = index
        while end + 1 < len(ordered) and ordered[end + 1] == ordered[end] + 1:
            end += 1
        out.append(str(ordered[index]) if end == index else f"{ordered[index]}-{ordered[end]}")
        index = end + 1
    return ",".join(out)


def format_failure(name: str, message: str, stack: str, stack_lines: int = 3) -> list[str]:
    text = clip(text_of(message), 300)
    out = [f"{name or '?'}: {text}" if text else name or "?"]
    if isinstance(stack, str):
        out.extend(f"    {clip(line.strip(), 200)}" for line in stack.split("\n") if line.strip())
        out = out[:1 + stack_lines]
    return out


def coverage_entries(result: dict[str, Any]) -> list[tuple[str, float, list[int]]]:
    """(class, percent, uncovered lines) from both shapes the CLI has used for coverage."""
    entries: list[tuple[str, float, list[int]]] = []
    block = result.get("coverage")
    for item in as_list(block.get("coverage") if isinstance(block, dict) else block):
        name = item.get("name")
        total, covered = as_int(item.get("totalLines")), as_int(item.get("totalCovered"))
        if not isinstance(name, str) or not total:
            continue
        percent = as_int(item.get("coveredPercent"))
        percent = percent if percent is not None else (covered or 0) * 100 // total
        lines = item.get("lines")
        missed = [int(n) for n, hits in lines.items() if str(n).isdigit() and hits == 0] if isinstance(lines, dict) else []
        entries.append((name, percent, missed))
    for item in as_list(result.get("codecoverage")):
        name = item.get("name")
        percent = as_int(item.get("percentage"))
        if isinstance(name, str) and percent is not None:
            missed = item.get("uncoveredLines")
            entries.append((name, percent, [n for n in missed if isinstance(n, int)] if isinstance(missed, list) else []))
    return entries


def condense_tests(result: dict[str, Any], minimum: int) -> list[str]:
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    tests = as_list(result.get("tests"))
    failures = [t for t in tests if str(t.get("Outcome", "")).lower() not in ("pass", "skip", "")]
    out: list[str] = []
    head = []
    outcome = summary.get("outcome")
    if isinstance(outcome, str):
        head.append(f"outcome: {outcome}")
    counts = []
    ran = summary.get("testsRan", len(tests) if tests else None)
    for label, value in (
        ("run", ran), ("passed", summary.get("passing")), ("failed", summary.get("failing", len(failures) if tests else None)),
        ("skipped", summary.get("skipped")),
    ):
        if value is not None:
            counts.append(f"{value} {label}")
    if counts:
        head.append(", ".join(counts))
    duration = summary.get("testTotalTime") or summary.get("testExecutionTime")
    if duration:
        head.append(f"time {duration}")
    out.append("  |  ".join(head) if head else "tests")
    cover = [
        f"{label} {summary[key]}" for label, key in (("org-wide", "orgWideCoverage"), ("run", "testRunCoverage"))
        if summary.get(key) not in (None, "")
    ]
    if cover:
        out.append("coverage: " + ", ".join(cover))
    for test in failures:
        klass = test.get("ApexClass") if isinstance(test.get("ApexClass"), dict) else {}
        name = test.get("FullName") or ".".join(p for p in (klass.get("Name"), test.get("MethodName")) if p)
        out.extend(format_failure(name, test.get("Message"), test.get("StackTrace")))
    low = sorted((e for e in coverage_entries(result) if e[1] < minimum), key=lambda e: (e[1], e[0]))
    if low:
        out.append(f"below {minimum}% coverage ({len(low)}):")
        for name, percent, missed in low[:30]:
            out.append(f"  {name}: {percent}%" + (f"  uncovered {ranges(missed)}" if missed else ""))
        if len(low) > 30:
            out.append(f"  +{len(low) - 30} more classes")
    return out


def run_tests(args: argparse.Namespace) -> int:
    expected = "the JSON of `sf apex run test --json`"
    result = unwrap(load_json(read_source(args.file), expected), expected, ("summary", "tests"))
    print("\n".join(condense_tests(result, args.min)))
    return 0


def component_failures(result: dict[str, Any]) -> list[dict[str, Any]]:
    details = result.get("details") if isinstance(result.get("details"), dict) else {}
    failures = as_list(details.get("componentFailures"))
    if failures:
        return [
            {
                "type": f.get("componentType"), "name": f.get("fullName"), "file": f.get("fileName"),
                "line": f.get("lineNumber"), "problem": f.get("problem"),
            }
            for f in failures
        ]
    return [
        {"type": f.get("type"), "name": f.get("fullName"), "file": f.get("filePath"), "line": f.get("lineNumber"),
         "problem": f.get("error") or f.get("problem")}
        for f in as_list(result.get("files")) if str(f.get("state", "")).lower() in ("failed", "error")
    ]


def condense_deploy(result: dict[str, Any]) -> list[str]:
    details = result.get("details") if isinstance(result.get("details"), dict) else {}
    tests = details.get("runTestResult") if isinstance(details.get("runTestResult"), dict) else {}
    out: list[str] = []
    head = []
    if isinstance(result.get("status"), str):
        head.append(f"status: {result['status']}")
    deployed, errors, total = (as_int(result.get(k)) for k in ("numberComponentsDeployed", "numberComponentErrors", "numberComponentsTotal"))
    if deployed is not None or errors is not None:
        head.append(f"components: {deployed if deployed is not None else '?'} deployed, {errors if errors is not None else '?'} failed"
                    + (f" of {total}" if total is not None else ""))
    ran = as_int(result.get("numberTestsCompleted", tests.get("numTestsRun")))
    failed = as_int(result.get("numberTestErrors", tests.get("numFailures")))
    if ran is not None or failed is not None:
        head.append(f"tests: {ran if ran is not None else '?'} run, {failed if failed is not None else '?'} failed")
    out.append("  |  ".join(head) if head else "deploy result")
    grouped: dict[str, list[str]] = {}
    for item in component_failures(result):
        problem = text_of(item["problem"]) or "(no problem text)"
        where = text_of(item["file"])
        if where and item["line"] not in (None, ""):
            where += f":{item['line']}"
        label = f"{text_of(item['type'])} {text_of(item['name'])}".strip() or "?"
        grouped.setdefault(problem, []).append(f"{label} ({where})" if where else label)
    for problem, items in sorted(grouped.items(), key=lambda pair: -len(pair[1])):
        out.append(f"{len(items)}x {clip(problem, 300)}" if len(items) > 1 else clip(problem, 300))
        out.extend(f"  {entry}" for entry in items[:25])
        if len(items) > 25:
            out.append(f"  +{len(items) - 25} more")
    for failure in as_list(tests.get("failures")):
        name = ".".join(p for p in (failure.get("name"), failure.get("methodName")) if p)
        out.extend(format_failure(name, failure.get("message"), failure.get("stackTrace")))
    for warning in as_list(tests.get("codeCoverageWarnings")):
        label = text_of(warning.get("name"))
        out.append(f"coverage warning: {label + ': ' if label else ''}{clip(text_of(warning.get('message')), 300)}")
    return out


def run_errors(args: argparse.Namespace) -> int:
    expected = "the JSON of `sf project deploy start --json`"
    keys = ("details", "files", "numberComponentErrors", "numberComponentsTotal", "numberComponentsDeployed")
    result = unwrap(load_json(read_source(args.file), expected), expected, keys)
    print("\n".join(condense_deploy(result)))
    return 0


@dataclass(slots=True)
class OrgRow:
    alias: str
    username: str
    kind: str
    host: str
    expires: str
    default: bool


def org_kind(entry: dict[str, Any], bucket: str) -> str:
    host = urlparse(str(entry.get("instanceUrl") or "")).netloc.lower()
    if bucket == "scratchOrgs" or entry.get("isScratch") is True or (bucket == "" and entry.get("expirationDate")):
        return "scratch"
    if bucket == "sandboxes" or entry.get("isSandbox") is True:
        return "sandbox"
    if bucket == "devHubs" or entry.get("isDevHub") is True:
        return "Dev Hub"
    if bucket:
        return "production"
    if ".scratch." in host:
        return "scratch"
    if ".sandbox." in host:
        return "sandbox"
    return "unknown"


def org_rows(result: Any) -> list[OrgRow]:
    buckets: list[tuple[str, list[dict[str, Any]]]] = []
    if isinstance(result, list):
        buckets.append(("nonScratchOrgs", as_list(result)))
    elif isinstance(result, dict) and "username" in result:
        buckets.append(("", [result]))
    elif isinstance(result, dict):
        for key in ("scratchOrgs", "sandboxes", "devHubs", "nonScratchOrgs", "other"):
            buckets.append((key, as_list(result.get(key))))
    rows: list[OrgRow] = []
    seen: set[str] = set()
    for bucket, entries in buckets:
        for entry in entries:
            identity = str(entry.get("username") or entry.get("orgId") or entry.get("id") or id(entry))
            if identity in seen:
                continue
            seen.add(identity)
            expires = str(entry.get("expirationDate") or "")[:10]
            kind = org_kind(entry, bucket)
            rows.append(OrgRow(
                alias=text_of(entry.get("alias")), username=text_of(entry.get("username")), kind=kind,
                host=urlparse(str(entry.get("instanceUrl") or "")).netloc,
                expires=expires if kind == "scratch" else "",
                default=entry.get("isDefaultUsername") is True or entry.get("isDefaultDevHubUsername") is True,
            ))
    return rows


def run_sf(alias: str | None) -> str:
    executable = shutil.which("sf")
    if not executable:
        raise SynError("the `sf` CLI was not found on PATH; install it or pass --from <file|-> with saved JSON")
    if alias and alias.startswith("-"):
        raise SynError(f"expected an org alias or username, got {alias!r}")
    command = [executable, "org", "display", "--target-org", alias, "--json"] if alias else [executable, "org", "list", "--json"]
    try:
        done = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
            stdin=subprocess.DEVNULL, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SynError(f"could not run `{' '.join(command[1:])}`: {error}") from error
    if done.returncode != 0 and not done.stdout.strip():
        first = (done.stderr.strip().splitlines() or ["no output"])[0]
        raise SynError(f"`sf` failed with exit code {done.returncode}: {first}")
    return done.stdout


def run_org(args: argparse.Namespace) -> int:
    expected = "the JSON of `sf org list --json` or `sf org display --json`"
    text = read_source(args.source) if args.source else run_sf(args.alias)
    data = load_json(text, expected)
    if isinstance(data, dict):
        result: Any = unwrap(data, expected, ("nonScratchOrgs", "scratchOrgs", "devHubs", "sandboxes", "other", "username"))
    elif isinstance(data, list):
        result = data
    else:
        raise SynError(f"expected {expected}, got a JSON {type(data).__name__}")
    rows = org_rows(result)
    if not rows:
        print("no orgs found")
        return 0
    for row in rows:
        fields = [row.alias, row.username, row.kind, row.host, f"expires {row.expires}" if row.expires else ""]
        print(("* " if row.default else "  ") + "  ".join(value for value in fields if value))
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    log = subparsers.add_parser("log", help="condense an Apex debug log")
    log.add_argument("file", help="log file or - for stdin")
    log.add_argument("--debug", type=int, default=40, metavar="N", help="USER_DEBUG entries shown (default 40)")
    log.set_defaults(run=run_log)

    tests = subparsers.add_parser("tests", help="condense `sf apex run test --json`")
    tests.add_argument("file", help="JSON file or - for stdin")
    tests.add_argument("--min", type=int, default=75, metavar="PCT", help="coverage threshold (default 75)")
    tests.set_defaults(run=run_tests)

    errors = subparsers.add_parser("errors", help="condense `sf project deploy start --json`")
    errors.add_argument("file", help="JSON file or - for stdin")
    errors.set_defaults(run=run_errors)

    org = subparsers.add_parser("org", help="one line per authenticated org")
    org.add_argument("alias", nargs="?", help="show only this org (sf org display)")
    org.add_argument("--from", dest="source", metavar="FILE", help="condense saved JSON (file or -) instead of running sf")
    org.set_defaults(run=run_org)
