//! Formula-aware records over the existing validated OOXML package reader.
//! Shared formula reconstruction is inspection only; it never changes the
//! strict importer's grouped-formula calculation or preservation restrictions.
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use serde_json::{Value as Json, json};

use crate::toolkit::{self, CellReference};
use crate::xlsx::{self, XlsxError};
use crate::xml_patterns::{Node, Xml};

const MAIN: &str = "http://schemas.openxmlformats.org/spreadsheetml/2006/main";
const MAX_RECORDS: usize = 100_000;
const MAX_RESPONSE: usize = 1024 * 1024;
type Result<T> = std::result::Result<T, XlsxError>;
fn err(message: impl Into<String>) -> XlsxError {
    XlsxError(message.into())
}

#[derive(Clone, Debug)]
pub struct ExtractionOptions {
    pub patterns: Option<Vec<String>>,
    pub sheet: Option<String>,
    pub offset: u64,
    pub limit: usize,
}
impl Default for ExtractionOptions {
    fn default() -> Self {
        Self {
            patterns: None,
            sheet: None,
            offset: 0,
            limit: 100,
        }
    }
}
pub fn pattern_catalog() -> Json {
    serde_json::from_str(include_str!("extraction_patterns.json"))
        .expect("embedded extraction catalog must be valid JSON")
}
struct Pattern {
    id: String,
    part_kind: String,
    paths: Vec<Vec<String>>,
}
impl Pattern {
    fn matches(&self, xml: &Xml, node: &Node) -> bool {
        self.paths.iter().any(|path| xml.matches(node, MAIN, path))
    }
}
#[derive(Default)]
struct SharedFormula {
    master: Option<usize>,
    group_range: Option<String>,
    problem: Option<String>,
}
struct Diagnostics {
    values: Vec<Json>,
    count: usize,
}
impl Diagnostics {
    fn push(
        &mut self,
        code: &str,
        message: &str,
        part: &str,
        sheet: Option<&str>,
        cell: Option<&str>,
    ) {
        self.count += 1;
        if self.values.len() < 100 {
            self.values
                .push(json!({"code":code,"message":message,"part":part,"sheet":sheet,"cell":cell}));
        }
    }
}
fn formula_cell<'a>(xml: &'a Xml, node: &'a Node) -> Option<&'a str> {
    node.parent
        .and_then(|id| xml.nodes[id].attrs.get("r"))
        .map(String::as_str)
}
fn decimal_u32(value: &str) -> Option<u32> {
    (!value.is_empty() && value.bytes().all(|b| b.is_ascii_digit()))
        .then(|| value.parse().ok())
        .flatten()
}
fn rectangle(value: &str) -> Option<(CellReference, CellReference)> {
    let (first, last) = value.split_once(':').unwrap_or((value, value));
    let first = CellReference::parse(None, first).ok()?;
    let last = CellReference::parse(None, last).ok()?;
    if first.row > last.row || first.column > last.column {
        return None;
    }
    Some((first, last))
}
fn shared_groups(xml: &Xml) -> BTreeMap<usize, SharedFormula> {
    let mut members: BTreeMap<u32, Vec<usize>> = BTreeMap::new();
    let mut result = BTreeMap::new();
    for (index, node) in xml.nodes.iter().enumerate() {
        if xml.matches(node, MAIN, &["worksheet", "sheetData", "row", "c", "f"])
            && node.attrs.get("t").is_some_and(|kind| kind == "shared")
        {
            if let Some(group) = node.attrs.get("si").and_then(|value| decimal_u32(value)) {
                members.entry(group).or_default().push(index);
            } else {
                result.insert(
                    index,
                    SharedFormula {
                        group_range: node.attrs.get("ref").cloned(),
                        problem: Some("shared si must be a nonnegative decimal u32".into()),
                        ..SharedFormula::default()
                    },
                );
            }
        }
    }
    for indices in members.values() {
        let masters = indices
            .iter()
            .copied()
            .filter(|index| xml.nodes[*index].attrs.contains_key("ref"))
            .collect::<Vec<_>>();
        let master = (masters.len() == 1).then(|| masters[0]);
        let group_range = master.and_then(|index| xml.nodes[index].attrs.get("ref").cloned());
        let mut problem = None;
        if let Some(master) = master {
            let formula = &xml.nodes[master];
            let range = group_range.as_deref().and_then(rectangle);
            let master_coordinate =
                formula_cell(xml, formula).and_then(|cell| CellReference::parse(None, cell).ok());
            if let (Some((first, last)), Some(origin)) = (range, master_coordinate)
                && !formula.text.trim().is_empty()
            {
                for index in indices {
                    let coordinate = formula_cell(xml, &xml.nodes[*index])
                        .and_then(|cell| CellReference::parse(None, cell).ok());
                    if coordinate.as_ref().is_none_or(|cell| {
                        cell.row < first.row
                            || cell.row > last.row
                            || cell.column < first.column
                            || cell.column > last.column
                    }) {
                        problem =
                            Some("shared formula member lies outside the master range".into());
                        break;
                    }
                    let coordinate = coordinate.unwrap();
                    if let Err(error) = toolkit::copy_formula(
                        &formula.text,
                        coordinate.row as i32 - origin.row as i32,
                        coordinate.column as i32 - origin.column as i32,
                    ) {
                        problem = Some(format!("shared formula cannot be copied: {error}"));
                        break;
                    }
                }
            } else {
                problem =
                    Some("shared master requires formula text and a valid bounded range".into());
            }
        } else {
            problem = Some("shared group requires exactly one master".into());
        }
        for index in indices {
            result.insert(
                *index,
                SharedFormula {
                    master,
                    group_range: group_range
                        .clone()
                        .or_else(|| xml.nodes[*index].attrs.get("ref").cloned()),
                    problem: problem.clone(),
                },
            );
        }
    }
    result
}
fn empty_analysis(status: &str) -> Json {
    json!({"status":status,"functions":[],"references":[],"categories":[]})
}
fn analysis(formula: &str, catalog: &Json) -> std::result::Result<Json, String> {
    let parsed = toolkit::analyze_formula(formula).map_err(|error| error.to_string())?;
    let functions=parsed["functions"].as_array().into_iter().flatten().filter_map(Json::as_str).map(|name| {
        catalog["functions"].get(name).cloned().unwrap_or_else(||json!({"name":name,"known":false,"category":null,"python":"unknown","rust":"unknown"}))
    }).collect::<Vec<_>>();
    let categories = functions
        .iter()
        .filter_map(|function| function["category"].as_str())
        .collect::<BTreeSet<_>>();
    Ok(
        json!({"status":"parsed","functions":functions,"references":parsed["references"],"categories":categories}),
    )
}

/// Extract a bounded page from one immutable package read. The package reader
/// performs the same ZIP, relationship, content-type and scalar validation as
/// import; shared groups alone are inspected permissively to report diagnostics.
pub fn extract_xlsx(path: impl AsRef<Path>, options: ExtractionOptions) -> Result<Json> {
    if !(1..=100).contains(&options.limit) {
        return Err(err("limit must be between 1 and 100"));
    }
    let catalog = pattern_catalog();
    let declared = catalog["patterns"]
        .as_array()
        .expect("pattern catalog array");
    let names = options.patterns.as_ref().cloned().unwrap_or_else(|| {
        declared
            .iter()
            .map(|pattern| pattern["id"].as_str().unwrap().to_string())
            .collect()
    });
    let selected = names.iter().collect::<BTreeSet<_>>();
    if names.is_empty()
        || selected.len() != names.len()
        || names
            .iter()
            .any(|name| !declared.iter().any(|pattern| pattern["id"] == *name))
    {
        return Err(err(
            "patterns must be a nonempty unique list of catalog pattern IDs",
        ));
    }
    let patterns = declared
        .iter()
        .filter(|pattern| selected.contains(&pattern["id"].as_str().unwrap().to_string()))
        .map(|pattern| Pattern {
            id: pattern["id"].as_str().unwrap().into(),
            part_kind: pattern["part_kind"].as_str().unwrap().into(),
            paths: pattern["paths"]
                .as_array()
                .unwrap()
                .iter()
                .map(|path| {
                    path.as_array()
                        .unwrap()
                        .iter()
                        .map(|name| name.as_str().unwrap().to_string())
                        .collect()
                })
                .collect(),
        })
        .collect::<Vec<_>>();
    let source = xlsx::extraction_source(path.as_ref())?;
    let sheet_filter = options
        .sheet
        .as_ref()
        .map(|name| {
            source
                .sheets
                .iter()
                .find(|sheet| sheet.eq_ignore_ascii_case(name))
                .cloned()
                .ok_or_else(|| err("unknown worksheet filter"))
        })
        .transpose()?;
    let mut counts = patterns
        .iter()
        .map(|pattern| (pattern.id.clone(), 0usize))
        .collect::<BTreeMap<_, _>>();
    let mut records = Vec::new();
    let mut total = 0u64;
    let mut diagnostics = Diagnostics {
        values: Vec::new(),
        count: 0,
    };
    for (part, kind) in &source.part_kinds {
        let relevant = patterns
            .iter()
            .filter(|pattern| pattern.part_kind == *kind)
            .collect::<Vec<_>>();
        if relevant.is_empty() {
            continue;
        }
        let xml = Xml::parse(
            source
                .parts
                .get(part)
                .ok_or_else(|| err("missing validated part"))?,
        )?;
        let groups =
            if kind == "worksheet" && relevant.iter().any(|pattern| pattern.id == "formula") {
                shared_groups(&xml)
            } else {
                BTreeMap::new()
            };
        for (index, node) in xml.nodes.iter().enumerate() {
            for pattern in &relevant {
                if !pattern.matches(&xml, node) {
                    continue;
                }
                let mut owner = source.part_sheets.get(part).map(String::as_str);
                if pattern.id == "defined_name"
                    && let Some(local) = node.attrs.get("localSheetId")
                {
                    let local = decimal_u32(local)
                        .and_then(|id| source.sheets.get(id as usize))
                        .ok_or_else(|| err("invalid defined-name localSheetId"))?;
                    owner = Some(local);
                }
                if sheet_filter
                    .as_ref()
                    .is_some_and(|filter| owner != Some(filter.as_str()))
                {
                    continue;
                }
                total += 1;
                if total > MAX_RECORDS as u64 {
                    return Err(err("selected extraction records exceed 100000"));
                }
                *counts.get_mut(&pattern.id).unwrap() += 1;
                let cell = match pattern.id.as_str() {
                    "cell" => node.attrs.get("r").map(String::as_str),
                    "formula" => formula_cell(&xml, node),
                    _ => None,
                };
                let data = match pattern.id.as_str() {
                    "cell" => {
                        let value = xlsx::stored_value(&xml, node, &source.strings)?;
                        let formula = xml.child(node, "f").is_some();
                        let style = node
                            .attrs
                            .get("s")
                            .map(|s| s.parse::<u64>().map_err(|_| err("invalid style ID")))
                            .transpose()?;
                        json!({"value":if formula{Json::Null}else{json!(value)},"cached_value":if formula{json!(value)}else{Json::Null},"cell_type":node.attrs.get("t").map(String::as_str).unwrap_or("n"),"style_id":style})
                    }
                    "formula" | "table_formula" => {
                        let kind = if pattern.id == "table_formula" {
                            "table"
                        } else {
                            node.attrs.get("t").map(String::as_str).unwrap_or("normal")
                        };
                        let mut master_cell = None;
                        let mut group_range = node.attrs.get("ref").cloned();
                        let mut effective = Some(node.text.clone());
                        if kind == "shared" {
                            let group = groups
                                .get(&index)
                                .expect("each shared formula was inspected");
                            group_range = group.group_range.clone();
                            master_cell = group
                                .master
                                .and_then(|master| formula_cell(&xml, &xml.nodes[master]));
                            if let Some(problem) = &group.problem {
                                effective = None;
                                diagnostics.push("shared_formula", problem, part, owner, cell);
                            } else {
                                let master = group.master.expect("resolved shared master");
                                if master == index {
                                    effective = Some(xml.nodes[master].text.clone());
                                } else {
                                    let origin = CellReference::parse(None, master_cell.unwrap())?;
                                    let target = CellReference::parse(None, cell.unwrap())?;
                                    effective = Some(toolkit::copy_formula(
                                        &xml.nodes[master].text,
                                        target.row as i32 - origin.row as i32,
                                        target.column as i32 - origin.column as i32,
                                    )?);
                                }
                            }
                        }
                        let parsed = if let Some(formula) = &effective {
                            match analysis(formula, &catalog) {
                                Ok(parsed) => parsed,
                                Err(message) => {
                                    diagnostics.push("formula_syntax", &message, part, owner, cell);
                                    empty_analysis("unsupported")
                                }
                            }
                        } else {
                            empty_analysis("unresolved")
                        };
                        json!({"kind":kind,"shared_index":node.attrs.get("si"),"group_range":group_range,"master_cell":master_cell,"effective_formula":effective,"analysis":parsed})
                    }
                    "defined_name" => {
                        let parsed = match analysis(&node.text, &catalog) {
                            Ok(parsed) => parsed,
                            Err(message) => {
                                diagnostics.push("formula_syntax", &message, part, owner, None);
                                empty_analysis("unsupported")
                            }
                        };
                        json!({"analysis":parsed})
                    }
                    "shared_string" => json!({"value":xlsx::rich_text(&xml,node)}),
                    "validation" => {
                        json!({"formula1":xml.child(node,"formula1").map(|node|&node.text),"formula2":xml.child(node,"formula2").map(|node|&node.text)})
                    }
                    _ => json!({}),
                };
                if total > options.offset && records.len() < options.limit {
                    records.push(json!({"pattern":pattern.id,"part":part,"path":xml.path(node),"sheet":owner,"cell":cell,"attributes":node.attributes(),"text":node.text,"data":data}));
                }
            }
        }
    }
    let next = options.offset.saturating_add(records.len() as u64);
    let report = json!({"schema_version":1,"profile":"xlsx-extraction-v1","records":records,"counts":counts,"total_records":total,"offset":options.offset,"limit":options.limit,"next_offset":if next<total{Some(next)}else{None},"truncated":next<total,"diagnostics":diagnostics.values,"diagnostic_count":diagnostics.count,"diagnostics_truncated":diagnostics.count>100});
    if serde_json::to_vec(&report)
        .map_err(|error| err(error.to_string()))?
        .len()
        > MAX_RESPONSE
    {
        return Err(err(
            "extraction response exceeds 1 MiB; request a smaller page",
        ));
    }
    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU64, Ordering};
    use zip::{ZipArchive, ZipWriter, write::SimpleFileOptions};

    fn fixture(transform: impl FnOnce(&mut BTreeMap<String, Vec<u8>>)) -> PathBuf {
        static NEXT: AtomicU64 = AtomicU64::new(0);
        let root = std::env::temp_dir().join(format!(
            "workbook-forge-extraction-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir_all(&root).unwrap();
        let source = root.join("original.xlsx");
        xlsx::export_xlsx(&toolkit::operating_scenario(), &source).unwrap();
        let mut archive = ZipArchive::new(std::fs::File::open(&source).unwrap()).unwrap();
        let mut parts = BTreeMap::new();
        for i in 0..archive.len() {
            let mut member = archive.by_index(i).unwrap();
            let mut data = Vec::new();
            member.read_to_end(&mut data).unwrap();
            parts.insert(member.name().to_string(), data);
        }
        transform(&mut parts);
        let target = root.join("fixture.xlsx");
        let mut output = ZipWriter::new(std::fs::File::create(&target).unwrap());
        for (name, bytes) in parts {
            output
                .start_file(name, SimpleFileOptions::default())
                .unwrap();
            output.write_all(&bytes).unwrap();
        }
        output.finish().unwrap();
        target
    }
    fn replace(parts: &mut BTreeMap<String, Vec<u8>>, part: &str, from: &str, to: &str) {
        let text = String::from_utf8(parts[part].clone()).unwrap();
        assert!(text.contains(from), "{from} missing from {part}");
        parts.insert(part.into(), text.replace(from, to).into_bytes());
    }
    fn select(pattern: &str) -> ExtractionOptions {
        ExtractionOptions {
            patterns: Some(vec![pattern.into()]),
            ..ExtractionOptions::default()
        }
    }
    #[test]
    fn normal_scenario_extracts_values_caches_and_mapped_formula_references() {
        let path = fixture(|_| {});
        let before = std::fs::read(&path).unwrap();
        let report = extract_xlsx(&path, ExtractionOptions::default()).unwrap();
        assert_eq!(report["profile"], "xlsx-extraction-v1");
        assert!(report["counts"]["formula"].as_u64().unwrap() > 0);
        let cell = report["records"]
            .as_array()
            .unwrap()
            .iter()
            .find(|record| {
                record["pattern"] == "cell"
                    && record["sheet"] == "Forecast"
                    && record["cell"] == "F2"
            })
            .unwrap();
        assert_eq!(cell["data"]["value"], Json::Null);
        assert_eq!(cell["data"]["cached_value"], 7400.0);
        let formula = report["records"]
            .as_array()
            .unwrap()
            .iter()
            .find(|record| {
                record["pattern"] == "formula"
                    && record["sheet"] == "Forecast"
                    && record["cell"] == "F2"
            })
            .unwrap();
        assert_eq!(formula["data"]["analysis"]["status"], "parsed");
        assert_eq!(formula["data"]["analysis"]["functions"][0]["name"], "SUM");
        assert_eq!(formula["data"]["analysis"]["functions"][0]["known"], true);
        assert_eq!(std::fs::read(path).unwrap(), before);
    }
    #[test]
    fn shared_formula_followers_copy_mixed_anchors_and_preserve_source_text() {
        let path = fixture(|parts| {
            replace(
                parts,
                "xl/worksheets/sheet1.xml",
                "</sheetData>",
                "<row r=\"10\"><c r=\"K10\"><f t=\"shared\" si=\"01\" ref=\"K10:L10\">SUM($A10,B$1,$C$1,'Forecast'!D10)</f><v>99</v></c><c r=\"L10\"><f t=\"shared\" si=\"1\">retained follower text</f><v>88</v></c></row></sheetData>",
            )
        });
        let report = extract_xlsx(&path, select("formula")).unwrap();
        let formulas = report["records"].as_array().unwrap();
        let master = formulas
            .iter()
            .find(|record| record["cell"] == "K10")
            .unwrap();
        let follower = formulas
            .iter()
            .find(|record| record["cell"] == "L10")
            .unwrap();
        assert_eq!(
            master["data"]["effective_formula"],
            "SUM($A10,B$1,$C$1,'Forecast'!D10)"
        );
        assert_eq!(follower["text"], "retained follower text");
        assert_eq!(follower["data"]["master_cell"], "K10");
        assert_eq!(follower["data"]["shared_index"], "1");
        assert!(
            follower["data"]["effective_formula"]
                .as_str()
                .unwrap()
                .contains("$A10,C$1,$C$1")
        );
        assert_eq!(
            follower["data"]["analysis"]["references"][3]["start"]["column"],
            5
        );
        assert_eq!(report["diagnostic_count"], 0);
        // Strict import retains its original grouped-formula profile.
        assert!(xlsx::import_xlsx(&path, BTreeMap::new(), BTreeMap::new()).is_err());
    }
    #[test]
    fn malformed_shared_groups_are_diagnosed_and_never_partially_resolved() {
        for (master, follower) in [
            (
                "<f t=\"shared\" si=\"0\" ref=\"K10:K10\">A1</f>",
                "<f t=\"shared\" si=\"0\"/>",
            ),
            (
                "<f t=\"shared\" si=\"0\" ref=\"K10:L10\">Table1[Value]</f>",
                "<f t=\"shared\" si=\"0\"/>",
            ),
            ("<f t=\"shared\" si=\"0\"/>", "<f t=\"shared\" si=\"0\"/>"),
            (
                "<f t=\"shared\" si=\"4294967296\">A1</f>",
                "<f t=\"shared\" si=\"invalid\"/>",
            ),
        ] {
            let path = fixture(|parts| {
                replace(
                    parts,
                    "xl/worksheets/sheet1.xml",
                    "</sheetData>",
                    &format!(
                        "<row r=\"10\"><c r=\"K10\">{master}<v>99</v></c><c r=\"L10\">{follower}<v>88</v></c></row></sheetData>"
                    ),
                )
            });
            let report = extract_xlsx(&path, select("formula")).unwrap();
            let group = report["records"]
                .as_array()
                .unwrap()
                .iter()
                .filter(|record| record["cell"] == "K10" || record["cell"] == "L10")
                .collect::<Vec<_>>();
            assert_eq!(group.len(), 2);
            assert!(
                group
                    .iter()
                    .all(|record| record["data"]["effective_formula"].is_null())
            );
            assert_eq!(report["diagnostic_count"], 2);
            assert!(
                report["diagnostics"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .all(|diagnostic| diagnostic["code"] == "shared_formula")
            );
            assert_eq!(
                extract_xlsx(&path, select("cell")).unwrap()["diagnostic_count"],
                0
            );
        }
    }
    #[test]
    fn names_strings_decoys_and_function_mappings_use_structural_meaning() {
        let path = fixture(|parts| {
            replace(
                parts,
                "xl/worksheets/sheet1.xml",
                "</sheetData>",
                "<row r=\"10\"><c r=\"K10\"><f>MYSTERY(&quot;SUM(A1)&quot;)</f><v>1</v></c><c r=\"L10\" t=\"inlineStr\"><is><t>東京</t><rPh sb=\"0\" eb=\"2\"><t>とうきょう</t></rPh></is></c></row></sheetData><extLst><ext uri=\"opaque\"><c r=\"Z99\"><f>BAD(1)</f></c></ext></extLst>",
            );
            replace(
                parts,
                "xl/workbook.xml",
                "</sheets>",
                "</sheets><definedNames><definedName name=\"Local\" localSheetId=\"0\">Assumptions!$B$1</definedName><definedName name=\"Global\">Table1[Value]</definedName></definedNames>",
            );
        });
        let report = extract_xlsx(&path, ExtractionOptions::default()).unwrap();
        assert!(
            !report["records"]
                .as_array()
                .unwrap()
                .iter()
                .any(|record| record["cell"] == "Z99")
        );
        let mystery = report["records"]
            .as_array()
            .unwrap()
            .iter()
            .find(|record| record["pattern"] == "formula" && record["cell"] == "K10")
            .unwrap();
        assert_eq!(
            mystery["data"]["analysis"]["functions"],
            json!([{"name":"MYSTERY","known":false,"category":null,"python":"unknown","rust":"unknown"}])
        );
        assert_eq!(mystery["data"]["analysis"]["references"], json!([]));
        let text = report["records"]
            .as_array()
            .unwrap()
            .iter()
            .find(|record| record["cell"] == "L10")
            .unwrap();
        assert_eq!(text["data"]["value"], "東京");
        assert_eq!(report["diagnostic_count"], 1);
        let filtered = extract_xlsx(
            &path,
            ExtractionOptions {
                patterns: Some(vec!["defined_name".into()]),
                sheet: Some("aSSUMPTIONS".into()),
                ..ExtractionOptions::default()
            },
        )
        .unwrap();
        assert_eq!(filtered["total_records"], 1);
        assert_eq!(filtered["records"][0]["sheet"], "Assumptions");
        assert_eq!(filtered["diagnostic_count"], 0);
    }
    #[test]
    fn pagination_and_selection_are_bounded_before_serialization() {
        let path = fixture(|_| {});
        let first = extract_xlsx(
            &path,
            ExtractionOptions {
                limit: 1,
                ..select("cell")
            },
        )
        .unwrap();
        assert_eq!(first["next_offset"], 1);
        let next = extract_xlsx(
            &path,
            ExtractionOptions {
                offset: 1,
                limit: 1,
                ..select("cell")
            },
        )
        .unwrap();
        assert_ne!(first["records"], next["records"]);
        assert_eq!(first["counts"], next["counts"]);
        assert!(
            extract_xlsx(
                &path,
                ExtractionOptions {
                    patterns: Some(vec!["cell".into(), "cell".into()]),
                    ..ExtractionOptions::default()
                }
            )
            .is_err()
        );
        assert!(
            extract_xlsx(
                &path,
                ExtractionOptions {
                    limit: 101,
                    ..ExtractionOptions::default()
                }
            )
            .is_err()
        );
        assert!(
            extract_xlsx(
                &path,
                ExtractionOptions {
                    sheet: Some("missing".into()),
                    ..ExtractionOptions::default()
                }
            )
            .is_err()
        );
        let beyond = extract_xlsx(
            &path,
            ExtractionOptions {
                offset: u64::MAX,
                ..select("cell")
            },
        )
        .unwrap();
        assert_eq!(beyond["records"], json!([]));
        assert_eq!(beyond["truncated"], false);
    }
    #[test]
    fn extraction_caps_selected_records_and_emitted_bytes() {
        let path = fixture(|parts| {
            replace(
                parts,
                "xl/worksheets/sheet1.xml",
                "</worksheet>",
                &format!(
                    "<mergeCells>{}</mergeCells></worksheet>",
                    "<mergeCell ref=\"A1:B1\"/>".repeat(100001)
                ),
            )
        });
        assert!(
            extract_xlsx(&path, select("merged_range"))
                .unwrap_err()
                .to_string()
                .contains("100000")
        );
        let path = fixture(|parts| {
            replace(
                parts,
                "[Content_Types].xml",
                "</Types>",
                "<Override PartName=\"/xl/sharedStrings.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml\"/></Types>",
            );
            let mut text = format!("<sst xmlns=\"{MAIN}\">");
            for _ in 0..40 {
                text.push_str(&format!("<si><t>{}</t></si>", "x".repeat(32767)));
            }
            text.push_str("</sst>");
            parts.insert("xl/sharedStrings.xml".into(), text.into_bytes());
        });
        assert!(
            extract_xlsx(&path, select("shared_string"))
                .unwrap_err()
                .to_string()
                .contains("1 MiB")
        );
        assert_eq!(
            extract_xlsx(
                &path,
                ExtractionOptions {
                    limit: 1,
                    ..select("shared_string")
                }
            )
            .unwrap()["counts"]["shared_string"],
            40
        );
    }
    #[test]
    fn ambiguous_cell_payloads_fail_import_and_extraction_before_selection() {
        for payload in [
            "<f>1</f><f>2</f>",
            "<v>1</v><v>2</v>",
            "<f>1<extLst>40</extLst>+2</f>",
            "<v><x/></v>",
            "<is/><is/>",
        ] {
            let path = fixture(|parts| {
                replace(
                    parts,
                    "xl/worksheets/sheet1.xml",
                    "</sheetData>",
                    &format!("<row r=\"10\"><c r=\"K10\">{payload}</c></row></sheetData>"),
                )
            });
            assert!(xlsx::import_xlsx(&path, BTreeMap::new(), BTreeMap::new()).is_err());
            assert!(extract_xlsx(&path, select("column")).is_err());
        }
    }
}
