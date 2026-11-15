//! A batch boundary between Python and the toolkit workbook form.
//!
//! The bridge uses toolkit::WorkbookModel and toolkit::Session. It is not
//! the canonical model::Workbook. JSON is an interchange representation, not a
//! persistence or synchronization protocol. Calculation and session edits
//! remain entirely inside Rust.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyInt;
use serde::{Serialize, de::DeserializeOwned};
use std::collections::BTreeMap;
use workbook_forge::toolkit;

const MAX_REQUEST_BYTES: usize = 128 * 1024 * 1024;

fn decode<T: DeserializeOwned>(source: &str) -> PyResult<T> {
    if source.len() > MAX_REQUEST_BYTES {
        return Err(PyValueError::new_err("native request exceeds 128 MiB"));
    }
    serde_json::from_str(source).map_err(|error| PyValueError::new_err(error.to_string()))
}

fn encode(value: &impl Serialize) -> PyResult<String> {
    serde_json::to_string(value).map_err(|error| PyValueError::new_err(error.to_string()))
}

fn invalid(error: impl std::fmt::Display) -> PyErr {
    PyValueError::new_err(error.to_string())
}

/// A v1 toolkit document and the legacy `revision` it may still carry (0
/// when absent). The model holds no revision; the caller decides what the
/// number seeds or is echoed in.
fn document(source: &str) -> PyResult<(toolkit::WorkbookModel, u64)> {
    if source.len() > MAX_REQUEST_BYTES {
        return Err(PyValueError::new_err("native request exceeds 128 MiB"));
    }
    toolkit::read_document(source.as_bytes())
        .map_err(|error| PyValueError::new_err(error.to_string()))
}

#[pyclass(name = "Session", frozen)]
struct NativeSession {
    inner: toolkit::Session,
}

#[pymethods]
impl NativeSession {
    /// `names_json` is the workbook's defined-name table (read-only), a JSON
    /// array of `{name, sheet, formula, hidden}`. The session starts at
    /// `revision` when it is given; otherwise at the legacy `revision` key of
    /// a v1 document that still carries one, and at 0 without it.
    #[new]
    #[pyo3(signature = (model_json, names_json=None, *, revision=None))]
    fn new(
        model_json: &str,
        names_json: Option<&str>,
        revision: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<Self> {
        // The seed is refused as the Python engine refuses it: only an exact
        // int in the unsigned 64-bit range, so a bool is not read as 0 or 1.
        let seed = revision
            .map(|seed| {
                seed.is_exact_instance_of::<PyInt>()
                    .then(|| seed.extract::<u64>().ok())
                    .flatten()
                    .ok_or_else(|| {
                        PyValueError::new_err(
                            "invalid_request: revision must be an unsigned 64-bit integer",
                        )
                    })
            })
            .transpose()?;
        let (model, legacy) = document(model_json)?;
        let names = names_json.map(names).transpose()?;
        let revision = seed.unwrap_or(legacy);
        Ok(Self {
            inner: toolkit::Session::with_names_at(model, names, revision).map_err(invalid)?,
        })
    }

    /// The session revision. The snapshot document does not carry it.
    #[getter]
    fn revision(&self) -> u64 {
        self.inner.revision()
    }

    fn snapshot(&self) -> PyResult<String> {
        encode(&self.inner.snapshot())
    }

    /// The snapshot and the revision it is at, read under one lock.
    fn snapshot_with_revision(&self) -> PyResult<(String, u64)> {
        let (model, revision) = self.inner.snapshot_with_revision();
        Ok((encode(&model)?, revision))
    }

    /// A detached copy at the same revision. Edits on it never reach this
    /// session.
    fn fork(&self) -> Self {
        Self {
            inner: self.inner.fork(),
        }
    }

    #[pyo3(signature = (edits_json, expected_revision=None))]
    fn apply(
        &self,
        py: Python<'_>,
        edits_json: &str,
        expected_revision: Option<u64>,
    ) -> PyResult<u64> {
        let edits = decode(edits_json)?;
        py.detach(|| self.inner.apply(edits, expected_revision))
            .map_err(invalid)
    }

    #[pyo3(signature = (workers=1))]
    fn calculate(&self, py: Python<'_>, workers: usize) -> PyResult<String> {
        let report = py.detach(|| self.inner.calculate(workers));
        encode(&report)
    }

    #[pyo3(signature = (inputs_json, expected_revision=None))]
    fn set_inputs(
        &self,
        py: Python<'_>,
        inputs_json: &str,
        expected_revision: Option<u64>,
    ) -> PyResult<u64> {
        // A value the cell-value decoder refuses carries the Python engine's
        // code for it.
        if inputs_json.len() > MAX_REQUEST_BYTES {
            return Err(PyValueError::new_err("native request exceeds 128 MiB"));
        }
        let inputs: BTreeMap<String, toolkit::CellValue> = serde_json::from_str(inputs_json)
            .map_err(|error| PyValueError::new_err(format!("invalid_value: {error}")))?;
        // The revision check, the validation and the commit share one lock
        // in the session, so another Python thread cannot edit between them
        // while the interpreter is detached.
        py.detach(|| self.inner.set_inputs(&inputs, expected_revision))
            .map_err(invalid)
    }

    /// One pass as a `calculation-report.v2`, published like `calculate`.
    /// `calc_json` pins the clock and seed (`{now_ms, utc_offset_minutes,
    /// seed}`); without it the pass captures them at the session offset.
    #[pyo3(signature = (workers=1, calc_json=None, *, model_version=None, source_fingerprint=None))]
    fn calculate_report(
        &self,
        py: Python<'_>,
        workers: usize,
        calc_json: Option<&str>,
        model_version: Option<u32>,
        source_fingerprint: Option<&str>,
    ) -> PyResult<String> {
        let context = match calc_json {
            Some(source) => {
                let context = serde_json::from_str::<workbook_forge::CalcContext>(source)
                    .map_err(|error| PyValueError::new_err(format!("invalid_request: {error}")))?;
                // The struct reader has no range rule; the offset is held to
                // the one the session setter and the Python engine enforce.
                workbook_forge::volatile::check_offset(context.utc_offset_minutes).map_err(
                    |message| PyValueError::new_err(format!("invalid_request: calc: {message}")),
                )?;
                context
            }
            None => workbook_forge::CalcContext::capture(self.inner.utc_offset_minutes()),
        };
        let pass = py.detach(|| self.inner.calculate_pass(workers, &context));
        encode(&workbook_forge::session::report_v2(
            &pass,
            "rust",
            model_version,
            source_fingerprint,
        ))
    }
}

#[pyfunction]
fn scenario() -> PyResult<String> {
    encode(&toolkit::operating_scenario())
}

fn names(source: &str) -> PyResult<workbook_forge::DefinedNames> {
    let entries: Vec<workbook_forge::DefinedName> = decode(source)?;
    Ok(workbook_forge::DefinedNames::new(entries))
}

#[pyfunction]
#[pyo3(signature = (model_json, names_json=None))]
fn inspect(model_json: &str, names_json: Option<&str>) -> PyResult<String> {
    // A stateless call has no session: it reports the legacy `revision` the
    // document carried, or 0.
    let (model, revision) = document(model_json)?;
    model.validate().map_err(invalid)?;
    let names = names_json.map(names).transpose()?;
    encode(&toolkit::inspect_with_names_at(
        &model,
        names.as_ref(),
        revision,
    ))
}

#[pyfunction]
fn copy_formula(formula: &str, row_delta: i32, column_delta: i32) -> PyResult<String> {
    toolkit::copy_formula(formula, row_delta, column_delta).map_err(invalid)
}

#[pyfunction]
fn analyze_formula(formula: &str) -> PyResult<String> {
    encode(&toolkit::analyze_formula(formula).map_err(invalid)?)
}

/// `names_json` is the workbook's defined-name table, the same JSON array
/// `Session` takes. Without it, a name in a formula is refused as unsupported.
#[pyfunction]
#[pyo3(signature = (model_json, workers=1, names_json=None))]
fn calculate(
    py: Python<'_>,
    model_json: &str,
    workers: usize,
    names_json: Option<&str>,
) -> PyResult<String> {
    // A stateless call has no session of its own: the report carries the
    // legacy `revision` the document held, or 0.
    let (model, revision) = document(model_json)?;
    model.validate().map_err(invalid)?;
    // names_json absent selects a session with no names, which calculates
    // as toolkit::calculate does.
    let table = names_json.map(names).transpose()?;
    let session = toolkit::Session::with_names_at(model, table, revision).map_err(invalid)?;
    encode(&py.detach(|| session.calculate(workers)))
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<NativeSession>()?;
    module.add_function(wrap_pyfunction!(scenario, module)?)?;
    module.add_function(wrap_pyfunction!(inspect, module)?)?;
    module.add_function(wrap_pyfunction!(copy_formula, module)?)?;
    module.add_function(wrap_pyfunction!(analyze_formula, module)?)?;
    module.add_function(wrap_pyfunction!(calculate, module)?)?;
    Ok(())
}
