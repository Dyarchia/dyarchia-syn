"""Byte spans, decoded values and file conventions reported by ``syn.xmlspan``."""

from __future__ import annotations

import pytest

from syn.xmlspan import parse, splice, well_formed

SAMPLE = (
    "﻿<?xml version=\"1.0\" encoding=\"UTF-8\"?>\r\n"
    "<Root xmlns=\"urn:x\">\r\n"
    "\t<!-- comment > with a bracket -->\r\n"
    "\t<item>\r\n"
    "\t\t<name>Café &amp; Bar</name>\r\n"
    "\t\t<empty/>\r\n"
    "\t\t<nil xsi:nil=\"true\" a=\"x>y\"/>\r\n"
    "\t\t<code><![CDATA[a < b]]></code>\r\n"
    "\t\t<deep><x><x/></x><y>1</y></deep>\r\n"
    "\t</item>\r\n"
    "\t<single>v</single>\r\n"
    "\t<x><x/></x>\r\n"
    "</Root>"
).encode()


def test_spans_values_and_conventions() -> None:
    doc = parse(SAMPLE)
    data = doc.data
    assert doc.root.name == "Root"
    assert [block.name for block in doc.blocks] == ["item", "single", "x"]
    item, single, nested_same = doc.blocks
    assert data[item.start : item.end].startswith(b"<item>") and data[item.start : item.end].endswith(b"</item>")
    name, empty, nil, code, deep = item.children
    assert data[name.start : name.end] == "<name>Café &amp; Bar</name>".encode()
    assert name.value == "Café & Bar"
    assert data[name.open_end : name.close_start] == "Café &amp; Bar".encode()
    assert empty.self_closing and empty.value == "" and data[empty.start : empty.end] == b"<empty/>"
    assert nil.self_closing and data[nil.start : nil.end] == b'<nil xsi:nil="true" a="x>y"/>'
    assert code.value == "a < b"
    assert not deep.leaf and deep.value is None and doc.inner(deep) == "<x><x/></x><y>1</y>"
    assert single.leaf and single.value == "v"
    assert not nested_same.leaf and data[nested_same.start : nested_same.end] == b"<x><x/></x>"
    assert doc.newline == "\r\n" and doc.bom and not doc.final_newline
    assert doc.indent_unit == "\t"
    assert doc.indent_at(item.start) == "\t"


def test_line_span_and_escape() -> None:
    doc = parse(SAMPLE)
    item = doc.blocks[0]
    start, end = doc.line_span(item)
    assert doc.data[start:end].startswith(b"\t<item>") and doc.data[start:end].endswith(b"</item>\r\n")
    assert doc.escape("a&b <c>\n\"q\"") == "a&amp;b &lt;c&gt;\r\n&quot;q&quot;"
    raw_quotes = parse(b"<R>\n  <a>\n    <b>it's</b>\n  </a>\n</R>\n")
    assert raw_quotes.escape("x'y") == "x'y"
    assert raw_quotes.indent_unit == "  " and raw_quotes.newline == "\n" and raw_quotes.final_newline


def test_inline_element_owns_no_line() -> None:
    doc = parse(b"<R><a><b>1</b></a><a><b>2</b></a></R>")
    first = doc.blocks[0]
    assert doc.line_span(first) == (first.start, first.end)


def test_splice_and_well_formed() -> None:
    data = b"<R><a>1</a></R>"
    assert splice(data, [(6, 7, b"2"), (3, 3, b"<z/>"), (11, 11, b"<y/>")]) == b"<R><z/><a>2</a><y/></R>"
    assert well_formed(data) and not well_formed(b"<R><a></R>")


def test_malformed_reports_line() -> None:
    with pytest.raises(ValueError, match="line 2"):
        parse(b"<R>\n<a></b>\n</R>")
