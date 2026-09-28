//! Bounded, framework-neutral operations over an owned workbook session.
//!
//! Workbook text is data. This adapter never interprets it as executable agent
//! instructions, and filesystem access is restricted to the host's export root.
use std::collections::{BTreeMap, BTreeSet};
use std::io::{self, BufRead, Write};
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use serde_json::{Map, Value, json};

use crate::toolkit::{
    self, CalculationReport, CellReference, CellValue, Edit, Session, ToolkitError, WorkbookModel,
};
use crate::xlsx::{self, ImportedWorkbook};

pub const MAX_REQUEST_BYTES: usize = 1024 * 1024;
pub const MAX_RESPONSE_BYTES: usize = 256 * 1024;
const MAX_ITEMS: usize = 100;
type Result<T> = std::result::Result<T, ToolkitError>;
type Arguments = Map<String, Value>;

/// The same versioned catalog shipped with the Python package.
pub fn operation_catalog() -> Value {
    serde_json::from_str(include_str!("agent_operations.json"))
        .expect("the embedded operation catalog must be valid JSON")
}

enum OwnedWorkbook {
    Authored(Session),
    Imported(Box<ImportedWorkbook>),
}
impl OwnedWorkbook {
    fn snapshot(&self) -> WorkbookModel {
        match self {
            Self::Authored(session) => session.snapshot(),
            Self::Imported(imported) => imported.snapshot(),
        }
    }
    fn validate_imported(&self, edits: &[Edit]) -> Result<()> {
        match self {
            Self::Authored(_) => Ok(()),
            Self::Imported(imported) => imported
                .validate_edits(edits)
                .map_err(|error| ToolkitError::new("unsupported_operation", error.to_string())),
        }
    }
    fn apply(&self, edits: Vec<Edit>, revision: u64) -> Result<u64> {
        match self {
            Self::Authored(session) => session.apply(edits, Some(revision)),
            Self::Imported(imported) => imported
                .apply(edits, Some(revision))
                .map_err(|error| ToolkitError::new("unsupported_operation", error.to_string())),
        }
    }
}

/// Owns a transient session. Calls are serialized, including preview and export,
/// so every response and file refers to a coherent revision of this adapter.
pub struct AgentWorkbook {
    workbook: Mutex<OwnedWorkbook>,
    output_dir: PathBuf,
}
impl AgentWorkbook {
    pub fn new(model: WorkbookModel, output_dir: impl AsRef<Path>) -> Result<Self> {
        let session = Session::new(model)?;
        Ok(Self {
            workbook: Mutex::new(OwnedWorkbook::Authored(session)),
            output_dir: export_directory(output_dir.as_ref())?,
        })
    }
    pub fn from_imported(imported: ImportedWorkbook, output_dir: impl AsRef<Path>) -> Result<Self> {
        Ok(Self {
            workbook: Mutex::new(OwnedWorkbook::Imported(Box::new(imported))),
            output_dir: export_directory(output_dir.as_ref())?,
        })
    }
    /// Handle one JSON envelope. Malformed requests and operation failures are
    /// responses, rather than panics, and leave the adapter available for reuse.
    pub fn handle(&self, request: Value) -> Value {
        let workbook = match self.workbook.lock() {
            Ok(workbook) => workbook,
            Err(_) => {
                return failure(None, None, 0, "session_failure", "agent lock poisoned");
            }
        };
        let model = workbook.snapshot();
        if serde_json::to_vec(&request).map_or(true, |bytes| bytes.len() > MAX_REQUEST_BYTES) {
            return failure(
                None,
                None,
                model.revision,
                "resource_limit",
                "request exceeds 1 MiB",
            );
        }
        let (id, operation, arguments) = match parse_envelope(&request) {
            Ok(envelope) => envelope,
            Err(error) => {
                let id = request
                    .get("id")
                    .and_then(Value::as_str)
                    .filter(|id| id.len() <= 128);
                let operation = request
                    .get("operation")
                    .and_then(Value::as_str)
                    .filter(|operation| !operation.is_empty());
                return failure(id, operation, model.revision, &error.code, &error.message);
            }
        };
        let outcome = self.execute(&workbook, &model, operation, arguments, id);
        match outcome {
            Ok(result) => {
                bounded_response(success(id, operation, workbook.snapshot().revision, result))
            }
            Err(error) => failure(
                id,
                Some(operation),
                workbook.snapshot().revision,
                &error.code,
                &error.message,
            ),
        }
    }
    /// Return a transport error with the current revision and no request identity.
    pub fn transport_error(&self, code: &str, message: &str) -> Value {
        let revision = self
            .workbook
            .lock()
            .map(|w| w.snapshot().revision)
            .unwrap_or(0);
        failure(None, None, revision, code, message)
    }
    fn execute(
        &self,
        workbook: &OwnedWorkbook,
        model: &WorkbookModel,
        operation: &str,
        arguments: &Arguments,
        id: Option<&str>,
    ) -> Result<Value> {
        match operation {
            "discover" => {
                only(arguments, &[])?;
                Ok(operation_catalog())
            }
            "describe" => {
                only(arguments, &[])?;
                Ok(json!({
                    "sheets":model.sheets.iter().map(|sheet|json!({"id":sheet.id,"name":sheet.name,"populated_cells":sheet.cells.len()})).collect::<Vec<_>>(),
                    "inputs":model.inputs,"outputs":model.outputs,
                    "limits":operation_catalog()["limits"],
                    "source":{"kind":if matches!(workbook,OwnedWorkbook::Imported(_)){"xlsx"}else{"authored"}}
                }))
            }
            "read" | "explain" => read_page(model, arguments, operation == "explain"),
            "calculate" => {
                only(arguments, &["outputs", "workers"])?;
                let workers = integer(arguments, "workers", Some(1), "invalid_request")?;
                if !(1..=8).contains(&workers) {
                    return Err(error("invalid_request", "workers must be between 1 and 8"));
                }
                let selected = select_outputs(model, arguments.get("outputs"))?;
                Ok(compact_report(&toolkit::calculate(
                    &selected,
                    workers as usize,
                )))
            }
            "preview_inputs" | "set_inputs" => {
                let preview = operation == "preview_inputs";
                only(
                    arguments,
                    if preview {
                        &["values", "expected_revision", "outputs"]
                    } else {
                        &["values", "expected_revision"]
                    },
                )?;
                check_revision(model, arguments, true)?;
                let values = input_values(arguments)?;
                let edits = toolkit::validate_inputs(model, &values).map_err(input_error)?;
                let candidate = validate_candidate(workbook, model, &edits)?;
                if preview {
                    let before = select_outputs(model, arguments.get("outputs"))?;
                    let after = select_outputs(&candidate, arguments.get("outputs"))?;
                    let changes = edits.iter().map(|edit| {
                        let (sheet,address) = resolve(model, &edit.sheet, &edit.address).expect("validated input binding");
                        json!({"sheet":sheet.name,"address":address,"before":sheet.cells.get(&address).map(|cell| &cell.value).unwrap_or(&CellValue::Blank),"after":edit.value})
                    }).collect::<Vec<_>>();
                    Ok(
                        json!({"applied":false,"base_revision":model.revision,"proposed_revision":candidate.revision,"changes":changes,"before":compact_report(&toolkit::calculate(&before,1)),"after":compact_report(&toolkit::calculate(&after,1))}),
                    )
                } else {
                    self.commit(
                        workbook,
                        model,
                        edits,
                        candidate.revision,
                        (id, operation, None),
                    )
                }
            }
            "edit" => {
                only(arguments, &["edits", "expected_revision"])?;
                check_revision(model, arguments, true)?;
                let raw = arguments
                    .get("edits")
                    .and_then(Value::as_array)
                    .ok_or_else(|| error("invalid_request", "edits must be an array"))?;
                if raw.is_empty() || raw.len() > MAX_ITEMS {
                    return Err(error(
                        "invalid_request",
                        "edits must contain 1 to 100 entries",
                    ));
                }
                let mut edits = Vec::with_capacity(raw.len());
                for item in raw {
                    let object = item
                        .as_object()
                        .ok_or_else(|| error("invalid_request", "each edit must be an object"))?;
                    only(object, &["sheet", "address", "value", "formula", "style"])?;
                    if matches!(workbook, OwnedWorkbook::Imported(_))
                        && object.contains_key("style")
                    {
                        return Err(error(
                            "unsupported_operation",
                            "imported style edits are unsupported",
                        ));
                    }
                    if (object.contains_key("value") && object.contains_key("formula"))
                        || !["value", "formula", "style"]
                            .iter()
                            .any(|name| object.contains_key(*name))
                    {
                        return Err(error(
                            "invalid_request",
                            "edit needs value, formula or style; value and formula are mutually exclusive",
                        ));
                    }
                    validate_scalar_property(object.get("value"))?;
                    edits.push(
                        serde_json::from_value::<Edit>(item.clone())
                            .map_err(|e| error("invalid_request", e.to_string()))?,
                    );
                }
                let candidate = validate_candidate(workbook, model, &edits)?;
                self.commit(
                    workbook,
                    model,
                    edits,
                    candidate.revision,
                    (id, operation, Some(raw)),
                )
            }
            "export" => {
                only(arguments, &["filename", "expected_revision"])?;
                check_revision(model, arguments, true)?;
                let filename = text(arguments, "filename", "invalid_request")?;
                if !safe_filename(filename) {
                    return Err(error(
                        "invalid_request",
                        "filename must be a single safe .xlsx name",
                    ));
                }
                let target = self.output_dir.join(filename);
                if target.symlink_metadata().is_ok() {
                    return Err(error("export_error", "output already exists"));
                }
                // No file is created if any required result is unavailable.
                let selected = select_outputs(model, None)?;
                if !toolkit::calculate(&selected, 1).diagnostics.is_empty() {
                    return Err(error("export_error", "required calculation is unsupported"));
                }
                let path = match workbook {
                    OwnedWorkbook::Authored(_) => xlsx::export_xlsx(model, &target),
                    OwnedWorkbook::Imported(imported) => imported.export(&target),
                }
                .map_err(|e| error("export_error", e.to_string()))?;
                let bytes = std::fs::metadata(path)
                    .map_err(|e| error("export_error", e.to_string()))?
                    .len();
                Ok(json!({"filename":filename,"revision":model.revision,"bytes":bytes}))
            }
            _ => Err(error("unknown_operation", "unknown agent operation")),
        }
    }
    fn commit(
        &self,
        workbook: &OwnedWorkbook,
        model: &WorkbookModel,
        edits: Vec<Edit>,
        proposed_revision: u64,
        context: (Option<&str>, &str, Option<&Vec<Value>>),
    ) -> Result<Value> {
        let (id, operation, submitted) = context;
        let changed = edits
            .iter()
            .enumerate()
            .map(|(index, edit)| {
                let (sheet, address) =
                    resolve(model, &edit.sheet, &edit.address).expect("validated edit");
                let mut fields = Vec::new();
                for (name, present) in [
                    ("value", edit.value.is_some()),
                    ("formula", edit.formula.is_some()),
                    ("style", edit.style.is_some()),
                ] {
                    if submitted.map_or(present, |items| items[index].get(name).is_some()) {
                        fields.push(name);
                    }
                }
                json!({"sheet":sheet.name,"address":address,"fields":fields})
            })
            .collect::<Vec<_>>();
        let receipt = json!({"previous_revision":model.revision,"revision":proposed_revision,"changed_cells":changed});
        ensure_response_size(&success(id, operation, proposed_revision, receipt.clone()))?;
        workbook.apply(edits, model.revision)?;
        Ok(receipt)
    }
}

fn export_directory(path: &Path) -> Result<PathBuf> {
    std::fs::create_dir_all(path).map_err(|e| error("export_error", e.to_string()))?;
    std::fs::canonicalize(path).map_err(|e| error("export_error", e.to_string()))
}
fn error(code: &str, message: impl Into<String>) -> ToolkitError {
    ToolkitError::new(code, message)
}
fn input_error(mut error: ToolkitError) -> ToolkitError {
    if error.code == "unknown_input" {
        error.code = "invalid_input".into();
    }
    error
}
fn parse_envelope(request: &Value) -> Result<(Option<&str>, &str, &Arguments)> {
    let envelope = request
        .as_object()
        .ok_or_else(|| error("invalid_request", "request must be an object"))?;
    only(envelope, &["id", "operation", "arguments"])?;
    let id = match envelope.get("id") {
        None => None,
        Some(Value::String(id)) if id.len() <= 128 => Some(id.as_str()),
        _ => {
            return Err(error(
                "invalid_request",
                "id must be text of at most 128 UTF-8 bytes",
            ));
        }
    };
    let operation = text(envelope, "operation", "invalid_request")?;
    // A static empty map avoids modifying or cloning the caller's envelope.
    static EMPTY: std::sync::OnceLock<Arguments> = std::sync::OnceLock::new();
    let arguments = match envelope.get("arguments") {
        None => EMPTY.get_or_init(Map::new),
        Some(Value::Object(arguments)) => arguments,
        _ => return Err(error("invalid_request", "arguments must be an object")),
    };
    Ok((id, operation, arguments))
}
fn only(arguments: &Arguments, names: &[&str]) -> Result<()> {
    if arguments.keys().any(|name| !names.contains(&name.as_str())) {
        return Err(error("invalid_request", "unknown argument property"));
    }
    Ok(())
}
fn text<'a>(arguments: &'a Arguments, name: &str, _code: &str) -> Result<&'a str> {
    arguments
        .get(name)
        .and_then(Value::as_str)
        .filter(|text| !text.is_empty())
        .ok_or_else(|| error("invalid_request", format!("{name} must be nonempty text")))
}

fn integer(arguments: &Arguments, name: &str, default: Option<u64>, code: &str) -> Result<u64> {
    match arguments.get(name) {
        None => default.ok_or_else(|| error(code, format!("{name} is required"))),
        Some(value) => value
            .as_u64()
            .ok_or_else(|| error(code, format!("{name} must be a nonnegative integer"))),
    }
}
fn check_revision(model: &WorkbookModel, arguments: &Arguments, required: bool) -> Result<()> {
    if required || arguments.contains_key("expected_revision") {
        let expected = integer(arguments, "expected_revision", None, "invalid_request")?;
        if expected != model.revision {
            return Err(error(
                "revision_conflict",
                "expected revision does not match current revision",
            ));
        }
    }
    Ok(())
}
fn validate_scalar_property(value: Option<&Value>) -> Result<()> {
    if let Some(Value::Object(object)) = value {
        only(object, &["error", "message"])?;
        if object.get("error").and_then(Value::as_str).is_none()
            || object
                .get("message")
                .is_some_and(|value| !value.is_string())
        {
            return Err(error("invalid_request", "error and message must be text"));
        }
    }
    Ok(())
}
fn input_values(arguments: &Arguments) -> Result<BTreeMap<String, CellValue>> {
    let values = arguments
        .get("values")
        .and_then(Value::as_object)
        .ok_or_else(|| error("invalid_request", "values must be an object"))?;
    if values.is_empty() || values.len() > MAX_ITEMS {
        return Err(error(
            "invalid_request",
            "values must contain 1 to 100 named inputs",
        ));
    }
    for value in values.values() {
        validate_scalar_property(Some(value))?;
    }
    serde_json::from_value(Value::Object(values.clone()))
        .map_err(|e| error("invalid_input", e.to_string()))
}
fn validate_candidate(
    workbook: &OwnedWorkbook,
    model: &WorkbookModel,
    edits: &[Edit],
) -> Result<WorkbookModel> {
    let candidate = Session::new(model.clone())?;
    candidate.apply(edits.to_vec(), Some(model.revision))?;
    workbook.validate_imported(edits)?;
    Ok(candidate.snapshot())
}
fn select_outputs(model: &WorkbookModel, outputs: Option<&Value>) -> Result<WorkbookModel> {
    let names = match outputs {
        None => model.outputs.keys().cloned().collect::<Vec<_>>(),
        Some(Value::Array(names)) => names
            .iter()
            .map(|v| {
                v.as_str()
                    .filter(|name| !name.is_empty())
                    .map(str::to_owned)
                    .ok_or_else(|| error("invalid_request", "output names must be text"))
            })
            .collect::<Result<Vec<_>>>()?,
        _ => return Err(error("invalid_request", "outputs must be an array")),
    };
    if names.is_empty() && outputs.is_none() {
        return Err(error(
            "invalid_input",
            "explicit output bindings are required",
        ));
    }
    if names.is_empty()
        || names.len() > MAX_ITEMS
        || names.iter().collect::<BTreeSet<_>>().len() != names.len()
    {
        return Err(error(
            "invalid_request",
            "outputs must contain 1 to 100 unique bindings",
        ));
    }
    let mut selected = model.clone();
    selected.outputs = names
        .into_iter()
        .map(|name| {
            model
                .outputs
                .get(&name)
                .cloned()
                .map(|binding| (name, binding))
                .ok_or_else(|| error("invalid_input", "unknown output binding"))
        })
        .collect::<Result<_>>()?;
    Ok(selected)
}
fn compact_report(report: &CalculationReport) -> Value {
    json!({"revision":report.revision,"outputs":report.outputs,
        "diagnostics":report.diagnostics.iter().take(MAX_ITEMS).collect::<Vec<_>>(),
        "diagnostic_count":report.diagnostics.len(),"diagnostics_truncated":report.diagnostics.len()>MAX_ITEMS,
        "evaluated_cell_count":report.evaluated_cells.len(),"stale":report.stale})
}
fn resolve<'a>(
    model: &'a WorkbookModel,
    sheet: &str,
    address: &str,
) -> Result<(&'a toolkit::Sheet, String)> {
    let sheet = model
        .sheets
        .iter()
        .find(|s| s.name.eq_ignore_ascii_case(sheet))
        .ok_or_else(|| error("invalid_reference", "unknown worksheet"))?;
    let reference = CellReference::parse(None, address)?;
    Ok((
        sheet,
        crate::encode_ref(reference.column as usize, reference.row as usize),
    ))
}
fn label(model: &WorkbookModel, sheet: &str, address: &str) -> Result<String> {
    let (sheet, address) = resolve(model, sheet, address)?;
    Ok(format!("{}!{}", sheet.name, address))
}
fn read_page(model: &WorkbookModel, arguments: &Arguments, explain: bool) -> Result<Value> {
    only(
        arguments,
        if explain {
            &["output", "offset", "limit", "expected_revision"]
        } else {
            &[
                "sheet",
                "range",
                "output",
                "offset",
                "limit",
                "expected_revision",
            ]
        },
    )?;
    let offset = integer(arguments, "offset", Some(0), "invalid_request")?;
    let limit = integer(arguments, "limit", Some(40), "invalid_request")?;
    if !(1..=100).contains(&limit) {
        return Err(error("invalid_request", "limit must be between 1 and 100"));
    }
    check_revision(model, arguments, offset > 0)?;
    if arguments.contains_key("sheet") == arguments.contains_key("output") {
        return Err(error(
            "invalid_request",
            "select exactly one sheet or output",
        ));
    }
    if arguments.contains_key("range") && !arguments.contains_key("sheet") {
        return Err(error("invalid_request", "range requires sheet"));
    }
    let inspection = toolkit::inspect(model);
    let dependencies = inspection.get("dependencies").and_then(Value::as_object);
    let mut selected = BTreeSet::new();
    let mut report = None;
    let mut binding = None;
    if arguments.contains_key("output") {
        let output = text(arguments, "output", "invalid_input")?;
        let output_binding = model
            .outputs
            .get(output)
            .ok_or_else(|| error("invalid_input", "unknown output binding"))?;
        binding = Some(output_binding);
        let mut pending = vec![label(
            model,
            &output_binding.sheet,
            &output_binding.address,
        )?];
        while let Some(current) = pending.pop() {
            if selected.insert(current.clone())
                && let Some(direct) = dependencies
                    .and_then(|d| d.get(&current))
                    .and_then(Value::as_array)
            {
                pending.extend(direct.iter().filter_map(Value::as_str).map(str::to_owned));
            }
        }
        if explain {
            let selected_model = select_outputs(model, Some(&json!([output])))?;
            report = Some(toolkit::calculate(&selected_model, 1));
        }
    } else {
        let sheet_name = text(arguments, "sheet", "invalid_reference")?;
        let sheet = model
            .sheets
            .iter()
            .find(|s| s.name.eq_ignore_ascii_case(sheet_name))
            .ok_or_else(|| error("invalid_reference", "unknown worksheet"))?;
        let range = if arguments.contains_key("range") {
            let range = text(arguments, "range", "invalid_reference")?;
            let (start, end) = range.split_once(':').unwrap_or((range, range));
            let start = CellReference::parse(None, start)?;
            let end = CellReference::parse(None, end)?;
            if start.row > end.row || start.column > end.column {
                return Err(error("invalid_reference", "range bounds are reversed"));
            }
            Some((start, end))
        } else {
            None
        };
        for address in sheet.cells.keys() {
            let cell = CellReference::parse(None, address)?;
            if range.as_ref().is_none_or(|(start, end)| {
                cell.row >= start.row
                    && cell.row <= end.row
                    && cell.column >= start.column
                    && cell.column <= end.column
            }) {
                selected.insert(format!("{}!{address}", sheet.name));
            }
        }
    }
    let mut ordered = selected
        .iter()
        .map(|key| {
            let (sheet, address) = key.rsplit_once('!').expect("engine cell label");
            let index = model
                .sheets
                .iter()
                .position(|s| s.name == sheet)
                .expect("engine sheet");
            let cell = CellReference::parse(None, address).expect("engine reference");
            (index, cell.row, cell.column, key)
        })
        .collect::<Vec<_>>();
    ordered.sort_unstable();
    let start = usize::try_from(offset)
        .unwrap_or(usize::MAX)
        .min(ordered.len());
    let end = start.saturating_add(limit as usize).min(ordered.len());
    let cells=ordered[start..end].iter().map(|(index,_,_,key)| {
        let sheet=&model.sheets[*index];
        let address=key.rsplit_once('!').unwrap().1;
        let cell=sheet.cells.get(address);
        let direct=dependencies.and_then(|d|d.get(*key)).and_then(Value::as_array);
        let count=direct.map_or(0,Vec::len);
        let mut record=json!({"sheet_id":sheet.id,"sheet":sheet.name,"address":address,
            "content":cell.map(|cell|json!(cell)).unwrap_or_else(||json!({"value":null})),
            "dependencies":direct.map(|d|d.iter().take(MAX_ITEMS).cloned().collect::<Vec<_>>()).unwrap_or_default(),
            "dependency_count":count,"dependencies_truncated":count>MAX_ITEMS});
        if let Some(report)=&report {
            let value=report.values.get(*key);
            let origin=if value.is_none(){"unavailable"} else if cell.is_some_and(|cell|cell.formula.is_some()) {"calculated"}
                else if cell.is_none_or(|cell|cell.value==CellValue::Blank){"blank"}else{"authored"};
            record["calculated_value"]=json!(value);
            record["value_origin"]=json!(origin);
        }
        record
    }).collect::<Vec<_>>();
    let diagnostics = if let Some(report) = &report {
        json!(report.diagnostics)
    } else {
        inspection["diagnostics"].clone()
    };
    let diagnostics = diagnostics
        .as_array()
        .into_iter()
        .flatten()
        .filter(
            |diagnostic| match (diagnostic["sheet"].as_str(), diagnostic["address"].as_str()) {
                (Some(sheet), Some(address)) => selected.contains(&format!("{sheet}!{address}")),
                _ => true,
            },
        )
        .collect::<Vec<_>>();
    let mut page = json!({"cells":cells,"offset":offset,"limit":limit,"total_cells":ordered.len(),
        "next_offset":if end<ordered.len(){Some(end)}else{None},"truncated":end<ordered.len(),
        "diagnostics":diagnostics.iter().take(MAX_ITEMS).collect::<Vec<_>>(),"diagnostic_count":diagnostics.len(),"diagnostics_truncated":diagnostics.len()>MAX_ITEMS});
    if let Some(report) = report {
        let output = text(arguments, "output", "invalid_input")?;
        page["output"] = json!(output);
        page["binding"] = json!(binding);
        page["value"] = json!(report.outputs.get(output));
    }
    Ok(page)
}
fn safe_filename(filename: &str) -> bool {
    let Some(stem) = filename.strip_suffix(".xlsx") else {
        return false;
    };
    (1..=120).contains(&stem.len())
        && stem.as_bytes()[0].is_ascii_alphanumeric()
        && stem
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'-'))
}
fn success(id: Option<&str>, operation: &str, revision: u64, result: Value) -> Value {
    json!({"schema_version":1,"id":id,"operation":operation,"ok":true,"revision":revision,"result":result})
}
fn failure(
    id: Option<&str>,
    operation: Option<&str>,
    revision: u64,
    code: &str,
    message: &str,
) -> Value {
    let message = message.chars().take(4096).collect::<String>();
    let response = json!({"schema_version":1,"id":id,"operation":operation,"ok":false,"revision":revision,"error":{"code":code,"message":message}});
    if serde_json::to_vec(&response).map_or(true, |bytes| bytes.len() > MAX_RESPONSE_BYTES) {
        json!({"schema_version":1,"id":id,"operation":null,"ok":false,"revision":revision,"error":{"code":"resource_limit","message":"response metadata exceeds 256 KiB"}})
    } else {
        response
    }
}
fn ensure_response_size(response: &Value) -> Result<()> {
    if serde_json::to_vec(response).map_or(true, |bytes| bytes.len() > MAX_RESPONSE_BYTES) {
        Err(error(
            "resource_limit",
            "response exceeds 256 KiB; request a smaller page or output selection",
        ))
    } else {
        Ok(())
    }
}
fn bounded_response(response: Value) -> Value {
    if let Err(error) = ensure_response_size(&response) {
        failure(
            response["id"].as_str(),
            response["operation"].as_str(),
            response["revision"].as_u64().unwrap_or(0),
            &error.code,
            &error.message,
        )
    } else {
        response
    }
}

/// Process bounded JSON lines, draining oversized lines before accepting the next
/// request. The buffer never grows past the request limit, even without a newline.
pub fn serve_json_lines<R: BufRead, W: Write>(
    agent: &AgentWorkbook,
    mut reader: R,
    mut writer: W,
) -> io::Result<()> {
    loop {
        let mut line = Vec::new();
        let mut oversized = false;
        let mut received = false;
        loop {
            let chunk = reader.fill_buf()?;
            if chunk.is_empty() {
                break;
            }
            received = true;
            let newline = chunk.iter().position(|b| *b == b'\n');
            let count = newline.unwrap_or(chunk.len());
            if !oversized {
                if line.len().saturating_add(count) > MAX_REQUEST_BYTES {
                    oversized = true;
                    line.clear();
                } else {
                    line.extend_from_slice(&chunk[..count]);
                }
            }
            reader.consume(count + usize::from(newline.is_some()));
            if newline.is_some() {
                break;
            }
        }
        if !received {
            break;
        }
        let response = if oversized {
            agent.transport_error("resource_limit", "request exceeds 1 MiB")
        } else {
            match serde_json::from_slice(&line) {
                Ok(request) => agent.handle(request),
                Err(_) => agent.transport_error(
                    "invalid_request",
                    "request line must contain valid UTF-8 JSON",
                ),
            }
        };
        serde_json::to_writer(&mut writer, &response)?;
        writer.write_all(b"\n")?;
        writer.flush()?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::toolkit::{Cell, CellAddress, InputBinding, Sheet};
    use std::sync::atomic::{AtomicU64, Ordering};

    fn directory() -> PathBuf {
        static NEXT: AtomicU64 = AtomicU64::new(0);
        let path = std::env::temp_dir().join(format!(
            "workbook-forge-agent-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir_all(&path).unwrap();
        path
    }
    fn scenario() -> AgentWorkbook {
        AgentWorkbook::new(toolkit::operating_scenario(), directory()).unwrap()
    }
    fn call(agent: &AgentWorkbook, operation: &str, arguments: Value) -> Value {
        agent.handle(json!({"id":"test","operation":operation,"arguments":arguments}))
    }
    fn ok(response: Value) -> Value {
        assert_eq!(response["ok"], true, "{response}");
        response["result"].clone()
    }
    fn code(response: Value, expected: &str) {
        assert_eq!(response["ok"], false, "{response}");
        assert_eq!(response["error"]["code"], expected, "{response}");
    }

    #[test]
    fn scenario_preview_commit_explain_export_and_reimport() {
        let agent = scenario();
        assert_eq!(
            ok(call(&agent, "discover", json!({})))["profile"],
            "agent-workbook-v1"
        );
        let description = ok(call(&agent, "describe", json!({})));
        assert_eq!(description["source"]["kind"], "authored");
        assert!(description.get("cells").is_none());
        let preview = ok(call(
            &agent,
            "preview_inputs",
            json!({"values":{"unit_price":25},"expected_revision":0}),
        ));
        assert_eq!(preview["applied"], false);
        assert_eq!(preview["before"]["outputs"]["profit"], 1440.0);
        assert_eq!(preview["after"]["outputs"]["profit"], 3290.0);
        assert_eq!(preview["proposed_revision"], 1);
        assert_eq!(call(&agent, "describe", json!({}))["revision"], 0);
        let receipt = ok(call(
            &agent,
            "set_inputs",
            json!({"values":{"unit_price":25},"expected_revision":0}),
        ));
        assert_eq!(receipt["revision"], 1);
        assert_eq!(receipt["changed_cells"][0]["fields"], json!(["value"]));
        let report = ok(call(&agent, "calculate", json!({"workers":2})));
        assert_eq!(report["outputs"]["revenue"], 9250.0);
        assert_eq!(report["outputs"]["profit"], 3290.0);
        assert!(
            (report["outputs"]["break_even_units"].as_f64().unwrap() - 1000.0 / 17.0).abs() < 1e-12
        );
        assert!(report.get("values").is_none());
        let explanation = ok(call(&agent, "explain", json!({"output":"profit"})));
        assert_eq!(explanation["value"], 3290.0);
        assert!(
            explanation["cells"]
                .as_array()
                .unwrap()
                .iter()
                .any(|cell| cell["value_origin"] == "calculated")
        );
        let exported = ok(call(
            &agent,
            "export",
            json!({"filename":"scenario.xlsx","expected_revision":1}),
        ));
        assert!(exported["bytes"].as_u64().unwrap() > 0);
        let bindings = toolkit::operating_scenario();
        let imported = xlsx::import_xlsx(
            agent.output_dir.join("scenario.xlsx"),
            bindings.inputs,
            bindings.outputs,
        )
        .unwrap();
        assert_eq!(
            serde_json::to_value(imported.calculate(1)).unwrap()["outputs"]["profit"],
            3290.0
        );
        code(
            call(
                &agent,
                "export",
                json!({"filename":"scenario.xlsx","expected_revision":1}),
            ),
            "export_error",
        );
        assert_eq!(call(&agent, "describe", json!({}))["revision"], 1);
    }
    #[test]
    fn request_errors_are_atomic_and_revision_checks_are_exact() {
        let agent = scenario();
        for arguments in [
            json!({"values":{"unit_price":-1},"expected_revision":0}),
            json!({"values":{"missing":1},"expected_revision":0}),
        ] {
            code(call(&agent, "set_inputs", arguments), "invalid_input");
        }
        code(
            call(
                &agent,
                "edit",
                json!({"edits":[{"sheet":"Assumptions","address":"B1","value":25},{"sheet":"missing","address":"A1","value":2}],"expected_revision":0}),
            ),
            "invalid_reference",
        );
        for arguments in [
            json!({"edits":[],"expected_revision":0}),
            json!({"edits":[{"sheet":"Assumptions","address":"B1"}],"expected_revision":0}),
            json!({"edits":[{"sheet":"Assumptions","address":"B1","value":25,"formula":null}],"expected_revision":0}),
        ] {
            code(call(&agent, "edit", arguments), "invalid_request");
        }
        code(
            call(
                &agent,
                "set_inputs",
                json!({"values":{"unit_price":25},"expected_revision":true}),
            ),
            "invalid_request",
        );
        code(
            call(&agent, "calculate", json!({"workers":1.0})),
            "invalid_request",
        );
        code(
            call(&agent, "calculate", json!({"outputs":["profit","profit"]})),
            "invalid_request",
        );
        code(
            call(
                &agent,
                "export",
                json!({"filename":"../bad.xlsx","expected_revision":0}),
            ),
            "invalid_request",
        );
        code(
            call(&agent, "describe", json!({"ignored":true})),
            "invalid_request",
        );
        code(
            call(
                &agent,
                "set_inputs",
                json!({"values":{"unit_price":25},"expected_revision":1}),
            ),
            "revision_conflict",
        );
        assert_eq!(call(&agent, "describe", json!({}))["revision"], 0);
        assert_eq!(
            ok(call(&agent, "calculate", json!({})))["outputs"]["profit"],
            1440.0
        );
    }
    #[test]
    fn output_selection_and_trace_do_not_use_imported_caches() {
        let mut sheet = Sheet::new("A!B");
        sheet.cells.insert("B1".into(), Cell::formula("=A1+1"));
        sheet.cells.insert(
            "C1".into(),
            Cell {
                cached_value: Some(CellValue::Number(999.0)),
                ..Cell::formula("=UNIMPLEMENTED(1)")
            },
        );
        let model = WorkbookModel {
            sheets: vec![sheet],
            outputs: BTreeMap::from([
                ("good".into(), CellAddress::new("A!B", "B1")),
                ("bad".into(), CellAddress::new("A!B", "C1")),
            ]),
            ..WorkbookModel::default()
        };
        let agent = AgentWorkbook::new(model, directory()).unwrap();
        let report = ok(call(&agent, "calculate", json!({"outputs":["good"]})));
        assert_eq!(report["outputs"]["good"], 1.0);
        assert_eq!(report["diagnostic_count"], 0);
        let trace = ok(call(&agent, "explain", json!({"output":"good"})));
        assert_eq!(trace["total_cells"], 2);
        assert_eq!(trace["cells"][0]["address"], "A1");
        assert_eq!(trace["cells"][0]["value_origin"], "blank");
        assert_eq!(trace["cells"][0]["content"], json!({"value":null}));
        let bad = ok(call(&agent, "explain", json!({"output":"bad"})));
        assert_eq!(bad["value"], Value::Null);
        assert_eq!(bad["cells"][0]["value_origin"], "unavailable");
        assert_eq!(bad["cells"][0]["content"]["cached_value"], 999.0);
        assert_eq!(bad["cells"][0]["calculated_value"], Value::Null);
        code(
            call(
                &agent,
                "export",
                json!({"filename":"unsupported.xlsx","expected_revision":0}),
            ),
            "export_error",
        );
        assert!(!agent.output_dir.join("unsupported.xlsx").exists());
    }
    #[test]
    fn paginated_closures_include_blanks_and_clip_dependencies() {
        let mut sheet = Sheet::new("Sheet");
        sheet
            .cells
            .insert("B1".into(), Cell::formula("=SUM(A1:A150)"));
        let model = WorkbookModel {
            sheets: vec![sheet],
            outputs: BTreeMap::from([("sum".into(), CellAddress::new("Sheet", "B1"))]),
            ..WorkbookModel::default()
        };
        let agent = AgentWorkbook::new(model, directory()).unwrap();
        let first = ok(call(&agent, "read", json!({"output":"sum","limit":2})));
        assert_eq!(first["total_cells"], 151);
        assert_eq!(first["next_offset"], 2);
        assert_eq!(first["cells"][1]["address"], "B1");
        assert_eq!(first["cells"][1]["dependency_count"], 150);
        assert_eq!(
            first["cells"][1]["dependencies"].as_array().unwrap().len(),
            100
        );
        assert_eq!(first["cells"][1]["dependencies_truncated"], true);
        code(
            call(&agent, "read", json!({"output":"sum","offset":2})),
            "invalid_request",
        );
        let next = ok(call(
            &agent,
            "read",
            json!({"output":"sum","offset":2,"expected_revision":0,"limit":2}),
        ));
        assert_eq!(next["cells"][0]["address"], "A2");
        ok(call(
            &agent,
            "edit",
            json!({"edits":[{"sheet":"Sheet","address":"A1","value":2}],"expected_revision":0}),
        ));
        code(
            call(
                &agent,
                "read",
                json!({"output":"sum","offset":2,"expected_revision":0}),
            ),
            "revision_conflict",
        );
    }
    #[test]
    fn oversized_responses_refuse_reads_but_not_compact_mutations() {
        let mut sheet = Sheet::new("Large");
        for row in 1..=10 {
            sheet.cells.insert(
                format!("A{row}"),
                Cell::value(CellValue::Text("é".repeat(32000))),
            );
        }
        let agent = AgentWorkbook::new(
            WorkbookModel {
                sheets: vec![sheet],
                ..WorkbookModel::default()
            },
            directory(),
        )
        .unwrap();
        code(
            call(&agent, "read", json!({"sheet":"Large"})),
            "resource_limit",
        );
        let receipt = ok(call(
            &agent,
            "edit",
            json!({"edits":[{"sheet":"Large","address":"A1","value":"x".repeat(32000),"style":null}],"expected_revision":0}),
        ));
        assert_eq!(
            receipt["changed_cells"][0]["fields"],
            json!(["value", "style"])
        );
        assert_eq!(receipt["revision"], 1);
        let huge = agent.handle(json!({"operation":"X".repeat(MAX_RESPONSE_BYTES)}));
        code(huge.clone(), "resource_limit");
        assert!(serde_json::to_vec(&huge).unwrap().len() <= MAX_RESPONSE_BYTES);
    }
    #[test]
    fn line_transport_drains_overflow_and_continues_after_invalid_utf8() {
        let agent = scenario();
        let mut input = vec![b'x'; MAX_REQUEST_BYTES + 10];
        input.extend_from_slice(
            b"\n\xff\n{not json}\n{\"operation\":\"describe\"}\n{\"operation\":\"calculate\"}",
        );
        let mut output = Vec::new();
        serve_json_lines(&agent, std::io::Cursor::new(input), &mut output).unwrap();
        let responses = output
            .split(|b| *b == b'\n')
            .filter(|line| !line.is_empty())
            .map(|line| serde_json::from_slice::<Value>(line).unwrap())
            .collect::<Vec<_>>();
        assert_eq!(responses.len(), 5);
        code(responses[0].clone(), "resource_limit");
        code(responses[1].clone(), "invalid_request");
        code(responses[2].clone(), "invalid_request");
        assert_eq!(responses[3]["ok"], true);
        assert_eq!(responses[4]["ok"], true);
    }
    #[test]
    fn imported_preview_and_edits_enforce_the_preservation_boundary() {
        let root = directory();
        let model = toolkit::operating_scenario();
        let source = root.join("source.xlsx");
        xlsx::export_xlsx(&model, &source).unwrap();
        let source_bytes = std::fs::read(&source).unwrap();
        let imported = xlsx::import_xlsx(&source, model.inputs, model.outputs).unwrap();
        let agent = AgentWorkbook::from_imported(imported, &root).unwrap();
        assert_eq!(
            ok(call(&agent, "describe", json!({})))["source"]["kind"],
            "xlsx"
        );
        ok(call(
            &agent,
            "preview_inputs",
            json!({"values":{"unit_price":25},"expected_revision":0}),
        ));
        code(
            call(
                &agent,
                "edit",
                json!({"edits":[{"sheet":"Assumptions","address":"B1","value":25,"style":null}],"expected_revision":0}),
            ),
            "unsupported_operation",
        );
        code(
            call(
                &agent,
                "edit",
                json!({"edits":[{"sheet":"Assumptions","address":"Z99","value":1}],"expected_revision":0}),
            ),
            "unsupported_operation",
        );
        ok(call(
            &agent,
            "set_inputs",
            json!({"values":{"unit_price":25},"expected_revision":0}),
        ));
        ok(call(
            &agent,
            "export",
            json!({"filename":"edited.xlsx","expected_revision":1}),
        ));
        assert_eq!(std::fs::read(&source).unwrap(), source_bytes);
        let mut input = InputBinding::number("Assumptions", "Z99", None, None);
        input.required = false;
        let imported = xlsx::import_xlsx(
            &source,
            BTreeMap::from([("missing".into(), input)]),
            BTreeMap::from([("price".into(), CellAddress::new("Assumptions", "B1"))]),
        )
        .unwrap();
        let agent = AgentWorkbook::from_imported(imported, &root).unwrap();
        for operation in ["preview_inputs", "set_inputs"] {
            code(
                call(
                    &agent,
                    operation,
                    json!({"values":{"missing":1},"expected_revision":0}),
                ),
                "unsupported_operation",
            );
        }
        assert_eq!(call(&agent, "describe", json!({}))["revision"], 0);
    }
}
