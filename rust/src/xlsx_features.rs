//! Typed inspect metadata and write guards for drop-downs, pivots and charts.
//!
//! The same rules as `python/workbook_forge/xlsx_features.py`
//! (docs/xlsx-features.md); `tools/corpus/feature-cases.json` holds the cases
//! both engines must agree on. Every feature stays in the package
//! byte-for-byte; this module only reads it.
use super::{Package, Result, rel_part, relationships, resolve};
use crate::toolkit::CellValue;
use crate::xml_patterns::{Node, Xml};
use serde_json::{Value as Json, json};
use std::collections::BTreeMap;

const MAIN: &str = "http://schemas.openxmlformats.org/spreadsheetml/2006/main";
const REL: &str = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
const DRAWING: &str = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing";
const CHART: &str = "http://schemas.openxmlformats.org/drawingml/2006/chart";
const CHART_KINDS: [&str; 16] = [
    "area3DChart",
    "areaChart",
    "bar3DChart",
    "barChart",
    "bubbleChart",
    "doughnutChart",
    "line3DChart",
    "lineChart",
    "ofPieChart",
    "pie3DChart",
    "pieChart",
    "radarChart",
    "scatterChart",
    "stockChart",
    "surface3DChart",
    "surfaceChart",
];
const OPERATORS: [&str; 8] = [
    "between",
    "notBetween",
    "equal",
    "notEqual",
    "greaterThan",
    "lessThan",
    "greaterThanOrEqual",
    "lessThanOrEqual",
];
const MAX_LIST_CELLS: u64 = 10_000;

type Bounds = (u32, u32, u32, u32);

fn column_number(letters: &str) -> Option<u32> {
    if letters.is_empty() || letters.len() > 3 || !letters.bytes().all(|b| b.is_ascii_alphabetic())
    {
        return None;
    }
    Some(letters.bytes().fold(0, |n, b| {
        n * 26 + u32::from(b.to_ascii_uppercase() - b'A' + 1)
    }))
}
pub(crate) fn column_name(mut number: u32) -> String {
    let mut name = String::new();
    while number > 0 {
        let rem = (number - 1) % 26;
        name.insert(0, (b'A' + rem as u8) as char);
        number = (number - 1) / 26;
    }
    name
}
fn split_cell(text: &str) -> Option<(Option<u32>, Option<u32>)> {
    let plain = text.replace('$', "");
    let letters = plain.bytes().take_while(u8::is_ascii_alphabetic).count();
    let (column, row) = plain.split_at(letters);
    let column = if column.is_empty() {
        None
    } else {
        Some(column_number(column)?)
    };
    let row = if row.is_empty() {
        None
    } else if row.bytes().all(|b| b.is_ascii_digit()) && !row.starts_with('0') {
        Some(row.parse().ok()?)
    } else {
        return None;
    };
    if column.is_none() && row.is_none() {
        return None;
    }
    Some((column, row))
}
/// `A1`, `A1:B4`, `A:A` or `2:2` -> (c1, r1, c2, r2).
pub(crate) fn range_bounds(reference: &str) -> Option<Bounds> {
    let (first, last) = reference.split_once(':').unwrap_or((reference, reference));
    let whole = reference.contains(':');
    let (a, b) = (split_cell(first)?, split_cell(last)?);
    let (c1, r1, c2, r2) = match (a, b) {
        ((Some(c1), Some(r1)), (Some(c2), Some(r2))) => (c1, r1, c2, r2),
        ((Some(c1), None), (Some(c2), None)) if whole => (c1, 1, c2, 1_048_576),
        ((None, Some(r1)), (None, Some(r2))) if whole => (1, r1, 16_384, r2),
        _ => return None,
    };
    let (c1, c2) = (c1.min(c2), c1.max(c2));
    let (r1, r2) = (r1.min(r2), r1.max(r2));
    (c1 >= 1 && c2 <= 16_384 && r1 >= 1 && r2 <= 1_048_576).then_some((c1, r1, c2, r2))
}
pub(crate) fn position(address: &str) -> Option<(u32, u32)> {
    match split_cell(address)? {
        (Some(column), Some(row)) => Some((column, row)),
        _ => None,
    }
}
fn contains(bounds: Bounds, column: u32, row: u32) -> bool {
    (bounds.0..=bounds.2).contains(&column) && (bounds.1..=bounds.3).contains(&row)
}
/// `'My Sheet'!$A$1:$A$4` -> (Some("My Sheet"), "$A$1:$A$4").
fn split_reference(text: &str) -> Option<(Option<String>, String)> {
    let text = text.trim();
    let (sheet, cells) = if let Some(rest) = text.strip_prefix('\'') {
        let mut name = String::new();
        let mut chars = rest.char_indices().peekable();
        let mut end = None;
        while let Some((index, ch)) = chars.next() {
            if ch == '\'' {
                if chars.peek().map(|(_, c)| *c) == Some('\'') {
                    name.push('\'');
                    chars.next();
                    continue;
                }
                end = Some(index);
                break;
            }
            name.push(ch);
        }
        let after = &rest[end? + 1..];
        (Some(name), after.strip_prefix('!')?.to_string())
    } else if let Some((sheet, cells)) = text.split_once('!') {
        if sheet.is_empty()
            || !sheet
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'.')
        {
            return None;
        }
        (Some(sheet.to_string()), cells.to_string())
    } else {
        (None, text.to_string())
    };
    range_bounds(&cells)?;
    Some((sheet, cells))
}
fn parse_inline_list(text: &str) -> Option<Vec<String>> {
    let body = text.strip_prefix('"')?.strip_suffix('"')?;
    if body.replace("\"\"", "").contains('"') {
        return None;
    }
    Some(
        body.replace("\"\"", "\"")
            .split(',')
            .map(|item| item.trim().to_string())
            .collect(),
    )
}
fn parse_array_constant(text: &str) -> Option<Vec<String>> {
    let body = text.trim().strip_prefix('{')?.strip_suffix('}')?;
    let mut items = Vec::new();
    let mut current = String::new();
    let mut quoted = false;
    let mut raw = Vec::new();
    for ch in body.chars() {
        if ch == '"' {
            quoted = !quoted;
        }
        if !quoted && (ch == ',' || ch == ';') {
            raw.push(std::mem::take(&mut current));
        } else {
            current.push(ch);
        }
    }
    raw.push(current);
    for item in raw {
        let item = item.trim();
        if item.len() >= 2 && item.starts_with('"') && item.ends_with('"') {
            items.push(item[1..item.len() - 1].replace("\"\"", "\""));
        } else if finite_array_number(item)
            || item.eq_ignore_ascii_case("TRUE")
            || item.eq_ignore_ascii_case("FALSE")
        {
            items.push(item.to_string());
        } else {
            return None;
        }
    }
    Some(items)
}
/// One finite literal: optional sign, at most one dot, optional exponent.
/// `inf`, `nan` and a second dot are not numbers in this grammar.
fn finite_array_number(item: &str) -> bool {
    let rest = item
        .strip_prefix('+')
        .or_else(|| item.strip_prefix('-'))
        .unwrap_or(item);
    if rest.is_empty() {
        return false;
    }
    let (body, exponent) = match rest.find(['e', 'E']) {
        Some(index) => (&rest[..index], Some(&rest[index + 1..])),
        None => (rest, None),
    };
    if body.is_empty() || body.matches('.').count() > 1 {
        return false;
    }
    let mut digits = 0usize;
    for byte in body.bytes() {
        if byte.is_ascii_digit() {
            digits += 1;
        } else if byte != b'.' {
            return false;
        }
    }
    if digits == 0 {
        return false;
    }
    if let Some(exponent) = exponent {
        let digits = exponent
            .strip_prefix('+')
            .or_else(|| exponent.strip_prefix('-'))
            .unwrap_or(exponent);
        if digits.is_empty() || !digits.bytes().all(|b| b.is_ascii_digit()) {
            return false;
        }
    }
    item.parse::<f64>().is_ok_and(f64::is_finite)
}
fn number(text: Option<&str>) -> Option<f64> {
    let value: f64 = text?.trim().trim_start_matches('=').parse().ok()?;
    value.is_finite().then_some(value)
}
fn flag(node: &Node, name: &str, default: bool) -> bool {
    node.attrs
        .get(name)
        .map_or(default, |v| v == "1" || v == "true")
}

/// (scope sheet or None, lowercased name) -> expression.
pub(crate) type Names = BTreeMap<(Option<String>, String), String>;
pub(crate) fn defined_names(wb: &Xml, root: &Node, entries: &[String]) -> Names {
    let mut names = Names::new();
    for group in wb.children(root, MAIN, "definedNames") {
        for node in wb.children(group, MAIN, "definedName") {
            let Some(name) = node.attrs.get("name") else {
                continue;
            };
            let scope = node
                .attrs
                .get("localSheetId")
                .and_then(|id| id.parse::<usize>().ok())
                .and_then(|id| entries.get(id).cloned());
            names.insert((scope, name.to_lowercase()), node.text.trim().to_string());
        }
    }
    names
}

#[derive(Clone, Debug)]
enum ListSource {
    Values(Vec<String>),
    Range(String, Bounds),
    Unresolved,
}
#[derive(Clone, Debug)]
pub(crate) struct Rule {
    sheet: String,
    bounds: Vec<Bounds>,
    kind: String,
    operator: String,
    formula1: Option<String>,
    formula2: Option<String>,
    enforced: bool,
    list: ListSource,
    sqref: String,
    pub(crate) record: Json,
}

fn list_source(sheet: &str, formula: &str, names: &Names, depth: u8) -> (Json, ListSource) {
    let text = formula.trim().trim_start_matches('=').trim();
    if let Some(values) = parse_inline_list(text) {
        return (
            json!({"kind":"inline","values":values}),
            ListSource::Values(values),
        );
    }
    if let Some((target, cells)) = split_reference(text) {
        let target = target.unwrap_or_else(|| sheet.to_string());
        let range = cells.replace('$', "");
        let bounds = range_bounds(&range).expect("checked reference");
        return (
            json!({"kind":"range","sheet":target,"range":range}),
            ListSource::Range(target, bounds),
        );
    }
    let is_name = !text.is_empty()
        && text
            .chars()
            .next()
            .is_some_and(|c| c.is_ascii_alphabetic() || c == '_' || c == '\\')
        && text
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | '\\'));
    if is_name && depth == 0 {
        let key = text.to_lowercase();
        let Some(expression) = names
            .get(&(Some(sheet.to_string()), key.clone()))
            .or_else(|| names.get(&(None, key)))
        else {
            return (
                json!({"kind":"name","name":text,"resolves_to":null}),
                ListSource::Unresolved,
            );
        };
        if let Some(values) = parse_array_constant(expression) {
            return (
                json!({"kind":"name","name":text,"resolves_to":expression,"values":values}),
                ListSource::Values(values),
            );
        }
        let (inner, source) = list_source(sheet, expression, names, depth + 1);
        if let ListSource::Range(target, bounds) = source {
            return (
                json!({"kind":"name","name":text,"resolves_to":expression,"sheet":inner["sheet"],"range":inner["range"]}),
                ListSource::Range(target, bounds),
            );
        }
        return (
            json!({"kind":"name","name":text,"resolves_to":expression}),
            ListSource::Unresolved,
        );
    }
    (
        json!({"kind":"formula","formula":text}),
        ListSource::Unresolved,
    )
}

pub(crate) fn read_validations(sheet: &str, xml: &Xml, root: &Node, names: &Names) -> Vec<Rule> {
    let mut rules = Vec::new();
    let Some(group) = xml.child(root, "dataValidations") else {
        return rules;
    };
    for (index, node) in xml.children(group, MAIN, "dataValidation").enumerate() {
        let kind = node
            .attrs
            .get("type")
            .cloned()
            .unwrap_or_else(|| "none".into());
        let formula1 = xml.child(node, "formula1").map(|n| n.text.clone());
        let formula2 = xml.child(node, "formula2").map(|n| n.text.clone());
        let error_style = node
            .attrs
            .get("errorStyle")
            .cloned()
            .unwrap_or_else(|| "stop".into());
        let show_error = flag(node, "showErrorMessage", false);
        let operator = node
            .attrs
            .get("operator")
            .cloned()
            .unwrap_or_else(|| "between".into());
        let sqref = node.attrs.get("sqref").cloned().unwrap_or_default();
        let mut source = Json::Null;
        let mut list = ListSource::Unresolved;
        let mut allowed = Json::Null;
        let mut reason: Option<String> = None;
        match kind.as_str() {
            "list" => {
                let (record, parsed) =
                    list_source(sheet, formula1.as_deref().unwrap_or(""), names, 0);
                if let ListSource::Values(values) = &parsed {
                    allowed = json!(values);
                }
                if matches!(parsed, ListSource::Unresolved) {
                    reason = Some("list source is a formula this engine does not evaluate".into());
                } else if let ListSource::Range(_, (c1, r1, c2, r2)) = &parsed
                    && u64::from(*c2 - *c1 + 1) * u64::from(*r2 - *r1 + 1) > MAX_LIST_CELLS
                {
                    reason = Some(format!(
                        "list source has more than {MAX_LIST_CELLS} cells, so this engine does not evaluate it"
                    ));
                }
                source = record;
                list = parsed;
            }
            "whole" | "decimal" | "date" | "time" | "textLength" => {
                let two = matches!(operator.as_str(), "between" | "notBetween");
                if !OPERATORS.contains(&operator.as_str()) {
                    reason = Some(format!("unknown operator {operator}"));
                } else if number(formula1.as_deref()).is_none()
                    || (two && number(formula2.as_deref()).is_none())
                {
                    reason = Some("bounds are formulas this engine does not evaluate".into());
                }
            }
            "custom" => reason = Some("custom formula rules are not evaluated".into()),
            _ => reason = Some("no rule".into()),
        }
        if reason.is_none() && !show_error {
            reason = Some(
                "Excel shows no error alert (showErrorMessage is off), so any value is accepted"
                    .into(),
            );
        } else if reason.is_none() && error_style != "stop" {
            reason = Some(format!(
                "errorStyle {error_style} lets the entry through in Excel"
            ));
        }
        let has_operator = !matches!(kind.as_str(), "list" | "custom" | "none");
        let record = json!({
            "sheet": sheet,
            "index": index,
            "sqref": sqref.split_whitespace().collect::<Vec<_>>(),
            "type": kind,
            "operator": if has_operator { json!(operator) } else { Json::Null },
            "allow_blank": flag(node, "allowBlank", false),
            "show_error": show_error,
            "error_style": error_style,
            "in_cell_dropdown": kind == "list" && !flag(node, "showDropDown", false),
            "formula1": formula1,
            "formula2": formula2,
            "error_title": node.attrs.get("errorTitle"),
            "error": node.attrs.get("error"),
            "source": source,
            "allowed_values": allowed,
            "enforced": reason.is_none(),
            "unenforced_reason": reason,
        });
        rules.push(Rule {
            sheet: sheet.to_string(),
            bounds: sqref.split_whitespace().filter_map(range_bounds).collect(),
            enforced: reason.is_none(),
            kind,
            operator,
            formula1,
            formula2,
            list,
            sqref,
            record,
        });
    }
    rules
}

fn upper_bool(value: bool) -> &'static str {
    if value { "TRUE" } else { "FALSE" }
}
fn item_matches(item: &CellValue, value: &CellValue) -> bool {
    let item_text = match item {
        CellValue::Text(t) => t.clone(),
        CellValue::Number(n) => general_text(*n),
        CellValue::Boolean(b) => upper_bool(*b).into(),
        _ => return false,
    };
    match value {
        CellValue::Boolean(b) => item_text.trim().eq_ignore_ascii_case(upper_bool(*b)),
        CellValue::Number(n) => match item {
            CellValue::Number(m) => m == n,
            CellValue::Boolean(_) => false,
            _ => number(Some(&item_text)) == Some(*n),
        },
        CellValue::Text(t) => match item {
            CellValue::Boolean(b) => t.trim().eq_ignore_ascii_case(upper_bool(*b)),
            // Text never matches a number in the source range.
            CellValue::Number(_) => false,
            // Excel's list check is not case-sensitive.
            _ => item_text.to_lowercase() == t.to_lowercase(),
        },
        _ => false,
    }
}
/// A number as text with 15 significant digits (`%.15g`, `E` exponent), the
/// rule Python's `xlsx_features.general_text` uses.
pub(crate) fn general_text(value: f64) -> String {
    if value == 0.0 || !value.is_finite() {
        return if value.is_finite() {
            "0".into()
        } else {
            value.to_string()
        };
    }
    let scientific = format!("{value:.14e}");
    let (mantissa, exponent) = scientific.split_once('e').expect("exponent");
    let exponent: i32 = exponent.parse().expect("exponent digits");
    let trim = |text: String| -> String {
        if text.contains('.') {
            text.trim_end_matches('0').trim_end_matches('.').to_string()
        } else {
            text
        }
    };
    if !(-4..15).contains(&exponent) {
        let sign = if exponent < 0 { '-' } else { '+' };
        return format!("{}E{sign}{:02}", trim(mantissa.to_string()), exponent.abs());
    }
    trim(format!("{value:.*}", (14 - exponent).max(0) as usize))
}

fn shown(value: &CellValue) -> String {
    match value {
        CellValue::Text(t) => format!("'{t}'"),
        CellValue::Number(n) => general_text(*n),
        CellValue::Boolean(b) => upper_bool(*b).into(),
        CellValue::Error { error, .. } => error.clone(),
        CellValue::Blank => "blank".into(),
    }
}

impl Rule {
    /// Current allowed values; `read(sheet, address)` returns a cell's value.
    fn allowed(
        &self,
        read: &mut dyn FnMut(&str, &str) -> Option<CellValue>,
    ) -> Option<Vec<CellValue>> {
        match &self.list {
            ListSource::Values(values) => {
                Some(values.iter().cloned().map(CellValue::Text).collect())
            }
            ListSource::Range(sheet, (c1, r1, c2, r2)) => {
                if u64::from(c2 - c1 + 1) * u64::from(r2 - r1 + 1) > MAX_LIST_CELLS {
                    return None;
                }
                let mut values = Vec::new();
                for row in *r1..=*r2 {
                    for column in *c1..=*c2 {
                        match read(sheet, &format!("{}{row}", column_name(column))) {
                            None | Some(CellValue::Blank) => {}
                            Some(CellValue::Text(t)) if t.is_empty() => {}
                            Some(value) => values.push(value),
                        }
                    }
                }
                Some(values)
            }
            ListSource::Unresolved => None,
        }
    }
    fn violation(
        &self,
        value: &CellValue,
        read: &mut dyn FnMut(&str, &str) -> Option<CellValue>,
    ) -> Option<String> {
        if !self.enforced {
            return None;
        }
        match value {
            // Clearing a cell is never validated in Excel.
            CellValue::Blank => return None,
            CellValue::Text(t) if t.is_empty() => return None,
            CellValue::Error { .. } => {
                return Some("an error value is not an allowed entry".into());
            }
            _ => {}
        }
        if self.kind == "list" {
            let allowed = self.allowed(read)?;
            if allowed.iter().any(|item| item_matches(item, value)) {
                return None;
            }
            let list: Vec<String> = allowed
                .iter()
                .take(20)
                .map(|item| match item {
                    CellValue::Text(t) => t.clone(),
                    other => shown(other),
                })
                .collect();
            let more = if allowed.len() > 20 { ", ..." } else { "" };
            return Some(format!(
                "value is not in the drop-down list ({}{more})",
                list.join(", ")
            ));
        }
        let low = number(self.formula1.as_deref())?;
        let high = number(self.formula2.as_deref());
        let measured = if self.kind == "textLength" {
            match value {
                CellValue::Text(t) => t.chars().count() as f64,
                CellValue::Number(n) => general_text(*n).chars().count() as f64,
                CellValue::Boolean(b) => upper_bool(*b).len() as f64,
                _ => return None,
            }
        } else {
            let CellValue::Number(n) = value else {
                return Some(format!("a {} rule needs a number", self.kind));
            };
            if self.kind == "whole" && n.fract() != 0.0 {
                return Some("a whole-number rule needs an integer".into());
            }
            *n
        };
        let upper = high.unwrap_or(low);
        let ok = match self.operator.as_str() {
            "between" => low <= measured && measured <= upper,
            "notBetween" => !(low <= measured && measured <= upper),
            "equal" => measured == low,
            "notEqual" => measured != low,
            "greaterThan" => measured > low,
            "lessThan" => measured < low,
            "greaterThanOrEqual" => measured >= low,
            "lessThanOrEqual" => measured <= low,
            _ => return None,
        };
        if ok {
            return None;
        }
        let mut bound = self.formula1.clone().unwrap_or_default();
        if matches!(self.operator.as_str(), "between" | "notBetween")
            && let Some(second) = &self.formula2
        {
            bound = format!("{bound} and {second}");
        }
        Some(format!(
            "{} rule {} {bound} is not met",
            self.kind, self.operator
        ))
    }
}

/// Every pivot table on a worksheet: location, cache source and fields.
pub(crate) struct Pivot {
    sheet: String,
    name: String,
    location: String,
    bounds: Option<Bounds>,
    pub(crate) record: Json,
}
fn rels_of(package: &Package, part: &str) -> BTreeMap<String, (String, String, bool)> {
    let Ok(bytes) = package.get(&rel_part(part)) else {
        return BTreeMap::new();
    };
    let Ok(items) = relationships(bytes) else {
        return BTreeMap::new();
    };
    items
        .into_iter()
        .filter_map(|(id, (kind, target, external))| {
            let kind = kind.rsplit('/').next().unwrap_or("").to_string();
            if external {
                return Some((id, (kind, target, true)));
            }
            resolve(part, &target)
                .ok()
                .map(|resolved| (id, (kind, resolved, false)))
        })
        .collect()
}
pub(crate) fn read_pivots(
    package: &Package,
    sheets: &BTreeMap<String, String>,
    order: &[String],
) -> Result<Vec<Pivot>> {
    let mut pivots = Vec::new();
    for sheet in order {
        let Some(part) = sheets.get(sheet) else {
            continue;
        };
        for (kind, target, external) in rels_of(package, part).into_values() {
            if kind != "pivotTable" || external || package.get(&target).is_err() {
                continue;
            }
            let xml = Xml::parse(package.get(&target)?)?;
            let root = &xml.nodes[0];
            let location = xml
                .child(root, "location")
                .and_then(|n| n.attrs.get("ref").cloned())
                .unwrap_or_default();
            let mut source = serde_json::Map::new();
            source.insert("type".into(), Json::Null);
            let mut fields: Vec<String> = Vec::new();
            for (cache_kind, cache_part, cache_external) in rels_of(package, &target).into_values()
            {
                if cache_kind != "pivotCacheDefinition"
                    || cache_external
                    || package.get(&cache_part).is_err()
                {
                    continue;
                }
                let cache = Xml::parse(package.get(&cache_part)?)?;
                let cache_root = &cache.nodes[0];
                if let Some(cache_source) = cache.child(cache_root, "cacheSource") {
                    source.insert("type".into(), json!(cache_source.attrs.get("type")));
                    if let Some(worksheet) = cache.child(cache_source, "worksheetSource") {
                        for key in ["sheet", "ref", "name"] {
                            if let Some(value) = worksheet.attrs.get(key) {
                                source.insert(key.into(), json!(value));
                            }
                        }
                    }
                }
                if let Some(group) = cache.child(cache_root, "cacheFields") {
                    fields = cache
                        .children(group, MAIN, "cacheField")
                        .map(|f| f.attrs.get("name").cloned().unwrap_or_default())
                        .collect();
                }
                source.insert("cache_part".into(), json!(cache_part));
                source.insert(
                    "refresh_on_load".into(),
                    json!(flag(cache_root, "refreshOnLoad", false)),
                );
            }
            let field_name = |index: &str| -> Json {
                match index.parse::<i64>() {
                    Ok(-2) => json!("Values"),
                    Ok(n) if n >= 0 && (n as usize) < fields.len() => json!(fields[n as usize]),
                    Ok(n) => json!(format!("#{n}")),
                    Err(_) => Json::Null,
                }
            };
            let axis = |group: &str, item: &str, attribute: &str| -> Vec<Json> {
                xml.child(root, group)
                    .map(|g| {
                        xml.children(g, MAIN, item)
                            .filter_map(|f| f.attrs.get(attribute))
                            .filter(|i| {
                                i.trim_start_matches('-')
                                    .bytes()
                                    .all(|b| b.is_ascii_digit())
                            })
                            .map(|i| field_name(i.as_str()))
                            .collect()
                    })
                    .unwrap_or_default()
            };
            let data_fields: Vec<Json> = xml
                .child(root, "dataFields")
                .map(|g| {
                    xml.children(g, MAIN, "dataField")
                        .map(|f| {
                            json!({
                                "name": f.attrs.get("name"),
                                "field": f.attrs.get("fld").filter(|i| i.bytes().all(|b| b.is_ascii_digit())).map(|i| field_name(i.as_str())),
                                "function": f.attrs.get("subtotal").cloned().unwrap_or_else(|| "sum".into()),
                            })
                        })
                        .collect()
                })
                .unwrap_or_default();
            let name = root.attrs.get("name").cloned().unwrap_or_default();
            pivots.push(Pivot {
                sheet: sheet.clone(),
                name: name.clone(),
                bounds: range_bounds(&location),
                record: json!({
                    "sheet": sheet,
                    "part": target,
                    "name": name,
                    "location": location,
                    "source": source,
                    "fields": fields,
                    "row_fields": axis("rowFields", "field", "x"),
                    "column_fields": axis("colFields", "field", "x"),
                    "page_fields": axis("pageFields", "pageField", "fld"),
                    "data_fields": data_fields,
                    "refresh": "unsupported: the cached output is preserved as stored; edits to the source do not update it",
                }),
                location,
            });
        }
    }
    Ok(pivots)
}

fn anchor_cell(xml: &Xml, anchor: &Node, tag: &str) -> Option<String> {
    let marker = anchor
        .children
        .iter()
        .map(|id| &xml.nodes[*id])
        .find(|n| n.ns == DRAWING && n.local == tag)?;
    let read = |name: &str| -> Option<u32> {
        marker
            .children
            .iter()
            .map(|id| &xml.nodes[*id])
            .find(|n| n.ns == DRAWING && n.local == name)?
            .text
            .trim()
            .parse()
            .ok()
    };
    Some(format!(
        "{}{}",
        column_name(read("col")? + 1),
        read("row")? + 1
    ))
}
fn descendants<'a>(xml: &'a Xml, node: &'a Node, out: &mut Vec<&'a Node>) {
    for id in &node.children {
        let child = &xml.nodes[*id];
        out.push(child);
        descendants(xml, child, out);
    }
}
fn chart_record(package: &Package, part: &str) -> Result<Json> {
    let xml = Xml::parse(package.get(part)?)?;
    let root = &xml.nodes[0];
    let mut kinds = Vec::new();
    let mut series = Vec::new();
    let find = |node: &Node, name: &str| -> Option<usize> {
        node.children
            .iter()
            .copied()
            .find(|id| xml.nodes[*id].ns == CHART && xml.nodes[*id].local == name)
    };
    let plot = find(root, "chart").and_then(|chart| find(&xml.nodes[chart], "plotArea"));
    if let Some(plot) = plot {
        for id in &xml.nodes[plot].children {
            let node = &xml.nodes[*id];
            if node.ns != CHART || !CHART_KINDS.contains(&node.local.as_str()) {
                continue;
            }
            kinds.push(node.local.clone());
            for ser_id in &node.children {
                let ser = &xml.nodes[*ser_id];
                if ser.ns != CHART || ser.local != "ser" {
                    continue;
                }
                let mut refs = serde_json::Map::new();
                for role in ["tx", "cat", "val", "xVal", "yVal", "bubbleSize"] {
                    let Some(child) = find(ser, role) else {
                        continue;
                    };
                    let mut all = Vec::new();
                    descendants(&xml, &xml.nodes[child], &mut all);
                    if let Some(formula) = all.iter().find(|n| n.local == "f" && !n.text.is_empty())
                    {
                        refs.insert(role.into(), json!(formula.text));
                    }
                }
                series.push(json!({"chart_type": node.local, "refs": refs}));
            }
        }
    }
    Ok(json!({"part": part, "types": kinds, "series": series}))
}
/// Every chart on a worksheet drawing or a chartsheet.
pub(crate) fn read_charts(
    package: &Package,
    hosts: &[(String, String, &'static str)],
) -> Result<Vec<Json>> {
    let mut charts = Vec::new();
    for (sheet, part, host) in hosts {
        for (kind, drawing, external) in rels_of(package, part).into_values() {
            if kind != "drawing" || external || package.get(&drawing).is_err() {
                continue;
            }
            let drawing_rels = rels_of(package, &drawing);
            let xml = Xml::parse(package.get(&drawing)?)?;
            let root = &xml.nodes[0];
            for anchor_id in &root.children {
                let anchor = &xml.nodes[*anchor_id];
                let mut all = Vec::new();
                descendants(&xml, anchor, &mut all);
                for frame in all.iter().filter(|n| n.ns == CHART && n.local == "chart") {
                    let Some(rid) = frame.attrs.get(&format!("{{{REL}}}id")) else {
                        continue;
                    };
                    let Some((rel_kind, target, rel_external)) = drawing_rels.get(rid) else {
                        continue;
                    };
                    if rel_kind != "chart" || *rel_external || package.get(target).is_err() {
                        continue;
                    }
                    let mut chart = chart_record(package, target)?;
                    let anchor_text = if *host == "worksheet" {
                        match (
                            anchor_cell(&xml, anchor, "from"),
                            anchor_cell(&xml, anchor, "to"),
                        ) {
                            (Some(a), Some(b)) => json!(format!("{a}:{b}")),
                            (Some(a), None) => json!(a),
                            _ => Json::Null,
                        }
                    } else {
                        json!("whole sheet")
                    };
                    chart["sheet"] = json!(sheet);
                    chart["host"] = json!(host);
                    chart["anchor"] = anchor_text;
                    charts.push(chart);
                }
            }
        }
    }
    Ok(charts)
}
/// Sheet names a chart series formula reads.
pub(crate) fn referenced_sheets(formula: &str) -> Vec<String> {
    let mut out = Vec::new();
    let chars: Vec<char> = formula.chars().collect();
    let mut i = 0;
    while i < chars.len() {
        if chars[i] == '\'' {
            let mut name = String::new();
            i += 1;
            while i < chars.len() {
                if chars[i] == '\'' {
                    if chars.get(i + 1) == Some(&'\'') {
                        name.push('\'');
                        i += 2;
                        continue;
                    }
                    break;
                }
                name.push(chars[i]);
                i += 1;
            }
            if chars.get(i + 1) == Some(&'!') {
                out.push(name);
            }
            i += 1;
        } else if chars[i].is_ascii_alphanumeric() || chars[i] == '_' {
            let start = i;
            while i < chars.len()
                && (chars[i].is_ascii_alphanumeric() || matches!(chars[i], '_' | '.'))
            {
                i += 1;
            }
            if chars.get(i) == Some(&'!') {
                out.push(chars[start..i].iter().collect());
            }
        } else {
            i += 1;
        }
    }
    out
}

/// The cell-write rules an imported workbook's features impose.
#[derive(Clone, Default)]
pub(crate) struct FeatureGuard {
    rules: Vec<Rule>,
    pivots: Vec<(String, String, String, Option<Bounds>)>,
    charted: Vec<String>,
}
impl FeatureGuard {
    pub(crate) fn new(rules: Vec<Rule>, pivots: &[Pivot], charts: &[Json]) -> Self {
        let mut charted = Vec::new();
        for chart in charts {
            for series in chart["series"].as_array().into_iter().flatten() {
                for formula in series["refs"]
                    .as_object()
                    .into_iter()
                    .flat_map(|m| m.values())
                {
                    if let Some(formula) = formula.as_str() {
                        charted.extend(
                            referenced_sheets(formula)
                                .into_iter()
                                .map(|s| s.to_lowercase()),
                        );
                    }
                }
            }
        }
        charted.sort();
        charted.dedup();
        Self {
            rules,
            pivots: pivots
                .iter()
                .map(|p| {
                    (
                        p.sheet.clone(),
                        p.name.clone(),
                        p.location.clone(),
                        p.bounds,
                    )
                })
                .collect(),
            charted,
        }
    }
    pub(crate) fn is_charted(&self, sheet: &str) -> bool {
        self.charted.contains(&sheet.to_lowercase())
    }
    /// `Err(message)` (with its `code: ` prefix) when the write must be refused.
    pub(crate) fn check(
        &self,
        sheet: &str,
        address: &str,
        value: Option<&CellValue>,
        formula: bool,
        read: &mut dyn FnMut(&str, &str) -> Option<CellValue>,
    ) -> std::result::Result<(), String> {
        let Some((column, row)) = position(address) else {
            return Ok(());
        };
        for (pivot_sheet, name, location, bounds) in &self.pivots {
            if bounds.is_some_and(|b| contains(b, column, row))
                && pivot_sheet.eq_ignore_ascii_case(sheet)
            {
                return Err(format!(
                    "pivot_output_protected: {sheet}!{address} is inside pivot table {name} output {location}; pivot output is preserved as stored and pivot refresh is unsupported"
                ));
            }
        }
        if formula {
            return Ok(()); // Formula entries are not checked (Excel checks the result at entry).
        }
        let Some(value) = value else { return Ok(()) };
        let Some(rule) = self.rules.iter().find(|r| {
            r.sheet.eq_ignore_ascii_case(sheet)
                && r.bounds.iter().any(|b| contains(*b, column, row))
        }) else {
            return Ok(());
        };
        match rule.violation(value, read) {
            None => Ok(()),
            Some(why) => Err(format!(
                "validation_failed: {sheet}!{address} {} is refused by the data validation on {}: {why}",
                shown(value),
                rule.sqref
            )),
        }
    }
    /// Range-sourced allowed values as they stand in `read`, for inspect.
    pub(crate) fn resolve_records(
        &mut self,
        read: &mut dyn FnMut(&str, &str) -> Option<CellValue>,
    ) {
        for rule in &mut self.rules {
            if matches!(rule.list, ListSource::Range(..)) {
                let values: Option<Vec<Json>> = rule.allowed(read).map(|items| {
                    items
                        .into_iter()
                        .map(|item| match item {
                            CellValue::Text(t) => json!(t),
                            CellValue::Number(n) => json!(n),
                            CellValue::Boolean(b) => json!(b),
                            other => json!(shown(&other)),
                        })
                        .collect()
                });
                rule.record["allowed_values"] = json!(values);
            }
        }
    }
    pub(crate) fn records(&self) -> Vec<Json> {
        self.rules.iter().map(|r| r.record.clone()).collect()
    }
}
