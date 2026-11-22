"""The table of repeated blocks in Salesforce metadata XML.

A metadata file such as a profile is mostly one block type repeated hundreds
of times. Each entry here says which child elements identify one block (its
key) and whether the order of the blocks carries meaning. Everything else in
``syn`` (tables, diff) relies on this table and nothing else to understand a
file, so supporting a new metadata type means adding lines here.

Only direct children of the root are blocks: repeats nested deeper (rule
entries, picklist values inside a field) are not listed. A block type that is
missing here is still handled: its key is inferred from the file and its
order is treated as meaningful.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BlockSpec:
    """``key`` lists the child element names that identify a block; ``ordered`` blocks keep file order."""

    key: tuple[str, ...]
    ordered: bool = False


def _keyed(field: str, *blocks: str, ordered: bool = False) -> dict[str, BlockSpec]:
    return {block: BlockSpec((field,), ordered) for block in blocks}


_ACCESS: dict[str, BlockSpec] = {
    "applicationVisibilities": BlockSpec(("application",)),
    "classAccesses": BlockSpec(("apexClass",)),
    "customMetadataTypeAccesses": BlockSpec(("name",)),
    "customPermissions": BlockSpec(("name",)),
    "customSettingAccesses": BlockSpec(("name",)),
    "externalDataSourceAccesses": BlockSpec(("externalDataSource",)),
    "fieldPermissions": BlockSpec(("field",)),
    "flowAccesses": BlockSpec(("flow",)),
    "objectPermissions": BlockSpec(("object",)),
    "pageAccesses": BlockSpec(("apexPage",)),
    "recordTypeVisibilities": BlockSpec(("recordType",)),
    "userPermissions": BlockSpec(("name",)),
}
"""Blocks that profiles and permission sets share, with the same shape."""

_PERMISSION_SET = _ACCESS | {
    "tabSettings": BlockSpec(("tab",)),
    "externalCredentialPrincipalAccesses": BlockSpec(("externalCredentialPrincipal",)),
}

BLOCKS: dict[str, dict[str, BlockSpec]] = {
    "Profile": _ACCESS
    | {
        "tabVisibilities": BlockSpec(("tab",)),
        "layoutAssignments": BlockSpec(("layout", "recordType")),
        "categoryGroupVisibilities": BlockSpec(("dataCategoryGroup",)),
    },
    "PermissionSet": _PERMISSION_SET,
    "MutingPermissionSet": _PERMISSION_SET,
    "CustomLabels": _keyed("fullName", "labels"),
    "CustomObject": _keyed(
        "fullName",
        "businessProcesses",
        "compactLayouts",
        "fieldSets",
        "fields",
        "indexes",
        "listViews",
        "recordTypes",
        "sharingReasons",
        "validationRules",
        "webLinks",
    ),
    "Workflow": _keyed(
        "fullName", "alerts", "fieldUpdates", "flowActions", "knowledgePublishes", "outboundMessages", "rules", "tasks"
    ),
    "SharingRules": _keyed(
        "fullName", "sharingCriteriaRules", "sharingGuestRules", "sharingOwnerRules", "sharingTerritoryRules"
    ),
    "GlobalValueSet": _keyed("fullName", "customValue", ordered=True),
    "StandardValueSet": _keyed("fullName", "standardValue", ordered=True),
    "GlobalValueSetTranslation": _keyed("masterLabel", "valueTranslation"),
    "StandardValueSetTranslation": _keyed("masterLabel", "valueTranslation"),
    "Translations": _keyed("name", "customApplications", "customLabels", "customTabs", "quickActions", "reportTypes"),
    "CustomObjectTranslation": _keyed(
        "name",
        "fieldSets",
        "fields",
        "quickActions",
        "recordTypes",
        "sharingReasons",
        "standardFields",
        "validationRules",
        "webLinks",
        "workflowTasks",
    )
    | _keyed("layout", "layouts"),
    "AssignmentRules": _keyed("fullName", "assignmentRule"),
    "AutoResponseRules": _keyed("fullName", "autoResponseRule"),
    "EscalationRules": _keyed("fullName", "escalationRule"),
    "MatchingRules": _keyed("fullName", "matchingRules"),
    "Queue": _keyed("sobjectType", "queueSobject"),
    "RecordType": _keyed("picklist", "picklistValues"),
    "CustomMetadata": _keyed("field", "values"),
    "Layout": _keyed("relatedList", "relatedLists", ordered=True),
    "FlexiPage": _keyed("name", "flexiPageRegions", ordered=True),
    "Flow": _keyed(
        "name",
        "actionCalls",
        "assignments",
        "choices",
        "collectionProcessors",
        "constants",
        "decisions",
        "dynamicChoiceSets",
        "formulas",
        "loops",
        "recordCreates",
        "recordDeletes",
        "recordLookups",
        "recordUpdates",
        "screens",
        "subflows",
        "textTemplates",
        "variables",
        "waits",
    ),
}
"""Root element name, then block element name."""


def spec_for(root: str, block: str) -> BlockSpec | None:
    """Return the spec of ``block`` under a file whose root element is ``root``, or ``None`` if unknown."""
    return BLOCKS.get(root, {}).get(block)
