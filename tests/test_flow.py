"""Tests for ``syn flow`` on three realistic flow fixtures."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from syn import flow
from syn.cli import SynError

NS = 'xmlns="http://soap.sforce.com/2006/04/metadata"'

RECORD_TRIGGERED = f"""<?xml version="1.0" encoding="UTF-8"?>
<Flow {NS}>
    <apiVersion>60.0</apiVersion>
    <constants>
        <name>Big_Threshold</name>
        <dataType>Number</dataType>
        <value>
            <numberValue>1000.0</numberValue>
        </value>
    </constants>
    <decisions>
        <name>Check_Amount</name>
        <label>Check Amount</label>
        <locationX>314</locationX>
        <locationY>288</locationY>
        <defaultConnector>
            <targetReference>Log_Small</targetReference>
        </defaultConnector>
        <defaultConnectorLabel>Small deal</defaultConnectorLabel>
        <rules>
            <name>Big_Deal</name>
            <conditionLogic>and</conditionLogic>
            <conditions>
                <leftValueReference>$Record.Amount</leftValueReference>
                <operator>GreaterThan</operator>
                <rightValue>
                    <elementReference>Big_Threshold</elementReference>
                </rightValue>
            </conditions>
            <conditions>
                <leftValueReference>$Record.StageName</leftValueReference>
                <operator>EqualTo</operator>
                <rightValue>
                    <stringValue>Closed Won</stringValue>
                </rightValue>
            </conditions>
            <connector>
                <targetReference>Update_Account</targetReference>
            </connector>
            <label>Big Deal</label>
        </rules>
        <rules>
            <name>Medium_Deal</name>
            <conditionLogic>1 AND (2 OR 3)</conditionLogic>
            <conditions>
                <leftValueReference>$Record.Amount</leftValueReference>
                <operator>GreaterThan</operator>
                <rightValue>
                    <numberValue>100.0</numberValue>
                </rightValue>
            </conditions>
            <conditions>
                <leftValueReference>$Record.Type</leftValueReference>
                <operator>EqualTo</operator>
                <rightValue>
                    <stringValue>New</stringValue>
                </rightValue>
            </conditions>
            <conditions>
                <leftValueReference>$Record.Description</leftValueReference>
                <operator>IsNull</operator>
                <rightValue>
                    <booleanValue>false</booleanValue>
                </rightValue>
            </conditions>
            <connector>
                <targetReference>Log_Small</targetReference>
            </connector>
            <label>Medium Deal</label>
        </rules>
    </decisions>
    <description>Marks the account hot when a big deal closes.</description>
    <formulas>
        <name>Discount</name>
        <dataType>Number</dataType>
        <expression>{{!$Record.Amount}} * 0.1</expression>
        <scale>2</scale>
    </formulas>
    <interviewLabel>Opp Closed {{!$Flow.CurrentDateTime}}</interviewLabel>
    <label>Opp Closed Won</label>
    <processMetadataValues>
        <name>BuilderType</name>
        <value>
            <stringValue>LightningFlowBuilder</stringValue>
        </value>
    </processMetadataValues>
    <processType>AutoLaunchedFlow</processType>
    <recordUpdates>
        <name>Update_Account</name>
        <label>Update Account</label>
        <locationX>50</locationX>
        <locationY>408</locationY>
        <faultConnector>
            <targetReference>Handle_Fault</targetReference>
        </faultConnector>
        <filterLogic>and</filterLogic>
        <filters>
            <field>Id</field>
            <operator>EqualTo</operator>
            <value>
                <elementReference>$Record.AccountId</elementReference>
            </value>
        </filters>
        <inputAssignments>
            <field>Rating</field>
            <value>
                <stringValue>Hot</stringValue>
            </value>
        </inputAssignments>
        <inputAssignments>
            <field>Description</field>
            <value>
                <elementReference>Note</elementReference>
            </value>
        </inputAssignments>
        <object>Account</object>
    </recordUpdates>
    <assignments>
        <name>Handle_Fault</name>
        <label>Handle Fault</label>
        <locationX>50</locationX>
        <locationY>528</locationY>
        <assignmentItems>
            <assignToReference>Failed</assignToReference>
            <operator>Assign</operator>
            <value>
                <booleanValue>true</booleanValue>
            </value>
        </assignmentItems>
    </assignments>
    <assignments>
        <name>Log_Small</name>
        <label>Log Small</label>
        <locationX>578</locationX>
        <locationY>408</locationY>
        <assignmentItems>
            <assignToReference>Total</assignToReference>
            <operator>Add</operator>
            <value>
                <numberValue>1.0</numberValue>
            </value>
        </assignmentItems>
    </assignments>
    <assignments>
        <name>Leftover</name>
        <label>Leftover</label>
        <locationX>800</locationX>
        <locationY>408</locationY>
        <assignmentItems>
            <assignToReference>Total</assignToReference>
            <operator>Assign</operator>
            <value>
                <numberValue>0.0</numberValue>
            </value>
        </assignmentItems>
    </assignments>
    <runInMode>SystemModeWithoutSharing</runInMode>
    <start>
        <locationX>188</locationX>
        <locationY>0</locationY>
        <connector>
            <targetReference>Check_Amount</targetReference>
        </connector>
        <filterLogic>and</filterLogic>
        <filters>
            <field>StageName</field>
            <operator>EqualTo</operator>
            <value>
                <stringValue>Closed Won</stringValue>
            </value>
        </filters>
        <object>Opportunity</object>
        <recordTriggerType>Update</recordTriggerType>
        <triggerType>RecordAfterSave</triggerType>
    </start>
    <status>Active</status>
    <textTemplates>
        <name>Note</name>
        <isViewedAsPlainText>true</isViewedAsPlainText>
        <text>Closed won by {{!$User.Name}}</text>
    </textTemplates>
    <variables>
        <name>Failed</name>
        <dataType>Boolean</dataType>
        <isCollection>false</isCollection>
        <isInput>false</isInput>
        <isOutput>true</isOutput>
        <value>
            <booleanValue>false</booleanValue>
        </value>
    </variables>
    <variables>
        <name>Total</name>
        <dataType>Number</dataType>
        <isCollection>false</isCollection>
        <isInput>false</isInput>
        <isOutput>false</isOutput>
        <scale>0</scale>
    </variables>
</Flow>
"""

SCREEN_FLOW = f"""<?xml version="1.0" encoding="UTF-8"?>
<Flow {NS}>
    <apiVersion>59.0</apiVersion>
    <assignments>
        <name>Add_To_Total</name>
        <label>Add To Total</label>
        <locationX>176</locationX>
        <locationY>458</locationY>
        <assignmentItems>
            <assignToReference>Total</assignToReference>
            <operator>Add</operator>
            <value>
                <elementReference>Loop_Items.Amount</elementReference>
            </value>
        </assignmentItems>
        <connector>
            <targetReference>Loop_Items</targetReference>
        </connector>
    </assignments>
    <label>Sum Items</label>
    <loops>
        <name>Loop_Items</name>
        <label>Loop Items</label>
        <locationX>176</locationX>
        <locationY>350</locationY>
        <collectionReference>Items</collectionReference>
        <iterationOrder>Asc</iterationOrder>
        <nextValueConnector>
            <targetReference>Add_To_Total</targetReference>
        </nextValueConnector>
        <noMoreValuesConnector>
            <targetReference>Show_Result</targetReference>
        </noMoreValuesConnector>
    </loops>
    <processType>Flow</processType>
    <screens>
        <name>Pick_Items</name>
        <label>Pick Items</label>
        <locationX>176</locationX>
        <locationY>242</locationY>
        <allowBack>true</allowBack>
        <allowFinish>true</allowFinish>
        <connector>
            <targetReference>Loop_Items</targetReference>
        </connector>
        <fields>
            <name>Intro</name>
            <fieldText>&lt;p&gt;Pick the items to add up&lt;/p&gt;</fieldText>
            <fieldType>DisplayText</fieldType>
        </fields>
        <fields>
            <name>Section1</name>
            <fieldType>RegionContainer</fieldType>
            <fields>
                <name>Section1_Column1</name>
                <fieldType>Region</fieldType>
                <fields>
                    <name>Note_Input</name>
                    <dataType>String</dataType>
                    <fieldType>InputField</fieldType>
                    <isRequired>false</isRequired>
                </fields>
            </fields>
        </fields>
        <showFooter>true</showFooter>
        <showHeader>true</showHeader>
    </screens>
    <screens>
        <name>Show_Result</name>
        <label>Show Result</label>
        <locationX>176</locationX>
        <locationY>650</locationY>
        <allowBack>false</allowBack>
        <allowFinish>true</allowFinish>
        <fields>
            <name>Result_Text</name>
            <fieldText>&lt;p&gt;Total: {{!Total}}&lt;/p&gt;</fieldText>
            <fieldType>DisplayText</fieldType>
        </fields>
    </screens>
    <start>
        <locationX>50</locationX>
        <locationY>0</locationY>
        <connector>
            <targetReference>Pick_Items</targetReference>
        </connector>
    </start>
    <status>Draft</status>
    <variables>
        <name>Items</name>
        <dataType>SObject</dataType>
        <isCollection>true</isCollection>
        <isInput>true</isInput>
        <isOutput>false</isOutput>
        <objectType>OpportunityLineItem</objectType>
    </variables>
    <variables>
        <name>Total</name>
        <dataType>Number</dataType>
        <isCollection>false</isCollection>
        <isInput>false</isInput>
        <isOutput>true</isOutput>
        <scale>2</scale>
        <value>
            <numberValue>0.0</numberValue>
        </value>
    </variables>
</Flow>
"""

CALLS_FLOW = f"""<?xml version="1.0" encoding="UTF-8"?>
<Flow {NS}>
    <actionCalls>
        <name>Send_Mail</name>
        <label>Send Mail</label>
        <locationX>176</locationX>
        <locationY>350</locationY>
        <actionName>emailSimple</actionName>
        <actionType>emailSimple</actionType>
        <connector>
            <targetReference>Wait_A_Day</targetReference>
        </connector>
        <inputParameters>
            <name>emailBody</name>
            <value>
                <elementReference>Body</elementReference>
            </value>
        </inputParameters>
        <inputParameters>
            <name>emailAddresses</name>
            <value>
                <stringValue>ops@example.com</stringValue>
            </value>
        </inputParameters>
        <outputParameters>
            <assignToReference>MailSent</assignToReference>
            <name>success</name>
        </outputParameters>
    </actionCalls>
    <apiVersion>61.0</apiVersion>
    <label>Notify And Delegate</label>
    <processType>AutoLaunchedFlow</processType>
    <start>
        <locationX>50</locationX>
        <locationY>0</locationY>
        <connector>
            <targetReference>Call_Child</targetReference>
        </connector>
    </start>
    <status>Active</status>
    <subflows>
        <name>Call_Child</name>
        <label>Call Child</label>
        <locationX>176</locationX>
        <locationY>242</locationY>
        <connector>
            <targetReference>Send_Mail</targetReference>
        </connector>
        <flowName>Child_Flow</flowName>
        <inputAssignments>
            <name>AccountId</name>
            <value>
                <elementReference>recordId</elementReference>
            </value>
        </inputAssignments>
        <outputAssignments>
            <assignToReference>ChildResult</assignToReference>
            <name>result</name>
        </outputAssignments>
    </subflows>
    <variables>
        <name>Body</name>
        <dataType>String</dataType>
        <isCollection>false</isCollection>
        <isInput>false</isInput>
        <isOutput>false</isOutput>
    </variables>
    <waits>
        <name>Wait_A_Day</name>
        <label>Wait A Day</label>
        <locationX>176</locationX>
        <locationY>458</locationY>
        <defaultConnectorLabel>Default Path</defaultConnectorLabel>
        <waitEvents>
            <name>One_Day</name>
            <conditionLogic>and</conditionLogic>
            <connector>
                <targetReference>Done</targetReference>
            </connector>
            <eventType>AlarmEvent</eventType>
            <inputParameters>
                <name>AlarmTime</name>
                <value>
                    <elementReference>Tomorrow</elementReference>
                </value>
            </inputParameters>
            <label>One Day</label>
        </waitEvents>
    </waits>
    <customErrors>
        <name>Done</name>
        <label>Done</label>
        <locationX>176</locationX>
        <locationY>566</locationY>
        <customErrorMessages>
            <errorMessage>Finished early</errorMessage>
            <isFieldError>false</isFieldError>
        </customErrorMessages>
    </customErrors>
</Flow>
"""


def run_flow(capsys: pytest.CaptureFixture[str], path: Path, *extra: str) -> str:
    parser = argparse.ArgumentParser()
    flow.register(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["flow", str(path), *extra])
    assert args.run(args) == 0
    return capsys.readouterr().out


@pytest.fixture
def record_flow(tmp_path: Path) -> Path:
    path = tmp_path / "Opp.flow-meta.xml"
    path.write_text(RECORD_TRIGGERED, encoding="utf-8")
    return path


def test_outline_follows_execution_order(capsys: pytest.CaptureFixture[str], record_flow: Path) -> None:
    out = run_flow(capsys, record_flow)
    order = [out.index(name) for name in ("start (start)", "Check_Amount (decision)", "Update_Account (recordUpdate)", "Handle_Fault (assignment)", "Log_Small (assignment)")]
    assert order == sorted(order)
    assert "trigger RecordAfterSave on Opportunity (Update)" in out
    assert 'entry StageName = "Closed Won"' in out


def test_decision_conditions_and_logic(capsys: pytest.CaptureFixture[str], record_flow: Path) -> None:
    out = run_flow(capsys, record_flow)
    assert '$Record.Amount > Big_Threshold AND $Record.StageName = "Closed Won" -> Update_Account' in out
    assert '($Record.Amount > 100.0) AND (($Record.Type = "New") OR ($Record.Description is not null)) -> Log_Small' in out
    assert "default -> Log_Small" in out


def test_record_update_and_fault_path(capsys: pytest.CaptureFixture[str], record_flow: Path) -> None:
    out = run_flow(capsys, record_flow)
    assert "object Account" in out
    assert "where Id = $Record.AccountId" in out
    assert 'set Rating = "Hot", Description = Note' in out
    assert "fault -> Handle_Fault" in out
    assert "Failed = true" in out
    assert "Total += 1.0" in out


def test_element_printed_once(capsys: pytest.CaptureFixture[str], record_flow: Path) -> None:
    out = run_flow(capsys, record_flow)
    assert out.count("Log_Small (assignment)") == 1
    assert out.count("-> Log_Small") == 2


def test_unreachable_resources_and_meta(capsys: pytest.CaptureFixture[str], record_flow: Path) -> None:
    out = run_flow(capsys, record_flow)
    assert "unreachable:\n  Leftover (assignment)" in out
    assert "Leftover (assignment)\n  Total" not in out
    assert "var Failed: Boolean output = false" in out
    assert "var Total: Number" in out
    assert "formula Discount: Number = {!$Record.Amount} * 0.1" in out
    assert "constant Big_Threshold: Number = 1000.0" in out
    assert "template Note: 27 chars" in out
    assert 'flow label="Opp Closed Won" processType=AutoLaunchedFlow status=Active apiVersion=60.0 runInMode=SystemModeWithoutSharing' in out


@pytest.mark.parametrize("fixture", [RECORD_TRIGGERED, SCREEN_FLOW, CALLS_FLOW])
def test_noise_never_printed_and_outline_is_small(capsys: pytest.CaptureFixture[str], tmp_path: Path, fixture: str) -> None:
    path = tmp_path / "f.flow-meta.xml"
    path.write_text(fixture, encoding="utf-8")
    out = run_flow(capsys, path)
    for noise in ("locationX", "locationY", "processMetadataValues", "BuilderType"):
        assert noise not in out
    assert len(out) * 3 < len(fixture)


def test_screen_flow_with_loop(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    path = tmp_path / "s.flow-meta.xml"
    path.write_text(SCREEN_FLOW, encoding="utf-8")
    out = run_flow(capsys, path)
    assert "fields Intro:DisplayText, Section1:RegionContainer, Section1_Column1:Region, Note_Input:InputField" in out
    assert "over Items, asc" in out
    assert "each -> Add_To_Total" in out
    assert "done -> Show_Result" in out
    assert out.count("Loop_Items (loop)") == 1
    assert "Total += Loop_Items.Amount" in out
    assert "-> Loop_Items" in out
    assert out.index("Add_To_Total (assignment)") < out.index("Show_Result (screen)")
    assert "var Items: SObject(OpportunityLineItem) collection input" in out
    assert "unreachable" not in out


def test_subflow_action_wait_and_custom_error(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    path = tmp_path / "c.flow-meta.xml"
    path.write_text(CALLS_FLOW, encoding="utf-8")
    out = run_flow(capsys, path)
    assert "flow Child_Flow" in out
    assert "in AccountId = recordId" in out
    assert "out ChildResult <- result" in out
    assert "action emailSimple (emailSimple)" in out
    assert 'in emailBody = Body, emailAddresses = "ops@example.com"' in out
    assert "out MailSent <- success" in out
    assert "event One_Day (AlarmEvent) AlarmTime=Tomorrow -> Done" in out
    assert "error Finished early" in out
    order = [out.index(n) for n in ("Call_Child (subflow)", "Send_Mail (actionCall)", "Wait_A_Day (wait)", "Done (customError)")]
    assert order == sorted(order)


def test_detail_view(capsys: pytest.CaptureFixture[str], record_flow: Path) -> None:
    out = run_flow(capsys, record_flow, "Update_Account")
    assert out.startswith("Update_Account (recordUpdate)")
    assert "label: Update Account" in out
    assert "faultConnector: targetReference=Handle_Fault" in out
    assert 'inputAssignments: field=Rating; value="Hot"' in out
    assert "reached from: Check_Amount" in out
    assert "locationX" not in out


def test_detail_view_is_case_insensitive_and_covers_start(capsys: pytest.CaptureFixture[str], record_flow: Path) -> None:
    assert "recordTriggerType: Update" in run_flow(capsys, record_flow, "start")
    assert run_flow(capsys, record_flow, "update_account").startswith("Update_Account")


def test_detail_unknown_element_lists_names(record_flow: Path) -> None:
    with pytest.raises(SynError, match="Check_Amount"):
        flow.detail(flow.load(str(record_flow)), "Nope")


def test_unknown_kind_and_unknown_fields_do_not_raise(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    xml = f"""<Flow {NS}>
    <start><connector><targetReference>Odd</targetReference></connector></start>
    <orchestratedStages>
        <name>Odd</name>
        <futureField>keep me</futureField>
        <connector><targetReference>Gone</targetReference></connector>
    </orchestratedStages>
    <status>Active</status>
</Flow>"""
    path = tmp_path / "u.flow-meta.xml"
    path.write_text(xml, encoding="utf-8")
    out = run_flow(capsys, path)
    assert "Odd (orchestratedStage)" in out
    assert "Gone (missing)" in out
    assert "futureField: keep me" in run_flow(capsys, path, "Odd")


def test_errors_are_syn_errors(tmp_path: Path) -> None:
    with pytest.raises(SynError):
        flow.load(str(tmp_path / "missing.xml"))
    bad = tmp_path / "bad.xml"
    bad.write_text("<Flow", encoding="utf-8")
    with pytest.raises(SynError):
        flow.load(str(bad))
    other = tmp_path / "other.xml"
    other.write_text("<Profile/>", encoding="utf-8")
    with pytest.raises(SynError, match="not a Flow"):
        flow.load(str(other))


def test_async_path_is_labelled_and_detail_skips_the_name(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """Regression: a run-asynchronously path has no name, so its target printed as a second unlabelled start edge."""
    path = tmp_path / "Async.flow-meta.xml"
    path.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<Flow {NS}>
    <actionCalls>
        <name>Notify</name>
        <label>Notify</label>
        <actionName>Notifier</actionName>
        <actionType>apex</actionType>
    </actionCalls>
    <actionCalls>
        <name>Main</name>
        <label>Main</label>
        <actionName>Worker</actionName>
        <actionType>apex</actionType>
    </actionCalls>
    <start>
        <connector>
            <targetReference>Main</targetReference>
        </connector>
        <object>Account</object>
        <scheduledPaths>
            <connector>
                <targetReference>Notify</targetReference>
            </connector>
            <pathType>AsyncAfterCommit</pathType>
        </scheduledPaths>
        <triggerType>RecordAfterSave</triggerType>
    </start>
</Flow>
""", encoding="utf-8")
    out = run_flow(capsys, path).splitlines()
    assert out[:5] == [
        "start (start)",
        "  trigger RecordAfterSave on Account",
        "  scheduled AsyncAfterCommit -> Notify",
        "  -> Main",
        "Main (actionCall)",
    ]
    detail = run_flow(capsys, path, "Main").splitlines()
    assert detail[:2] == ["Main (actionCall)", "  label: Main"]
