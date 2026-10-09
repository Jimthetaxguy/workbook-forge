"""Bounded XML parsing and namespace-qualified structural paths.

These primitives inspect decoded XML; they never infer structure from a regex
or accept a same-named descendant in place of a direct child.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from xml.etree import ElementTree as ET

MAX_XML_PART_BYTES = 32 * 1024 * 1024
MAX_XML_DEPTH = 128
MAX_XML_NODES = 1_000_000


class XMLPatternError(ValueError):
    """Malformed XML or an unsupported XML resource requirement."""


class XMLLimitError(XMLPatternError):
    """XML exceeds the accepted resource or declaration profile."""


class _BoundedTreeBuilder(ET.TreeBuilder):
    def __init__(self, max_depth: int, max_nodes: int):
        super().__init__()
        self.depth = 0
        self.nodes = 0
        self.max_depth = max_depth
        self.max_nodes = max_nodes

    def start(self, tag: str, attrs: dict[str, str]) -> ET.Element:
        self.depth += 1
        self.nodes += 1
        # Check before TreeBuilder allocates this element or attaches it.
        if self.depth > self.max_depth:
            raise XMLLimitError("XML depth exceeds limit")
        if self.nodes > self.max_nodes:
            raise XMLLimitError("XML element count exceeds limit")
        return super().start(tag, attrs)

    def end(self, tag: str) -> ET.Element:
        result = super().end(tag)
        self.depth -= 1
        return result

    def doctype(self, name: str, pubid: str | None, system: str | None) -> None:
        raise XMLLimitError("DTD/entity declarations are not accepted")


def parse_xml(data: bytes, *, max_bytes: int = MAX_XML_PART_BYTES) -> ET.Element:
    """Parse accepted ElementTree encodings with limits enforced during parsing."""
    if len(data) > max_bytes:
        raise XMLLimitError("XML part exceeds size limit")
    upper = data.upper()
    for encoding in ("ascii", "utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be"):
        if any(token.encode(encoding) in upper for token in ("<!DOCTYPE", "<!ENTITY")):
            raise XMLLimitError("DTD/entity declarations are not accepted")
    parser = ET.XMLParser(target=_BoundedTreeBuilder(MAX_XML_DEPTH, MAX_XML_NODES))
    try:
        # Bounded feeds also avoid asking the parser to consume an entire part
        # before a custom target can interrupt a pathological document.
        for offset in range(0, len(data), 64 * 1024):
            parser.feed(data[offset:offset + 64 * 1024])
        return parser.close()
    except (ET.ParseError, LookupError, UnicodeError) as error:
        raise XMLPatternError(str(error)) from error


def qualified(namespace: str, local_name: str) -> str:
    return f"{{{namespace}}}{local_name}"


def direct_text(element: ET.Element) -> str:
    """Collect direct character data, including child tails but no descendants."""
    return (element.text or "") + "".join(child.tail or "" for child in element)


def select_path(root: ET.Element, namespace: str, path: Sequence[str]) -> Iterator[ET.Element]:
    """Select an exact root-to-node path in one namespace, in document order."""
    if not path or root.tag != qualified(namespace, path[0]):
        return
    if len(path) == 1:
        yield root
        return
    yield from root.iterfind("/".join(qualified(namespace, name) for name in path[1:]))


@dataclass(frozen=True)
class XMLNode:
    element: ET.Element
    tags: tuple[str, ...]
    path: str
    ancestors: tuple[ET.Element, ...]


def walk_paths(root: ET.Element) -> Iterator[XMLNode]:
    """Visit bounded XML in preorder with same-expanded-name sibling indexes."""
    def walk(node: ET.Element, tags: tuple[str, ...], path: str,
             ancestors: tuple[ET.Element, ...]) -> Iterator[XMLNode]:
        yield XMLNode(node, tags, path, ancestors)
        counts: dict[str, int] = {}
        for child in node:
            if not isinstance(child.tag, str):
                continue
            counts[child.tag] = counts.get(child.tag, 0) + 1
            local_name = child.tag.rsplit("}", 1)[-1]
            yield from walk(child, tags + (child.tag,),
                            f"{path}/{local_name}[{counts[child.tag]}]", ancestors + (node,))

    local_name = root.tag.rsplit("}", 1)[-1]
    yield from walk(root, (root.tag,), f"/{local_name}[1]", ())


# OOXML string parts write a character that XML 1.0 cannot hold as `_xHHHH_`
# (ECMA-376 part 1, 22.9.2.19 ST_Xstring). Excel also writes a carriage
# return that way, since an XML parser would fold it into a line feed. A
# literal `_xHHHH_` is written with its underscore escaped: `_x005F_xHHHH_`.
_OOXML_ESCAPE = re.compile(r"_x([0-9A-Fa-f]{4})_")
# The same shape without consuming the trailing underscore: in a literal
# `_x005F_x0041_` two tokens share that underscore, and both need escaping.
_OOXML_ESCAPE_SHAPE = re.compile(r"_x([0-9A-Fa-f]{4})(?=_)")


def decode_ooxml_escapes(text: str) -> str:
    """Replace each `_xHHHH_` in a string part with the character it stands for.

    One pass, left to right, so `_x005F_x0041_` yields the literal `_x0041_`.
    """
    if "_x" not in text:
        return text
    return _OOXML_ESCAPE.sub(_decoded_escape, text)


def _decoded_escape(match: re.Match[str]) -> str:
    codepoint = int(match.group(1), 16)
    if 0xD800 <= codepoint <= 0xDFFF:
        # A lone surrogate is not a character; the escape stays as written.
        return match.group(0)
    return chr(codepoint)


def encode_ooxml_escapes(text: str) -> str:
    """Write text the way `decode_ooxml_escapes` reads it back unchanged.

    Characters XML 1.0 cannot hold, and the carriage return, become
    `_xHHHH_`; a literal `_xHHHH_` becomes `_x005F_xHHHH_`.
    """
    escaped = _OOXML_ESCAPE_SHAPE.sub(lambda match: f"_x005F_x{match.group(1)}", text)
    return "".join(
        f"_x{ord(char):04X}_" if _needs_ooxml_escape(char) else char for char in escaped
    )


def _needs_ooxml_escape(char: str) -> bool:
    codepoint = ord(char)
    return (codepoint < 0x20 and codepoint not in (0x9, 0xA)) or codepoint in (0xFFFE, 0xFFFF)


def rich_text(element: ET.Element, namespace: str) -> str:
    """Resolve plain/rich strings without phonetic or extension descendants.

    Each run is decoded on its own: an escape never spans two runs.
    """
    chunks: list[str] = []
    for child in element:
        if child.tag == qualified(namespace, "t"):
            chunks.append(decode_ooxml_escapes(direct_text(child)))
        elif child.tag == qualified(namespace, "r"):
            text = child.find(qualified(namespace, "t"))
            if text is not None:
                chunks.append(decode_ooxml_escapes(direct_text(text)))
    return "".join(chunks)
