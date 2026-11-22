"""Tests for ``syn find``, ``syn map`` and ``syn index`` on a small SFDX repo built in ``tmp_path``."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from syn import find

DEFAULT = "force-app/main/default"


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def profile(field: str = "Account.Region__c") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<Profile xmlns="http://soap.sforce.com/2006/04/metadata">\n'
        f"    <fieldPermissions>\n        <editable>true</editable>\n        <field>{field}</field>\n"
        "        <readable>true</readable>\n    </fieldPermissions>\n</Profile>\n"
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    write(tmp_path, "sfdx-project.json", '{"packageDirectories": [{"path": "force-app", "default": true}], "sourceApiVersion": "60.0"}')
    write(tmp_path, f"{DEFAULT}/classes/InvoiceService.cls", (
        "public with sharing class InvoiceService {\n"
        "    public static void run(List<Account> accounts) {\n"
        "        for (Account a : accounts) {\n"
        "            String region = Account.Region__c.getDescribe().getName();\n"
        "        }\n"
        "    }\n"
        "}\n"
    ))
    write(tmp_path, f"{DEFAULT}/classes/InvoiceService.cls-meta.xml", "<ApexClass><apiVersion>60.0</apiVersion></ApexClass>")
    write(tmp_path, f"{DEFAULT}/classes/InvoiceServiceTest.cls", (
        "@IsTest\nprivate class InvoiceServiceTest {\n    @IsTest static void works() {\n"
        "        Account a = new Account(Region__c = 'EU');\n        InvoiceService.run(new List<Account>{a});\n    }\n}\n"
    ))
    write(tmp_path, f"{DEFAULT}/triggers/AccountTrigger.trigger", "trigger AccountTrigger on Account (before insert) {\n    InvoiceService.run(Trigger.new);\n}\n")
    write(tmp_path, f"{DEFAULT}/objects/Account/Account.object-meta.xml", "<CustomObject><label>Account</label></CustomObject>")
    write(tmp_path, f"{DEFAULT}/objects/Account/fields/Region__c.field-meta.xml", "<CustomField>\n    <fullName>Region__c</fullName>\n    <type>Text</type>\n</CustomField>\n")
    write(tmp_path, f"{DEFAULT}/objects/Account/fields/Tier__c.field-meta.xml", "<CustomField><fullName>Tier__c</fullName></CustomField>")
    write(tmp_path, f"{DEFAULT}/lwc/accountCard/accountCard.js", "import REGION from '@salesforce/schema/Account.Region__c';\nexport default class AccountCard {}\n")
    write(tmp_path, f"{DEFAULT}/lwc/accountCard/accountCard.html", "<template>\n  <p>{region}</p>\n</template>\n")
    write(tmp_path, f"{DEFAULT}/flows/Region_Router.flow-meta.xml", "<Flow>\n  <elements>\n    <field>Account.Region__c</field>\n  </elements>\n</Flow>\n")
    for name in ("Admin", "Sales", "Support"):
        write(tmp_path, f"{DEFAULT}/profiles/{name}.profile-meta.xml", profile())
    write(tmp_path, f"{DEFAULT}/permissionsets/Regional.permissionset-meta.xml", profile())
    write(tmp_path, f"{DEFAULT}/layouts/Account-Layout.layout-meta.xml", "<Layout><layoutItems><field>Tier__c</field></layoutItems></Layout>")
    write(tmp_path, f"{DEFAULT}/weird/notes.txt", "nothing\n")
    return tmp_path


def run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> str:
    parser = argparse.ArgumentParser(prog="syn")
    find.register(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(argv)
    assert args.run(args) == 0
    return capsys.readouterr().out


def test_component_typing() -> None:
    assert find.classify("force-app/main/default/classes/Foo.cls") == ("ApexClass", "Foo")
    assert find.classify("force-app/main/default/classes/Foo.cls-meta.xml") == ("ApexClass", "Foo")
    assert find.classify("x/triggers/Foo.trigger") == ("ApexTrigger", "Foo")
    assert find.classify("x/objects/Account/fields/Region__c.field-meta.xml") == ("CustomField", "Account.Region__c")
    assert find.classify("x/objects/Account/Account.object-meta.xml") == ("CustomObject", "Account")
    assert find.classify("x/objects/Order__c/validationRules/Need.validationRule-meta.xml") == ("ValidationRule", "Order__c.Need")
    assert find.classify("x/objects/Order__c/recordTypes/Std.recordType-meta.xml") == ("RecordType", "Order__c.Std")
    assert find.classify("x/lwc/foo/foo.js") == ("LightningComponentBundle", "foo")
    assert find.classify("x/lwc/jsconfig.json")[0] == "other"
    assert find.classify("x/aura/bar/barController.js") == ("AuraDefinitionBundle", "bar")
    assert find.classify("x/flows/Go.flow-meta.xml") == ("Flow", "Go")
    assert find.classify("x/profiles/Admin.profile-meta.xml") == ("Profile", "Admin")
    assert find.classify("x/layouts/Account-Account Layout.layout-meta.xml") == ("Layout", "Account-Account Layout")
    assert find.classify("x/staticresources/logo.resource-meta.xml") == ("StaticResource", "logo")
    assert find.classify("x/staticresources/bundle/app.js") == ("StaticResource", "bundle")
    assert find.classify("x/mystery/Thing.widget-meta.xml") == ("Widget", "Thing")
    assert find.classify("x/mystery/readme.txt")[0] == "other"


def test_tokens_expand_and_filter() -> None:
    assert set(find.expand("account.region__c")) == {"account.region__c", "account", "region__c"}
    assert set(find.expand("a.b.cde")) == {"a.b.cde", "cde", "a.b", "b.cde"}
    assert find.expand("true") == ()
    assert find.expand("12345") == ()
    assert find.expand("ab") == ()


def test_find_groups_mentions(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(repo)
    out = run(["find", "Account.Region__c"], capsys)
    first = out.splitlines()[0]
    assert first.startswith("defined: CustomField Account.Region__c")
    assert f"{DEFAULT}/objects/Account/fields/Region__c.field-meta.xml" in first
    assert "Profile: 3 files, 3 mentions  e.g. Admin, Sales, Support" in out
    assert "profiles/" not in out
    assert "PermissionSet: 1 files, 1 mentions  e.g. Regional" in out
    assert f"{DEFAULT}/classes/InvoiceService.cls:4" in out
    assert f"{DEFAULT}/lwc/accountCard/accountCard.js:1" in out
    assert f"{DEFAULT}/flows/Region_Router.flow-meta.xml:3" in out
    assert "Region__c.field-meta.xml:" not in out
    assert out.index("ApexClass:") < out.index("Profile:")


def test_find_short_name_and_case(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(repo)
    short = run(["find", "Region__c"], capsys)
    assert "defined: CustomField Account.Region__c" in short
    assert f"{DEFAULT}/classes/InvoiceServiceTest.cls:4" in short
    assert run(["find", "REGION__C"], capsys) == short
    assert run(["find", "region__c"], capsys) == short


def test_find_type_filter_and_glob(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(repo)
    only = run(["find", "Region__c", "--type", "ApexClass"], capsys)
    assert "ApexClass:" in only and "Profile" not in only and "Flow" not in only
    alias = run(["find", "Region__c", "--type", "lwc,flow"], capsys)
    assert "LightningComponentBundle:" in alias and "Flow:" in alias and "ApexClass" not in alias
    scoped = run(["find", "Region__c", "--in", "*/classes/*"], capsys)
    assert "InvoiceService.cls" in scoped and "Flow:" not in scoped


def test_find_lines_and_limit(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(repo)
    out = run(["find", "InvoiceService", "--lines"], capsys)
    assert "defined: ApexClass InvoiceService" in out
    assert "    5: InvoiceService.run(new List<Account>{a});" in out
    assert "    2: InvoiceService.run(Trigger.new);" in out
    plain = run(["find", "InvoiceService"], capsys)
    assert "Trigger.new" not in plain
    limited = run(["find", "Region__c", "--limit", "1"], capsys)
    assert "+1 more" in limited
    dotted = run(["find", "InvoiceService.run", "--lines"], capsys)
    assert "AccountTrigger.trigger:2" in dotted


def test_find_miss_suggests(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(repo)
    out = run(["find", "Invoice"], capsys)
    assert out.startswith("no mentions of Invoice")
    assert "close names: " in out and "InvoiceService" in out
    nothing = run(["find", "Zzzzzz"], capsys)
    assert nothing.strip() == "no mentions of Zzzzzz"


def test_find_rejects_bad_query(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from syn.cli import SynError

    monkeypatch.chdir(repo)
    parser = argparse.ArgumentParser()
    find.register(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["find", "a b"])
    with pytest.raises(SynError):
        args.run(args)


def test_incremental_refresh(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(repo)
    before = run(["find", "Region__c"], capsys)
    assert "AccountTrigger.trigger" not in before and "InvoiceServiceTest.cls" in before

    trigger = repo / DEFAULT / "triggers/AccountTrigger.trigger"
    trigger.write_text("trigger AccountTrigger on Account (before insert) {\n    Object o = Account.Region__c;\n}\n", encoding="utf-8")
    stat = trigger.stat()
    os.utime(trigger, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    (repo / DEFAULT / "classes/InvoiceServiceTest.cls").unlink()
    write(repo, f"{DEFAULT}/classes/NewThing.cls", "public class NewThing {\n    String r = Account.Region__c;\n}\n")

    after = run(["find", "Region__c"], capsys)
    assert "AccountTrigger.trigger:2" in after
    assert "InvoiceServiceTest.cls" not in after
    assert "NewThing.cls:2" in after
    assert "defined: ApexClass NewThing" in run(["find", "NewThing"], capsys)


def test_index_rebuild(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(repo)
    out = run(["index"], capsys)
    assert out.startswith("indexed ") and " files, " in out and "token rows in" in out
    assert (repo / ".syn" / "index.db").is_file()
    assert (repo / ".syn" / ".gitignore").is_file()
    assert "Profile: 3 files" in run(["find", "Region__c"], capsys)


def test_root_from_subfolder(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(repo / DEFAULT / "classes")
    assert "defined: ApexClass InvoiceService" in run(["find", "InvoiceService"], capsys)
    assert (repo / ".syn" / "index.db").is_file()
    assert not (repo / DEFAULT / "classes" / ".syn").exists()


def test_map(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(repo)
    out = run(["map"], capsys)
    assert len(out.splitlines()) <= 40
    assert "source API 60.0" in out
    assert "packages: force-app" in out
    assert "apex: 2 classes (1 tests), 1 triggers" in out
    assert "objects by fields: Account 2" in out
    assert "ApexClass 2" in out and "Profile 3" in out and "CustomField 2" in out
    assert "largest files:" in out
    assert "force-app/main/default/" in out
    assert "\\" not in out


def test_map_without_sfdx_uses_git_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / ".git").mkdir()
    write(tmp_path, "src/classes/A.cls", "public class A {}\n")
    write(tmp_path, "node_modules/x/classes/B.cls", "public class B {}\n")
    sub = tmp_path / "src"
    monkeypatch.chdir(sub)
    out = run(["map"], capsys)
    assert "(no sfdx-project.json, whole repo)" in out
    assert "apex: 1 classes (0 tests), 0 triggers" in out


def test_binary_and_large_files_are_skipped(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(repo)
    (repo / DEFAULT / "staticresources").mkdir()
    (repo / DEFAULT / "staticresources" / "blob.bin").write_bytes(b"Region__c\0\0Region__c")
    (repo / DEFAULT / "staticresources" / "big.js").write_text("var Region__c;" * 200_000, encoding="utf-8")
    out = run(["find", "Region__c"], capsys)
    assert "blob.bin" not in out and "big.js" not in out
    assert "StaticResource" in run(["map"], capsys)


def test_scale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    write(tmp_path, "sfdx-project.json", '{"packageDirectories": [{"path": "force-app"}]}')
    classes = tmp_path / DEFAULT / "classes"
    classes.mkdir(parents=True)
    for index in range(3000):
        body = f"public class Gen{index} {{\n    Integer counter{index} = {index};\n    Account.Region__c x;\n}}\n"
        (classes / f"Gen{index}.cls").write_text(body, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    started = time.perf_counter()
    out = run(["find", "Gen1234"], capsys)
    assert time.perf_counter() - started < 20
    assert "defined: ApexClass Gen1234" in out
    started = time.perf_counter()
    out = run(["find", "Region__c", "--limit", "2"], capsys)
    assert time.perf_counter() - started < 3
    assert "ApexClass: 3000 files, 3000 mentions" in out


def test_dotted_field_finds_unqualified_mentions(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Regression: ``find Invoice__c.Amount__c`` used to miss ``inv.Amount__c`` in Apex and ``Amount__c`` in a layout."""
    write(repo, f"{DEFAULT}/objects/Invoice__c/fields/Amount__c.field-meta.xml", "<CustomField><fullName>Amount__c</fullName></CustomField>")
    write(repo, f"{DEFAULT}/classes/Billing.cls", "public class Billing {\n    void f(Invoice__c inv) {\n        inv.Amount__c = 1;\n    }\n}\n")
    write(repo, f"{DEFAULT}/classes/Other.cls", "public class Other {\n    void f(Quote__c q) {\n        q.Amount__c = 1;\n    }\n}\n")
    write(repo, f"{DEFAULT}/layouts/Invoice__c-Invoice Layout.layout-meta.xml", "<Layout>\n<field>Amount__c</field>\n</Layout>\n")
    write(repo, f"{DEFAULT}/profiles/Quoter.profile-meta.xml", profile("Quote__c.Amount__c"))
    write(repo, f"{DEFAULT}/classes/Both.cls", "public class Both {\n    Invoice__c i;\n    void f(Quote__c q) {\n        q.Amount__c = 1;\n    }\n}\n")
    monkeypatch.chdir(repo)
    out = run(["find", "Invoice__c.Amount__c"], capsys)
    assert f"  {DEFAULT}/classes/Billing.cls:3 ?" in out
    assert f"  {DEFAULT}/classes/Both.cls:4 ?" in out
    assert "Layout: 1 files, 1 mentions  e.g. Invoice__c-Invoice Layout\n" in out
    assert "Other.cls" not in out and "Quoter" not in out
    assert out.endswith("? = bare Amount__c in a file that also mentions Invoice__c: may be another object's field\n")
    exact = run(["find", "Account.Region__c"], capsys)
    assert f"  {DEFAULT}/classes/InvoiceService.cls:4\n" in exact
    assert f"  {DEFAULT}/classes/InvoiceServiceTest.cls:4 ?\n" in exact
    assert "AccountTrigger.trigger:2" in run(["find", "--type", "trigger", "InvoiceService"], capsys)
    monkeypatch.chdir(repo / DEFAULT)
    scoped = run(["find", "Invoice__c.Amount__c", "--in", "classes/*"], capsys)
    assert "  classes/Billing.cls:3 ?" in scoped and "Layout" not in scoped


def test_no_project_marker_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: outside any project ``map`` used to index the current folder tree and write ``.syn`` there."""
    from syn.cli import SynError

    monkeypatch.setattr(Path, "exists", lambda self: False)
    with pytest.raises(SynError, match="no sfdx-project.json or .git"):
        find.find_root(tmp_path)


def test_paths_are_relative_to_the_current_folder(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Regression: from a subfolder, printed paths were root-relative and could not be passed back to syn."""
    monkeypatch.chdir(repo / DEFAULT / "classes")
    out = run(["find", "InvoiceService"], capsys)
    assert "defined: ApexClass InvoiceService  InvoiceService.cls" in out
    assert "  ../triggers/AccountTrigger.trigger:2" in out
    assert find.relative_path("a/b/c.xml", "a/d") == "../b/c.xml"
    assert find.relative_path("x.json", "") == "x.json"


def test_parallel_refresh_does_not_crash(repo: Path) -> None:
    """Regression: two syn processes refreshing a new index at once failed on a duplicate id."""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    procs = [
        subprocess.Popen([sys.executable, "-m", "syn", "find", name], cwd=repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for name in ("InvoiceService", "Region__c", "AccountTrigger", "Tier__c")
    ]
    results = [proc.communicate(timeout=60) for proc in procs]
    assert [proc.returncode for proc in procs] == [0, 0, 0, 0]
    assert all(not err for _, err in results)


def test_find_sees_lwc_used_as_a_markup_tag(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A component is used in markup as ``<c-kebab-case>``, which never spells its camelCase bundle name."""
    lwc = "force-app/main/default/lwc"
    write(repo, f"{lwc}/invoiceSummary/invoiceSummary.js", "export default class InvoiceSummary {}\n")
    write(repo, f"{lwc}/parentCmp/parentCmp.html", "<template>\n<c-invoice-summary></c-invoice-summary>\n</template>\n")
    monkeypatch.chdir(repo)
    out = run(["find", "invoiceSummary"], capsys)
    assert "parentCmp.html:2" in out

