//! Conversion edges between the canonical workbook shape (`model`) and the
//! toolkit shape (`toolkit`), spec 09 step 0.
//!
//! Each edge returns the normalised target document, validated by the
//! target's own reader, together with a [`Conversion`] naming every field
//! path it dropped and every one it added. The conversions never reimplement
//! a reader's rules: `to_toolkit` ends in `WorkbookModel::validate`,
//! `to_canonical` ends in `model::hydrate`.
//!
//! Binding typing is not mechanical: `to_toolkit` carries an input binding
//! only when the caller supplies its kind, and drops it otherwise.

use crate::envelope::PreservationEnvelope;
use crate::model::{self, Scalar};
use crate::toolkit::{self, CellReference, CellValue, InputBinding, Style, WorkbookModel};
use serde::Serialize;
use serde_json::{Map, Number, json};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;

/// What a conversion dropped and added, as field paths in the canonical
/// hydrator's error-path grammar, sorted by code point without duplicates.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize)]
pub struct Conversion {
    pub dropped: Vec<String>,
    pub added: Vec<String>,
}

struct Record {
    dropped: BTreeSet<String>,
    added: BTreeSet<String>,
}

impl Record {
    fn new() -> Self {
        Self {
            dropped: BTreeSet::new(),
            added: BTreeSet::new(),
        }
    }
    fn drop(&mut self, path: String) {
        self.dropped.insert(path);
    }
    fn add(&mut self, path: String) {
        self.added.insert(path);
    }
    fn finish(self) -> Conversion {
        Conversion {
            dropped: self.dropped.into_iter().collect(),
            added: self.added.into_iter().collect(),
        }
    }
}

/// The target reader's own refusal, or `unknown_input` when `input_kinds`
/// names an input the workbook does not bind.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ConversionError {
    pub code: String,
    pub message: String,
}

impl ConversionError {
    fn new(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
        }
    }
}

impl fmt::Display for ConversionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.code, self.message)
    }
}

impl std::error::Error for ConversionError {}

impl From<toolkit::ToolkitError> for ConversionError {
    fn from(error: toolkit::ToolkitError) -> Self {
        Self::new(error.code, error.message)
    }
}

impl From<model::ModelError> for ConversionError {
    fn from(error: model::ModelError) -> Self {
        Self::new(error.code, error.message)
    }
}

/// Canonical to toolkit. The result has passed `WorkbookModel::validate`.
pub fn to_toolkit(
    workbook: &model::Workbook,
    input_kinds: &BTreeMap<String, String>,
) -> Result<(WorkbookModel, Conversion), ConversionError> {
    workbook.require_supported_versions()?;
    let mut record = Record::new();
    let bound = workbook.bindings.as_ref();
    for name in input_kinds.keys() {
        if !bound.is_some_and(|bindings| bindings.inputs.contains_key(name)) {
            return Err(ConversionError::new(
                "unknown_input",
                format!("input_kinds names '{name}', which the workbook does not bind"),
            ));
        }
    }

    record.drop("model_version".to_string());
    for key in workbook.metadata.keys() {
        record.drop(format!("metadata.{key}"));
    }
    if workbook.provenance.is_some() {
        record.drop("provenance".to_string());
    }
    for index in 0..workbook.diagnostics.len() {
        record.drop(format!("diagnostics[{index}]"));
    }
    record.add("revision".to_string());

    let mut sheets = Vec::with_capacity(workbook.sheets.len());
    for (index, sheet) in workbook.sheets.iter().enumerate() {
        let prefix = format!("sheets[{index}]");
        record.add(format!("{prefix}.id"));
        if sheet.dimensions.is_some() {
            record.drop(format!("{prefix}.dimensions"));
        }
        let mut cells = BTreeMap::new();
        for (address, cell) in &sheet.cells {
            let path = format!("{prefix}.cells.{address}");
            record.drop(format!("{path}.data_type"));
            if cell.provenance.is_some() {
                record.drop(format!("{path}.provenance"));
            }
            let value = toolkit_value(&cell.value)?;
            let mut out = toolkit::Cell::default();
            match &cell.formula {
                Some(formula) => {
                    record.drop(format!("{path}.formula.dependencies"));
                    record.drop(format!("{path}.formula.result"));
                    out.formula = Some(formula.expression.clone());
                    // A formula cell's canonical value is the imported cache in
                    // the toolkit shape; the cell itself holds no authored value.
                    if !matches!(value, CellValue::Blank) {
                        record.add(format!("{path}.cached_value"));
                        out.cached_value = Some(value);
                    }
                }
                None => out.value = value,
            }
            if let Some(number_format) = &cell.number_format {
                out.style = Some(Style {
                    number_format: Some(number_format.clone()),
                    ..Style::default()
                });
            }
            cells.insert(address.clone(), out);
        }
        sheets.push(toolkit::Sheet {
            id: format!("sheet-{}", index + 1),
            name: sheet.name.clone(),
            cells,
            column_widths: BTreeMap::new(),
        });
    }

    let mut inputs = BTreeMap::new();
    let mut outputs = BTreeMap::new();
    if let Some(bindings) = bound {
        for (name, cell) in &bindings.inputs {
            match input_kinds.get(name) {
                Some(kind) => {
                    record.add(format!("bindings.inputs.{name}.kind"));
                    record.add(format!("bindings.inputs.{name}.required"));
                    inputs.insert(
                        name.clone(),
                        InputBinding {
                            sheet: cell.sheet.clone(),
                            address: cell.address.clone(),
                            kind: kind.clone(),
                            required: true,
                            min: None,
                            max: None,
                            choices: None,
                        },
                    );
                }
                None => record.drop(format!("bindings.inputs.{name}")),
            }
        }
        for (name, cell) in &bindings.outputs {
            outputs.insert(
                name.clone(),
                toolkit::CellAddress::new(cell.sheet.clone(), cell.address.clone()),
            );
        }
    }

    let model = WorkbookModel {
        schema_version: 1,
        revision: 0,
        sheets,
        inputs,
        outputs,
    };
    model.validate()?;
    Ok((model, record.finish()))
}

/// Toolkit to canonical. Starts with `WorkbookModel::validate`, ends with
/// `model::hydrate` on the assembled document and returns what it read.
///
/// With an envelope the workbook carries imported provenance naming its
/// workbook part, `metadata.source` names the bytes, and a cell the baseline
/// document still holds unchanged carries imported provenance naming its
/// sheet part; a cell added or changed since import is authored. Without an
/// envelope no workbook provenance is written, because a toolkit document
/// does not say whether it was authored or imported; only a formula cell
/// whose value came from `cached_value` is marked imported. A formula the
/// canonical analyzer refuses keeps empty dependencies and its refusal
/// travels as a canonical diagnostic.
pub fn to_canonical(
    model: &WorkbookModel,
    envelope: Option<&PreservationEnvelope>,
) -> Result<(model::Workbook, Conversion), ConversionError> {
    model.validate()?;
    let mut record = Record::new();
    record.drop("revision".to_string());
    record.add("model_version".to_string());
    if envelope.is_some() {
        record.add("metadata.source".to_string());
        record.add("provenance".to_string());
    }

    let mut sheets = Vec::with_capacity(model.sheets.len());
    for (index, sheet) in model.sheets.iter().enumerate() {
        let prefix = format!("sheets[{index}]");
        record.drop(format!("{prefix}.id"));
        for column in sheet.column_widths.keys() {
            record.drop(format!("{prefix}.column_widths.{column}"));
        }
        if !sheet.cells.is_empty() {
            record.add(format!("{prefix}.dimensions"));
        }
        let sheet_part = envelope.and_then(|e| e.source.sheet_parts.get(&sheet.name).cloned());
        let mut bounds: Option<[u64; 4]> = None;
        let mut cells = BTreeMap::new();
        for (address, cell) in &sheet.cells {
            let path = format!("{prefix}.cells.{address}");
            let reference = CellReference::parse(None, address)?;
            let (row, column) = (u64::from(reference.row), u64::from(reference.column));
            bounds = Some(match bounds {
                None => [row, row, column, column],
                Some([r1, r2, c1, c2]) => {
                    [r1.min(row), r2.max(row), c1.min(column), c2.max(column)]
                }
            });
            record.add(format!("{path}.data_type"));
            // A formula cell's toolkit cache is its canonical value; a cache
            // on a value cell has no canonical home. A blank cache serialises
            // as null, which the toolkit reads back as no cache at all.
            let cached = cell
                .cached_value
                .as_ref()
                .filter(|cached| !matches!(cached, CellValue::Blank));
            let (value, from_cache) = match (&cell.formula, cached) {
                (Some(_), Some(cached)) => (canonical_value(cached)?, true),
                (Some(_), None) => (Scalar::Blank, false),
                (None, cached) => {
                    if cached.is_some() {
                        record.drop(format!("{path}.cached_value"));
                    }
                    (canonical_value(&cell.value)?, false)
                }
            };
            let formula = cell.formula.as_ref().map(|expression| {
                record.add(format!("{path}.formula.dependencies"));
                record.add(format!("{path}.formula.result"));
                model::Formula {
                    expression: canonical_expression(expression),
                    dependencies: Vec::new(),
                    result: Scalar::Blank,
                }
            });
            if cell.blocked_reason.is_some() {
                record.drop(format!("{path}.blocked_reason"));
            }
            let mut number_format = None;
            if let Some(style) = &cell.style {
                let keys = [
                    ("bold", style.bold.is_some()),
                    ("fill_color", style.fill_color.is_some()),
                    ("font_color", style.font_color.is_some()),
                    ("horizontal", style.horizontal.is_some()),
                ];
                for (key, present) in keys {
                    if present {
                        record.drop(format!("{path}.style.{key}"));
                    }
                }
                match style.number_format.as_deref() {
                    Some("General") => record.drop(format!("{path}.style.number_format")),
                    Some(format) => number_format = Some(format.to_string()),
                    None => {}
                }
            }
            let provenance = if let Some(envelope) = envelope {
                record.add(format!("{path}.provenance"));
                if baseline_holds(envelope, &sheet.name, address, cell) {
                    Some(model::Provenance {
                        origin: "imported".to_string(),
                        source: sheet_part.clone(),
                    })
                } else {
                    // The baseline says this cell was added or changed after
                    // import, so the package is not its source.
                    Some(model::Provenance {
                        origin: "authored".to_string(),
                        source: None,
                    })
                }
            } else if from_cache {
                record.add(format!("{path}.provenance"));
                Some(model::Provenance {
                    origin: "imported".to_string(),
                    source: None,
                })
            } else {
                None
            };
            cells.insert(
                address.clone(),
                model::Cell {
                    address: address.clone(),
                    data_type: model::value_type(&value).to_string(),
                    value,
                    number_format,
                    formula,
                    provenance,
                },
            );
        }
        sheets.push(model::Sheet {
            name: sheet.name.clone(),
            cells,
            dimensions: bounds,
        });
    }

    let mut inputs = BTreeMap::new();
    for (name, input) in &model.inputs {
        record.drop(format!("bindings.inputs.{name}.kind"));
        record.drop(format!("bindings.inputs.{name}.required"));
        let optional = [
            ("min", input.min.is_some()),
            ("max", input.max.is_some()),
            ("choices", input.choices.is_some()),
        ];
        for (key, present) in optional {
            if present {
                record.drop(format!("bindings.inputs.{name}.{key}"));
            }
        }
        inputs.insert(
            name.clone(),
            model::CellId {
                sheet: input.sheet.clone(),
                address: input.address.clone(),
            },
        );
    }
    let outputs: BTreeMap<String, model::CellId> = model
        .outputs
        .iter()
        .map(|(name, cell)| {
            (
                name.clone(),
                model::CellId {
                    sheet: cell.sheet.clone(),
                    address: cell.address.clone(),
                },
            )
        })
        .collect();
    let bindings = if inputs.is_empty() && outputs.is_empty() {
        None
    } else {
        Some(model::Bindings { inputs, outputs })
    };

    let mut metadata = Map::new();
    let provenance = envelope.map(|envelope| {
        metadata.insert(
            "source".to_string(),
            json!({
                "fingerprint": envelope.source.fingerprint,
                "parts": envelope.source.parts,
            }),
        );
        model::Provenance {
            origin: "imported".to_string(),
            source: Some(envelope.source.workbook_part.clone()),
        }
    });

    let mut workbook = model::Workbook {
        schema_version: 1,
        model_version: 1,
        metadata,
        sheets,
        source_path: None,
        provenance,
        bindings,
        diagnostics: Vec::new(),
    };
    // Dependencies come from the canonical engine's own analyzer, which needs
    // the assembled workbook to resolve sheet names; a formula it refuses
    // keeps an empty list and its refusal becomes a diagnostic, so it is
    // never mistaken for a formula with no references.
    let mut analysed = Vec::new();
    let mut diagnostics = Vec::new();
    for (index, sheet) in workbook.sheets.iter().enumerate() {
        for (address, cell) in &sheet.cells {
            if let Some(formula) = &cell.formula {
                let (dependencies, diagnostic) =
                    model::analyze(&workbook, &sheet.name, address, &formula.expression, None);
                analysed.push((index, address.clone(), dependencies));
                diagnostics.extend(diagnostic);
            }
        }
    }
    // The model's own order: sheet, address, code, function, message.
    diagnostics.sort_by_cached_key(|d| {
        (
            d.sheet.clone().unwrap_or_default(),
            d.address.clone().unwrap_or_default(),
            d.code.clone(),
            d.function.clone().unwrap_or_default(),
            d.message.clone(),
        )
    });
    for index in 0..diagnostics.len() {
        record.add(format!("diagnostics[{index}]"));
    }
    workbook.diagnostics = diagnostics;
    for (index, address, dependencies) in analysed {
        if let Some(formula) = workbook.sheets[index]
            .cells
            .get_mut(&address)
            .and_then(|cell| cell.formula.as_mut())
        {
            formula.dependencies = dependencies;
        }
    }
    let hydrated = model::hydrate(workbook.to_json()?.as_bytes())?;
    Ok((hydrated, record.finish()))
}

fn canonical_value(value: &CellValue) -> Result<Scalar, ConversionError> {
    Ok(match value {
        CellValue::Blank => Scalar::Blank,
        CellValue::Number(number) => Scalar::Number(
            canonical_number(*number)
                .ok_or_else(|| ConversionError::new("invalid_value", "numbers must be finite"))?,
        ),
        CellValue::Text(text) => Scalar::Text(text.clone()),
        CellValue::Boolean(value) => Scalar::Boolean(*value),
        CellValue::Error { error, message } => Scalar::Error {
            // The toolkit reads both spellings; the canonical shape has one.
            code: if error == "#N/A!" {
                "#N/A".to_string()
            } else {
                error.clone()
            },
            message: message.clone(),
        },
    })
}

/// Whether the envelope's baseline document still holds this cell as
/// imported: a sheet of the same name, the same address, and the same value,
/// formula and cache (a blank cache is no cache). Anything else was added or
/// changed after import.
fn baseline_holds(
    envelope: &PreservationEnvelope,
    sheet: &str,
    address: &str,
    cell: &toolkit::Cell,
) -> bool {
    let Some(original) = envelope
        .baseline
        .document
        .sheets
        .iter()
        .find(|baseline| baseline.name == sheet)
        .and_then(|baseline| baseline.cells.get(address))
    else {
        return false;
    };
    let cache = |cell: &toolkit::Cell| {
        cell.cached_value
            .as_ref()
            .filter(|cached| !matches!(cached, CellValue::Blank))
            .cloned()
    };
    original.value == cell.value
        && original.formula == cell.formula
        && cache(original) == cache(cell)
}

/// The canonical spelling of a formula: a leading `=`, which the importers'
/// raw OOXML text lacks. A representation change, not listed. An empty
/// expression stays empty for the canonical reader to refuse.
fn canonical_expression(expression: &str) -> String {
    if expression.is_empty() || expression.starts_with('=') {
        expression.to_string()
    } else {
        format!("={expression}")
    }
}

/// A toolkit number in the canonical representation: an integer when the
/// float is integer-valued with magnitude at most 2^53, otherwise the float
/// itself. Negative zero stays a float so its sign survives the round trip.
fn canonical_number(value: f64) -> Option<Number> {
    const EXACT: f64 = 9_007_199_254_740_992.0;
    let negative_zero = value == 0.0 && value.is_sign_negative();
    if value.is_finite() && value.fract() == 0.0 && value.abs() <= EXACT && !negative_zero {
        return Some(Number::from(value as i64));
    }
    Number::from_f64(value)
}

fn toolkit_value(value: &Scalar) -> Result<CellValue, ConversionError> {
    Ok(match value {
        Scalar::Blank => CellValue::Blank,
        Scalar::Number(number) => CellValue::Number(number.as_f64().ok_or_else(|| {
            ConversionError::new("invalid_value", "number is not representable as f64")
        })?),
        Scalar::Text(text) => CellValue::Text(text.clone()),
        Scalar::Boolean(value) => CellValue::Boolean(*value),
        Scalar::Error { code, message } => CellValue::Error {
            error: code.clone(),
            message: message.clone(),
        },
    })
}
