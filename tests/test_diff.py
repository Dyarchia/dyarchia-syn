"""Tests for ``syn diff`` against real git repositories created in ``tmp_path``."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

import pytest

from syn import diff
from syn.cli import SynError

NS = 'xmlns="http://soap.sforce.com/2006/04/metadata"'
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def profile(field_rows: str, user_rows: str = "", extra: str = "") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f"<Profile {NS}>\n{field_rows}{user_rows}{extra}    <custom>false</custom>\n</Profile>\n"
    )


def field_row(name: str, editable: str = "true", readable: str = "true") -> str:
    return (
        "    <fieldPermissions>\n"
        f"        <editable>{editable}</editable>\n"
        f"        <field>{name}</field>\n"
        f"        <readable>{readable}</readable>\n"
        "    </fieldPermissions>\n"
    )


def user_row(name: str, enabled: str = "true") -> str:
    return f"    <userPermissions>\n        <enabled>{enabled}</enabled>\n        <name>{name}</name>\n    </userPermissions>\n"


BASE_FIELDS = field_row("Account.A__c") + field_row("Account.B__c", "false") + field_row("Account.C__c")
BASE_USERS = user_row("ViewSetup") + user_row("ApiEnabled", "false")


def sh(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    sh(tmp_path, "init", "-q")
    sh(tmp_path, "config", "user.name", "Tester")
    sh(tmp_path, "config", "user.email", "tester@example.com")
    sh(tmp_path, "config", "core.autocrlf", "false")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def commit(repo: Path, message: str = "c") -> None:
    sh(repo, "add", "-A")
    sh(repo, "commit", "-q", "-m", message)


def write(repo: Path, relative: str, content: str) -> None:
    target = repo / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")


def run_diff(capsys: pytest.CaptureFixture[str], *argv: str) -> str:
    parser = argparse.ArgumentParser()
    diff.register(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["diff", *argv])
    assert args.run(args) == 0
    return capsys.readouterr().out


PROFILE_PATH = "force-app/profiles/Admin.profile-meta.xml"


@needs_git
def test_field_change_in_profile(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(repo, PROFILE_PATH, profile(BASE_FIELDS, BASE_USERS))
    commit(repo)
    write(repo, PROFILE_PATH, profile(field_row("Account.A__c") + field_row("Account.B__c", "true") + field_row("Account.C__c"), BASE_USERS))
    out = run_diff(capsys)
    assert f"M {PROFILE_PATH}" in out
    assert "~ fieldPermissions Account.B__c  editable: false -> true" in out
    assert "Account.A__c" not in out
    assert "total: 1 files (1 modified), 1 changes" in out


@needs_git
def test_reorder_of_unordered_blocks_is_no_change(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(repo, PROFILE_PATH, profile(BASE_FIELDS, BASE_USERS))
    commit(repo)
    reordered = field_row("Account.C__c") + field_row("Account.A__c") + field_row("Account.B__c", "false")
    write(repo, PROFILE_PATH, profile(reordered, user_row("ApiEnabled", "false") + user_row("ViewSetup")))
    out = run_diff(capsys)
    assert "fieldPermissions" not in out and "userPermissions" not in out
    assert "formatting only" in out
    assert "0 changes" not in out


@needs_git
def test_added_and_removed_rows(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(repo, PROFILE_PATH, profile(BASE_FIELDS, BASE_USERS))
    commit(repo)
    fields = field_row("Account.A__c") + field_row("Account.C__c") + field_row("Account.D__c", "false", "true")
    write(repo, PROFILE_PATH, profile(fields, user_row("ViewSetup") + user_row("ManageUsers")))
    out = run_diff(capsys)
    assert "+ fieldPermissions Account.D__c editable=false readable=true" in out
    assert "- fieldPermissions Account.B__c" in out
    assert "+ userPermissions ManageUsers enabled=true" in out
    assert "- userPermissions ApiEnabled" in out
    assert "total: 1 files (1 modified), 4 changes" in out


@needs_git
def test_same_change_in_three_profiles_is_collapsed(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    names = ("Admin", "Sales", "Support")
    for name in names:
        write(repo, f"force-app/profiles/{name}.profile-meta.xml", profile(BASE_FIELDS, BASE_USERS))
    commit(repo)
    for name in names:
        extra = user_row("ManageUsers") if name == "Sales" else ""
        write(repo, f"force-app/profiles/{name}.profile-meta.xml", profile(BASE_FIELDS + field_row("Account.New__c"), BASE_USERS + extra))
    out = run_diff(capsys)
    assert out.count("+ fieldPermissions Account.New__c editable=true readable=true") == 1
    assert "M 3 files: Admin.profile, Sales.profile, Support.profile" in out
    assert out.count("+ userPermissions ManageUsers") == 1
    assert "M force-app/profiles/Sales.profile-meta.xml" in out
    assert "total: 3 files (3 modified), 4 changes" in out


@needs_git
def test_formatting_only(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(repo, PROFILE_PATH, profile(BASE_FIELDS, BASE_USERS))
    commit(repo)
    spaced = profile(BASE_FIELDS, BASE_USERS).replace("    <", "\t<").replace("</Profile>", "\n\n</Profile>")
    write(repo, PROFILE_PATH, spaced)
    out = run_diff(capsys)
    assert f"M {PROFILE_PATH}  formatting only" in out
    assert "fieldPermissions" not in out


@needs_git
def test_non_xml_line_counts_and_untracked_files(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(repo, "notes/readme.md", "one\ntwo\nthree\n")
    write(repo, "force-app/classes/Foo.cls", "public class Foo {}\n")
    commit(repo)
    write(repo, "notes/readme.md", "one\nTWO\nthree\nfour\n")
    (repo / "force-app/classes/Foo.cls").unlink()
    write(repo, "force-app/profiles/New.profile-meta.xml", profile(BASE_FIELDS, BASE_USERS))
    write(repo, "scripts/run.sh", "a\nb\n")
    out = run_diff(capsys)
    assert "M notes/readme.md  +2 -1" in out
    assert "D force-app/classes/Foo.cls  -1" in out
    assert "A scripts/run.sh  +2" in out
    assert "A force-app/profiles/New.profile-meta.xml  Profile New (fieldPermissions x3, userPermissions x2)" in out
    assert "public class" not in out
    assert "total: 4 files (1 modified, 2 added, 1 deleted)" in out


@needs_git
def test_deleted_metadata_file(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(repo, PROFILE_PATH, profile(BASE_FIELDS, BASE_USERS))
    commit(repo)
    (repo / PROFILE_PATH).unlink()
    out = run_diff(capsys)
    assert f"D {PROFILE_PATH}  Profile Admin" in out


@needs_git
def test_two_revision_mode_and_paths(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(repo, PROFILE_PATH, profile(BASE_FIELDS, BASE_USERS))
    write(repo, "other/doc.txt", "x\n")
    commit(repo, "first")
    write(repo, PROFILE_PATH, profile(BASE_FIELDS + field_row("Account.Z__c"), BASE_USERS))
    write(repo, "other/doc.txt", "x\ny\n")
    commit(repo, "second")
    write(repo, PROFILE_PATH, profile(BASE_FIELDS, BASE_USERS))
    out = run_diff(capsys, "HEAD~1", "HEAD")
    assert "+ fieldPermissions Account.Z__c" in out
    assert "M other/doc.txt  +1 -0" in out
    only = run_diff(capsys, "HEAD~1", "HEAD", "--", "force-app")
    assert "doc.txt" not in only and "Account.Z__c" in only
    assert "total: 1 files" in only
    working = run_diff(capsys, "HEAD", "--", "force-app")
    assert "- fieldPermissions Account.Z__c" in working


@needs_git
def test_stat_mode(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(repo, PROFILE_PATH, profile(BASE_FIELDS, BASE_USERS))
    write(repo, "notes/a.txt", "1\n")
    commit(repo)
    write(repo, PROFILE_PATH, profile(BASE_FIELDS + field_row("Account.Z__c"), user_row("ViewSetup")))
    write(repo, "notes/a.txt", "1\n2\n")
    out = run_diff(capsys, "--stat")
    assert f"M {PROFILE_PATH}  2 changes" in out
    assert "M notes/a.txt  +1 -0" in out
    assert "fieldPermissions" not in out
    assert out.strip().splitlines()[-1] == "total: 2 files (2 modified), 2 changes"


@needs_git
def test_no_changes(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(repo, "a.txt", "1\n")
    commit(repo)
    assert run_diff(capsys).strip() == "total: no changes"


def test_files_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(tmp_path)
    write(tmp_path, "a.profile-meta.xml", profile(BASE_FIELDS, BASE_USERS))
    write(tmp_path, "b.profile-meta.xml", profile(field_row("Account.A__c", "false") + field_row("Account.B__c", "false") + field_row("Account.C__c"), BASE_USERS))
    out = run_diff(capsys, "--files", "a.profile-meta.xml", "b.profile-meta.xml")
    assert "~ fieldPermissions Account.A__c  editable: true -> false" in out
    assert "total: 1 files (1 modified), 1 changes" in out
    write(tmp_path, "x.txt", "a\nb\n")
    write(tmp_path, "y.txt", "a\nc\nd\n")
    assert "+2 -1" in run_diff(capsys, "--files", "x.txt", "y.txt")


def test_files_mode_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SynError, match="no such file"):
        diff.collect_files("nope.xml", "nope2.xml")


def test_not_a_git_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    with pytest.raises(SynError, match="not a git repository"):
        diff.collect_git([])


def test_unspecified_blocks_by_name_by_position_and_by_value() -> None:
    old = diff.ET.fromstring(
        f"""<Widget {NS}>
        <label>Old</label>
        <variables><name>a</name><dataType>String</dataType></variables>
        <variables><name>b</name><dataType>Number</dataType></variables>
        <choices><value>x</value></choices><choices><value>y</value></choices>
        <start><locationX>1</locationX><object>Account</object></start>
        </Widget>"""
    )
    new = diff.ET.fromstring(
        f"""<Widget {NS}>
        <label>New</label>
        <variables><name>b</name><dataType>Text</dataType></variables>
        <variables><name>c</name><dataType>Number</dataType></variables>
        <choices><value>x</value></choices><choices><value>z</value></choices>
        <start><locationX>99</locationX><object>Account</object></start>
        </Widget>"""
    )
    for root in (old, new):
        for el in root.iter():
            el.tag = el.tag.rpartition("}")[2]
    lines = diff.diff_roots(old, new)
    assert "~ label: Old -> New" in lines
    assert "~ variables[b].dataType: Number -> Text" in lines
    assert "+ variables[c] name=c dataType=Number" in lines
    assert "- variables[a]" in lines
    assert "~ choices[1].value: y -> z" in lines
    assert not any(line.startswith("~ start") for line in lines)


def test_repeated_leaf_values_diff_as_sets() -> None:
    def package(members: str) -> diff.ET.Element:
        return diff.ET.fromstring(f"<Package><types>{members}<name>ApexClass</name></types></Package>")

    old = package("<members>A</members><members>B</members>")
    new = package("<members>B</members><members>C</members>")
    assert diff.diff_roots(old, new) == ["~ types.members: +C, -A"]
    assert diff.diff_roots(old, package("<members>B</members><members>A</members>")) == ["~ types.members: reordered"]


def layout(*fields: str) -> diff.ET.Element:
    items = "".join(f"<layoutItems><behavior>Edit</behavior><field>{name}</field></layoutItems>" for name in fields)
    return diff.ET.fromstring(f"<Layout><layoutSections><layoutColumns>{items}</layoutColumns></layoutSections></Layout>")


def test_removing_one_unkeyed_item_is_one_line_with_its_content() -> None:
    """Regression: unkeyed items were paired by position, so one removal printed every later item as changed."""
    old = layout("Name", "Legacy__c", "Region__c", "Status__c", "Cost__c")
    new = layout("Name", "Region__c", "Status__c", "Cost__c")
    assert diff.diff_roots(old, new) == ["- layoutSections.layoutColumns.layoutItems[1] behavior=Edit field=Legacy__c"]
    changed = layout("Name", "Legacy__c", "Area__c", "Status__c", "Cost__c")
    assert diff.diff_roots(old, changed) == ["~ layoutSections.layoutColumns.layoutItems[2].field: Region__c -> Area__c"]


def test_composite_key_uses_the_tables_syntax() -> None:
    """Regression: diff joined composite keys with | and left a trailing | when the second part was empty."""
    def assignments(rows: str) -> diff.ET.Element:
        return diff.ET.fromstring(f"<Profile>{rows}</Profile>")

    plain = "<layoutAssignments><layout>Account-Layout</layout></layoutAssignments>"
    typed = "<layoutAssignments><layout>Account-Layout</layout><recordType>Account.Partner</recordType></layoutAssignments>"
    assert diff.diff_roots(assignments(""), assignments(plain + typed)) == [
        "+ layoutAssignments Account-Layout",
        "+ layoutAssignments Account-Layout+Account.Partner",
    ]


@needs_git
def test_unknown_revision_or_path_is_an_error(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Regression: a mistyped branch name was taken as a path and answered "no changes"."""
    write(repo, PROFILE_PATH, profile(BASE_FIELDS))
    commit(repo)
    with pytest.raises(SynError, match="nosuchrev is neither a revision nor a path"):
        run_diff(capsys, "nosuchrev")
    (repo / PROFILE_PATH).unlink()
    assert "D force-app/profiles/Admin.profile-meta.xml" in run_diff(capsys, PROFILE_PATH)


@needs_git
def test_subfolder_sees_the_whole_repo(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Regression: from a subfolder only changes under it were shown, with no hint that others existed."""
    write(repo, PROFILE_PATH, profile(BASE_FIELDS))
    write(repo, "force-app/classes/A.cls", "class A {}\n")
    commit(repo)
    write(repo, PROFILE_PATH, profile(BASE_FIELDS + field_row("Account.New__c")))
    write(repo, "force-app/classes/A.cls", "class A { }\n")
    write(repo, "force-app/classes/B.cls", "class B {}\n")
    monkeypatch.chdir(repo / "force-app" / "profiles")
    out = run_diff(capsys)
    assert "M Admin.profile-meta.xml" in out
    assert "M ../classes/A.cls  +1 -1" in out
    assert "A ../classes/B.cls  +1" in out
    scoped = run_diff(capsys, "Admin.profile-meta.xml")
    assert "A.cls" not in scoped and "+ fieldPermissions Account.New__c" in scoped


@needs_git
def test_malformed_xml_is_reported(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Regression: a metadata file broken by an edit was reported as a plain line count."""
    write(repo, PROFILE_PATH, profile(BASE_FIELDS))
    commit(repo)
    write(repo, PROFILE_PATH, profile(BASE_FIELDS) + "<broken\n")
    assert f"M {PROFILE_PATH}  malformed XML at line " in run_diff(capsys)
