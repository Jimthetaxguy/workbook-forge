//! A batch boundary between Python and the authoritative Rust workbook model.
//!
//! JSON is an interchange representation, not a persistence or synchronization
//! protocol. Calculation and session edits remain entirely inside Rust.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
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

#[pyclass(name = "Session", frozen)]
struct NativeSession {
    inner: toolkit::Session,
}

#[pymethods]
impl NativeSession {
    #[new]
    fn new(model_json: &str) -> PyResult<Self> {
        let model = decode(model_json)?;
        Ok(Self {
            inner: toolkit::Session::new(model).map_err(invalid)?,
        })
    }

    fn snapshot(&self) -> PyResult<String> {
        encode(&self.inner.snapshot())
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
        let inputs: BTreeMap<String, toolkit::CellValue> = decode(inputs_json)?;
        // Validation and publication use the same revision even when another
        // Python thread edits while the interpreter is detached.
        let model = self.inner.snapshot();
        if expected_revision.is_some_and(|revision| revision != model.revision) {
            return Err(PyValueError::new_err("model revision conflict"));
        }
        let edits = toolkit::validate_inputs(&model, &inputs).map_err(invalid)?;
        py.detach(|| self.inner.apply(edits, Some(model.revision)))
            .map_err(invalid)
    }
}

#[pyfunction]
fn scenario() -> PyResult<String> {
    encode(&toolkit::operating_scenario())
}

#[pyfunction]
fn inspect(model_json: &str) -> PyResult<String> {
    let model: toolkit::WorkbookModel = decode(model_json)?;
    model.validate().map_err(invalid)?;
    encode(&toolkit::inspect(&model))
}

#[pyfunction]
fn copy_formula(formula: &str, row_delta: i32, column_delta: i32) -> PyResult<String> {
    toolkit::copy_formula(formula, row_delta, column_delta).map_err(invalid)
}

#[pyfunction]
fn analyze_formula(formula: &str) -> PyResult<String> {
    encode(&toolkit::analyze_formula(formula).map_err(invalid)?)
}

#[pyfunction]
#[pyo3(signature = (model_json, workers=1))]
fn calculate(py: Python<'_>, model_json: &str, workers: usize) -> PyResult<String> {
    let model: toolkit::WorkbookModel = decode(model_json)?;
    model.validate().map_err(invalid)?;
    encode(&py.detach(|| toolkit::calculate(&model, workers)))
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
