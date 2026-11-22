"""``syn flow``: a Salesforce Flow shown as the graph it is.

A ``*.flow-meta.xml`` file stores the graph as a flat, alphabetically sorted
list of elements buried in screen coordinates. The outline walks it from
``<start>`` along every connector, prints each element once with the facts
needed to understand it, then lists the resources and the process metadata.
Element kinds are derived from the XML tag name, so an element type this
module has never seen is still listed, and the detail view dumps every field
generically instead of dropping the ones it does not recognise.
"""

from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from syn.cli import SynError

NOISE = frozenset({"locationX", "locationY", "processMetadataValues"})
VALUE_TAGS = frozenset(
    {"elementReference", "stringValue", "booleanValue", "numberValue", "dateValue", "dateTimeValue"}
)
RESOURCES = {
    "variables": "var",
    "formulas": "formula",
    "constants": "constant",
    "textTemplates": "template",
    "choices": "choice",
    "dynamicChoiceSets": "dynamicChoice",
    "stages": "stage",
}
META_KEYS = ("label", "processType", "status", "apiVersion", "runInMode")
OPERATORS = {
    "EqualTo": "=",
    "NotEqualTo": "!=",
    "GreaterThan": ">",
    "GreaterThanOrEqualTo": ">=",
    "LessThan": "<",
    "LessThanOrEqualTo": "<=",
    "Contains": "contains",
    "StartsWith": "starts with",
    "EndsWith": "ends with",
    "In": "in",
    "Assign": "=",
    "Add": "+=",
    "Subtract": "-=",
    "AddItem": "add",
}
UNARY = {
    "IsNull": ("is null", "is not null"),
    "WasSet": ("was set", "was not set"),
    "WasSelected": ("was selected", "was not selected"),
    "WasVisited": ("was visited", "was not visited"),
    "IsChanged": ("is changed", "is not changed"),
}
CONNECTOR_LABELS = {
    "connector": "",
    "defaultConnector": "default",
    "faultConnector": "fault",
    "nextValueConnector": "each",
    "noMoreValuesConnector": "done",
}
LABEL_ORDER = {"": 0, "each": 1, "done": 3, "default": 4, "fault": 5}
OPEN_ENDED = frozenset({"decisions", "loops", "waits"})

Edge = tuple[str, str, str]


def kids(el: ET.Element, tag: str | None = None) -> list[ET.Element]:
    """Children of ``el`` without the noise elements, optionally only those named ``tag``."""
    return [c for c in el if c.tag not in NOISE and (tag is None or c.tag == tag)]


def text(el: ET.Element, tag: str) -> str:
    child = el.find(tag)
    return (child.text or "").strip() if child is not None else ""


def pick(el: ET.Element, *tags: str) -> ET.Element | None:
    for tag in tags:
        child = el.find(tag)
        if child is not None:
            return child
    return None


def collapse(raw: str) -> str:
    return " ".join(raw.split())


def words(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", " ", name).lower()


def kind_of(tag: str) -> str:
    """Singular of a plural element tag: ``recordLookups`` is a ``recordLookup``."""
    if tag.endswith("ies"):
        return tag[:-3] + "y"
    return tag[:-1] if tag.endswith("s") else tag


def value(el: ET.Element | None) -> str:
    """Render a ``value`` / ``rightValue`` node: references plain, strings quoted, the rest as written."""
    if el is None:
        return ""
    parts = []
    for child in kids(el):
        raw = (child.text or "").strip()
        if child.tag == "stringValue":
            parts.append(json.dumps(raw, ensure_ascii=False))
        elif child.tag in VALUE_TAGS or raw:
            parts.append(raw)
        elif len(child):
            parts.append(flat(child))
    return " ".join(parts) if parts else (el.text or "").strip()


def is_value_node(el: ET.Element) -> bool:
    nested = kids(el)
    return bool(nested) and all(c.tag in VALUE_TAGS for c in nested)


def flat(el: ET.Element) -> str:
    """One-line ``key=value; key=value`` form of an element whose children are mostly leaves."""
    parts = []
    for child in kids(el):
        if not len(kids(child)):
            raw = collapse(child.text or "")
            if raw:
                parts.append(f"{child.tag}={raw}")
        elif is_value_node(child):
            parts.append(f"{child.tag}={value(child)}")
        else:
            parts.append(f"{child.tag}({flat(child)})")
    return "; ".join(parts)


def comparison(left: str, operator: str, right: ET.Element | None) -> str:
    unary = UNARY.get(operator)
    if unary:
        return f"{left} {unary[1] if value(right) == 'false' else unary[0]}"
    return f"{left} {OPERATORS.get(operator) or words(operator)} {value(right)}".rstrip()


def join_logic(items: list[str], logic: str) -> str:
    """Join conditions by ``and`` / ``or`` or substitute them into a custom ``1 AND (2 OR 3)`` logic."""
    mode = logic.strip() or "and"
    if re.search(r"\d", mode):
        return re.sub(r"\d+", lambda m: f"({items[int(m.group()) - 1]})" if 0 < int(m.group()) <= len(items) else m.group(), mode)
    return f" {'OR' if mode.lower() == 'or' else 'AND'} ".join(items)


def conditions(owner: ET.Element, item_tag: str, logic_tag: str) -> str:
    items = [
        comparison(
            text(c, "leftValueReference") or text(c, "field"),
            text(c, "operator"),
            pick(c, "rightValue", "value"),
        )
        for c in kids(owner, item_tag)
    ]
    return join_logic(items, text(owner, logic_tag)) if items else ""


def path_name(el: ET.Element) -> str:
    """The label of a branch: its ``name``, or the ``pathType`` of a run-asynchronously path, which has no name."""
    return text(el, "name") or text(el, "pathType")


def edges(el: ET.Element) -> list[Edge]:
    """Outgoing connectors as ``(label, target, connector tag)``, in reading order."""
    found: list[Edge] = []

    def walk(node: ET.Element, owner: str) -> None:
        for child in kids(node):
            if child.tag.endswith("onnector"):
                target = text(child, "targetReference")
                if target:
                    label = owner if child.tag == "connector" else CONNECTOR_LABELS.get(child.tag, child.tag)
                    found.append((label, target, child.tag))
            else:
                walk(child, path_name(child) or owner)

    walk(el, "")
    found.sort(key=lambda e: LABEL_ORDER.get(e[0], 2))
    return found


def load(path: str) -> ET.Element:
    file = Path(path)
    if not file.is_file():
        raise SynError(f"no such file: {path}")
    try:
        root = ET.fromstring(file.read_bytes())
    except ET.ParseError as error:
        raise SynError(f"{path} is not valid XML ({error})") from error
    for el in root.iter():
        el.tag = el.tag.rpartition("}")[2]
    if root.tag != "Flow":
        raise SynError(f"{path} is not a Flow file (root element is {root.tag})")
    return root


class Flow:
    """The pieces of a flow file, split by role."""

    def __init__(self, root: ET.Element) -> None:
        self.start: ET.Element | None = None
        self.elements: dict[str, ET.Element] = {}
        self.resources: list[ET.Element] = []
        self.meta: dict[str, str] = {}
        self.extras: list[ET.Element] = []
        for child in kids(root):
            if child.tag == "start":
                self.start = child
            elif child.tag in RESOURCES:
                self.resources.append(child)
            elif not kids(child):
                if (child.text or "").strip():
                    self.meta[child.tag] = collapse(child.text or "")
            elif text(child, "name"):
                self.elements.setdefault(text(child, "name"), child)
            else:
                self.extras.append(child)

    def entry_edges(self) -> list[Edge]:
        if self.start is not None:
            return edges(self.start)
        legacy = self.meta.pop("startElementReference", "")
        return [("", legacy, "connector")] if legacy else []

    def incoming(self) -> dict[str, list[str]]:
        reached: dict[str, list[str]] = {}
        sources = [("start", self.entry_edges())] + [(n, edges(e)) for n, e in self.elements.items()]
        for name, outgoing in sources:
            for _, target, _ in outgoing:
                if name not in reached.setdefault(target, []):
                    reached[target].append(name)
        return reached


def arrow(labelled: dict[str, str], used: set[str], name: str, missing: str) -> str:
    if name in labelled:
        used.add(name)
        return f" -> {labelled[name]}"
    return missing


def assignment_pairs(el: ET.Element, tag: str, label: str) -> list[str]:
    items = kids(el, tag)
    if not items:
        return []
    parts = [f"{text(i, 'field') or text(i, 'name')} = {value(i.find('value'))}" for i in items]
    return [f"{label} " + ", ".join(parts)]


def output_pairs(el: ET.Element, tag: str) -> list[str]:
    items = kids(el, tag)
    if not items:
        return []
    parts = [f"{text(i, 'assignToReference')} <- {text(i, 'field') or text(i, 'name')}" for i in items]
    return ["out " + ", ".join(parts)]


def screen_fields(el: ET.Element) -> list[str]:
    names = []
    for field in kids(el, "fields"):
        label = f"{text(field, 'name')}:{text(field, 'fieldType')}"
        extension = text(field, "extensionName")
        names.append(f"{label}({extension})" if extension else label)
        names.extend(screen_fields(field))
    return names


def start_lines(el: ET.Element, labelled: dict[str, str], used: set[str]) -> list[str]:
    lines = []
    trigger = [text(el, "triggerType")]
    if text(el, "object"):
        trigger.append(f"on {text(el, 'object')}")
    if text(el, "recordTriggerType"):
        trigger.append(f"({text(el, 'recordTriggerType')})")
    if any(trigger):
        lines.append("trigger " + " ".join(p for p in trigger if p))
    entry = conditions(el, "filters", "filterLogic")
    if entry:
        lines.append(f"entry {entry}")
    if text(el, "filterFormula"):
        lines.append(f"entry formula {collapse(text(el, 'filterFormula'))}")
    if text(el, "doesRequireRecordChangedToMeetCriteria") == "true":
        lines.append("entry only when the record newly meets the criteria")
    schedule = pick(el, "schedule")
    if schedule is not None:
        lines.append("schedule " + " ".join(p for p in (text(schedule, "frequency"), "from", text(schedule, "startDate"), text(schedule, "startTime")) if p))
    for path in kids(el, "scheduledPaths"):
        name = path_name(path)
        when = " ".join(p for p in (text(path, "offsetNumber"), text(path, "offsetUnit"), text(path, "timeSource"), text(path, "recordField")) if p)
        lines.append(f"scheduled {name}: {when}".rstrip(": ") + arrow(labelled, used, name, ""))
    return lines


def decision_lines(el: ET.Element, labelled: dict[str, str], used: set[str]) -> list[str]:
    lines = []
    for rule in kids(el, "rules"):
        name = text(rule, "name")
        lines.append(f"{name}: {conditions(rule, 'conditions', 'conditionLogic')}{arrow(labelled, used, name, ' -> end')}")
    return lines


def wait_lines(el: ET.Element, labelled: dict[str, str], used: set[str]) -> list[str]:
    lines = []
    for event in kids(el, "waitEvents"):
        name = text(event, "name")
        params = ", ".join(f"{text(p, 'name')}={value(p.find('value'))}" for p in kids(event, "inputParameters"))
        parts = [f"event {name}", f"({text(event, 'eventType')})" if text(event, "eventType") else "", params, conditions(event, "conditions", "conditionLogic")]
        lines.append(" ".join(p for p in parts if p) + arrow(labelled, used, name, ""))
    return lines


def common_lines(el: ET.Element, start: bool) -> list[str]:
    """Facts shared by many kinds: object, filters, assigned fields, mappings, collections."""
    lines = []
    record = el.tag.startswith("record")
    if text(el, "object") and not start:
        lines.append(f"object {text(el, 'object')}")
    if not start:
        where = conditions(el, "filters", "filterLogic")
        if where:
            lines.append(f"where {where}")
    if kids(el, "queriedFields"):
        lines.append("fields " + ", ".join(collapse(c.text or "") for c in kids(el, "queriedFields")))
    lines += assignment_pairs(el, "inputAssignments", "set" if record else "in")
    lines += assignment_pairs(el, "inputParameters", "in")
    for tag, label in (("inputReference", "from"), ("outputReference", "out"), ("assignRecordIdToReference", "id ->")):
        if text(el, tag):
            lines.append(f"{label} {text(el, tag)}")
    lines += output_pairs(el, "outputAssignments")
    lines += output_pairs(el, "outputParameters")
    if text(el, "sortField"):
        lines.append(f"sort {text(el, 'sortField')} {text(el, 'sortOrder')}".rstrip())
    if text(el, "getFirstRecordOnly") == "true":
        lines.append("first record only")
    if el.tag != "decisions" and not start:
        check = conditions(el, "conditions", "conditionLogic")
        if check:
            lines.append(f"if {check}")
    return lines


def facts(el: ET.Element, labelled: dict[str, str], used: set[str]) -> list[str]:
    tag = el.tag
    lines: list[str] = []
    if tag == "start":
        lines += start_lines(el, labelled, used)
    elif tag == "decisions":
        lines += decision_lines(el, labelled, used)
    elif tag == "waits":
        lines += wait_lines(el, labelled, used)
    elif tag == "screens":
        names = screen_fields(el)
        if names:
            lines.append("fields " + ", ".join(names))
    elif tag == "assignments":
        for item in kids(el, "assignmentItems"):
            operator = text(item, "operator")
            lines.append(f"{text(item, 'assignToReference')} {OPERATORS.get(operator) or words(operator)} {value(item.find('value'))}")
    elif tag == "loops":
        order = f", {text(el, 'iterationOrder').lower()}" if text(el, "iterationOrder") else ""
        lines.append(f"over {text(el, 'collectionReference')}{order}")
    elif tag == "actionCalls":
        kind = f" ({text(el, 'actionType')})" if text(el, "actionType") else ""
        lines.append(f"action {text(el, 'actionName')}{kind}")
    elif tag == "subflows":
        lines.append(f"flow {text(el, 'flowName')}")
    elif tag == "customErrors":
        for message in kids(el, "customErrorMessages"):
            field = f" (field {text(message, 'fieldSelection')})" if text(message, "fieldSelection") else ""
            lines.append(f"error {value(message.find('errorMessage')) or json.dumps(text(message, 'errorMessage'))}{field}")
    elif tag == "collectionProcessors":
        lines.append(f"{text(el, 'collectionProcessorType')} over {text(el, 'collectionReference')}".strip())
        for option in kids(el, "sortOptions"):
            lines.append(f"sort {text(option, 'sortField')} {text(option, 'sortOrder')}".rstrip())
        if text(el, "limit"):
            lines.append(f"limit {text(el, 'limit')}")
    elif tag == "transforms":
        for holder in kids(el, "transformValues"):
            for action in kids(holder, "transformValueActions"):
                lines.append(f"{text(action, 'transformType')} {text(action, 'outputFieldApiName')} = {value(action.find('value'))}".strip())
    return lines + common_lines(el, tag == "start")


def visible_edges(outgoing: list[Edge], used: set[str]) -> list[Edge]:
    return [e for e in outgoing if not (e[2] == "connector" and e[0] and e[0] in used)]


def element_lines(name: str, el: ET.Element, outgoing: list[Edge]) -> list[str]:
    labelled = {label: target for label, target, tag in outgoing if tag == "connector" and label}
    used: set[str] = set()
    lines = [f"{name} ({kind_of(el.tag)})"]
    lines += [f"  {line}" for line in facts(el, labelled, used)]
    rest = visible_edges(outgoing, used)
    if el.tag == "decisions" and not any(e[2] == "defaultConnector" for e in outgoing):
        lines.append("  default -> end")
    if el.tag not in OPEN_ENDED and not any(e[2] == "connector" for e in outgoing):
        lines.append("  -> end")
    for label, target, _ in rest:
        lines.append(f"  {label} -> {target}" if label else f"  -> {target}")
    return lines


def resource_line(el: ET.Element) -> str:
    tag, name = el.tag, text(el, "name")
    label = RESOURCES[tag]
    if tag == "variables":
        data_type = text(el, "dataType") + (f"({text(el, 'objectType')})" if text(el, "objectType") else "")
        flags = [word for word, key in (("collection", "isCollection"), ("input", "isInput"), ("output", "isOutput")) if text(el, key) == "true"]
        default = value(el.find("value"))
        parts = [data_type, *flags] + ([f"= {default}"] if default else [])
        return f"{label} {name}: " + " ".join(parts)
    if tag == "formulas":
        return f"{label} {name}: {text(el, 'dataType')} = {collapse(text(el, 'expression'))}"
    if tag == "constants":
        return f"{label} {name}: {text(el, 'dataType')} = {value(el.find('value'))}"
    if tag == "textTemplates":
        return f"{label} {name}: {len(text(el, 'text'))} chars"
    if tag == "choices":
        return f"{label} {name}: {json.dumps(text(el, 'choiceText'), ensure_ascii=False)} = {value(el.find('value'))}"
    rest = ET.Element(tag)
    rest.extend(c for c in kids(el) if c.tag != "name")
    return f"{label} {name}: {flat(rest)}".rstrip(": ")


def outline(root: ET.Element) -> list[str]:
    flow = Flow(root)
    lines: list[str] = []
    seen: set[str] = set()
    stack: list[str] = []

    def push(outgoing: list[Edge]) -> None:
        stack.extend(e[1] for e in reversed(outgoing))

    entry = flow.entry_edges()
    if flow.start is not None:
        lines += element_lines("start", flow.start, entry)
    elif entry:
        lines += ["start (start)", f"  -> {entry[0][1]}"]
    push(entry)
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        el = flow.elements.get(name)
        if el is None:
            lines.append(f"{name} (missing)")
            continue
        outgoing = edges(el)
        lines += element_lines(name, el, outgoing)
        push(outgoing)
    orphans = [n for n in flow.elements if n not in seen]
    if orphans:
        lines += ["", "unreachable:"] + [f"  {n} ({kind_of(flow.elements[n].tag)})" for n in orphans]
    if flow.resources:
        lines += [""] + [resource_line(r) for r in flow.resources]
    meta = " ".join(f"{k}={json.dumps(flow.meta[k], ensure_ascii=False) if k == 'label' else flow.meta[k]}" for k in META_KEYS if k in flow.meta)
    extras = [f"{c.tag}: {flat(c)}" for c in flow.extras if flat(c)]
    if meta or extras:
        lines += [""] + ([f"flow {meta}"] if meta else []) + extras
    return lines


def dump(el: ET.Element, depth: int = 0) -> list[str]:
    """Every non-empty field of ``el``, nested by indentation; flat nodes collapse to one line."""
    pad = "  " * depth
    lines = []
    for child in kids(el):
        nested = kids(child)
        if not nested:
            raw = collapse(child.text or "")
            if raw:
                lines.append(f"{pad}{child.tag}: {raw}")
        elif is_value_node(child):
            lines.append(f"{pad}{child.tag}: {value(child)}")
        elif all(not kids(c) or is_value_node(c) for c in nested):
            lines.append(f"{pad}{child.tag}: {flat(child)}")
        else:
            lines.append(f"{pad}{child.tag}:")
            lines += dump(child, depth + 1)
    return lines


def detail(root: ET.Element, wanted: str) -> list[str]:
    flow = Flow(root)
    candidates: dict[str, ET.Element] = dict(flow.elements)
    candidates.update({text(r, "name"): r for r in flow.resources})
    if flow.start is not None:
        candidates["start"] = flow.start
    found = candidates.get(wanted)
    if found is None:
        found = next((el for n, el in candidates.items() if n.lower() == wanted.lower()), None)
    if found is None:
        known = ", ".join(sorted(candidates)[:40])
        raise SynError(f"no element named {wanted!r}; known: {known}")
    name = text(found, "name") or "start"
    fields = ET.Element(found.tag)
    fields.extend(child for child in found if child.tag != "name")
    lines = [f"{name} ({kind_of(found.tag)})"] + [f"  {line}" for line in dump(fields)]
    sources = flow.incoming().get(name)
    if sources:
        lines.append("  reached from: " + ", ".join(sources))
    return lines


def run(args: argparse.Namespace) -> int:
    root = load(args.file)
    lines = detail(root, args.element) if args.element else outline(root)
    print("\n".join(lines))
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "flow",
        help="show a Flow as an execution-ordered outline",
        description="Outline a *.flow-meta.xml file, or show every field of one element.",
    )
    parser.add_argument("file", help="path to the flow file")
    parser.add_argument("element", nargs="?", help="API name of one element to show in full")
    parser.set_defaults(run=run)
