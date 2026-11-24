"""One realistic agent session on a small SFDX repo, driven through ``syn.cli.main`` across every command family."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest

from syn.cli import main

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

NS = 'xmlns="http://soap.sforce.com/2006/04/metadata"'
HEAD = '<?xml version="1.0" encoding="UTF-8"?>\n'
D = "force-app/main/default"


def field_perm(name: str, editable: str = "true") -> str:
    return (
        f"    <fieldPermissions>\n        <editable>{editable}</editable>\n        <field>{name}</field>\n"
        "        <readable>true</readable>\n    </fieldPermissions>\n"
    )


def access(root: str, extra: str = "") -> str:
    fields = "".join(field_perm(f"Invoice__c.{name}") for name in ("Amount__c", "Legacy__c", "Status__c"))
    classes = "    <classAccesses>\n        <apexClass>InvoiceService</apexClass>\n        <enabled>true</enabled>\n    </classAccesses>\n"
    return f"{HEAD}<{root} {NS}>\n{classes}{extra}{fields}</{root}>\n"


FLOW = f"""{HEAD}<Flow {NS}>
    <apiVersion>60.0</apiVersion>
    <decisions>
        <name>Check_Amount</name>
        <label>Check Amount</label>
        <locationX>176</locationX>
        <locationY>300</locationY>
        <defaultConnectorLabel>Default</defaultConnectorLabel>
        <rules>
            <name>Large</name>
            <conditionLogic>and</conditionLogic>
            <conditions>
                <leftValueReference>$Record.Amount__c</leftValueReference>
                <operator>GreaterThan</operator>
                <rightValue>
                    <numberValue>10000.0</numberValue>
                </rightValue>
            </conditions>
            <connector>
                <targetReference>Flag</targetReference>
            </connector>
            <label>Large</label>
        </rules>
    </decisions>
    <label>Invoice After Save</label>
    <processType>AutoLaunchedFlow</processType>
    <recordUpdates>
        <name>Flag</name>
        <label>Flag</label>
        <locationX>176</locationX>
        <locationY>450</locationY>
        <faultConnector>
            <targetReference>Notify</targetReference>
        </faultConnector>
        <inputReference>$Record</inputReference>
    </recordUpdates>
    <actionCalls>
        <name>Notify</name>
        <label>Notify</label>
        <actionName>Notifier</actionName>
        <actionType>apex</actionType>
    </actionCalls>
    <start>
        <connector>
            <targetReference>Check_Amount</targetReference>
        </connector>
        <object>Invoice__c</object>
        <recordTriggerType>CreateAndUpdate</recordTriggerType>
        <scheduledPaths>
            <connector>
                <targetReference>Notify</targetReference>
            </connector>
            <pathType>AsyncAfterCommit</pathType>
        </scheduledPaths>
        <triggerType>RecordAfterSave</triggerType>
    </start>
    <status>Active</status>
</Flow>
"""

LAYOUT = f"""{HEAD}<Layout {NS}>
    <layoutSections>
        <label>Information</label>
        <layoutColumns>
            <layoutItems>
                <behavior>Edit</behavior>
                <field>Amount__c</field>
            </layoutItems>
            <layoutItems>
                <behavior>Edit</behavior>
                <field>Legacy__c</field>
            </layoutItems>
            <layoutItems>
                <behavior>Edit</behavior>
                <field>Status__c</field>
            </layoutItems>
        </layoutColumns>
    </layoutSections>
</Layout>
"""

LABELS = f"""{HEAD}<CustomLabels {NS}>
    <labels>
        <fullName>Shipment_Sent</fullName>
        <language>en_US</language>
        <protected>false</protected>
        <shortDescription>Shipment Sent</shortDescription>
        <value>Envío realizado</value>
    </labels>
</CustomLabels>
"""

VALUES = f"""{HEAD}<GlobalValueSet {NS}>
    <customValue>
        <fullName>North</fullName>
        <default>false</default>
        <label>North</label>
    </customValue>
    <customValue>
        <fullName>South</fullName>
        <default>false</default>
        <label>South</label>
    </customValue>
    <masterLabel>Regions</masterLabel>
    <sorted>false</sorted>
</GlobalValueSet>
"""

SERVICE = """public with sharing class InvoiceService {
    public static void markPaid(List<Invoice__c> rows) {
        for (Invoice__c inv : rows) {
            inv.Amount__c = inv.Amount__c * 2;
            inv.Legacy__c = null;
        }
        update rows;
    }
}
"""

FILES = {
    "sfdx-project.json": '{"packageDirectories": [{"path": "force-app", "default": true}], "sourceApiVersion": "60.0"}\n',
    f"{D}/classes/InvoiceService.cls": SERVICE,
    f"{D}/classes/InvoiceService.cls-meta.xml": f"{HEAD}<ApexClass {NS}>\n    <apiVersion>60.0</apiVersion>\n</ApexClass>\n",
    f"{D}/triggers/InvoiceTrigger.trigger": "trigger InvoiceTrigger on Invoice__c (after update) {\n    InvoiceService.markPaid(Trigger.new);\n}\n",
    f"{D}/objects/Invoice__c/fields/Amount__c.field-meta.xml": f"{HEAD}<CustomField {NS}>\n    <fullName>Amount__c</fullName>\n</CustomField>\n",
    f"{D}/objects/Invoice__c/fields/Legacy__c.field-meta.xml": f"{HEAD}<CustomField {NS}>\n    <fullName>Legacy__c</fullName>\n</CustomField>\n",
    f"{D}/flows/Invoice_After_Save.flow-meta.xml": FLOW,
    f"{D}/layouts/Invoice__c-Invoice Layout.layout-meta.xml": LAYOUT,
    f"{D}/labels/CustomLabels.labels-meta.xml": LABELS,
    f"{D}/globalValueSets/Regions.globalValueSet-meta.xml": VALUES,
    f"{D}/profiles/Admin.profile-meta.xml": access("Profile", "    <custom>false</custom>\n"),
    f"{D}/profiles/Sales User.profile-meta.xml": access("Profile", "    <custom>true</custom>\n"),
    f"{D}/permissionsets/Billing.permissionset-meta.xml": access("PermissionSet"),
}
CRLF = f"{D}/profiles/Sales User.profile-meta.xml"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for rel, text in FILES.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.replace("\n", "\r\n").encode() if rel == CRLF else text.encode())
    for args in (["init", "-q"], ["config", "user.name", "T"], ["config", "user.email", "t@example.com"],
                 ["config", "core.autocrlf", "false"], ["add", "-A"], ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def syn(capsys: pytest.CaptureFixture[str], *argv: str, code: int = 0) -> str:
    assert main(list(argv)) == code
    return capsys.readouterr().out


def original(rel: str) -> str:
    text = FILES[rel]
    return text.replace("\n", "\r\n") if rel == CRLF else text


def test_agent_session(project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    out = syn(capsys, "map")
    assert "apex: 1 classes (0 tests), 1 triggers" in out and "objects by fields: Invoice__c 2" in out

    out = syn(capsys, "find", "Invoice__c.Amount__c")
    assert f"defined: CustomField Invoice__c.Amount__c  {D}/objects/Invoice__c/fields/Amount__c.field-meta.xml" in out
    assert f"  {D}/classes/InvoiceService.cls:4" in out
    assert f"  {D}/flows/Invoice_After_Save.flow-meta.xml:14" in out
    assert "Profile: 2 files, 2 mentions  e.g. Admin, Sales User" in out
    assert "Layout: 1 files" in out
    assert "InvoiceTrigger.trigger:2" in syn(capsys, "find", "InvoiceService")

    access_files = [f"{D}/profiles/*.profile-meta.xml", f"{D}/permissionsets/Billing.permissionset-meta.xml"]
    out = syn(capsys, "add", *access_files, "fieldPermissions", "field=Invoice__c.Discount__c", "editable=true", "readable=true")
    assert out == "Admin, Sales User, Billing: fieldPermissions +Invoice__c.Discount__c\n"
    out = syn(capsys, "set", f"{D}/profiles/Admin.profile-meta.xml", CRLF, "fieldPermissions", "Invoice__c.Amount__c", "editable=false")
    assert out == "Admin, Sales User: fieldPermissions Invoice__c.Amount__c editable true->false\n"
    out = syn(capsys, "del", *access_files, "fieldPermissions", "Invoice__c.Legacy__c")
    assert out == "Admin, Sales User, Billing: fieldPermissions -Invoice__c.Legacy__c\n"
    expected = (
        original(CRLF)
        .replace(field_perm("Invoice__c.Amount__c").replace("\n", "\r\n"),
                 (field_perm("Invoice__c.Amount__c", "false") + field_perm("Invoice__c.Discount__c")).replace("\n", "\r\n"))
        .replace(field_perm("Invoice__c.Legacy__c").replace("\n", "\r\n"), "")
    )
    assert (project / CRLF).read_bytes() == expected.encode()

    values = f"{D}/globalValueSets/Regions.globalValueSet-meta.xml"
    assert syn(capsys, "add", values, "customValue", "fullName=Northeast", "default=false", "label=Northeast", "--after", "North") == (
        "Regions: customValue +Northeast\n"
    )
    northeast = "    <customValue>\n        <fullName>Northeast</fullName>\n        <default>false</default>\n        <label>Northeast</label>\n    </customValue>\n"
    assert (project / values).read_text(encoding="utf-8") == VALUES.replace("    <customValue>\n        <fullName>South", northeast + "    <customValue>\n        <fullName>South")
    labels = f"{D}/labels/CustomLabels.labels-meta.xml"
    syn(capsys, "add", labels, "labels", "fullName=Invoice_Due", "language=en_US", "protected=false", "shortDescription=Due", 'value=Größe "fällig" & offen')
    assert "<value>Größe &quot;fällig&quot; &amp; offen</value>" in (project / labels).read_text(encoding="utf-8")
    assert syn(capsys, "rows", labels, "labels", "--cols", "value").splitlines() == [
        "fullName\tvalue", 'Invoice_Due\tGröße "fällig" & offen', "Shipment_Sent\tEnvío realizado"
    ]

    service = project / D / "classes/InvoiceService.cls"
    service.write_bytes(SERVICE.replace("            inv.Legacy__c = null;\n", "").encode())
    layout = project / D / "layouts/Invoice__c-Invoice Layout.layout-meta.xml"
    legacy_item = "            <layoutItems>\n                <behavior>Edit</behavior>\n                <field>Legacy__c</field>\n            </layoutItems>\n"
    layout.write_bytes(LAYOUT.replace(legacy_item, "").encode())
    flow = project / D / "flows/Invoice_After_Save.flow-meta.xml"
    flow.write_bytes(FLOW.replace("10000.0", "25000.0").replace("<locationX>176</locationX>", "<locationX>200</locationX>").encode())
    (project / D / "objects/Invoice__c/fields/Legacy__c.field-meta.xml").unlink()

    out = syn(capsys, "diff")
    assert "  ~ decisions Check_Amount  rules.conditions.rightValue.numberValue: 10000.0 -> 25000.0" in out
    assert "  - layoutSections.layoutColumns.layoutItems[1] behavior=Edit field=Legacy__c" in out
    assert "M 3 files: Billing.permissionset, Admin.profile, Sales User.profile" in out
    assert "  + fieldPermissions Invoice__c.Discount__c editable=true readable=true" in out
    assert "  - fieldPermissions Invoice__c.Legacy__c" in out
    assert "  ~ fieldPermissions Invoice__c.Amount__c  editable: true -> false" in out
    assert f"M {D}/classes/InvoiceService.cls  +0 -1" in out
    assert f"D {D}/objects/Invoice__c/fields/Legacy__c.field-meta.xml  CustomField Legacy__c" in out
    assert "+ customValue Northeast default=false label=Northeast" in out
    assert out.endswith("total: 9 files (8 modified, 1 deleted), 12 changes\n")
    changed = subprocess.run(["git", "diff", "--name-only"], cwd=project, capture_output=True, text=True, check=True).stdout.split("\n")
    assert len([name for name in changed if name]) == 9
    for path in project.rglob("*.xml"):
        ElementTree.fromstring(path.read_bytes())

    out = syn(capsys, "flow", f"{D}/flows/Invoice_After_Save.flow-meta.xml")
    assert "  scheduled AsyncAfterCommit -> Notify\n  -> Check_Amount\n" in out
    assert "  Large: $Record.Amount__c > 25000.0 -> Flag\n  default -> end\n" in out
    assert "  -> end\n  fault -> Notify\n" in out

    monkeypatch.chdir(project / D / "profiles")
    assert "M ../classes/InvoiceService.cls" in syn(capsys, "diff", "--stat")
    assert "  ../classes/InvoiceService.cls:4" in syn(capsys, "find", "Invoice__c.Amount__c")
    assert syn(capsys, "rows", "Admin.profile-meta.xml", "fieldPermissions", "--where", "editable=false") == (
        "field\teditable\treadable\nInvoice__c.Amount__c\tfalse\ttrue\n"
    )
    assert syn(capsys, "rows", "Nope.profile-meta.xml", code=1) == "Nope: error: no such file\n"
    assert main(["diff", "nosuchrev"]) == 2
    assert "neither a revision nor a path" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["set"])
    capsys.readouterr()


def test_org_condensers(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log = tmp_path / "debug.log"
    lines = ["60.0 APEX_CODE,FINEST"]
    for index in range(30):
        lines += [
            f"02:14:03.1 ({1000 + index * 10})|SOQL_EXECUTE_BEGIN|[4]|Aggregations:0|SELECT Id FROM Shipment__c",
            f"02:14:03.1 ({1005 + index * 10})|SOQL_EXECUTE_END|[4]|Rows:1",
            f"02:14:03.1 ({1006 + index * 10})|USER_DEBUG|[9]|DEBUG|invoice a01xx{index:010d}",
        ]
    lines += ["02:14:04.0 (900000)|FATAL_ERROR|System.LimitException: Too many SOQL queries: 101", "", "Class.A.b: line 3, column 1"]
    log.write_bytes("\r\n".join(lines).encode())
    out = syn(capsys, "log", str(log))
    assert out.startswith("FATAL: System.LimitException: Too many SOQL queries: 101\n  Class.A.b: line 3, column 1\n")
    assert "  line 9: invoice a01xx0000000000 (x30, numbers vary)\n" in out
    assert "  30x, 30 rows, line 4: SELECT Id FROM Shipment__c  <- likely query in a loop" in out

    run = {"result": {"summary": {"outcome": "Failed", "testsRan": 2, "passing": 1, "failing": 1}, "tests": [
        {"Outcome": "Pass", "FullName": "ATest.ok"},
        {"Outcome": "Fail", "FullName": "ATest.bad", "Message": "System.AssertException: boom", "StackTrace": "Class.ATest.bad: line 5, column 1"},
    ]}}
    (tmp_path / "tests.json").write_text(json.dumps(run), encoding="utf-8")
    assert syn(capsys, "tests", str(tmp_path / "tests.json")) == (
        "outcome: Failed  |  2 run, 1 passed, 1 failed\nATest.bad: System.AssertException: boom\n    Class.ATest.bad: line 5, column 1\n"
    )
    deploy = {"result": {"status": "Failed", "numberComponentErrors": 1, "numberComponentsDeployed": 0, "details": {"componentFailures": [
        {"componentType": "ApexClass", "fullName": "A", "fileName": "classes/A.cls", "lineNumber": "3", "problem": "Variable does not exist: x"},
    ]}}}
    (tmp_path / "deploy.json").write_text(json.dumps(deploy), encoding="utf-8")
    assert syn(capsys, "errors", str(tmp_path / "deploy.json")) == (
        "status: Failed  |  components: 0 deployed, 1 failed\nVariable does not exist: x\n  ApexClass A (classes/A.cls:3)\n"
    )


def test_closed_pipe_is_quiet(tmp_path: Path) -> None:
    rows = "".join(field_perm(f"Obj__c.F{index:05d}__c") for index in range(20000))
    path = tmp_path / "Big.profile-meta.xml"
    path.write_text(f"{HEAD}<Profile {NS}>\n{rows}</Profile>\n", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    proc = subprocess.Popen([sys.executable, "-m", "syn", "rows", str(path), "fieldPermissions"], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None and proc.stderr is not None
    proc.stdout.readline()
    proc.stdout.close()
    error = proc.stderr.read()
    proc.wait(timeout=60)
    assert b"Traceback" not in error and b"Exception" not in error


def test_help_lists_each_command_once() -> None:
    """Regression: the top-level help printed the whole command list twice, once as usage."""
    from syn.cli import build_parser

    text = build_parser().format_help()
    assert text.count("rows,set,add") == 0 and text.count("  diff ") == 1
    assert text.startswith("usage: syn [-h] [--version] COMMAND ...")
