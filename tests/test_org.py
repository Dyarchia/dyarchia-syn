"""Tests for ``syn log``, ``syn tests``, ``syn errors`` and ``syn org`` with inline fixtures."""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

import pytest

from syn import org
from syn.cli import SynError


def build_log() -> str:
    lines = [
        "64.0 APEX_CODE,DEBUG;APEX_PROFILING,INFO;DB,INFO",
        "12:00:00.0 (1000)|EXECUTION_STARTED",
        "12:00:00.0 (2000)|CODE_UNIT_STARTED|[EXTERNAL]|01p000000000001|InvoiceService.run",
        "12:00:00.1 (3000)|USER_DEBUG|[5]|DEBUG|starting batch",
        "12:00:00.1 (4000)|USER_DEBUG|[6]|DEBUG|tick",
        "12:00:00.1 (5000)|USER_DEBUG|[6]|DEBUG|tick",
        "12:00:00.1 (6000)|USER_DEBUG|[6]|DEBUG|tick",
        "12:00:00.1 (6500)|USER_DEBUG|[9]|DEBUG|multi",
        "second half of the message",
    ]
    for index in range(30):
        base = 10_000 + index * 1000
        lines.append(f"12:00:01.0 ({base})|SOQL_EXECUTE_BEGIN|[12]|Aggregations:0|SELECT Id, Name FROM Account WHERE Id = :a")
        lines.append(f"12:00:01.0 ({base + 500})|SOQL_EXECUTE_END|[12]|Rows:2")
    lines += [
        "12:00:02.0 (90000)|SOQL_EXECUTE_BEGIN|[30]|Aggregations:0|SELECT Id FROM Invoice__c",
        "12:00:02.0 (91000)|SOQL_EXECUTE_END|[30]|Rows:7",
        "12:00:02.0 (92000)|DML_BEGIN|[40]|Op:Insert|Type:Invoice__c|Rows:3",
        "12:00:02.0 (93000)|DML_END|[40]",
        "12:00:02.1 (94000)|EXCEPTION_THROWN|[50]|System.NullPointerException: Attempt to de-reference a null object",
        "12:00:02.1 (95000)|FATAL_ERROR|System.NullPointerException: Attempt to de-reference a null object",
        "",
        "Class.InvoiceService.run: line 50, column 1",
        "Class.InvoiceService.go: line 7, column 1",
        "garbage line without pipes that belongs to the stack",
        "12:00:02.2 (96000)|CODE_UNIT_FINISHED|InvoiceService.run",
        "12:00:02.2 (97000)|LIMIT_USAGE_FOR_NS|(default)|",
        "  Number of SOQL queries: 31 out of 100",
        "    Number of query rows: 100 out of 50000",
        "  Number of DML statements: 1 out of 150",
        "  Maximum CPU time: 9000 out of 10000",
        "  Maximum heap size: 10 out of 6000000",
        "  Number of callouts: 60 out of 100",
        "12:00:02.5 (2500000000)|EXECUTION_FINISHED",
    ]
    return "\n".join(lines) + "\n"


TEST_RUN = {
    "status": 0,
    "result": {
        "summary": {
            "outcome": "Failed", "testsRan": 5, "passing": 3, "failing": 1, "skipped": 1,
            "testTotalTime": "1523 ms", "orgWideCoverage": "79%", "testRunCoverage": "82%",
        },
        "tests": [
            {"Outcome": "Pass", "FullName": "InvoiceServiceTest.ok", "Message": None},
            {
                "Outcome": "Fail", "FullName": "InvoiceServiceTest.breaks", "ApexClass": {"Name": "InvoiceServiceTest"},
                "MethodName": "breaks", "Message": "System.AssertException: Assertion Failed: Expected: 1, Actual: 2",
                "StackTrace": "Class.InvoiceServiceTest.breaks: line 12, column 1\nl2\nl3\nl4",
            },
            {"Outcome": "Skip", "FullName": "InvoiceServiceTest.later"},
        ],
        "coverage": {"coverage": [
            {"name": "InvoiceService", "totalLines": 20, "totalCovered": 14, "coveredPercent": 70,
             "lines": {str(n): (0 if n in (12, 13, 14, 15, 40, 41, 43) else 1) for n in range(1, 21)} | {"40": 0, "41": 0, "43": 0}},
            {"name": "Healthy", "totalLines": 10, "totalCovered": 10, "coveredPercent": 100, "lines": {"1": 1}},
        ]},
    },
    "warnings": [],
}

DEPLOY = {
    "status": 1,
    "result": {
        "status": "Failed", "success": False, "numberComponentsDeployed": 4, "numberComponentErrors": 3,
        "numberComponentsTotal": 7, "numberTestsCompleted": 2, "numberTestErrors": 1,
        "details": {
            "componentFailures": [
                {"componentType": "ApexClass", "fullName": "A", "fileName": "classes/A.cls", "lineNumber": "12", "problem": "Variable does not exist: Foo__c", "success": False},
                {"componentType": "ApexClass", "fullName": "B", "fileName": "classes/B.cls", "lineNumber": 7, "problem": "Variable does not exist: Foo__c", "success": False},
                {"componentType": "CustomField", "fullName": "Account.X__c", "fileName": "objects/Account/fields/X__c.field-meta.xml", "problem": "Required field is missing: label"},
            ],
            "componentSuccesses": [{"componentType": "ApexClass", "fullName": "Fine"}],
            "runTestResult": {
                "failures": [{"name": "ATest", "methodName": "boom", "message": "bad", "stackTrace": "Class.ATest.boom: line 3, column 1"}],
                "codeCoverageWarnings": {"name": "A", "message": "Average test coverage across all Apex Classes is 60%"},
            },
        },
    },
}

ORG_LIST = {
    "status": 0,
    "result": {
        "nonScratchOrgs": [
            {"alias": "prod", "username": "me@corp.com", "instanceUrl": "https://corp.my.salesforce.com", "isDevHub": False,
             "accessToken": "00DSECRETTOKEN!abc", "isDefaultUsername": True, "sfdxAuthUrl": "force://PlatformCLI::SECRETAUTH@corp"},
            {"alias": "hub", "username": "hub@corp.com", "instanceUrl": "https://hub.my.salesforce.com", "isDevHub": True},
            {"alias": "qa", "username": "me@corp.com.qa", "instanceUrl": "https://corp--qa.sandbox.my.salesforce.com", "isSandbox": True},
        ],
        "devHubs": [{"alias": "hub", "username": "hub@corp.com", "instanceUrl": "https://hub.my.salesforce.com", "isDevHub": True}],
        "scratchOrgs": [
            {"alias": "scr1", "username": "test-abc@example.com", "instanceUrl": "https://x.scratch.my.salesforce.com",
             "expirationDate": "2026-12-01", "accessToken": "00DSECRETTOKEN2", "password": "hunter2"},
        ],
    },
}


def run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> str:
    parser = argparse.ArgumentParser(prog="syn")
    org.register(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(argv)
    assert args.run(args) == 0
    return capsys.readouterr().out


def save(tmp_path: Path, name: str, content: str | dict) -> str:
    path = tmp_path / name
    path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return str(path)


def test_log(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = run(["log", save(tmp_path, "a.log", build_log())], capsys)
    lines = out.splitlines()
    assert lines[0] == "FATAL: System.NullPointerException: Attempt to de-reference a null object"
    assert "  Class.InvoiceService.run: line 50, column 1" in lines
    assert "  garbage line without pipes that belongs to the stack" in lines
    assert "EXCEPTION_THROWN (1):" in out and "  [50] System.NullPointerException" in out
    assert "  line 5: starting batch" in lines
    assert "  line 6: tick (x3)" in lines
    assert "  line 9: multi / second half of the message" in lines
    assert "SOQL: 31 queries, 2 distinct" in out
    loop = [line for line in lines if "SELECT Id, Name FROM Account" in line][0]
    assert loop.startswith("  30x, 60 rows, line 12:") and loop.endswith("likely query in a loop")
    assert any("1x, 7 rows, line 30: SELECT Id FROM Invoice__c" in line for line in lines)
    assert "  Insert Invoice__c: 1x, 3 rows" in lines
    assert "  Number of SOQL queries: 31/100 (31%)" in lines
    assert "  Number of DML statements: 1/150 (0%)" in lines
    assert "  Maximum CPU time: 9000/10000 (90%)" in lines
    assert "  Number of callouts: 60/100 (60%)" in lines
    assert not any("heap" in line or "query rows" in line for line in lines)
    assert lines[-1] == "duration: 2.50 s"


def test_log_debug_cap_and_stdin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(build_log()))
    out = run(["log", "-", "--debug", "1"], capsys)
    assert "  line 5: starting batch" in out and "line 6" not in out and "+2 more entries" in out


def test_log_tolerates_junk(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["log", save(tmp_path, "j.log", "hello\nworld\n")], capsys).strip() == "no recognizable debug log lines"


def test_missing_file(tmp_path: Path) -> None:
    parser = argparse.ArgumentParser()
    org.register(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["log", str(tmp_path / "nope.log")])
    with pytest.raises(SynError, match="no such file"):
        args.run(args)


def test_tests(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = run(["tests", save(tmp_path, "t.json", TEST_RUN)], capsys)
    lines = out.splitlines()
    assert lines[0] == "outcome: Failed  |  5 run, 3 passed, 1 failed, 1 skipped  |  time 1523 ms"
    assert "coverage: org-wide 79%, run 82%" in lines
    assert "InvoiceServiceTest.breaks: System.AssertException: Assertion Failed: Expected: 1, Actual: 2" in lines
    assert "    Class.InvoiceServiceTest.breaks: line 12, column 1" in lines
    assert "    l3" in lines and "    l4" not in lines
    assert "InvoiceServiceTest.ok" not in out and "later" not in out
    assert "below 75% coverage (1):" in lines
    assert "  InvoiceService: 70%  uncovered 12-15,40-41,43" in lines
    assert "Healthy" not in out
    assert "Healthy" in run(["tests", save(tmp_path, "t.json", TEST_RUN), "--min", "101"], capsys)


def test_tests_old_coverage_shape_and_missing_keys(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = {"result": {"summary": {"outcome": "Passed"}, "codecoverage": [
        {"name": "Old", "percentage": "40%", "uncoveredLines": [3, 4, 9]}]}}
    out = run(["tests", save(tmp_path, "o.json", data)], capsys)
    assert out.splitlines()[0] == "outcome: Passed"
    assert "  Old: 40%  uncovered 3-4,9" in out


def test_shape_errors(tmp_path: Path) -> None:
    parser = argparse.ArgumentParser()
    org.register(parser.add_subparsers(dest="command"))
    for argv, pattern in (
        (["tests", save(tmp_path, "l.json", [1, 2])], "expected the JSON of `sf apex run test --json`"),
        (["tests", save(tmp_path, "x.json", {"foo": 1})], "summary or tests"),
        (["errors", save(tmp_path, "n.json", "not json")], "not JSON"),
        (["errors", save(tmp_path, "e.json", {"status": 1, "message": "No authorization found\nmore"})], "sf reported an error: No authorization found"),
        (["org", "--from", save(tmp_path, "o.json", 5)], "got a JSON int"),
    ):
        args = parser.parse_args(argv)
        with pytest.raises(SynError, match=pattern):
            args.run(args)


def test_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = run(["errors", save(tmp_path, "d.json", DEPLOY)], capsys)
    lines = out.splitlines()
    assert lines[0] == "status: Failed  |  components: 4 deployed, 3 failed of 7  |  tests: 2 run, 1 failed"
    assert lines[1] == "2x Variable does not exist: Foo__c"
    assert "  ApexClass A (classes/A.cls:12)" in lines and "  ApexClass B (classes/B.cls:7)" in lines
    assert "Required field is missing: label" in lines
    assert "  CustomField Account.X__c (objects/Account/fields/X__c.field-meta.xml)" in lines
    assert "ATest.boom: bad" in lines and "    Class.ATest.boom: line 3, column 1" in lines
    assert "coverage warning: A: Average test coverage across all Apex Classes is 60%" in lines
    assert "Fine" not in out


def test_errors_files_shape(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = {"result": {"status": "Failed", "files": [
        {"fullName": "A", "type": "ApexClass", "state": "Failed", "filePath": "classes/A.cls", "lineNumber": 3, "error": "boom (3:4)"},
        {"fullName": "B", "type": "ApexClass", "state": "Created", "filePath": "classes/B.cls"},
    ]}}
    out = run(["errors", save(tmp_path, "f.json", data)], capsys)
    assert "boom (3:4)" in out and "ApexClass A (classes/A.cls:3)" in out and "classes/B.cls" not in out


def test_org_list_hides_secrets(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = run(["org", "--from", save(tmp_path, "l.json", ORG_LIST)], capsys)
    for secret in ("SECRET", "hunter2", "force://", "00D"):
        assert secret not in out
    lines = out.splitlines()
    assert len(lines) == 4
    assert "* prod  me@corp.com  production  corp.my.salesforce.com" in lines
    assert "  hub  hub@corp.com  Dev Hub  hub.my.salesforce.com" in lines
    assert "  qa  me@corp.com.qa  sandbox  corp--qa.sandbox.my.salesforce.com" in lines
    assert "  scr1  test-abc@example.com  scratch  x.scratch.my.salesforce.com  expires 2026-12-01" in lines


def test_org_display_from_stdin(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    payload = {"status": 0, "result": {"username": "a@b.c", "alias": "al", "accessToken": "TOPSECRET",
                                       "instanceUrl": "https://z.scratch.my.salesforce.com", "expirationDate": "2026-05-04T00:00:00"}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    out = run(["org", "al", "--from", "-"], capsys)
    assert out.strip() == "al  a@b.c  scratch  z.scratch.my.salesforce.com  expires 2026-05-04"


def test_org_without_sf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(org.shutil, "which", lambda name: None)
    parser = argparse.ArgumentParser()
    org.register(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["org"])
    with pytest.raises(SynError, match="`sf` CLI was not found"):
        args.run(args)


def test_org_runs_sf_without_shell(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[tuple] = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return org.subprocess.CompletedProcess(command, 0, stdout=json.dumps(ORG_LIST), stderr="")

    monkeypatch.setattr(org.shutil, "which", lambda name: "C:/tools/sf.cmd")
    monkeypatch.setattr(org.subprocess, "run", fake_run)
    out = run(["org"], capsys)
    assert calls[0][0] == ["C:/tools/sf.cmd", "org", "list", "--json"]
    assert "shell" not in calls[0][1] and "SECRET" not in out
    run(["org", "qa"], capsys)
    assert calls[1][0] == ["C:/tools/sf.cmd", "org", "display", "--target-org", "qa", "--json"]


def test_org_rejects_option_like_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(org.shutil, "which", lambda name: "sf")
    with pytest.raises(SynError, match="alias"):
        org.run_sf("--evil")


def test_debug_lines_that_differ_only_in_numbers_collapse(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Regression: a debug statement in a loop printed one line per record id."""
    lines = [f"12:00:00.1 ({1000 + index})|USER_DEBUG|[9]|DEBUG|processing a01xx00000000{index:02d}" for index in range(50)]
    lines.append("12:00:00.2 (9000)|USER_DEBUG|[9]|DEBUG|done")
    out = run(["log", save(tmp_path, "loop.log", "\n".join(lines))], capsys).splitlines()
    assert out == ["USER_DEBUG (51):", "  line 9: processing a01xx0000000000 (x50, numbers vary)", "  line 9: done", "duration: 0 ms"]
