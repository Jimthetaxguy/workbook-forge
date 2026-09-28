//! Bounded XML tokens and exact namespace-qualified structural paths.
//! Raw byte offsets remain available internally for preservation-aware patches.
use crate::xlsx::XlsxError;
use quick_xml::{Reader, events::Event};
use std::collections::BTreeMap;
const MAIN: &str = "http://schemas.openxmlformats.org/spreadsheetml/2006/main";
const MAX_XML: usize = 32 * 1024 * 1024;
type Result<T> = std::result::Result<T, XlsxError>;
fn err(message: impl Into<String>) -> XlsxError {
    XlsxError(message.into())
}
#[derive(Clone, Debug)]
pub struct Node {
    pub(crate) name: String,
    pub(crate) local: String,
    pub(crate) ns: String,
    pub(crate) attrs: BTreeMap<String, String>,
    pub(crate) start: usize,
    pub(crate) close_start: usize,
    pub(crate) end: usize,
    pub(crate) children: Vec<usize>,
    pub(crate) text: String,
    pub(crate) parent: Option<usize>,
    sibling_index: usize,
}
#[derive(Clone, Debug)]
pub struct Xml {
    pub(crate) bytes: Vec<u8>,
    pub(crate) nodes: Vec<Node>,
}
impl Xml {
    pub fn parse(bytes: &[u8]) -> Result<Self> {
        if bytes.len() > MAX_XML {
            return Err(err("XML part exceeds 32 MiB"));
        }
        let source =
            std::str::from_utf8(bytes).map_err(|_| err("only UTF-8 OOXML is supported"))?;
        let mut reader = Reader::from_str(source);
        reader.config_mut().check_end_names = true;
        let mut nodes: Vec<Node> = Vec::new();
        let mut stack: Vec<(usize, BTreeMap<String, String>)> = Vec::new();
        let mut roots = 0;
        let mut sibling_counts: Vec<BTreeMap<(String, String), usize>> = vec![BTreeMap::new()];
        loop {
            let start = reader.buffer_position() as usize;
            let event = reader
                .read_event()
                .map_err(|e| err(format!("invalid XML: {e}")))?;
            let end = reader.buffer_position() as usize;
            match event {
                Event::Start(ref e) | Event::Empty(ref e) => {
                    if nodes.len() >= 1_000_000 {
                        return Err(err("XML node count exceeds 1000000"));
                    }
                    if stack.len() >= 128 {
                        return Err(err("XML nesting exceeds 128"));
                    }
                    let name = e.name().as_ref().to_string();
                    let mut namespaces = stack.last().map(|(_, ns)| ns.clone()).unwrap_or_default();
                    namespaces.insert("xml".into(), "http://www.w3.org/XML/1998/namespace".into());
                    let mut raw = BTreeMap::new();
                    for a in e.attributes() {
                        let a = a.map_err(|e| err(e.to_string()))?;
                        let key = a.key.as_ref().to_string();
                        let value = a
                            .normalized_value(quick_xml::XmlVersion::Implicit1_0)
                            .map_err(|e| err(e.to_string()))?
                            .into_owned();
                        if raw.insert(key.clone(), value.clone()).is_some() {
                            return Err(err("duplicate XML attribute"));
                        }
                        if key == "xmlns" {
                            if matches!(
                                value.as_str(),
                                "http://www.w3.org/XML/1998/namespace"
                                    | "http://www.w3.org/2000/xmlns/"
                            ) {
                                return Err(err("invalid default namespace binding"));
                            }
                            namespaces.insert(String::new(), value);
                        } else if let Some(prefix) = key.strip_prefix("xmlns:") {
                            if prefix == "xmlns"
                                || value.is_empty()
                                || value == "http://www.w3.org/2000/xmlns/"
                                || (prefix != "xml"
                                    && value == "http://www.w3.org/XML/1998/namespace")
                                || (prefix == "xml"
                                    && value != "http://www.w3.org/XML/1998/namespace")
                            {
                                return Err(err("invalid reserved XML namespace binding"));
                            }
                            namespaces.insert(prefix.into(), value);
                        }
                    }
                    let (prefix, local) = name.split_once(':').unwrap_or(("", &name));
                    let ns = namespaces.get(prefix).cloned().unwrap_or_default();
                    if !prefix.is_empty() && ns.is_empty() {
                        return Err(err("unbound XML namespace"));
                    }
                    let mut attrs = raw.clone();
                    for (key, value) in raw {
                        if let Some((prefix, local)) = key.split_once(':')
                            && prefix != "xmlns"
                        {
                            let uri = namespaces
                                .get(prefix)
                                .ok_or_else(|| err("unbound attribute namespace"))?;
                            if attrs.insert(format!("{{{uri}}}{local}"), value).is_some() {
                                return Err(err("duplicate expanded XML attribute"));
                            }
                        }
                    }
                    let id = nodes.len();
                    let parent = stack.last().map(|(parent, _)| *parent);
                    let count = sibling_counts
                        .last_mut()
                        .expect("root counter")
                        .entry((ns.clone(), local.to_string()))
                        .or_default();
                    *count += 1;
                    let sibling_index = *count;
                    nodes.push(Node {
                        name: name.clone(),
                        local: local.into(),
                        ns,
                        attrs,
                        start,
                        close_start: end,
                        end,
                        children: Vec::new(),
                        text: String::new(),
                        parent,
                        sibling_index,
                    });
                    if let Some((parent, _)) = stack.last() {
                        nodes[*parent].children.push(id);
                    } else {
                        roots += 1;
                    }
                    if matches!(event, Event::Start(_)) {
                        stack.push((id, namespaces));
                        sibling_counts.push(BTreeMap::new());
                    }
                }
                Event::End(_) => {
                    sibling_counts.pop();
                    let (id, _) = stack
                        .pop()
                        .ok_or_else(|| err("unexpected XML closing tag"))?;
                    nodes[id].close_start = start;
                    nodes[id].end = end;
                }
                Event::Text(e) => {
                    let normalized = e.xml10_content();
                    let text =
                        quick_xml::escape::unescape(&normalized).map_err(|e| err(e.to_string()))?;
                    if let Some((id, _)) = stack.last() {
                        nodes[*id].text.push_str(&text);
                    } else if !text.trim().is_empty() {
                        return Err(err("text outside XML root"));
                    }
                }
                Event::CData(e) => {
                    let normalized = e.xml10_content();
                    let text = normalized.as_ref();
                    if let Some((id, _)) = stack.last() {
                        nodes[*id].text.push_str(text);
                    } else {
                        return Err(err("CDATA outside root"));
                    }
                }
                Event::GeneralRef(e) => {
                    let text = e.as_ref();
                    let escaped = format!("&{text};");
                    let decoded = quick_xml::escape::unescape(&escaped)
                        .map_err(|_| err("unknown XML entity"))?;
                    if let Some((id, _)) = stack.last() {
                        nodes[*id].text.push_str(&decoded);
                    } else {
                        return Err(err("entity outside root"));
                    }
                }
                Event::DocType(_) => return Err(err("DTD/entity declarations are forbidden")),
                Event::Decl(e) => {
                    if let Some(encoding) = e.encoding() {
                        let encoding = encoding.map_err(|e| err(e.to_string()))?;
                        if !encoding.eq_ignore_ascii_case("utf-8") {
                            return Err(err("only UTF-8 OOXML is supported"));
                        }
                    }
                }
                Event::Eof => break,
                _ => {}
            }
        }
        if roots != 1 || !stack.is_empty() {
            return Err(err("XML must contain one balanced root"));
        }
        Ok(Self {
            bytes: bytes.to_vec(),
            nodes,
        })
    }
    pub fn root(&self, ns: &str, name: &str) -> Result<&Node> {
        let root = &self.nodes[0];
        if root.ns != ns || root.local != name {
            return Err(err(format!("unsupported XML root: {}", root.name)));
        }
        Ok(root)
    }
    pub fn children<'a>(
        &'a self,
        node: &'a Node,
        ns: &'a str,
        name: &'a str,
    ) -> impl Iterator<Item = &'a Node> {
        node.children
            .iter()
            .map(|id| &self.nodes[*id])
            .filter(move |n| n.ns == ns && n.local == name)
    }
    pub fn child<'a>(&'a self, node: &'a Node, name: &str) -> Option<&'a Node> {
        node.children
            .iter()
            .map(|id| &self.nodes[*id])
            .find(|n| n.ns == MAIN && n.local == name)
    }
    #[cfg(test)]
    pub(crate) fn all<'a>(&'a self, ns: &'a str, name: &'a str) -> impl Iterator<Item = &'a Node> {
        self.nodes
            .iter()
            .filter(move |n| n.ns == ns && n.local == name)
    }
    pub fn raw(&self, node: &Node) -> &[u8] {
        &self.bytes[node.start..node.end]
    }
}

impl Node {
    pub fn local_name(&self) -> &str {
        &self.local
    }
    pub fn namespace(&self) -> &str {
        &self.ns
    }
    pub fn direct_text(&self) -> &str {
        &self.text
    }
    /// Namespace declarations and raw prefix aliases are excluded.
    pub fn attributes(&self) -> BTreeMap<String, String> {
        self.attrs
            .iter()
            .filter(|(name, _)| {
                name.starts_with('{') || (!name.contains(':') && name.as_str() != "xmlns")
            })
            .map(|(name, value)| (name.clone(), value.clone()))
            .collect()
    }
}
impl Xml {
    /// Match a complete path from the root; descendant lookalikes never match.
    pub fn matches<T: AsRef<str>>(&self, node: &Node, namespace: &str, path: &[T]) -> bool {
        if path.is_empty() {
            return false;
        }
        let mut current = Some(node);
        for component in path.iter().rev() {
            let Some(node) = current else {
                return false;
            };
            if node.ns != namespace || node.local != component.as_ref() {
                return false;
            }
            current = node.parent.map(|id| &self.nodes[id]);
        }
        current.is_none()
    }
    pub fn select<'a>(
        &'a self,
        namespace: &'a str,
        path: &'a [&'a str],
    ) -> impl Iterator<Item = &'a Node> {
        self.nodes
            .iter()
            .filter(move |node| self.matches(node, namespace, path))
    }
    pub fn path(&self, node: &Node) -> String {
        let mut components = vec![format!("{}[{}]", node.local, node.sibling_index)];
        let mut parent = node.parent;
        while let Some(id) = parent {
            let ancestor = &self.nodes[id];
            components.push(format!("{}[{}]", ancestor.local, ancestor.sibling_index));
            parent = ancestor.parent;
        }
        components.reverse();
        format!("/{}", components.join("/"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn exact_paths_ignore_decoys_and_index_expanded_sibling_names() {
        let bytes=br#"<r xmlns="urn:main" xmlns:m="urn:main" xmlns:q="urn:other"><c q:id="&amp;">before<![CDATA[<raw>]]><q:child>excluded</q:child>tail&#13;</c><q:c/><m:c/><nested><c/></nested></r>"#;
        let xml = Xml::parse(bytes).unwrap();
        let cells = xml.select("urn:main", &["r", "c"]).collect::<Vec<_>>();
        assert_eq!(cells.len(), 2);
        assert_eq!(xml.path(cells[0]), "/r[1]/c[1]");
        assert_eq!(xml.path(cells[1]), "/r[1]/c[2]");
        assert_eq!(cells[0].direct_text(), "before<raw>tail\r");
        assert_eq!(
            cells[0].attributes(),
            BTreeMap::from([("{urn:other}id".into(), "&".into())])
        );
        assert_eq!(xml.raw(cells[1]), b"<m:c/>");
    }
    #[test]
    fn physical_newlines_normalize_without_changing_raw_preservation_bytes() {
        let xml = Xml::parse(b"<r>one\r\ntwo\rthree<![CDATA[\rfour]]>&#13;</r>").unwrap();
        let root = xml.root("", "r").unwrap();
        assert_eq!(root.direct_text(), "one\ntwo\nthree\nfour\r");
        assert!(xml.raw(root).windows(2).any(|w| w == b"\r\n"));
        assert!(Xml::parse(b"<!DOCTYPE r [<!ENTITY x 'bad'>]><r>&x;</r>").is_err());
        assert!(
            Xml::parse(format!("{}{}", "<r>".repeat(129), "</r>".repeat(129)).as_bytes()).is_err()
        );
    }
}
