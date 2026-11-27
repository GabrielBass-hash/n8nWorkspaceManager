//! Python bindings for `src/n8n_launcher/core/paths.py`: generated from the signatures in `port.rs` by rust-anything.
//! Regenerate with `rust-anything refresh` after changing them.

#![allow(clippy::too_many_arguments)]

use pyo3::prelude::*;

pub mod js;
pub mod port;
pub mod pyconv;
pub mod pynum;

#[allow(unused_imports)]
use pynum::Num;

fn error(message: String) -> PyErr {
    use pyo3::exceptions as e;
    /// `Err("Kind: text")` for a `Kind` the match below does not know: a built-in exception's name or
    /// `package.module.Class`, called with the text. None when it names no `Exception` subclass or the
    /// class cannot be made from the text alone (its constructor takes more arguments).
    fn named_error(kind: &str, text: &str) -> Option<PyErr> {
        Python::attach(|py| {
            // Each dotted part must be a Python identifier, by Python's own rules (`str.isidentifier`).
            let identifier = |part: &str| {
                pyo3::types::PyString::new(py, part)
                    .call_method0("isidentifier")
                    .and_then(|ok| ok.extract::<bool>())
                    .unwrap_or(false)
            };
            if !kind.split('.').all(identifier) {
                return None;
            }
            let (module, name) = kind.rsplit_once('.').unwrap_or(("builtins", kind));
            let class = py.import(module).ok()?.getattr(name).ok()?;
            let class = class.cast_into::<pyo3::types::PyType>().ok()?;
            if !class.is_subclass_of::<pyo3::exceptions::PyException>().ok()? {
                return None;
            }
            let value = class.call1((text,)).ok()?;
            if !value.is_instance(&class).ok()? {
                return None;
            }
            Some(PyErr::from_value(value))
        })
    }
    let (kind, text) = message.split_once(": ").unwrap_or(("", message.as_str()));
    let text = text.to_string();
    match kind {
        "TypeError" => e::PyTypeError::new_err(text),
        "KeyError" => e::PyKeyError::new_err(text),
        "IndexError" => e::PyIndexError::new_err(text),
        "OverflowError" => e::PyOverflowError::new_err(text),
        "ZeroDivisionError" => e::PyZeroDivisionError::new_err(text),
        "AttributeError" => e::PyAttributeError::new_err(text),
        "RuntimeError" => e::PyRuntimeError::new_err(text),
        "NotImplementedError" => e::PyNotImplementedError::new_err(text),
        "LookupError" => e::PyLookupError::new_err(text),
        "ArithmeticError" => e::PyArithmeticError::new_err(text),
        "UnicodeError" => e::PyUnicodeError::new_err(text),
        "ValueError" => e::PyValueError::new_err(text),
        _ => named_error(kind, &text).unwrap_or_else(|| e::PyValueError::new_err(message)),
    }
}

fn config_dir_inner() -> Result<serde_json::Value, String> {
    let out = port::config_dir()?;
    Ok(out)
}

/// `config_dir`.
#[pyfunction]
#[pyo3(name = "config_dir", signature = ())]
fn config_dir(py: Python<'_>, ) -> PyResult<Py<PyAny>> {
    config_dir_inner().map_err(error).and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn config_file_inner() -> Result<serde_json::Value, String> {
    let out = port::config_file()?;
    Ok(out)
}

/// `config_file`.
#[pyfunction]
#[pyo3(name = "config_file", signature = ())]
fn config_file(py: Python<'_>, ) -> PyResult<Py<PyAny>> {
    config_file_inner().map_err(error).and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn launcher_db_inner() -> Result<serde_json::Value, String> {
    let out = port::launcher_db()?;
    Ok(out)
}

/// `launcher_db`.
#[pyfunction]
#[pyo3(name = "launcher_db", signature = ())]
fn launcher_db(py: Python<'_>, ) -> PyResult<Py<PyAny>> {
    launcher_db_inner().map_err(error).and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn logs_dir_inner() -> Result<serde_json::Value, String> {
    let out = port::logs_dir()?;
    Ok(out)
}

/// `logs_dir`.
#[pyfunction]
#[pyo3(name = "logs_dir", signature = ())]
fn logs_dir(py: Python<'_>, ) -> PyResult<Py<PyAny>> {
    logs_dir_inner().map_err(error).and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn workspace_runtime_dir_inner(workspace_id: String) -> Result<serde_json::Value, String> {
    let out = port::workspace_runtime_dir(workspace_id)?;
    Ok(out)
}

/// `workspace_runtime_dir`.
#[pyfunction]
#[pyo3(name = "workspace_runtime_dir", signature = (workspace_id))]
fn workspace_runtime_dir(py: Python<'_>, workspace_id: String) -> PyResult<Py<PyAny>> {
    workspace_runtime_dir_inner(workspace_id).map_err(error).and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn browser_profile_dir_inner() -> Result<serde_json::Value, String> {
    let out = port::browser_profile_dir()?;
    Ok(out)
}

/// `browser_profile_dir`.
#[pyfunction]
#[pyo3(name = "browser_profile_dir", signature = ())]
fn browser_profile_dir(py: Python<'_>, ) -> PyResult<Py<PyAny>> {
    browser_profile_dir_inner().map_err(error).and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn compose_file_inner(workspace_id: String) -> Result<serde_json::Value, String> {
    let out = port::compose_file(workspace_id)?;
    Ok(out)
}

/// `compose_file`.
#[pyfunction]
#[pyo3(name = "compose_file", signature = (workspace_id))]
fn compose_file(py: Python<'_>, workspace_id: String) -> PyResult<Py<PyAny>> {
    compose_file_inner(workspace_id).map_err(error).and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn updates_dir_inner() -> Result<serde_json::Value, String> {
    let out = port::updates_dir()?;
    Ok(out)
}

/// `updates_dir`.
#[pyfunction]
#[pyo3(name = "updates_dir", signature = ())]
fn updates_dir(py: Python<'_>, ) -> PyResult<Py<PyAny>> {
    updates_dir_inner().map_err(error).and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

#[pymodule]
#[pyo3(name = "_native")]
fn native_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(config_dir, m)?)?;
    m.add_function(wrap_pyfunction!(config_file, m)?)?;
    m.add_function(wrap_pyfunction!(launcher_db, m)?)?;
    m.add_function(wrap_pyfunction!(logs_dir, m)?)?;
    m.add_function(wrap_pyfunction!(workspace_runtime_dir, m)?)?;
    m.add_function(wrap_pyfunction!(browser_profile_dir, m)?)?;
    m.add_function(wrap_pyfunction!(compose_file, m)?)?;
    m.add_function(wrap_pyfunction!(updates_dir, m)?)?;
    Ok(())
}
