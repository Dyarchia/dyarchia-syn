"""The ``rows``, ``set``, ``add`` and ``del`` commands, driven through argparse like ``syn.cli.main``."""

from __future__ import annotations

import argparse
import sys
import time
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest

from syn import tables
from syn.cli import SynError

PROFILE = """<?xml version="1.0" encoding="UTF-8"?>
<Profile xmlns="http://soap.sforce.com/2006/04/metadata">
    <applicationVisibilities>
        <application>standard__Sales</application>
        <default>true</default>
        <visible>true</visible>
    </applicationVisibilities>
    <custom>false</custom>
    <fieldPermissions>
        <editable>true</editable>
        <field>Account.Alpha__c</field>
        <readable>true</readable>
    </fieldPermissions>
    <fieldPermissions>
        <editable>false</editable>
        <field>Account.Gamma__c</field>
        <readable>true</readable>
    </fieldPermissions>
    <fieldPermissions>
        <editable>false</editable>
        <field>Contact.Email__c</field>
    </fieldPermissions>
    <layoutAssignments>
        <layout>Account-Account Layout</layout>
    </layoutAssignments>
    <layoutAssignments>
        <layout>Account-Account Layout</layout>
        <recordType>Account.Partner</recordType>
    </layoutAssignments>
    <userLicense>Salesforce</userLicense>
    <userPermissions>
        <enabled>true</enabled>
        <name>ViewSetup</name>
    </userPermissions>
</Profile>
"""

GAMMA = """    <fieldPermissions>
        <editable>false</editable>
        <field>Account.Gamma__c</field>
        <readable>true</readable>
    </fieldPermissions>
"""

LABELS = """<?xml version="1.0" encoding="UTF-8"?>
<CustomLabels xmlns="http://soap.sforce.com/2006/04/metadata">
  <!-- R&D labels -->
  <labels>
    <fullName>Greeting</fullName>
    <language>en_US</language>
    <value>R&amp;D &lt;team&gt;</value>
  </labels>
  <!-- end -->
</CustomLabels>"""


def run(*argv: object) -> int:
    """Mimic ``syn.cli.main`` without importing the other command modules."""
    parser = argparse.ArgumentParser(prog="syn")
    tables.register(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args([str(item) for item in argv])
    try:
        return int(args.run(args) or 0)
    except SynError as error:
        print(f"syn: {error}", file=sys.stderr)
        return 2


def write(path: Path, text: str, newline: str = "\n", bom: bool = False) -> Path:
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.replace("\n", newline).encode())
    return path


def assert_xml(path: Path) -> None:
    ElementTree.fromstring(path.read_bytes())


@pytest.fixture
def profile(tmp_path: Path) -> Path:
    return write(tmp_path / "Admin.profile-meta.xml", PROFILE)


def test_rows_summary(profile: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("rows", profile) == 0
    assert capsys.readouterr().out == (
        "Profile\n"
        "custom=false\tuserLicense=Salesforce\n"
        "block\tn\tkey\n"
        "applicationVisibilities\t1\tapplication\n"
        "fieldPermissions\t3\tfield\n"
        "layoutAssignments\t2\tlayout+recordType\n"
        "userPermissions\t1\tname\n"
    )


def test_rows_table_and_where_glob(profile: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("rows", profile, "fieldPermissions") == 0
    assert capsys.readouterr().out == (
        "field\teditable\treadable\n"
        "Account.Alpha__c\ttrue\ttrue\n"
        "Account.Gamma__c\tfalse\ttrue\n"
        "Contact.Email__c\tfalse\t\n"
    )
    assert run("rows", profile, "fieldPermissions", "--where", "field=Account.*", "--where", "editable=false") == 0
    assert capsys.readouterr().out == "field\teditable\treadable\nAccount.Gamma__c\tfalse\ttrue\n"
    assert run("rows", profile, "fieldPermissions", "--cols", "readable", "--where", "readable=") == 0
    assert capsys.readouterr().out == "field\treadable\nContact.Email__c\t\n"


def test_rows_unknown_block_lists_blocks(profile: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("rows", profile, "fieldPermission") == 2
    assert "blocks here: applicationVisibilities, custom, fieldPermissions" in capsys.readouterr().err


def test_set_changes_exact_bytes(profile: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("set", profile, "fieldPermissions", "Account.Gamma__c", "editable=true") == 0
    assert capsys.readouterr().out == "Admin: fieldPermissions Account.Gamma__c editable false->true\n"
    assert profile.read_bytes() == PROFILE.replace(GAMMA, GAMMA.replace(">false<", ">true<")).encode()
    assert_xml(profile)


def test_noop_set_is_byte_identical(profile: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = profile.read_bytes()
    assert run("set", profile, "fieldPermissions", "Account.Gamma__c", "editable=false", "readable=true") == 0
    assert capsys.readouterr().out == "Admin: fieldPermissions Account.Gamma__c unchanged\n"
    assert profile.read_bytes() == before


def test_set_inserts_missing_field_in_sibling_order(profile: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("set", profile, "fieldPermissions", "Contact.Email__c", "readable=true") == 0
    assert capsys.readouterr().out == "Admin: fieldPermissions Contact.Email__c readable ->true\n"
    expected = PROFILE.replace(
        "<field>Contact.Email__c</field>\n",
        "<field>Contact.Email__c</field>\n        <readable>true</readable>\n",
    )
    assert profile.read_bytes() == expected.encode()
    assert_xml(profile)


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_add_lands_in_sorted_position(tmp_path: Path, newline: str, capsys: pytest.CaptureFixture[str]) -> None:
    path = write(tmp_path / "Admin.profile-meta.xml", PROFILE, newline)
    assert run("add", path, "fieldPermissions", "readable=false", "field=Account.Beta__c", "editable=true") == 0
    assert capsys.readouterr().out == "Admin: fieldPermissions +Account.Beta__c\n"
    beta = GAMMA.replace("Gamma", "Beta").replace(">false<", ">TMP<").replace(">true<", ">false<").replace(">TMP<", ">true<")
    expected = PROFILE.replace(GAMMA, beta + GAMMA)
    assert path.read_bytes() == expected.replace("\n", newline).encode()
    assert_xml(path)


def test_add_new_block_type_goes_in_alphabetical_place(profile: Path) -> None:
    assert run("add", profile, "classAccesses", "enabled=true", "apexClass=Foo") == 0
    block = "    <classAccesses>\n        <apexClass>Foo</apexClass>\n        <enabled>true</enabled>\n    </classAccesses>\n"
    expected = PROFILE.replace("    <custom>", block + "    <custom>")
    assert profile.read_bytes() == expected.encode()
    assert_xml(profile)


def test_add_existing_key_fails(profile: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = profile.read_bytes()
    assert run("add", profile, "userPermissions", "name=ViewSetup", "enabled=false") == 1
    assert capsys.readouterr().out == "Admin: error: userPermissions ViewSetup already exists\n"
    assert profile.read_bytes() == before


def test_del_removes_exactly_the_rows_lines(profile: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("del", profile, "fieldPermissions", "Account.Gamma__c") == 0
    assert capsys.readouterr().out == "Admin: fieldPermissions -Account.Gamma__c\n"
    assert profile.read_bytes() == PROFILE.replace(GAMMA, "").encode()
    assert_xml(profile)


def test_composite_key(profile: Path, capsys: pytest.CaptureFixture[str]) -> None:
    key = "Account-Account Layout+Account.Partner"
    assert run("set", profile, "layoutAssignments", key, "layout=Account-Partner Layout") == 0
    capsys.readouterr()
    changed = PROFILE.replace(
        "<layout>Account-Account Layout</layout>\n        <recordType>",
        "<layout>Account-Partner Layout</layout>\n        <recordType>",
    )
    assert profile.read_bytes() == changed.encode()
    assert run("del", profile, "layoutAssignments", "Account-Account Layout") == 0
    first = "    <layoutAssignments>\n        <layout>Account-Account Layout</layout>\n    </layoutAssignments>\n"
    assert profile.read_bytes() == changed.replace(first, "").encode()
    assert_xml(profile)
    assert run("rows", profile, "layoutAssignments") == 0
    assert capsys.readouterr().out.endswith("layout\trecordType\nAccount-Partner Layout\tAccount.Partner\n")


def test_multi_file_glob_with_missing_key(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    admin = write(tmp_path / "Admin.profile-meta.xml", PROFILE)
    sales = write(tmp_path / "Sales.profile-meta.xml", PROFILE, "\r\n")
    other = write(tmp_path / "Other.profile-meta.xml", PROFILE.replace(GAMMA, ""))
    pattern = str(tmp_path / "*.profile-meta.xml")
    assert run("set", pattern, "fieldPermissions", "Account.Gamma__c", "readable=false") == 1
    assert capsys.readouterr().out == (
        "Other: error: fieldPermissions Account.Gamma__c not found\n"
        "Admin, Sales: fieldPermissions Account.Gamma__c readable true->false\n"
    )
    expected = PROFILE.replace(GAMMA, GAMMA.replace("<readable>true", "<readable>false"))
    assert admin.read_bytes() == expected.encode()
    assert sales.read_bytes() == expected.replace("\n", "\r\n").encode()
    assert other.read_bytes() == PROFILE.replace(GAMMA, "").encode()
    assert_xml(admin)
    assert_xml(sales)
    assert run("rows", pattern, "userPermissions") == 0
    assert capsys.readouterr().out == (
        "file\tname\tenabled\nAdmin\tViewSetup\ttrue\nOther\tViewSetup\ttrue\nSales\tViewSetup\ttrue\n"
    )
    assert run("rows", pattern, "fieldPermissions", "--cols", "readable") == 0
    rows = "Account.Alpha__c\ttrue\nAccount.Gamma__c\tfalse\nContact.Email__c\t\n"
    assert capsys.readouterr().out == (
        "field\treadable\n[Admin]\n" + rows + "[Other]\nAccount.Alpha__c\ttrue\nContact.Email__c\t\n[Sales]\n" + rows
    )


def test_bom_and_crlf_preserved(tmp_path: Path) -> None:
    path = write(tmp_path / "Admin.profile-meta.xml", PROFILE, "\r\n", bom=True)
    assert run("set", path, "userPermissions", "ViewSetup", "enabled=false") == 0
    expected = PROFILE.replace("<enabled>true</enabled>\n        <name>View", "<enabled>false</enabled>\n        <name>View")
    assert path.read_bytes() == b"\xef\xbb\xbf" + expected.replace("\n", "\r\n").encode()
    assert_xml(path)


def test_comments_and_entities(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write(tmp_path / "CustomLabels.labels-meta.xml", LABELS)
    assert run("rows", path, "labels") == 0
    assert capsys.readouterr().out == "fullName\tlanguage\tvalue\nGreeting\ten_US\tR&D <team>\n"
    assert run("set", path, "labels", "Greeting", "value=R&D <team>") == 0
    assert path.read_bytes() == LABELS.encode()
    assert run("set", path, "labels", "Greeting", "value=Tom's \"A&B\"") == 0
    assert path.read_bytes() == LABELS.replace("R&amp;D &lt;team&gt;", "Tom&apos;s &quot;A&amp;B&quot;").encode()
    assert run("add", path, "labels", "fullName=Bye", "value=x", "language=en_US") == 0
    added = "  <labels>\n    <fullName>Bye</fullName>\n    <language>en_US</language>\n    <value>x</value>\n  </labels>\n"
    assert path.read_text(encoding="utf-8").replace(added, "", 1).encode() == LABELS.replace(
        "R&amp;D &lt;team&gt;", "Tom&apos;s &quot;A&amp;B&quot;"
    ).encode()
    assert path.read_text(encoding="utf-8").index(added) < path.read_text(encoding="utf-8").index("<fullName>Greeting")
    assert_xml(path)


UNKNOWN = """<Thing>
    <item>
        <kind>a</kind>
        <code>B</code>
    </item>
    <item>
        <kind>a</kind>
        <code>A</code>
    </item>
</Thing>
"""


def test_inferred_key_on_unknown_root(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write(tmp_path / "thing.xml", UNKNOWN)
    assert run("rows", path) == 0
    assert capsys.readouterr().out == "Thing\nblock\tn\tkey\nitem\t2\tcode (inferred key)\n"
    assert run("rows", path, "item") == 0
    assert capsys.readouterr().out == "# key code (inferred key)\ncode\tkind\nB\ta\nA\ta\n"
    assert run("add", path, "item", "code=C", "kind=b") == 0
    assert capsys.readouterr().out == "thing: item +C (inferred key)\n"
    assert path.read_text().endswith("<code>A</code>\n    </item>\n    <item>\n        <kind>b</kind>\n        <code>C</code>\n    </item>\n</Thing>\n")
    assert run("add", path, "item", "code=0", "kind=z", "--before", "B") == 0
    assert path.read_text().startswith("<Thing>\n    <item>\n        <kind>z</kind>\n        <code>0</code>\n    </item>\n    <item>\n        <kind>a</kind>\n        <code>B</code>")
    assert_xml(path)


def test_single_line_file_stays_on_one_line(tmp_path: Path) -> None:
    row = "<fieldPermissions><editable>true</editable><field>A.b</field></fieldPermissions>"
    path = write(tmp_path / "Min.profile-meta.xml", f"<Profile>{row}</Profile>")
    assert run("add", path, "fieldPermissions", "field=A.a", "editable=false") == 0
    new = "<fieldPermissions><editable>false</editable><field>A.a</field></fieldPermissions>"
    assert path.read_text() == f"<Profile>{new}{row}</Profile>"
    assert_xml(path)


def test_block_named_like_a_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """In an SFDX tree a folder such as ``labels/`` sits next to the files; it must not be read as a FILE."""
    (tmp_path / "labels").mkdir()
    write(tmp_path / "labels" / "CustomLabels.labels-meta.xml", LABELS)
    monkeypatch.chdir(tmp_path)
    assert run("rows", "labels/CustomLabels.labels-meta.xml", "labels", "--cols", "language") == 0
    assert capsys.readouterr().out == "fullName\tlanguage\nGreeting\ten_US\n"
    assert run("set", "labels/*.xml", "labels", "Greeting", "language=es") == 0
    assert capsys.readouterr().out == "CustomLabels: labels Greeting language en_US->es\n"


def test_ordered_block_appends_or_follows_after(tmp_path: Path) -> None:
    values = "".join(f"    <customValue>\n        <fullName>{name}</fullName>\n    </customValue>\n" for name in ("Low", "High"))
    text = f"<GlobalValueSet>\n{values}    <sorted>false</sorted>\n</GlobalValueSet>\n"
    path = write(tmp_path / "Priority.globalValueSet-meta.xml", text)
    assert run("add", path, "customValue", "fullName=Mid", "--after", "Low") == 0
    assert run("add", path, "customValue", "fullName=Top") == 0
    names = [line.strip()[10:-11] for line in path.read_text().splitlines() if "<fullName>" in line]
    assert names == ["Low", "Mid", "High", "Top"]
    assert_xml(path)


def test_dry_run_writes_nothing(profile: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = profile.read_bytes()
    assert run("set", profile, "fieldPermissions", "Account.Gamma__c", "editable=true", "--dry-run") == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "Admin: fieldPermissions Account.Gamma__c editable false->true"
    assert out[3:] == ["@@ -14,3 +14,3 @@", "     <fieldPermissions>", "-        <editable>false</editable>",
                       "+        <editable>true</editable>", "         <field>Account.Gamma__c</field>"]
    assert profile.read_bytes() == before


def test_twenty_thousand_rows_set_is_fast(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rows = "".join(
        f"    <fieldPermissions>\n        <editable>false</editable>\n        <field>Obj__c.F{index:05d}__c</field>\n"
        f"        <readable>true</readable>\n    </fieldPermissions>\n"
        for index in range(20000)
    )
    text = f'<?xml version="1.0" encoding="UTF-8"?>\n<Profile xmlns="urn:x">\n{rows}</Profile>\n'
    path = write(tmp_path / "Big.profile-meta.xml", text)
    started = time.perf_counter()
    assert run("set", path, "fieldPermissions", "Obj__c.F19999__c", "editable=true") == 0
    elapsed = time.perf_counter() - started
    assert elapsed < 2, elapsed
    target = "<editable>false</editable>\n        <field>Obj__c.F19999__c"
    assert path.read_bytes() == text.replace(target, target.replace("false", "true")).encode()
    assert capsys.readouterr().out == "Big: fieldPermissions Obj__c.F19999__c editable false->true\n"


def test_dry_run_label_and_missing_file_wording(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Regression: the dry-run header used Windows backslashes and a missing file printed a raw errno."""
    folder = tmp_path / "profiles"
    folder.mkdir()
    write(folder / "Admin.profile-meta.xml", PROFILE)
    assert run("set", folder / "Admin.profile-meta.xml", "fieldPermissions", "Account.Gamma__c", "editable=true", "--dry-run") == 0
    out = capsys.readouterr().out.splitlines()
    assert out[1] == f"--- {(folder / 'Admin.profile-meta.xml').as_posix()}"
    assert run("rows", folder / "Nope.profile-meta.xml") == 1
    assert capsys.readouterr().out == "Nope: error: no such file\n"
