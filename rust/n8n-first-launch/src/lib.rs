//! Python bindings for `src/n8n_launcher/core/first_launch.py`: generated from the signatures in `port.rs` by rust-anything.
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

fn validate_password_inner(password: String) -> Result<(), String> {
    port::validate_password(password)?;
    Ok(())
}

/// `validate_password`.
#[pyfunction]
#[pyo3(name = "validate_password", signature = (password))]
fn validate_password(password: String) -> PyResult<()> {
    validate_password_inner(password).map_err(error)
}

fn build_initial_config_inner(email: String, password: String, work_dir: Bound<'_, PyAny>) -> Result<serde_json::Value, String> {
    let work_dir: serde_json::Value = crate::pyconv::FromPy::from_py(&work_dir)?;
    let out = port::build_initial_config(email, password, work_dir)?;
    Ok(out)
}

/// `build_initial_config`.
#[pyfunction]
#[pyo3(name = "build_initial_config", signature = (email, password, work_dir))]
fn build_initial_config(py: Python<'_>, email: String, password: String, work_dir: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    build_initial_config_inner(email, password, work_dir).map_err(error).and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

fn run_first_launch_inner(store: Bound<'_, PyAny>, docker: Bound<'_, PyAny>, email: String, password: String, work_dir: Bound<'_, PyAny>) -> Result<serde_json::Value, String> {
    let store: serde_json::Value = crate::pyconv::FromPy::from_py(&store)?;
    let docker: serde_json::Value = crate::pyconv::FromPy::from_py(&docker)?;
    let work_dir: serde_json::Value = crate::pyconv::FromPy::from_py(&work_dir)?;
    let out = port::run_first_launch(store, docker, email, password, work_dir)?;
    Ok(out)
}

/// `run_first_launch`.
#[pyfunction]
#[pyo3(name = "run_first_launch", signature = (store, docker, *, email, password, work_dir))]
fn run_first_launch(py: Python<'_>, store: Bound<'_, PyAny>, docker: Bound<'_, PyAny>, email: String, password: String, work_dir: Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    run_first_launch_inner(store, docker, email, password, work_dir).map_err(error).and_then(|v| crate::pyconv::ToPy::to_py(&v, py))
}

#[pymodule]
#[pyo3(name = "_native")]
fn native_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(validate_password, m)?)?;
    m.add_function(wrap_pyfunction!(build_initial_config, m)?)?;
    m.add_function(wrap_pyfunction!(run_first_launch, m)?)?;
    Ok(())
}
