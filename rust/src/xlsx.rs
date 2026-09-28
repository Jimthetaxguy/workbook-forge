//! Independent, bounded OOXML workbook import and preservation-aware export.
//!
//! ZIP/XML crates supply container primitives only. Workbook semantics, model
//! translation, styles, validation, and surgical cell patches are implemented
//! here. Imported packages retain an immutable byte baseline; no Python runs.
use crate::toolkit::{
    self, CalculationReport, Cell, CellAddress, CellValue, Edit, InputBinding, Session, Sheet,
    Style, WorkbookModel,
};
use quick_xml::{Reader, events::Event};
use serde_json::{Value as Json, json};
use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, OpenOptions};
use std::io::{Cursor, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use zip::{ZipArchive, ZipWriter, write::SimpleFileOptions};

const MAIN: &str = "http://schemas.openxmlformats.org/spreadsheetml/2006/main";
const REL: &str = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
const PKG: &str = "http://schemas.openxmlformats.org/package/2006/relationships";
const CT: &str = "http://schemas.openxmlformats.org/package/2006/content-types";
const WB_TYPE: &str = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml";
const WS_TYPE: &str = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml";
const MAX_PACKAGE: usize = 128 * 1024 * 1024;
const MAX_COMPRESSED: usize = 130 * 1024 * 1024;
const MAX_XML: usize = 32 * 1024 * 1024;
const MAX_ENTRIES: usize = 10_000;
static TEMP_ID: AtomicU64 = AtomicU64::new(0);

#[derive(Debug)]
pub struct XlsxError(pub String);
impl std::fmt::Display for XlsxError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.0.fmt(f)
    }
}
impl std::error::Error for XlsxError {}
impl From<std::io::Error> for XlsxError {
    fn from(e: std::io::Error) -> Self {
        Self(e.to_string())
    }
}
impl From<toolkit::ToolkitError> for XlsxError {
    fn from(e: toolkit::ToolkitError) -> Self {
        Self(e.to_string())
    }
}
fn err(message: impl Into<String>) -> XlsxError {
    XlsxError(message.into())
}
type Result<T> = std::result::Result<T, XlsxError>;

#[derive(Clone, Debug)]
struct Node {
    name: String,
    local: String,
    ns: String,
    attrs: BTreeMap<String, String>,
    start: usize,
    close_start: usize,
    end: usize,
    children: Vec<usize>,
    text: String,
}
#[derive(Clone, Debug)]
struct Xml {
    bytes: Vec<u8>,
    nodes: Vec<Node>,
}
impl Xml {
    fn parse(bytes: &[u8]) -> Result<Self> {
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
                    });
                    if let Some((parent, _)) = stack.last() {
                        nodes[*parent].children.push(id);
                    } else {
                        roots += 1;
                    }
                    if matches!(event, Event::Start(_)) {
                        stack.push((id, namespaces));
                    }
                }
                Event::End(_) => {
                    let (id, _) = stack
                        .pop()
                        .ok_or_else(|| err("unexpected XML closing tag"))?;
                    nodes[id].close_start = start;
                    nodes[id].end = end;
                }
                Event::Text(e) => {
                    let text = e.as_ref();
                    let text = quick_xml::escape::unescape(text).map_err(|e| err(e.to_string()))?;
                    if let Some((id, _)) = stack.last() {
                        nodes[*id].text.push_str(&text);
                    } else if !text.trim().is_empty() {
                        return Err(err("text outside XML root"));
                    }
                }
                Event::CData(e) => {
                    let text = e.as_ref();
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
    fn root(&self, ns: &str, name: &str) -> Result<&Node> {
        let root = &self.nodes[0];
        if root.ns != ns || root.local != name {
            return Err(err(format!("unsupported XML root: {}", root.name)));
        }
        Ok(root)
    }
    fn children<'a>(
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
    fn child<'a>(&'a self, node: &'a Node, name: &str) -> Option<&'a Node> {
        node.children
            .iter()
            .map(|id| &self.nodes[*id])
            .find(|n| n.ns == MAIN && n.local == name)
    }
    #[cfg(test)]
    fn all<'a>(&'a self, ns: &'a str, name: &'a str) -> impl Iterator<Item = &'a Node> {
        self.nodes
            .iter()
            .filter(move |n| n.ns == ns && n.local == name)
    }
    fn raw(&self, node: &Node) -> &[u8] {
        &self.bytes[node.start..node.end]
    }
}
fn attr<'a>(node: &'a Node, name: &str) -> Result<&'a str> {
    node.attrs
        .get(name)
        .map(String::as_str)
        .ok_or_else(|| err(format!("missing {name} on {}", node.name)))
}
fn xml_escape(value: &str) -> Result<String> {
    if value.chars().any(|c| {
        !matches!(c, '\t' | '\r' | '\n') && (c < ' ' || matches!(c, '\u{fffe}' | '\u{ffff}'))
    }) {
        return Err(err("invalid XML text character"));
    }
    Ok(value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;"))
}
fn resolve(base: &str, target: &str) -> Result<String> {
    if target.is_empty() || target.contains(['\\', '%', ':', '?', '#']) {
        return Err(err("unsupported relationship target"));
    }
    let mut parts: Vec<&str> = if target.starts_with('/') {
        Vec::new()
    } else {
        base.rsplit_once('/')
            .map(|(p, _)| p.split('/').collect())
            .unwrap_or_default()
    };
    for part in target.split('/') {
        match part {
            "" | "." => {}
            ".." => {
                if parts.pop().is_none() {
                    return Err(err("relationship escapes package"));
                }
            }
            p => parts.push(p),
        }
    }
    Ok(parts.join("/"))
}
fn rel_part(part: &str) -> String {
    match part.rsplit_once('/') {
        Some((dir, name)) => format!("{dir}/_rels/{name}.rels"),
        None => format!("_rels/{part}.rels"),
    }
}
fn relationships(bytes: &[u8]) -> Result<BTreeMap<String, (String, String, bool)>> {
    let xml = Xml::parse(bytes)?;
    let root = xml.root(PKG, "Relationships")?;
    let mut result = BTreeMap::new();
    for node in xml.children(root, PKG, "Relationship") {
        let id = attr(node, "Id")?.to_string();
        if result
            .insert(
                id,
                (
                    attr(node, "Type")?.into(),
                    attr(node, "Target")?.into(),
                    node.attrs
                        .get("TargetMode")
                        .is_some_and(|s| s == "External"),
                ),
            )
            .is_some()
        {
            return Err(err("duplicate relationship ID"));
        }
    }
    Ok(result)
}

#[derive(Clone)]
struct Package {
    parts: BTreeMap<String, Vec<u8>>,
}
impl Package {
    fn read(path: &Path) -> Result<Self> {
        if path
            .extension()
            .and_then(|s| s.to_str())
            .is_none_or(|s| !s.eq_ignore_ascii_case("xlsx"))
        {
            return Err(err("only .xlsx packages are supported"));
        }
        if fs::metadata(path)?.len() > MAX_COMPRESSED as u64 {
            return Err(err("compressed package exceeds 130 MiB"));
        }
        Self::from_bytes(&fs::read(path)?)
    }
    fn from_bytes(bytes: &[u8]) -> Result<Self> {
        if bytes.len() > MAX_COMPRESSED {
            return Err(err("compressed package exceeds 130 MiB"));
        }
        let offset = (bytes.len().saturating_sub(65557)..bytes.len().saturating_sub(21))
            .rev()
            .find(|i| bytes.get(*i..*i + 4) == Some(b"PK\x05\x06"))
            .ok_or_else(|| err("ZIP end record missing"))?;
        let u16at = |i| u16::from_le_bytes([bytes[offset + i], bytes[offset + i + 1]]);
        let u32at = |i| u32::from_le_bytes(bytes[offset + i..offset + i + 4].try_into().unwrap());
        if u16at(4) != 0
            || u16at(6) != 0
            || u16at(8) != u16at(10)
            || usize::from(u16at(10)) > MAX_ENTRIES
            || u32at(12) as usize > MAX_XML
            || u32at(16) == u32::MAX
        {
            return Err(err("ZIP directory exceeds bounded single-volume profile"));
        }
        if offset + 22 + usize::from(u16at(20)) != bytes.len() {
            return Err(err("invalid ZIP trailing data"));
        }
        let mut cursor = u32at(16) as usize;
        let directory_end = cursor
            .checked_add(u32at(12) as usize)
            .ok_or_else(|| err("invalid ZIP directory bounds"))?;
        if directory_end != offset {
            return Err(err("unsupported ZIP directory layout"));
        }
        let mut records = 0;
        while cursor < directory_end {
            if cursor + 46 > directory_end || bytes.get(cursor..cursor + 4) != Some(b"PK\x01\x02") {
                return Err(err("invalid ZIP directory record"));
            }
            let len16 =
                |at| u16::from_le_bytes([bytes[cursor + at], bytes[cursor + at + 1]]) as usize;
            cursor += 46 + len16(28) + len16(30) + len16(32);
            records += 1;
            if records > MAX_ENTRIES || cursor > directory_end {
                return Err(err("ZIP directory work limit"));
            }
        }
        if records != u16at(10) as usize {
            return Err(err("ZIP directory count mismatch"));
        }
        let mut archive = ZipArchive::new(Cursor::new(bytes)).map_err(|e| err(e.to_string()))?;
        if archive.len() > MAX_ENTRIES {
            return Err(err("too many ZIP parts"));
        }
        let mut parts = BTreeMap::new();
        let mut total = 0;
        for index in 0..archive.len() {
            let mut member = archive.by_index(index).map_err(|e| err(e.to_string()))?;
            let name = member.name().to_string();
            if name.starts_with('/')
                || name.contains('\\')
                || name.split('/').any(|p| matches!(p, ".." | "." | ""))
                || member.encrypted()
            {
                return Err(err("unsafe, encrypted, or directory ZIP entry"));
            }
            let is_xml = name.to_ascii_lowercase().ends_with(".xml")
                || name.to_ascii_lowercase().ends_with(".rels");
            let limit = if is_xml { MAX_XML } else { MAX_PACKAGE };
            if member.size() > limit as u64 || total as u64 + member.size() > MAX_PACKAGE as u64 {
                return Err(err("uncompressed package size limit"));
            }
            let mut data = Vec::new();
            member
                .by_ref()
                .take(limit as u64 + 1)
                .read_to_end(&mut data)?;
            if data.len() > limit || data.len() as u64 != member.size() {
                return Err(err("invalid ZIP part size"));
            }
            total += data.len();
            if total > MAX_PACKAGE {
                return Err(err("uncompressed package size limit"));
            }
            if is_xml {
                Xml::parse(&data)?;
            }
            if name.to_ascii_lowercase().contains("vbaproject") {
                return Err(err("VBA content is forbidden"));
            }
            if parts.insert(name, data).is_some() {
                return Err(err("duplicate ZIP part"));
            }
        }
        Ok(Self { parts })
    }
    fn get(&self, part: &str) -> Result<&[u8]> {
        self.parts
            .get(part)
            .map(Vec::as_slice)
            .ok_or_else(|| err(format!("missing package part: {part}")))
    }
    fn encode(&self) -> Result<Vec<u8>> {
        if self.parts.len() > MAX_ENTRIES
            || self.parts.values().map(Vec::len).sum::<usize>() > MAX_PACKAGE
        {
            return Err(err("output package limit"));
        }
        let mut writer = ZipWriter::new(Cursor::new(Vec::new()));
        for (name, data) in &self.parts {
            if (name.to_ascii_lowercase().ends_with(".xml")
                || name.to_ascii_lowercase().ends_with(".rels"))
                && data.len() > MAX_XML
            {
                return Err(err("XML output limit"));
            }
            writer
                .start_file(
                    name,
                    SimpleFileOptions::default()
                        .compression_method(zip::CompressionMethod::Deflated),
                )
                .map_err(|e| err(e.to_string()))?;
            writer.write_all(data)?;
        }
        let bytes = writer
            .finish()
            .map_err(|e| err(e.to_string()))?
            .into_inner();
        if bytes.len() > MAX_COMPRESSED {
            return Err(err("compressed output limit"));
        }
        Ok(bytes)
    }
}
fn publish(package: &Package, path: &Path) -> Result<PathBuf> {
    if path
        .extension()
        .and_then(|s| s.to_str())
        .is_none_or(|s| !s.eq_ignore_ascii_case("xlsx"))
    {
        return Err(err("output must end in .xlsx"));
    }
    if path.exists() {
        return Err(err("output path already exists"));
    }
    let bytes = package.encode()?;
    Package::from_bytes(&bytes)?;
    let parent = path.parent().unwrap_or_else(|| Path::new("."));
    fs::create_dir_all(parent)?;
    let temporary = parent.join(format!(
        ".workbook-forge-{}-{}.tmp",
        std::process::id(),
        TEMP_ID.fetch_add(1, Ordering::Relaxed)
    ));
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)?;
    let result = (|| {
        file.write_all(&bytes)?;
        file.sync_all()?;
        fs::hard_link(&temporary, path)?;
        Ok(path.to_path_buf())
    })();
    drop(file);
    let _ = fs::remove_file(temporary);
    result
}

fn number(text: &str) -> Result<f64> {
    let value: f64 = text.parse().map_err(|_| err("invalid numeric value"))?;
    if !value.is_finite() {
        return Err(err("nonfinite numeric value"));
    }
    Ok(value)
}
fn integer(text: &str) -> Result<usize> {
    text.parse().map_err(|_| err("invalid integer metadata"))
}
fn boolean(text: &str) -> Result<bool> {
    match text {
        "1" | "true" => Ok(true),
        "0" | "false" => Ok(false),
        _ => Err(err("invalid Boolean metadata")),
    }
}
fn coordinate(address: &str) -> Result<(u32, u32)> {
    let cell = toolkit::CellReference::parse(None, address)?;
    Ok((cell.column, cell.row))
}
fn address(column: u32, row: u32) -> String {
    crate::encode_ref(column as usize, row as usize)
}
fn bounds(reference: &str) -> Result<(u32, u32, u32, u32)> {
    let (first, last) = reference.split_once(':').unwrap_or((reference, reference));
    let (c1, r1) = coordinate(first)?;
    let (c2, r2) = coordinate(last)?;
    if c1 > c2 || r1 > r2 {
        return Err(err("reversed result range"));
    }
    Ok((c1, r1, c2, r2))
}
fn block_range(sheet: &mut Sheet, reference: &str, reason: &str, work: &mut usize) -> Result<()> {
    let (c1, r1, c2, r2) = bounds(reference)?;
    *work = work.saturating_add(((c2 - c1 + 1) as usize).saturating_mul((r2 - r1 + 1) as usize));
    if *work > toolkit::MAX_WORKBOOK_CELLS {
        return Err(err("unsupported region expansion exceeds 100000 cells"));
    }
    for row in r1..=r2 {
        for col in c1..=c2 {
            let cell = sheet.cells.entry(address(col, row)).or_default();
            if cell.value != CellValue::Blank {
                cell.cached_value = Some(std::mem::take(&mut cell.value));
            }
            cell.blocked_reason = Some(reason.into());
        }
    }
    Ok(())
}
fn rich_text(xml: &Xml, node: &Node) -> String {
    let mut out = String::new();
    for child in &node.children {
        let child = &xml.nodes[*child];
        if child.ns != MAIN {
            continue;
        }
        if child.local == "t" {
            out.push_str(&child.text);
        } else if child.local == "r"
            && let Some(text) = xml.child(child, "t")
        {
            out.push_str(&text.text);
        }
    }
    out
}

fn stored_value(xml: &Xml, node: &Node, strings: &[String]) -> Result<CellValue> {
    let value = xml.child(node, "v").map(|v| v.text.as_str());
    match node.attrs.get("t").map(String::as_str).unwrap_or("n") {
        "inlineStr" => Ok(CellValue::Text(
            xml.child(node, "is")
                .map(|n| rich_text(xml, n))
                .unwrap_or_default(),
        )),
        "s" => match value {
            Some(v) => Ok(CellValue::Text(
                strings
                    .get(integer(v)?)
                    .ok_or_else(|| err("invalid shared string index"))?
                    .clone(),
            )),
            None => Ok(CellValue::Blank),
        },
        "str" | "d" => Ok(value
            .map(|v| CellValue::Text(v.into()))
            .unwrap_or(CellValue::Blank)),
        "b" => Ok(match value {
            Some(v) => CellValue::Boolean(boolean(v)?),
            None => CellValue::Blank,
        }),
        "e" => Ok(CellValue::Error {
            error: value.ok_or_else(|| err("empty error cell"))?.into(),
            message: None,
        }),
        "n" => Ok(match value {
            Some("") | None => CellValue::Blank,
            Some(v) => CellValue::Number(number(v)?),
        }),
        _ => Ok(CellValue::Blank),
    }
}
fn parse_styles(package: &Package, types: &BTreeMap<String, String>) -> Result<Vec<Style>> {
    let candidates: Vec<_> = types
        .iter()
        .filter(|(_, kind)| kind.ends_with("spreadsheetml.styles+xml"))
        .map(|(part, _)| part)
        .collect();
    if candidates.len() > 1 {
        return Err(err("multiple style tables"));
    }
    let Some(part) = candidates.first() else {
        return Ok(Vec::new());
    };
    let xml = Xml::parse(package.get(part)?)?;
    let root = xml.root(MAIN, "styleSheet")?;
    let mut formats: BTreeMap<usize, String> = [
        (0, "General"),
        (1, "0"),
        (2, "0.00"),
        (9, "0%"),
        (10, "0.00%"),
        (14, "mm-dd-yy"),
        (49, "@"),
    ]
    .into_iter()
    .map(|(id, s)| (id, s.into()))
    .collect();
    for node in xml
        .child(root, "numFmts")
        .into_iter()
        .flat_map(|n| xml.children(n, MAIN, "numFmt"))
    {
        formats.insert(
            integer(attr(node, "numFmtId")?)?,
            attr(node, "formatCode")?.into(),
        );
    }
    let fonts = xml
        .child(root, "fonts")
        .map(|n| xml.children(n, MAIN, "font").collect::<Vec<_>>())
        .unwrap_or_default();
    let fills = xml
        .child(root, "fills")
        .map(|n| xml.children(n, MAIN, "fill").collect::<Vec<_>>())
        .unwrap_or_default();
    let mut styles = Vec::new();
    if let Some(xfs) = xml.child(root, "cellXfs") {
        for xf in xml.children(xfs, MAIN, "xf") {
            let mut style = Style {
                number_format: formats
                    .get(&integer(
                        xf.attrs.get("numFmtId").map(String::as_str).unwrap_or("0"),
                    )?)
                    .cloned(),
                ..Style::default()
            };
            let font = fonts
                .get(integer(
                    xf.attrs.get("fontId").map(String::as_str).unwrap_or("0"),
                )?)
                .ok_or_else(|| err("style font index out of bounds"))?;
            if let Some(b) = xml.child(font, "b") {
                style.bold = Some(boolean(
                    b.attrs.get("val").map(String::as_str).unwrap_or("1"),
                )?);
            }
            style.font_color = xml
                .child(font, "color")
                .and_then(|c| c.attrs.get("rgb"))
                .cloned();
            let fill = fills
                .get(integer(
                    xf.attrs.get("fillId").map(String::as_str).unwrap_or("0"),
                )?)
                .ok_or_else(|| err("style fill index out of bounds"))?;
            if let Some(pattern) = xml.child(fill, "patternFill")
                && pattern
                    .attrs
                    .get("patternType")
                    .is_some_and(|s| s == "solid")
            {
                style.fill_color = xml
                    .child(pattern, "fgColor")
                    .and_then(|c| c.attrs.get("rgb"))
                    .cloned();
            }
            style.horizontal = xml
                .child(xf, "alignment")
                .and_then(|n| n.attrs.get("horizontal"))
                .filter(|s| matches!(s.as_str(), "left" | "center" | "right"))
                .cloned();
            styles.push(style);
        }
    }
    Ok(styles)
}
struct Decoded {
    model: WorkbookModel,
    workbook_part: String,
    sheet_parts: BTreeMap<String, String>,
    date1904: bool,
    capabilities: Json,
}
fn decode(
    package: &Package,
    inputs: BTreeMap<String, InputBinding>,
    outputs: BTreeMap<String, CellAddress>,
) -> Result<Decoded> {
    let ct = Xml::parse(package.get("[Content_Types].xml")?)?;
    let root = ct.root(CT, "Types")?;
    let mut types = BTreeMap::new();
    for node in ct.children(root, CT, "Override") {
        let kind = attr(node, "ContentType")?;
        if kind.to_ascii_lowercase().contains("macroenabled")
            || kind.to_ascii_lowercase().contains("vba")
        {
            return Err(err("macro content is forbidden"));
        }
        let part = attr(node, "PartName")?.trim_start_matches('/').to_string();
        if types.insert(part, kind.into()).is_some() {
            return Err(err("duplicate content type"));
        }
    }
    for node in ct.children(root, CT, "Default") {
        let kind = attr(node, "ContentType")?.to_ascii_lowercase();
        if kind.contains("macroenabled") || kind.contains("vba") {
            return Err(err("macro content is forbidden"));
        }
    }
    let roots = relationships(package.get("_rels/.rels")?)?;
    let offices: Vec<_> = roots
        .values()
        .filter(|(kind, _, _)| kind == &format!("{REL}/officeDocument"))
        .collect();
    if offices.len() != 1 || offices[0].2 {
        return Err(err("one internal workbook relationship required"));
    }
    let workbook_part = resolve("", &offices[0].1)?;
    if types.get(&workbook_part).map(String::as_str) != Some(WB_TYPE) {
        return Err(err(
            "macro-free transitional workbook content type required",
        ));
    }
    let wb = Xml::parse(package.get(&workbook_part)?)?;
    let wbroot = wb.root(MAIN, "workbook")?;
    let date1904 = wb
        .child(wbroot, "workbookPr")
        .and_then(|n| n.attrs.get("date1904"))
        .map(|s| boolean(s))
        .transpose()?
        .unwrap_or(false);
    let rels = relationships(package.get(&rel_part(&workbook_part))?)?;
    let mut strings = Vec::new();
    let string_parts: Vec<_> = types
        .iter()
        .filter(|(_, kind)| kind.ends_with("spreadsheetml.sharedStrings+xml"))
        .collect();
    if string_parts.len() > 1 {
        return Err(err("multiple shared string tables"));
    }
    if let Some((part, _)) = string_parts.first() {
        let xml = Xml::parse(package.get(part)?)?;
        let root = xml.root(MAIN, "sst")?;
        for item in xml.children(root, MAIN, "si") {
            strings.push(rich_text(&xml, item));
        }
    }
    let styles = parse_styles(package, &types)?;
    let mut model = WorkbookModel {
        inputs,
        outputs,
        ..WorkbookModel::default()
    };
    let mut sheet_parts = BTreeMap::new();
    let mut region_work = 0;
    let mut cell_work = 0;
    let mut width_work = 0;
    let mut table_metadata = Vec::new();
    let mut validation_metadata = Vec::new();
    let sheets = wb
        .child(wbroot, "sheets")
        .ok_or_else(|| err("workbook has no worksheets"))?;
    for node in wb.children(sheets, MAIN, "sheet") {
        let name = attr(node, "name")?;
        let rel_id = attr(node, &format!("{{{REL}}}id"))?;
        let (kind, target, external) = rels
            .get(rel_id)
            .ok_or_else(|| err("missing worksheet relationship"))?;
        if *external || kind != &format!("{REL}/worksheet") {
            return Err(err("only internal worksheets supported"));
        }
        let part = resolve(&workbook_part, target)?;
        if types.get(&part).map(String::as_str) != Some(WS_TYPE) {
            return Err(err("invalid worksheet content type"));
        }
        let xml = Xml::parse(package.get(&part)?)?;
        let root = xml.root(MAIN, "worksheet")?;
        let mut sheet = Sheet::new(name);
        sheet.id = attr(node, "sheetId")?.into();
        let mut groups = Vec::new();
        let mut masters = BTreeSet::new();
        let mut followers = Vec::new();
        let data = xml
            .child(root, "sheetData")
            .ok_or_else(|| err("worksheet missing sheetData"))?;
        for row in xml.children(data, MAIN, "row") {
            let row_number: usize = integer(attr(row, "r")?)?;
            for source in xml.children(row, MAIN, "c") {
                cell_work += 1;
                if cell_work > toolkit::MAX_WORKBOOK_CELLS {
                    return Err(err("import exceeds 100000 populated cells"));
                }
                let reference = attr(source, "r")?;
                let (col, r) = coordinate(reference)?;
                if r as usize != row_number || reference != address(col, r) {
                    return Err(err("cell/row coordinates must be canonical and consistent"));
                }
                let value = stored_value(&xml, source, &strings)?;
                let mut cell = Cell::default();
                if let Some(formula) = xml.child(source, "f") {
                    cell.formula = Some(formula.text.clone());
                    cell.cached_value = Some(value);
                    let kind = formula
                        .attrs
                        .get("t")
                        .map(String::as_str)
                        .unwrap_or("normal");
                    if kind != "normal" || formula.text.is_empty() {
                        cell.blocked_reason = Some(format!("unsupported formula kind {kind}"));
                    }
                    if date1904 {
                        cell.blocked_reason =
                            Some("1904 date-system formula calculation is unsupported".into());
                    }
                    if matches!(kind, "array" | "dataTable" | "shared") {
                        if let Some(reference) = formula.attrs.get("ref") {
                            bounds(reference)?;
                            groups.push(reference.clone());
                            if kind == "shared" {
                                masters.insert(attr(formula, "si")?.to_string());
                            }
                        } else if kind == "shared" {
                            followers.push(attr(formula, "si")?.to_string());
                        } else {
                            return Err(err("unbounded grouped formula"));
                        }
                    }
                } else {
                    cell.value = value;
                }
                if source.attrs.get("t").is_some_and(|s| {
                    !matches!(s.as_str(), "n" | "b" | "e" | "s" | "str" | "inlineStr")
                }) {
                    cell.blocked_reason = Some("unsupported stored cell type".into());
                }
                if let Some(style) = source.attrs.get("s") {
                    cell.style = Some(
                        styles
                            .get(integer(style)?)
                            .ok_or_else(|| err("invalid cell style index"))?
                            .clone(),
                    );
                }
                if sheet.cells.insert(reference.into(), cell).is_some() {
                    return Err(err("duplicate cell address"));
                }
            }
        }
        if followers.iter().any(|id| !masters.contains(id)) {
            return Err(err("shared formula follower missing master"));
        }
        for reference in groups {
            block_range(
                &mut sheet,
                &reference,
                "grouped formula result region",
                &mut region_work,
            )?;
        }
        if let Some(cols) = xml.child(root, "cols") {
            for col in xml.children(cols, MAIN, "col") {
                let first = integer(attr(col, "min")?)?;
                let last = integer(attr(col, "max")?)?;
                if first == 0 || last < first || last > 16384 {
                    return Err(err("invalid column range"));
                }
                width_work += last - first + 1;
                if width_work > 100_000 {
                    return Err(err("column expansion work limit"));
                }
                if let Some(width) = col.attrs.get("width") {
                    let width = number(width)?;
                    if !(0.0..=255.0).contains(&width) {
                        return Err(err("column width outside range"));
                    }
                    if width > 0.0 {
                        for column in first..=last {
                            sheet.column_widths.insert(
                                crate::encode_ref(column, 1).trim_end_matches('1').into(),
                                width,
                            );
                        }
                    }
                }
            }
        }
        if let Some(validations) = xml.child(root, "dataValidations") {
            for validation in xml.children(validations, MAIN, "dataValidation") {
                validation_metadata.push(json!({"sheet":name,"attributes":validation.attrs,"formula":xml.child(validation,"formula1").map(|n|&n.text)}));
            }
        }
        if let Some(tables) = xml.child(root, "tableParts") {
            let sheet_rels = relationships(package.get(&rel_part(&part))?)?;
            for table in xml.children(tables, MAIN, "tablePart") {
                let rid = attr(table, &format!("{{{REL}}}id"))?;
                let (kind, target, external) = sheet_rels
                    .get(rid)
                    .ok_or_else(|| err("missing table relationship"))?;
                if *external || kind != &format!("{REL}/table") {
                    return Err(err("invalid table relationship"));
                }
                let table_part = resolve(&part, target)?;
                if types
                    .get(&table_part)
                    .is_none_or(|s| !s.ends_with("spreadsheetml.table+xml"))
                {
                    return Err(err("invalid table content type"));
                }
                let tx = Xml::parse(package.get(&table_part)?)?;
                let tr = tx.root(MAIN, "table")?;
                let (c1, r1, c2, r2) = bounds(attr(tr, "ref")?)?;
                let header = integer(
                    tr.attrs
                        .get("headerRowCount")
                        .map(String::as_str)
                        .unwrap_or("1"),
                )?;
                let totals = integer(
                    tr.attrs
                        .get("totalsRowCount")
                        .map(String::as_str)
                        .unwrap_or("0"),
                )?;
                let insert = usize::from(boolean(
                    tr.attrs.get("insertRow").map(String::as_str).unwrap_or("0"),
                )?);
                let totals_shown = boolean(
                    tr.attrs
                        .get("totalsRowShown")
                        .map(String::as_str)
                        .unwrap_or("0"),
                )?;
                if header > 1 || totals > 1 || header + totals + insert > (r2 - r1 + 1) as usize {
                    return Err(err("unsupported table header/totals bounds"));
                }
                let (header, totals, insert) = (header as u32, totals as u32, insert as u32);
                let columns = tx
                    .child(tr, "tableColumns")
                    .ok_or_else(|| err("table missing columns"))?;
                let columns: Vec<_> = tx.children(columns, MAIN, "tableColumn").collect();
                if columns.len() != (c2 - c1 + 1) as usize {
                    return Err(err("table column count mismatch"));
                }
                for (index, column) in columns.iter().enumerate() {
                    let col = c1 + index as u32;
                    if tx.child(column, "calculatedColumnFormula").is_some() {
                        let (first, last) = if totals_shown && totals == 0 {
                            (r1, r2)
                        } else {
                            (r1 + header, r2 - totals - insert)
                        };
                        if first <= last {
                            block_range(
                                &mut sheet,
                                &format!("{}:{}", address(col, first), address(col, last)),
                                "table calculated-column region",
                                &mut region_work,
                            )?;
                        }
                    }
                    if tx.child(column, "totalsRowFormula").is_some()
                        || column
                            .attrs
                            .get("totalsRowFunction")
                            .is_some_and(|s| s != "none")
                    {
                        let first = if totals > 0 { r2 - totals + 1 } else { r1 };
                        block_range(
                            &mut sheet,
                            &format!("{}:{}", address(col, first), address(col, r2)),
                            "table totals result region",
                            &mut region_work,
                        )?;
                    }
                }
                table_metadata.push(json!({"sheet":name,"part":table_part,"attributes":tr.attrs}));
            }
        }
        if sheet_parts.values().any(|existing| existing == &part) {
            return Err(err("worksheets cannot share the same package part"));
        }
        sheet_parts.insert(name.into(), part);
        model.sheets.push(sheet);
    }
    if model.sheets.is_empty() {
        return Err(err("workbook has no worksheets"));
    }
    model.validate()?;
    let names: Vec<_> = wb
        .child(wbroot, "definedNames")
        .into_iter()
        .flat_map(|n| wb.children(n, MAIN, "definedName"))
        .map(|n| json!({"attributes":n.attrs,"expression":n.text}))
        .collect();
    let capabilities = json!({"backend":"rust-ooxml","date_system":if date1904{"1904"}else{"1900"},"styles":styles.len(),"defined_names":names,"tables":table_metadata,"validation":validation_metadata,"supported":["UTF-8 macro-free transitional XLSX","scalar cells","normal formulas","basic styles","column widths","existing-cell content edits"],"preserved_only":["unknown package parts","unknown worksheet XML","names","tables","imported styles and validation"],"rejected":["macros","external execution","new-cell insertion on import","imported style/layout/validation changes","1904 formula calculation and edits","grouped/table result edits"],"parts":package.parts.keys().collect::<Vec<_>>()});
    Ok(Decoded {
        model,
        workbook_part,
        sheet_parts,
        date1904,
        capabilities,
    })
}

/// An immutable source package plus a native transient workbook session.
pub struct ImportedWorkbook {
    source: PathBuf,
    baseline: Package,
    original: WorkbookModel,
    session: Session,
    workbook_part: String,
    sheet_parts: BTreeMap<String, String>,
    date1904: bool,
    capabilities: Json,
}
pub fn import_xlsx(
    path: impl AsRef<Path>,
    inputs: BTreeMap<String, InputBinding>,
    outputs: BTreeMap<String, CellAddress>,
) -> Result<ImportedWorkbook> {
    let path = path.as_ref();
    let package = Package::read(path)?;
    let decoded = decode(&package, inputs, outputs)?;
    Ok(ImportedWorkbook {
        source: fs::canonicalize(path)?,
        baseline: package,
        original: decoded.model.clone(),
        session: Session::new(decoded.model)?,
        workbook_part: decoded.workbook_part,
        sheet_parts: decoded.sheet_parts,
        date1904: decoded.date1904,
        capabilities: decoded.capabilities,
    })
}
impl ImportedWorkbook {
    pub fn snapshot(&self) -> WorkbookModel {
        self.session.snapshot()
    }
    pub fn capabilities(&self) -> &Json {
        &self.capabilities
    }
    pub fn calculate(&self, workers: usize) -> CalculationReport {
        self.session.calculate(workers)
    }
    pub fn apply(&self, edits: Vec<Edit>, expected_revision: Option<u64>) -> Result<u64> {
        for edit in &edits {
            if self.date1904 && edit.formula.is_some() {
                return Err(err("1904 formula edits are unsupported"));
            }
            if edit.style.is_some() {
                return Err(err("imported style edits are unsupported"));
            }
            let sheet = self
                .original
                .sheets
                .iter()
                .find(|s| s.name.eq_ignore_ascii_case(&edit.sheet))
                .ok_or_else(|| err("unknown worksheet"))?;
            let (col, row) = coordinate(&edit.address)?;
            if !sheet.cells.contains_key(&address(col, row)) {
                return Err(err("new-cell insertion on import is unsupported"));
            }
        }
        Ok(self.session.apply(edits, expected_revision)?)
    }
    pub fn set_inputs(
        &self,
        values: &BTreeMap<String, CellValue>,
        expected_revision: Option<u64>,
    ) -> Result<u64> {
        let model = self.snapshot();
        if expected_revision.is_some_and(|r| r != model.revision) {
            return Err(err("revision conflict"));
        }
        self.apply(
            toolkit::validate_inputs(&model, values)?,
            Some(model.revision),
        )
    }
    pub fn export(&self, path: impl AsRef<Path>) -> Result<PathBuf> {
        let path = path.as_ref();
        let canonical_parent = path
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .unwrap_or_else(|| Path::new("."));
        let same_original_path = fs::canonicalize(canonical_parent)
            .ok()
            .zip(path.file_name())
            .is_some_and(|(parent, name)| parent.join(name) == self.source);
        if same_original_path || (path.exists() && fs::canonicalize(path)? == self.source) {
            return Err(err("refusing source overwrite"));
        }
        let model = self.snapshot();
        let report = self.calculate(1);
        if report.stale || report.revision != model.revision {
            return Err(err("stale calculation during export"));
        }
        let mut package = self.baseline.clone();
        patch(
            &mut package,
            &self.original,
            &model,
            &report,
            &self.workbook_part,
            &self.sheet_parts,
            self.date1904,
        )?;
        if self.snapshot().revision != model.revision {
            return Err(err("model changed during export"));
        }
        publish(&package, path)
    }
}

fn validation(binding: &InputBinding) -> Result<String> {
    let (c, r) = coordinate(&binding.address)?;
    let address = address(c, r);
    let kind = match binding.kind.as_str() {
        "number" => "ISNUMBER",
        "text" => "ISTEXT",
        "boolean" => "ISLOGICAL",
        _ => return Err(err("invalid input type")),
    };
    let mut clauses = vec![format!("{kind}({address})")];
    if let Some(min) = binding.min {
        clauses.push(format!(
            "{address}>={}",
            serde_json::to_string(&min).unwrap()
        ));
    }
    if let Some(max) = binding.max {
        clauses.push(format!(
            "{address}<={}",
            serde_json::to_string(&max).unwrap()
        ));
    }
    if let Some(choices) = &binding.choices {
        let mut options = Vec::new();
        for choice in choices {
            options.push(match choice {
                CellValue::Text(text) => {
                    format!("EXACT({address},\"{}\")", text.replace('"', "\"\""))
                }
                CellValue::Boolean(b) => {
                    format!("{address}={}()", if *b { "TRUE" } else { "FALSE" })
                }
                CellValue::Number(n) => format!("{address}={}", serde_json::to_string(n).unwrap()),
                _ => return Err(err("unsupported XLSX validation choice")),
            });
        }
        clauses.push(format!("OR({})", options.join(",")));
    }
    let mut formula = format!("AND({})", clauses.join(","));
    if !binding.required {
        formula = format!("OR(ISBLANK({address}),{formula})");
    }
    if formula.encode_utf16().count() > 255 {
        return Err(err("XLSX validation formula exceeds 255 UTF-16 units"));
    }
    xml_escape(&formula)?;
    Ok(formula)
}
fn checked_report(report: &CalculationReport) -> Result<()> {
    if report.stale || !report.diagnostics.is_empty() {
        return Err(err(format!(
            "calculation cannot be exported: {:?}",
            report.diagnostics
        )));
    }
    Ok(())
}
fn formula_cache(
    report: &CalculationReport,
    sheet: &str,
    address: &str,
) -> Result<Option<CellValue>> {
    match report.values.get(&format!("{sheet}!{address}")) {
        Some(toolkit::CalculatedValue::Scalar(value)) => Ok(Some(value.clone())),
        Some(toolkit::CalculatedValue::Array { .. }) => {
            Err(err("worksheet array spill export is unsupported"))
        }
        None => Ok(None),
    }
}
fn cell_xml(
    reference: &str,
    cell: &Cell,
    cache: Option<&CellValue>,
    original: Option<(&Xml, &Node)>,
    style: Option<usize>,
) -> Result<String> {
    let prefix = original
        .and_then(|(_, n)| n.name.rsplit_once(':').map(|(p, _)| format!("{p}:")))
        .unwrap_or_default();
    let mut attributes = original
        .map(|(_, n)| {
            n.attrs
                .iter()
                .filter(|(k, _)| !k.starts_with('{'))
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect::<BTreeMap<_, _>>()
        })
        .unwrap_or_default();
    attributes.insert("r".into(), reference.into());
    attributes.remove("t");
    if let Some(style) = style {
        attributes.insert("s".into(), style.to_string());
    }
    let value = if cell.formula.is_some() {
        cache
    } else {
        Some(&cell.value)
    };
    let mut body = String::new();
    if let Some(formula) = &cell.formula {
        if let Some((xml, node)) = original
            && let Some(old) = xml.child(node, "f")
            && old.text == formula.trim_start_matches('=')
        {
            body.push_str(std::str::from_utf8(xml.raw(old)).unwrap());
        } else {
            body.push_str(&format!(
                "<{prefix}f>{}</{prefix}f>",
                xml_escape(formula.strip_prefix('=').unwrap_or(formula))?
            ));
        }
    }
    if let Some(value) = value {
        match value {
            CellValue::Blank => {}
            CellValue::Number(n) => {
                body.push_str(&format!("<{prefix}v>{n}</{prefix}v>"));
            }
            CellValue::Boolean(b) => {
                attributes.insert("t".into(), "b".into());
                body.push_str(&format!("<{prefix}v>{}</{prefix}v>", u8::from(*b)));
            }
            CellValue::Error { error, .. } => {
                attributes.insert("t".into(), "e".into());
                body.push_str(&format!("<{prefix}v>{}</{prefix}v>", xml_escape(error)?));
            }
            CellValue::Text(text) => {
                if cell.formula.is_some() {
                    attributes.insert("t".into(), "str".into());
                    body.push_str(&format!("<{prefix}v>{}</{prefix}v>", xml_escape(text)?));
                } else {
                    attributes.insert("t".into(), "inlineStr".into());
                    body.push_str(&format!(
                        "<{prefix}is><{prefix}t xml:space=\"preserve\">{}</{prefix}t></{prefix}is>",
                        xml_escape(text)?
                    ));
                }
            }
        }
    }
    if let Some((xml, node)) = original {
        for child in &node.children {
            let child = &xml.nodes[*child];
            if child.ns != MAIN || !matches!(child.local.as_str(), "f" | "v" | "is") {
                body.push_str(std::str::from_utf8(xml.raw(child)).unwrap());
            }
        }
    }
    let attrs = attributes
        .iter()
        .map(|(k, v)| Ok(format!(" {k}=\"{}\"", xml_escape(v)?)))
        .collect::<Result<Vec<_>>>()?
        .join("");
    Ok(format!("<{prefix}c{attrs}>{body}</{prefix}c>"))
}
fn rgb(color: &str) -> String {
    if color.len() == 6 {
        format!("FF{}", color.to_ascii_uppercase())
    } else {
        color.to_ascii_uppercase()
    }
}
fn style_table(model: &WorkbookModel) -> Result<(String, BTreeMap<String, usize>)> {
    let default = Style::default();
    let mut styles = vec![default.clone()];
    let mut ids = BTreeMap::from([(serde_json::to_string(&default).unwrap(), 0)]);
    let mut work = 512;
    for sheet in &model.sheets {
        for cell in sheet.cells.values() {
            if let Some(style) = &cell.style {
                let key = serde_json::to_string(style).unwrap();
                if let std::collections::btree_map::Entry::Vacant(entry) = ids.entry(key) {
                    work += entry.key().len() * 6 + 512;
                    if work > MAX_XML {
                        return Err(err("style XML work limit"));
                    }
                    entry.insert(styles.len());
                    styles.push(style.clone());
                }
            }
        }
    }
    let mut formats = String::new();
    let mut fonts = String::new();
    let mut fills = String::from(
        "<fill><patternFill patternType=\"none\"/></fill><fill><patternFill patternType=\"gray125\"/></fill>",
    );
    let mut xfs = String::new();
    let mut fill_count = 2;
    let mut format_count = 0;
    for (index, style) in styles.iter().enumerate() {
        fonts.push_str("<font><sz val=\"11\"/><name val=\"Arial\"/>");
        if style.bold == Some(true) {
            fonts.push_str("<b/>");
        }
        if let Some(color) = &style.font_color {
            fonts.push_str(&format!("<color rgb=\"{}\"/>", rgb(color)));
        }
        fonts.push_str("</font>");
        let fill = if let Some(color) = &style.fill_color {
            let id = fill_count;
            fill_count += 1;
            fills.push_str(&format!("<fill><patternFill patternType=\"solid\"><fgColor rgb=\"{}\"/><bgColor indexed=\"64\"/></patternFill></fill>",rgb(color)));
            id
        } else {
            0
        };
        let format = if let Some(code) = &style.number_format {
            let id = 164 + index;
            format_count += 1;
            formats.push_str(&format!(
                "<numFmt numFmtId=\"{id}\" formatCode=\"{}\"/>",
                xml_escape(code)?
            ));
            id
        } else {
            0
        };
        xfs.push_str(&format!("<xf numFmtId=\"{format}\" fontId=\"{index}\" fillId=\"{fill}\" borderId=\"0\" xfId=\"0\" applyNumberFormat=\"1\" applyFont=\"1\" applyFill=\"1\""));
        if let Some(horizontal) = &style.horizontal {
            xfs.push_str(&format!(
                " applyAlignment=\"1\"><alignment horizontal=\"{horizontal}\"/></xf>"
            ));
        } else {
            xfs.push_str("/>");
        }
    }
    Ok((
        format!(
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?><styleSheet xmlns=\"{MAIN}\"><numFmts count=\"{format_count}\">{formats}</numFmts><fonts count=\"{}\">{fonts}</fonts><fills count=\"{fill_count}\">{fills}</fills><borders count=\"1\"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count=\"1\"><xf numFmtId=\"0\" fontId=\"0\" fillId=\"0\" borderId=\"0\"/></cellStyleXfs><cellXfs count=\"{}\">{xfs}</cellXfs><cellStyles count=\"1\"><cellStyle name=\"Normal\" xfId=\"0\" builtinId=\"0\"/></cellStyles></styleSheet>",
            styles.len(),
            styles.len()
        ),
        ids,
    ))
}
fn fresh(model: &WorkbookModel, report: &CalculationReport) -> Result<Package> {
    model.validate()?;
    checked_report(report)?;
    if model.sheets.is_empty() {
        return Err(err("at least one worksheet is required"));
    }
    let (mut parts, mut sheet_nodes, mut rel_nodes, mut type_nodes) =
        (BTreeMap::new(), String::new(), String::new(), String::new());
    let (styles, style_ids) = style_table(model)?;
    parts.insert("xl/styles.xml".into(), styles.into_bytes());
    for (index, sheet) in model.sheets.iter().enumerate() {
        let id = index + 1;
        let part = format!("xl/worksheets/sheet{id}.xml");
        sheet_nodes.push_str(&format!(
            "<sheet name=\"{}\" sheetId=\"{id}\" r:id=\"rId{id}\"/>",
            xml_escape(&sheet.name)?
        ));
        rel_nodes.push_str(&format!("<Relationship Id=\"rId{id}\" Type=\"{REL}/worksheet\" Target=\"worksheets/sheet{id}.xml\"/>"));
        type_nodes.push_str(&format!(
            "<Override PartName=\"/{part}\" ContentType=\"{WS_TYPE}\"/>"
        ));
        let mut xml =
            format!("<?xml version=\"1.0\" encoding=\"UTF-8\"?><worksheet xmlns=\"{MAIN}\">");
        if !sheet.column_widths.is_empty() {
            xml.push_str("<cols>");
            let mut widths = sheet
                .column_widths
                .iter()
                .map(|(c, w)| Ok((coordinate(&format!("{c}1"))?.0, w)))
                .collect::<Result<Vec<_>>>()?;
            widths.sort_by_key(|(c, _)| *c);
            for (column, width) in widths {
                xml.push_str(&format!(
                    "<col min=\"{column}\" max=\"{column}\" width=\"{width}\" customWidth=\"1\"/>"
                ));
            }
            xml.push_str("</cols>");
        }
        xml.push_str("<sheetData>");
        let mut cells = sheet
            .cells
            .iter()
            .map(|(a, c)| Ok((coordinate(a)?, a, c)))
            .collect::<Result<Vec<_>>>()?;
        cells.sort_by_key(|((c, r), _, _)| (*r, *c));
        let mut row = 0;
        for ((_, r), reference, cell) in cells {
            if cell.blocked_reason.is_some() {
                return Err(err("fresh export cannot invent preserved-only content"));
            }
            if r != row {
                if row != 0 {
                    xml.push_str("</row>");
                }
                xml.push_str(&format!("<row r=\"{r}\">"));
                row = r;
            }
            let cache = if cell.formula.is_some() {
                formula_cache(report, &sheet.name, reference)?
            } else {
                None
            };
            let style = style_ids
                .get(
                    &serde_json::to_string(cell.style.as_ref().unwrap_or(&Style::default()))
                        .unwrap(),
                )
                .copied();
            xml.push_str(&cell_xml(reference, cell, cache.as_ref(), None, style)?);
            if xml.len() > MAX_XML {
                return Err(err("worksheet XML exceeds 32 MiB"));
            }
        }
        if row != 0 {
            xml.push_str("</row>");
        }
        xml.push_str("</sheetData>");
        let bindings: Vec<_> = model
            .inputs
            .values()
            .filter(|b| b.sheet.eq_ignore_ascii_case(&sheet.name))
            .collect();
        if !bindings.is_empty() {
            xml.push_str(&format!("<dataValidations count=\"{}\">", bindings.len()));
            for binding in bindings {
                let (c, r) = coordinate(&binding.address)?;
                xml.push_str(&format!("<dataValidation type=\"custom\" allowBlank=\"{}\" showErrorMessage=\"1\" errorStyle=\"stop\" sqref=\"{}\"><formula1>{}</formula1></dataValidation>",u8::from(!binding.required),address(c,r),xml_escape(&validation(binding)?)?));
                if xml.len() > MAX_XML {
                    return Err(err("validation XML exceeds 32 MiB"));
                }
            }
            xml.push_str("</dataValidations>");
        }
        xml.push_str("</worksheet>");
        parts.insert(part, xml.into_bytes());
        if parts.values().map(Vec::len).sum::<usize>() > MAX_PACKAGE {
            return Err(err("output package exceeds 128 MiB"));
        }
    }
    parts.insert("xl/workbook.xml".into(),format!("<?xml version=\"1.0\" encoding=\"UTF-8\"?><workbook xmlns=\"{MAIN}\" xmlns:r=\"{REL}\"><workbookPr date1904=\"0\"/><sheets>{sheet_nodes}</sheets><calcPr calcMode=\"auto\" fullCalcOnLoad=\"1\" forceFullCalc=\"1\"/></workbook>").into_bytes());
    parts.insert("xl/_rels/workbook.xml.rels".into(),format!("<?xml version=\"1.0\" encoding=\"UTF-8\"?><Relationships xmlns=\"{PKG}\">{rel_nodes}<Relationship Id=\"styles\" Type=\"{REL}/styles\" Target=\"styles.xml\"/></Relationships>").into_bytes());
    parts.insert("_rels/.rels".into(),format!("<?xml version=\"1.0\" encoding=\"UTF-8\"?><Relationships xmlns=\"{PKG}\"><Relationship Id=\"rId1\" Type=\"{REL}/officeDocument\" Target=\"xl/workbook.xml\"/></Relationships>").into_bytes());
    parts.insert("[Content_Types].xml".into(),format!("<?xml version=\"1.0\" encoding=\"UTF-8\"?><Types xmlns=\"{CT}\"><Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/><Default Extension=\"xml\" ContentType=\"application/xml\"/><Override PartName=\"/xl/workbook.xml\" ContentType=\"{WB_TYPE}\"/>{type_nodes}<Override PartName=\"/xl/styles.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml\"/></Types>").into_bytes());
    Ok(Package { parts })
}
/// Generate a new workbook using native calculation and original OOXML code.
pub fn export_xlsx(model: &WorkbookModel, path: impl AsRef<Path>) -> Result<PathBuf> {
    let report = toolkit::calculate(model, 1);
    publish(&fresh(model, &report)?, path.as_ref())
}

fn replace_spans(bytes: &[u8], mut patches: Vec<(usize, usize, Vec<u8>)>) -> Result<Vec<u8>> {
    patches.sort_by_key(|p| p.0);
    let mut out = Vec::new();
    let mut cursor = 0;
    for (start, end, replacement) in patches {
        if start < cursor || end < start || end > bytes.len() {
            return Err(err("overlapping or invalid XML patches"));
        }
        out.extend_from_slice(&bytes[cursor..start]);
        out.extend(replacement);
        cursor = end;
        if out.len() > MAX_XML {
            return Err(err("patched XML exceeds 32 MiB"));
        }
    }
    out.extend_from_slice(&bytes[cursor..]);
    if out.len() > MAX_XML {
        return Err(err("patched XML exceeds 32 MiB"));
    }
    Ok(out)
}
fn verify_validation(xml: &Xml, model: &WorkbookModel, sheet: &str) -> Result<()> {
    for binding in model
        .inputs
        .values()
        .filter(|b| b.sheet.eq_ignore_ascii_case(sheet))
    {
        let (c, r) = coordinate(&binding.address)?;
        let mut matching = Vec::new();
        for node in xml
            .child(xml.root(MAIN, "worksheet")?, "dataValidations")
            .into_iter()
            .flat_map(|n| xml.children(n, MAIN, "dataValidation"))
        {
            for reference in attr(node, "sqref")?.split_whitespace() {
                let (c1, r1, c2, r2) = bounds(reference)?;
                if c >= c1 && c <= c2 && r >= r1 && r <= r2 {
                    matching.push(node);
                    break;
                }
            }
        }
        let valid = if matching.len() == 1 {
            let node = matching[0];
            node.attrs.get("type").is_some_and(|s| s == "custom")
                && node.attrs.get("errorStyle").is_none_or(|s| s == "stop")
                && boolean(
                    node.attrs
                        .get("showErrorMessage")
                        .map(String::as_str)
                        .unwrap_or("0"),
                )?
                && boolean(
                    node.attrs
                        .get("allowBlank")
                        .map(String::as_str)
                        .unwrap_or("0"),
                )? == !binding.required
                && xml.child(node, "formula1").is_some_and(|f| {
                    f.text.trim().trim_start_matches('=') == validation(binding).unwrap_or_default()
                })
        } else {
            false
        };
        if !valid {
            return Err(err(format!(
                "input validation mismatch at {sheet}!{}; imported validation changes are unsupported",
                binding.address
            )));
        }
    }
    Ok(())
}
fn recalc(package: &mut Package, workbook_part: &str) -> Result<()> {
    let rel_path = rel_part(workbook_part);
    let rel_xml = Xml::parse(package.get(&rel_path)?)?;
    let mut rel_patches = Vec::new();
    let mut removed = BTreeSet::new();
    for node in rel_xml.children(rel_xml.root(PKG, "Relationships")?, PKG, "Relationship") {
        if node
            .attrs
            .get("Type")
            .is_some_and(|s| s == &format!("{REL}/calcChain"))
        {
            if node
                .attrs
                .get("TargetMode")
                .is_some_and(|s| s == "External")
            {
                return Err(err("external calculation chain"));
            }
            removed.insert(resolve(workbook_part, attr(node, "Target")?)?);
            rel_patches.push((node.start, node.end, Vec::new()));
        }
    }
    if !rel_patches.is_empty() {
        package
            .parts
            .insert(rel_path, replace_spans(&rel_xml.bytes, rel_patches)?);
        let types = Xml::parse(package.get("[Content_Types].xml")?)?;
        let patches = types
            .children(types.root(CT, "Types")?, CT, "Override")
            .filter(|n| {
                n.attrs
                    .get("PartName")
                    .is_some_and(|s| removed.contains(s.trim_start_matches('/')))
            })
            .map(|n| (n.start, n.end, Vec::new()))
            .collect();
        package.parts.insert(
            "[Content_Types].xml".into(),
            replace_spans(&types.bytes, patches)?,
        );
        for part in removed {
            package.parts.remove(&part);
        }
    }
    let xml = Xml::parse(package.get(workbook_part)?)?;
    let root = xml.root(MAIN, "workbook")?;
    let prefix = root
        .name
        .rsplit_once(':')
        .map(|(p, _)| format!("{p}:"))
        .unwrap_or_default();
    let node = xml.child(root, "calcPr");
    let mut attrs = node.map(|n| n.attrs.clone()).unwrap_or_default();
    attrs.insert("calcMode".into(), "auto".into());
    attrs.insert("fullCalcOnLoad".into(), "1".into());
    attrs.insert("forceFullCalc".into(), "1".into());
    let attributes = attrs
        .iter()
        .filter(|(k, _)| !k.starts_with('{'))
        .map(|(k, v)| Ok(format!(" {k}=\"{}\"", xml_escape(v)?)))
        .collect::<Result<Vec<_>>>()?
        .join("");
    let replacement = format!("<{prefix}calcPr{attributes}/>").into_bytes();
    let patch = if let Some(node) = node {
        (node.start, node.end, replacement)
    } else {
        let trailing = [
            "oleSize",
            "customWorkbookViews",
            "pivotCaches",
            "smartTagPr",
            "smartTagTypes",
            "webPublishing",
            "fileRecoveryPr",
            "webPublishObjects",
            "extLst",
        ];
        let offset = root
            .children
            .iter()
            .map(|id| &xml.nodes[*id])
            .find(|n| n.ns == MAIN && trailing.contains(&n.local.as_str()))
            .map(|n| n.start)
            .unwrap_or(root.close_start);
        (offset, offset, replacement)
    };
    package.parts.insert(
        workbook_part.into(),
        replace_spans(&xml.bytes, vec![patch])?,
    );
    Ok(())
}
fn patch(
    package: &mut Package,
    original: &WorkbookModel,
    model: &WorkbookModel,
    report: &CalculationReport,
    workbook_part: &str,
    sheet_parts: &BTreeMap<String, String>,
    date1904: bool,
) -> Result<()> {
    checked_report(report)?;
    if original.inputs != model.inputs || original.sheets.len() != model.sheets.len() {
        return Err(err("imported structure/validation edits are unsupported"));
    }
    for (before, after) in original.sheets.iter().zip(&model.sheets) {
        if before.id != after.id
            || before.name != after.name
            || before.column_widths != after.column_widths
        {
            return Err(err("imported layout edits are unsupported"));
        }
        let part = &sheet_parts[&after.name];
        let xml = Xml::parse(package.get(part)?)?;
        verify_validation(&xml, model, &after.name)?;
        let data = xml
            .child(xml.root(MAIN, "worksheet")?, "sheetData")
            .ok_or_else(|| err("missing sheetData"))?;
        let cells: BTreeMap<_, _> = xml
            .children(data, MAIN, "row")
            .flat_map(|row| xml.children(row, MAIN, "c"))
            .map(|n| Ok((attr(n, "r")?.to_string(), n)))
            .collect::<Result<_>>()?;
        let mut patches = Vec::new();
        for (reference, cell) in &after.cells {
            let old = before
                .cells
                .get(reference)
                .ok_or_else(|| err("new-cell insertion is unsupported"))?;
            if old.style != cell.style {
                return Err(err("imported style edits are unsupported"));
            }
            let content_changed = old.value != cell.value || old.formula != cell.formula;
            let cache = if cell.formula.is_some() {
                formula_cache(report, &after.name, reference)?
            } else {
                None
            };
            if !content_changed && cache.is_none() {
                continue;
            }
            if old.blocked_reason.is_some() || cell.blocked_reason.is_some() {
                return Err(err("cannot change preserved-only cell"));
            }
            if date1904 && (cell.formula.is_some() || old.formula.is_some()) {
                return Err(err("1904 formula edits/caches are unsupported"));
            }
            let node = cells
                .get(reference)
                .ok_or_else(|| err("missing imported cell XML"))?;
            patches.push((
                node.start,
                node.end,
                cell_xml(reference, cell, cache.as_ref(), Some((&xml, node)), None)?.into_bytes(),
            ));
        }
        if !patches.is_empty() {
            package
                .parts
                .insert(part.clone(), replace_spans(&xml.bytes, patches)?);
        }
    }
    recalc(package, workbook_part)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    struct Temp(PathBuf);
    impl Temp {
        fn new() -> Self {
            let unique = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            let path = std::env::temp_dir().join(format!(
                "workbook-forge-rust-test-{}-{unique}-{}",
                std::process::id(),
                TEMP_ID.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&path).unwrap();
            Self(path)
        }
        fn path(&self, name: &str) -> PathBuf {
            self.0.join(name)
        }
    }
    impl Drop for Temp {
        fn drop(&mut self) {
            if let Ok(entries) = fs::read_dir(&self.0) {
                for entry in entries.flatten() {
                    let _ = fs::remove_file(entry.path());
                }
            }
            let _ = fs::remove_dir(&self.0);
        }
    }
    fn scenario_package() -> Package {
        let model = toolkit::operating_scenario();
        fresh(&model, &toolkit::calculate(&model, 1)).unwrap()
    }
    fn modify(package: &mut Package, part: &str, from: &str, to: &str) {
        let source = String::from_utf8(package.parts[part].clone()).unwrap();
        assert!(source.contains(from));
        package
            .parts
            .insert(part.into(), source.replace(from, to).into_bytes());
    }
    #[test]
    fn native_scenario_roundtrip_and_new_path_contract() {
        let temp = Temp::new();
        let model = toolkit::operating_scenario();
        let source = temp.path("scenario.xlsx");
        export_xlsx(&model, &source).unwrap();
        let imported = import_xlsx(&source, model.inputs.clone(), model.outputs.clone()).unwrap();
        assert_eq!(
            imported.calculate(1).outputs,
            toolkit::calculate(&model, 1).outputs
        );
        assert!(imported.capabilities()["styles"].as_u64().unwrap() > 1);
        imported
            .set_inputs(
                &BTreeMap::from([("unit_price".into(), CellValue::Number(25.0))]),
                Some(0),
            )
            .unwrap();
        let output = temp.path("edited.xlsx");
        imported.export(&output).unwrap();
        let reopened = import_xlsx(&output, model.inputs, model.outputs).unwrap();
        assert_eq!(
            reopened.calculate(1).outputs["revenue"],
            toolkit::CalculatedValue::Scalar(CellValue::Number(9250.0))
        );
        assert!(imported.export(&output).is_err());
        assert!(imported.export(&source).is_err());
    }
    #[test]
    fn immutable_baseline_preserves_unknown_parts_and_unrelated_formula_xml() {
        let temp = Temp::new();
        let mut package = scenario_package();
        package
            .parts
            .insert("custom/payload.bin".into(), vec![0, 255, 7, 19]);
        modify(
            &mut package,
            "xl/worksheets/sheet2.xml",
            "</sheetData>",
            "<row r=\"10\"><c r=\"K10\"><f>UNKNOWN_FUNCTION(1)</f><v>123</v><extLst><ext uri=\"urn:opaque\"><x:payload xmlns:x=\"urn:opaque\">opaque</x:payload></ext></extLst></c></row></sheetData>",
        );
        let untouched = package.parts["xl/worksheets/sheet2.xml"].clone();
        let source = temp.path("source.xlsx");
        publish(&package, &source).unwrap();
        let model = toolkit::operating_scenario();
        let imported = import_xlsx(&source, model.inputs, model.outputs).unwrap();
        fs::write(&source, b"source path replaced after import").unwrap();
        imported
            .set_inputs(
                &BTreeMap::from([("unit_price".into(), CellValue::Number(25.0))]),
                None,
            )
            .unwrap();
        let target = temp.path("preserved.xlsx");
        imported.export(&target).unwrap();
        let output = Package::read(&target).unwrap();
        assert_eq!(output.parts["custom/payload.bin"], vec![0, 255, 7, 19]);
        let old = Xml::parse(&untouched).unwrap();
        let new = Xml::parse(output.get("xl/worksheets/sheet2.xml").unwrap()).unwrap();
        let old_cell = old
            .all(MAIN, "c")
            .find(|n| n.attrs.get("r").is_some_and(|s| s == "K10"))
            .unwrap();
        let new_cell = new
            .all(MAIN, "c")
            .find(|n| n.attrs.get("r").is_some_and(|s| s == "K10"))
            .unwrap();
        assert_eq!(old.raw(old_cell), new.raw(new_cell));
    }
    #[test]
    fn imported_edit_guards_are_atomic_and_snapshots_cannot_tamper_caches() {
        let temp = Temp::new();
        let model = toolkit::operating_scenario();
        let source = temp.path("source.xlsx");
        export_xlsx(&model, &source).unwrap();
        let imported = import_xlsx(&source, model.inputs, model.outputs).unwrap();
        let before = imported.snapshot();
        let edits = vec![
            Edit::value("Assumptions", "B1", CellValue::Number(30.0)),
            Edit::formula("Forecast", "Z100", "=1"),
        ];
        assert!(imported.apply(edits, None).is_err());
        assert_eq!(imported.snapshot(), before);
        assert!(
            imported
                .apply(
                    vec![Edit {
                        sheet: "Assumptions".into(),
                        address: "B1".into(),
                        value: None,
                        formula: None,
                        style: Some(Style {
                            bold: Some(true),
                            ..Style::default()
                        })
                    }],
                    None
                )
                .is_err()
        );
        let mut detached = imported.snapshot();
        detached.sheets[0].cells.get_mut("B1").unwrap().value = CellValue::Number(999.0);
        assert_eq!(imported.snapshot(), before);
        assert!(
            imported
                .apply(
                    vec![Edit::value("Assumptions", "B1", CellValue::Number(30.0))],
                    Some(99)
                )
                .is_err()
        );
    }
    #[test]
    fn grouped_result_caches_never_become_inputs() {
        let temp = Temp::new();
        let mut package = scenario_package();
        modify(
            &mut package,
            "xl/worksheets/sheet1.xml",
            "</sheetData>",
            "<row r=\"10\"><c r=\"A10\"><f t=\"array\" ref=\"A10:A11\">1</f><v>99</v></c><c r=\"B10\"><f>A11+1</f><v>100</v></c></row><row r=\"11\"><c r=\"A11\"><v>99</v></c></row></sheetData>",
        );
        let source = temp.path("group.xlsx");
        publish(&package, &source).unwrap();
        let imported = import_xlsx(
            source,
            BTreeMap::new(),
            BTreeMap::from([("r".into(), CellAddress::new("Assumptions", "B10"))]),
        )
        .unwrap();
        let snap = imported.snapshot();
        let cell = &snap.sheets[0].cells["A11"];
        assert_eq!(cell.value, CellValue::Blank);
        assert_eq!(cell.cached_value, Some(CellValue::Number(99.0)));
        assert!(!imported.calculate(1).diagnostics.is_empty());
        let target = temp.path("blocked.xlsx");
        assert!(imported.export(&target).is_err());
        assert!(!target.exists());
    }
    #[test]
    fn date1904_existing_and_new_formulas_remain_blocked() {
        let temp = Temp::new();
        let mut package = scenario_package();
        modify(
            &mut package,
            "xl/workbook.xml",
            "date1904=\"0\"",
            "date1904=\"1\"",
        );
        let source = temp.path("date1904.xlsx");
        publish(&package, &source).unwrap();
        let imported = import_xlsx(
            source,
            BTreeMap::new(),
            BTreeMap::from([("input".into(), CellAddress::new("Assumptions", "B1"))]),
        )
        .unwrap();
        assert!(
            imported
                .apply(
                    vec![Edit::formula("Assumptions", "B1", "=DATE(2024,1,1)")],
                    None
                )
                .is_err()
        );
        assert!(
            imported
                .apply(
                    vec![Edit::formula("Forecast", "F2", "=DATE(2024,1,1)")],
                    None
                )
                .is_err()
        );
        imported.export(temp.path("preserved1904.xlsx")).unwrap();
        assert_eq!(imported.snapshot().revision, 0);
    }
    #[test]
    fn imported_validation_changes_are_not_silently_accepted() {
        let temp = Temp::new();
        let mut package = scenario_package();
        modify(
            &mut package,
            "xl/worksheets/sheet1.xml",
            "B1&gt;=0.0",
            "B1&gt;=1.0",
        );
        let source = temp.path("different-validation.xlsx");
        publish(&package, &source).unwrap();
        let model = toolkit::operating_scenario();
        let imported = import_xlsx(source, model.inputs, model.outputs).unwrap();
        let output = temp.path("rejected.xlsx");
        assert!(
            imported
                .export(&output)
                .unwrap_err()
                .0
                .contains("validation mismatch")
        );
        assert!(!output.exists());
    }
    #[test]
    fn scalar_error_caches_and_array_export_boundaries() {
        let temp = Temp::new();
        let mut model = toolkit::operating_scenario();
        model.sheets[0].cells.get_mut("B1").unwrap().value = CellValue::Number(8.0);
        let source = temp.path("error.xlsx");
        export_xlsx(&model, &source).unwrap();
        let imported = import_xlsx(source, model.inputs.clone(), model.outputs.clone()).unwrap();
        assert!(
            matches!(&imported.snapshot().sheets[1].cells["F4"].cached_value,Some(CellValue::Error{error,..})if error=="#N/A")
        );
        model.sheets[1].cells.get_mut("F2").unwrap().formula = Some("=SORT(B2:B4)".into());
        let output = temp.path("array.xlsx");
        assert!(export_xlsx(&model, &output).is_err());
        assert!(!output.exists());
    }
    #[test]
    fn ambiguous_table_totals_never_expose_cached_inputs() {
        let temp = Temp::new();
        let mut package = scenario_package();
        modify(
            &mut package,
            "xl/worksheets/sheet1.xml",
            "</sheetData>",
            "<row r=\"10\"><c r=\"H10\" t=\"inlineStr\"><is><t>Input</t></is></c><c r=\"I10\" t=\"inlineStr\"><is><t>Total</t></is></c><c r=\"K10\"><f>I12+1</f><v>100</v></c></row><row r=\"11\"><c r=\"H11\"><v>1</v></c><c r=\"I11\"><v>2</v></c></row><row r=\"12\"><c r=\"H12\"><v>1</v></c><c r=\"I12\"><v>99</v></c></row></sheetData>",
        );
        modify(
            &mut package,
            "xl/worksheets/sheet1.xml",
            "</worksheet>",
            &format!(
                "<tableParts count=\"1\"><tablePart xmlns:r=\"{REL}\" r:id=\"table1\"/></tableParts></worksheet>"
            ),
        );
        package.parts.insert("xl/worksheets/_rels/sheet1.xml.rels".into(),format!("<Relationships xmlns=\"{PKG}\"><Relationship Id=\"table1\" Type=\"{REL}/table\" Target=\"../tables/table1.xml\"/></Relationships>").into_bytes());
        package.parts.insert("xl/tables/table1.xml".into(),format!("<table xmlns=\"{MAIN}\" id=\"1\" name=\"Table1\" displayName=\"Table1\" ref=\"H10:I12\"><tableColumns count=\"2\"><tableColumn id=\"1\" name=\"Input\"/><tableColumn id=\"2\" name=\"Total\" totalsRowFunction=\"sum\"/></tableColumns></table>").into_bytes());
        modify(
            &mut package,
            "[Content_Types].xml",
            "</Types>",
            "<Override PartName=\"/xl/tables/table1.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml\"/></Types>",
        );
        let source = temp.path("ambiguous-totals.xlsx");
        publish(&package, &source).unwrap();
        let imported = import_xlsx(
            &source,
            BTreeMap::new(),
            BTreeMap::from([("r".into(), CellAddress::new("Assumptions", "K10"))]),
        )
        .unwrap();
        assert!(
            imported.snapshot().sheets[0].cells["I12"]
                .blocked_reason
                .is_some()
        );
        assert!(!imported.calculate(1).diagnostics.is_empty());
        modify(
            &mut package,
            "xl/tables/table1.xml",
            "ref=\"H10:I12\"",
            "ref=\"H10:I12\" headerRowCount=\"4294967296\"",
        );
        let invalid = temp.path("overflow-header.xlsx");
        publish(&package, &invalid).unwrap();
        assert!(import_xlsx(invalid, BTreeMap::new(), BTreeMap::new()).is_err());
    }

    #[test]
    fn xml_entities_namespaces_phonetics_and_archive_limits() {
        assert!(Xml::parse(b"<!DOCTYPE x [<!ENTITY boom 'x'>]><x>&boom;</x>").is_err());
        assert!(Xml::parse(b"<x xmlns:a='u' xmlns:b='u' a:id='1' b:id='2'/>").is_err());
        assert!(Xml::parse(b"<x xmlns:xml='bad'/>").is_err());
        let source = format!(
            "<is xmlns=\"{MAIN}\"><t>東京</t><rPh sb=\"0\" eb=\"2\"><t>とうきょう</t></rPh><r><t>&amp;大阪</t></r></is>"
        );
        let xml = Xml::parse(source.as_bytes()).unwrap();
        assert_eq!(rich_text(&xml, &xml.nodes[0]), "東京&大阪");
        let mut package = scenario_package();
        package
            .parts
            .insert("custom/ANNOTATION.XML".into(), b"<!DOCTYPE x><x/>".to_vec());
        assert!(Package::from_bytes(&package.encode().unwrap()).is_err());
        let bytes = scenario_package().encode().unwrap();
        let mut wrong = bytes.clone();
        let offset = wrong.len() - 22;
        wrong[offset + 10..offset + 12].copy_from_slice(&0u16.to_le_bytes());
        wrong[offset + 8..offset + 10].copy_from_slice(&0u16.to_le_bytes());
        assert!(Package::from_bytes(&wrong).is_err());
        let mut traversal = scenario_package();
        traversal.parts.insert("../outside.bin".into(), vec![0]);
        assert!(Package::from_bytes(&traversal.encode().unwrap()).is_err());
    }
}
