//! The calculation session contract (spec 05; spec 09 step 1).
//!
//! [`CalculationSession`] owns a toolkit workbook model and a revision
//! counter that always starts at 0, and answers every pass with a
//! `calculation-report.v2`. It is a contract layer over
//! [`toolkit::Session`], which keeps its own signatures and legacy report.
use std::collections::BTreeMap;

use serde::Serialize;

use crate::convert::{self, Conversion};
use crate::model;
use crate::toolkit::{
    self, CalculatedValue, CalculationPass, CellValue, Diagnostic, ToolkitError, WorkbookModel,
};
use crate::{CalcContext, DefinedNames};

/// One bound input in a report: where it is and the value the pass read.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct InputRow {
    pub name: String,
    pub sheet: String,
    pub address: String,
    /// `"authored"` when this session, or the session it was forked from,
    /// wrote the cell; otherwise null.
    pub origin: Option<String>,
    pub value: CellValue,
}

/// One bound output in a report.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct OutputRow {
    pub name: String,
    pub sheet: String,
    pub address: String,
    /// A formula cell is `"calculated"` or `"not_calculated"`. A plain cell
    /// takes its origin as an input does.
    pub origin: Option<String>,
    /// Null for a formula cell the pass gave no value, or whose value the
    /// output result budget did not admit. A cached value is never reported.
    pub result: Option<CalculatedValue>,
}

/// `calculation-report.v2`. Every key is always written; an absent value is
/// null.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct CalculationReportV2 {
    pub report_version: u32,
    pub stage: String,
    pub engine: String,
    pub behavior_profile: String,
    pub schema_version: u32,
    pub model_version: Option<u32>,
    pub source_fingerprint: Option<String>,
    /// The session revision the pass read.
    pub revision: u64,
    /// Revision of the published report the pass used as its incremental
    /// base; null on a full pass.
    pub base_revision: Option<u64>,
    pub calc: CalcContext,
    pub inputs: Vec<InputRow>,
    pub outputs: Vec<OutputRow>,
    pub values: BTreeMap<String, CalculatedValue>,
    pub evaluated_cells: Vec<String>,
    pub stale: bool,
    pub diagnostics: Vec<Diagnostic>,
    /// Formula cells in the pass's closure that have a value.
    pub calculated_formulas: u64,
    /// Formula cells in the pass's closure that have none.
    pub unresolved_formulas: u64,
}

/// One input cell a preview would change.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct InputChange {
    pub sheet: String,
    pub address: String,
    pub before: CellValue,
    pub after: CellValue,
}

/// What `preview_inputs` returns. Nothing in it was applied to the session.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Preview {
    pub applied: bool,
    pub base_revision: u64,
    pub proposed_revision: u64,
    pub changes: Vec<InputChange>,
    pub before: CalculationReportV2,
    pub after: CalculationReportV2,
}

const AUTHORED: &str = "authored";

/// The canonical `Sheet!A1` key of a binding, split into sheet and address.
/// A session's model has passed validation, so the binding resolves; the
/// fallback only keeps this function total.
fn locate(model: &WorkbookModel, sheet: &str, address: &str) -> (String, String, String) {
    let key = model
        .resolve(sheet, address)
        .unwrap_or_else(|_| format!("{sheet}!{address}"));
    let (sheet, address) = key
        .rsplit_once('!')
        .map(|(sheet, address)| (sheet.to_string(), address.to_string()))
        .unwrap_or_else(|| (sheet.to_string(), address.to_string()));
    (key, sheet, address)
}

/// Build a `calculation-report.v2` from one pass. This is the only place the
/// Rust engine assembles the v2 shape; the native bridge calls it too.
pub fn report_v2(
    pass: &CalculationPass,
    engine: &str,
    model_version: Option<u32>,
    source_fingerprint: Option<&str>,
) -> CalculationReportV2 {
    let (model, report) = (&pass.model, &pass.report);
    let authored = |key: &str| pass.authored.contains(key).then(|| AUTHORED.to_string());
    let inputs = model
        .inputs
        .iter()
        .map(|(name, binding)| {
            let (key, sheet, address) = locate(model, &binding.sheet, &binding.address);
            InputRow {
                name: name.clone(),
                sheet,
                address,
                origin: authored(&key),
                value: model
                    .get_cell(&key)
                    .map_or(CellValue::Blank, |cell| cell.value.clone()),
            }
        })
        .collect();
    let outputs = model
        .outputs
        .iter()
        .map(|(name, binding)| {
            let (key, sheet, address) = locate(model, &binding.sheet, &binding.address);
            let cell = model.get_cell(&key);
            // The pass's budgeted outputs, not `values`: a bound output is
            // copied here only while the output result budget admits it, so
            // this report materialises no more than the legacy one does.
            let calculated = report.outputs.get(name).cloned();
            let (origin, result) = if cell.is_some_and(|cell| cell.formula.is_some()) {
                match calculated {
                    Some(value) => (Some("calculated".to_string()), Some(value)),
                    None => (Some("not_calculated".to_string()), None),
                }
            } else {
                let value = calculated.unwrap_or_else(|| {
                    CalculatedValue::Scalar(
                        cell.map_or(CellValue::Blank, |cell| cell.value.clone()),
                    )
                });
                (authored(&key), Some(value))
            };
            OutputRow {
                name: name.clone(),
                sheet,
                address,
                origin,
                result,
            }
        })
        .collect();
    CalculationReportV2 {
        report_version: 2,
        stage: "calculate".into(),
        engine: engine.into(),
        behavior_profile: "workbook-forge".into(),
        schema_version: 1,
        model_version,
        source_fingerprint: source_fingerprint.map(str::to_string),
        revision: report.revision,
        base_revision: pass.base_revision,
        calc: pass.context,
        inputs,
        outputs,
        values: report.values.clone(),
        evaluated_cells: report.evaluated_cells.clone(),
        stale: report.stale,
        diagnostics: report.diagnostics.clone(),
        calculated_formulas: pass.calculated_formulas,
        unresolved_formulas: pass.unresolved_formulas,
    }
}

/// An unbound input name is an invalid input at this layer, as on the agent
/// wire.
fn input_error(mut error: ToolkitError) -> ToolkitError {
    if error.code == "unknown_input" {
        error.code = "invalid_input".into();
    }
    error
}

/// A named-input batch is refused whole, before the revision is compared,
/// when a value is not a cell value. The typed map leaves one such case: a
/// number that is not finite.
fn cell_values(values: &BTreeMap<String, CellValue>) -> Result<(), ToolkitError> {
    match values
        .iter()
        .find(|(_, value)| matches!(value, CellValue::Number(number) if !number.is_finite()))
    {
        Some((name, _)) => Err(ToolkitError::new(
            "invalid_input",
            format!("{name}: value must be a finite number, text, boolean, blank or error object"),
        )),
        None => Ok(()),
    }
}

fn conflict() -> ToolkitError {
    ToolkitError::new(
        "revision_conflict",
        "expected revision does not match current revision",
    )
}

/// A canonical v1 document has a root `model_version`; a toolkit document
/// refuses that key. The two readers are disjoint on it.
fn is_canonical(root: &serde_json::Map<String, serde_json::Value>) -> bool {
    root.contains_key("model_version")
}

/// A calculation session over the owner model (in step 1, the v1 toolkit
/// document without `revision`). The revision starts at 0 and counts
/// committed input batches.
pub struct CalculationSession {
    inner: toolkit::Session,
    conversion: Option<Conversion>,
    model_version: Option<u32>,
    source_fingerprint: Option<String>,
}

impl CalculationSession {
    /// A session over an owner model, at revision 0.
    pub fn new(model: WorkbookModel, names: Option<DefinedNames>) -> Result<Self, ToolkitError> {
        Ok(Self {
            inner: toolkit::Session::with_names(model, names)?,
            conversion: None,
            model_version: None,
            source_fingerprint: None,
        })
    }

    /// Open either v1 document shape. A canonical document (it has a root
    /// `model_version`) is hydrated and converted with `input_kinds`; its
    /// defined names are used unless `names` is given. Any other object is
    /// read as a toolkit document, where `input_kinds` does not apply. Bytes
    /// that are not JSON, and a root that is not an object, are refused as
    /// `invalid_model` before a reader is chosen. The session
    /// starts at revision 0 whatever the document carried: a toolkit
    /// document's legacy `revision` key is checked, dropped and listed in
    /// [`CalculationSession::conversion`].
    pub fn open(
        document: &[u8],
        input_kinds: &BTreeMap<String, String>,
        names: Option<DefinedNames>,
    ) -> Result<Self, ToolkitError> {
        let source_fingerprint = Some(model::content_fingerprint(document));
        // Parsed to choose a reader and to see the raw root keys. The struct
        // reader would take a root array as positional fields, so only an
        // object goes on.
        let root = serde_json::from_slice::<serde_json::Value>(document).map_err(|error| {
            ToolkitError::new("invalid_model", format!("document is not JSON: {error}"))
        })?;
        let Some(root) = root.as_object() else {
            return Err(ToolkitError::new("invalid_model", "expected an object"));
        };
        if is_canonical(root) {
            let workbook = model::hydrate(document)
                .map_err(|error| ToolkitError::new(error.code, error.message))?;
            let names = match names {
                Some(names) => Some(names),
                None => model::defined_names(&workbook)
                    .map_err(|error| ToolkitError::new(error.code, error.message))?,
            };
            let (model, conversion) = convert::to_toolkit(&workbook, input_kinds)
                .map_err(|error| ToolkitError::new(error.code, error.message))?;
            return Ok(Self {
                inner: toolkit::Session::with_names(model, names)?,
                conversion: Some(conversion),
                model_version: Some(1),
                source_fingerprint,
            });
        }
        if !input_kinds.is_empty() {
            return Err(ToolkitError::new(
                "invalid_request",
                "input_kinds applies only to a canonical document",
            ));
        }
        let (model, _legacy_revision) = toolkit::read_document(document)
            .map_err(|error| ToolkitError::new("invalid_model", error.to_string()))?;
        let carried_revision = root.contains_key("revision");
        Ok(Self {
            inner: toolkit::Session::with_names(model, names)?,
            conversion: carried_revision.then(|| Conversion {
                dropped: vec!["revision".into()],
                added: Vec::new(),
            }),
            model_version: None,
            source_fingerprint,
        })
    }

    pub fn revision(&self) -> u64 {
        self.inner.revision()
    }

    /// What `open` dropped and added; `None` for a session built from a model
    /// and for a toolkit document read as it stands.
    pub fn conversion(&self) -> Option<&Conversion> {
        self.conversion.as_ref()
    }

    /// 1 when opened from a canonical v1 document; the toolkit shape has none.
    pub fn model_version(&self) -> Option<u32> {
        self.model_version
    }

    /// Fingerprint of the exact bytes given to `open`.
    pub fn source_fingerprint(&self) -> Option<&str> {
        self.source_fingerprint.as_deref()
    }

    pub fn snapshot(&self) -> WorkbookModel {
        self.inner.snapshot()
    }

    /// Calculate the session as it is and as it would be with `values`, on a
    /// detached fork. This session, its revision and its published pass are
    /// not touched.
    pub fn preview_inputs(
        &self,
        values: &BTreeMap<String, CellValue>,
        expected_revision: u64,
        context: &CalcContext,
    ) -> Result<Preview, ToolkitError> {
        cell_values(values)?;
        let fork = self.inner.fork();
        if fork.revision() != expected_revision {
            return Err(conflict());
        }
        let before_model = fork.snapshot();
        let before = self.report(&fork.calculate_pass(1, context));
        let proposed_revision = fork
            .set_inputs(values, Some(expected_revision))
            .map_err(input_error)?;
        let after_model = fork.snapshot();
        let after = self.report(&fork.calculate_pass(1, context));
        let value = |model: &WorkbookModel, key: &str| {
            model
                .get_cell(key)
                .map_or(CellValue::Blank, |cell| cell.value.clone())
        };
        let changes = values
            .keys()
            .filter_map(|name| before_model.inputs.get(name))
            .map(|binding| {
                let (key, sheet, address) = locate(&before_model, &binding.sheet, &binding.address);
                InputChange {
                    sheet,
                    address,
                    before: value(&before_model, &key),
                    after: value(&after_model, &key),
                }
            })
            .collect();
        Ok(Preview {
            applied: false,
            base_revision: expected_revision,
            proposed_revision,
            changes,
            before,
            after,
        })
    }

    /// Validate and commit named input values as one batch at
    /// `expected_revision`; all of them or none. Returns the new revision.
    pub fn set_inputs(
        &self,
        values: &BTreeMap<String, CellValue>,
        expected_revision: u64,
    ) -> Result<u64, ToolkitError> {
        cell_values(values)?;
        self.inner
            .set_inputs(values, Some(expected_revision))
            .map_err(input_error)
    }

    /// Capture a context at the session offset and calculate.
    pub fn calculate(&self, workers: usize) -> CalculationReportV2 {
        let context = CalcContext::capture(self.inner.utc_offset_minutes());
        self.calculate_with(workers, &context)
    }

    /// Calculate with a pinned clock and seed.
    pub fn calculate_with(&self, workers: usize, context: &CalcContext) -> CalculationReportV2 {
        self.report(&self.inner.calculate_pass(workers, context))
    }

    fn report(&self, pass: &CalculationPass) -> CalculationReportV2 {
        report_v2(
            pass,
            "rust",
            self.model_version,
            self.source_fingerprint.as_deref(),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::toolkit::operating_scenario;
    use std::collections::BTreeSet;

    fn context() -> CalcContext {
        CalcContext {
            now_ms: 1_767_225_600_000,
            utc_offset_minutes: 0,
            seed: 1,
        }
    }

    fn price(value: f64) -> BTreeMap<String, CellValue> {
        BTreeMap::from([("unit_price".to_string(), CellValue::Number(value))])
    }

    /// Every formula cell of the operating scenario: C2:C4, D2:D4, E2:E4 and
    /// F2:F4. The three bound outputs reach all twelve.
    fn formula_cells(model: &WorkbookModel) -> BTreeSet<String> {
        model
            .sheets
            .iter()
            .flat_map(|sheet| {
                sheet
                    .cells
                    .iter()
                    .filter(|(_, cell)| cell.formula.is_some())
                    .map(|(address, _)| format!("{}!{address}", sheet.name))
            })
            .collect()
    }

    #[test]
    fn stale_pass_is_reported_stale_and_not_published() {
        let session = CalculationSession::new(operating_scenario(), None).unwrap();
        let input = session.inner.begin_pass();
        assert_eq!(session.set_inputs(&price(25.0), 0).unwrap(), 1);
        let pass = session.inner.finish_pass(input, 1, &context());
        let stale = report_v2(&pass, "rust", None, None);
        assert!(stale.stale);
        assert_eq!(stale.revision, 0);
        assert_eq!(stale.base_revision, None);
        // The stale pass read the model before the edit: nothing authored.
        assert!(stale.inputs.iter().all(|row| row.origin.is_none()));
        assert!(session.inner.published().is_none());

        let next = session.calculate_with(1, &context());
        assert!(!next.stale);
        assert_eq!(next.revision, 1);
        assert_eq!(next.base_revision, None);
        let formulas = formula_cells(&session.snapshot());
        assert_eq!(formulas.len(), 12);
        assert_eq!(
            next.evaluated_cells
                .iter()
                .cloned()
                .collect::<BTreeSet<_>>(),
            formulas
        );
        assert_eq!(
            (next.calculated_formulas, next.unresolved_formulas),
            (12, 0)
        );
        let origin = |name: &str| {
            next.inputs
                .iter()
                .find(|row| row.name == name)
                .unwrap()
                .origin
                .clone()
        };
        assert_eq!(origin("unit_price").as_deref(), Some("authored"));
        assert_eq!(origin("unit_cost"), None);
    }

    #[test]
    fn open_starts_at_zero_and_reports_a_dropped_legacy_revision() {
        let mut document = serde_json::to_value(operating_scenario()).unwrap();
        let plain = serde_json::to_vec(&document).unwrap();
        let session = CalculationSession::open(&plain, &BTreeMap::new(), None).unwrap();
        assert_eq!(session.revision(), 0);
        assert_eq!(session.conversion(), None);
        assert_eq!(session.model_version(), None);
        assert_eq!(
            session.source_fingerprint(),
            Some(model::content_fingerprint(&plain).as_str())
        );

        document["revision"] = serde_json::json!(7);
        let legacy = serde_json::to_vec(&document).unwrap();
        let session = CalculationSession::open(&legacy, &BTreeMap::new(), None).unwrap();
        assert_eq!(session.revision(), 0);
        assert_eq!(
            session.conversion(),
            Some(&Conversion {
                dropped: vec!["revision".into()],
                added: Vec::new(),
            })
        );
        assert_eq!(
            session.set_inputs(&price(25.0), 7).unwrap_err().code,
            "revision_conflict"
        );
        assert_eq!(session.set_inputs(&price(25.0), 0).unwrap(), 1);
        let report = serde_json::to_value(session.calculate_with(1, &context())).unwrap();
        assert_eq!(report["revision"], 1);
        assert_eq!(report["model_version"], serde_json::Value::Null);
        assert_eq!(report["calc"]["seed"], "1");
        assert_eq!(report.as_object().unwrap().len(), 18);

        let kinds = BTreeMap::from([("unit_price".to_string(), "number".to_string())]);
        assert_eq!(
            CalculationSession::open(&plain, &kinds, None)
                .err()
                .unwrap()
                .code,
            "invalid_request"
        );
        assert_eq!(
            CalculationSession::open(b"not json", &BTreeMap::new(), None)
                .err()
                .unwrap()
                .code,
            "invalid_model"
        );
    }

    #[test]
    fn preview_runs_on_a_fork_and_leaves_the_session_alone() {
        let session = CalculationSession::new(operating_scenario(), None).unwrap();
        session.calculate_with(1, &context());
        assert_eq!(
            session
                .preview_inputs(&price(25.0), 1, &context())
                .unwrap_err()
                .code,
            "revision_conflict"
        );
        let unknown = BTreeMap::from([("nope".to_string(), CellValue::Number(1.0))]);
        assert_eq!(
            session
                .preview_inputs(&unknown, 0, &context())
                .unwrap_err()
                .code,
            "invalid_input"
        );
        let preview = session.preview_inputs(&price(25.0), 0, &context()).unwrap();
        assert!(!preview.applied);
        assert_eq!((preview.base_revision, preview.proposed_revision), (0, 1));
        assert_eq!(
            (preview.before.revision, preview.before.base_revision),
            (0, None)
        );
        assert_eq!(
            (preview.after.revision, preview.after.base_revision),
            (1, Some(0))
        );
        assert_eq!(
            preview.changes,
            vec![InputChange {
                sheet: "Assumptions".into(),
                address: "B1".into(),
                before: CellValue::Number(20.0),
                after: CellValue::Number(25.0),
            }]
        );
        // The session did not move, and its next pass reuses its own base.
        assert_eq!(session.revision(), 0);
        let next = session.calculate_with(1, &context());
        assert_eq!((next.revision, next.base_revision), (0, Some(0)));
        assert!(next.evaluated_cells.is_empty());
    }
}
