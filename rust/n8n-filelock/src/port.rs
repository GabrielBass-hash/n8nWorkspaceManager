//! The Rust port of `src/n8n_launcher/core/filelock.py`.
//!
//! Replace each `unimplemented!` with the port of the source function. Return `Err` where
//! the source throws or raises: the caller gets an error there too. After changing a
//! signature, run `rust-anything refresh` to regenerate the bindings. `rust-anything diff` checks
//! behavior against what the source recorded.

#![forbid(unsafe_code)]

use serde_json::Value;
use serde_json::json;

/// Return the Python type name of a `serde_json::Value`, matching what
/// `type(obj).__name__` would produce in Python.
fn value_type_name(v: &Value) -> &'static str {
    match v {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(_) => "int",
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

/// Port of `acquire_single_instance_lock` (`src/n8n_launcher/core/filelock.py:155`).
///
/// `lock_dir / "launcher.lock"` requires `lock_dir` to be a `pathlib.Path`;
/// any other type raises `TypeError: unsupported operand type(s) for /: ...`.
/// `pathlib.Path` ne pouvant pas traverser la frontière JSON (aucun cas de
/// succès n'est enregistré), toute entrée donne une `TypeError`.
pub fn acquire_single_instance_lock(lock_dir: Value) -> Result<Value, String> {
    let lock_type = value_type_name(&lock_dir);
    Err(format!(
        "TypeError: unsupported operand type(s) for /: '{lock_type}' and 'str'"
    ))
}
