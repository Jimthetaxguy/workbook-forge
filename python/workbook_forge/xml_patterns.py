"""Bounded XML parsing and namespace-qualified structural paths.

These primitives inspect decoded XML; they never infer structure from a regex
or accept a same-named descendant in place of a direct child.
"""

from __future__ import annotations

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


def rich_text(element: ET.Element, namespace: str) -> str:
    """Resolve plain/rich strings without phonetic or extension descendants."""
    chunks: list[str] = []
    for child in element:
        if child.tag == qualified(namespace, "t"):
            chunks.append(direct_text(child))
        elif child.tag == qualified(namespace, "r"):
            text = child.find(qualified(namespace, "t"))
            if text is not None:
                chunks.append(direct_text(text))
    return "".join(chunks)
