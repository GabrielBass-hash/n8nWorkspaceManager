//! Cross-process advisory locking backed by a local lock file.
//! Port de `src/n8n_launcher/core/filelock.py`.

use std::fs::{File, OpenOptions};
use std::path::Path;

/// Raised when a non-blocking lock cannot be acquired.
#[derive(Debug, thiserror::Error)]
#[error("le verrou {0} est déjà tenu")]
pub struct LockError(pub String);

/// Advisory inter-process lock backed by a file on disk.
///
/// Re-entrant within a single process: re-acquiring increments a depth counter.
/// Different instances pointing at the same file are independent, so a second
/// process genuinely competes for the OS lock.
pub struct FileLock {
    path: Box<Path>,
    file: Option<File>,
    depth: usize,
}

impl FileLock {
    pub fn new<P: AsRef<Path>>(path: P) -> Self {
        Self {
            path: path.as_ref().to_path_buf().into_boxed_path(),
            file: None,
            depth: 0,
        }
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn acquire(&mut self, shared: bool, blocking: bool) -> Result<(), LockError> {
        if self.file.is_some() {
            self.depth += 1;
            return Ok(());
        }

        if let Some(parent) = self.path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }

        // Ensure the file holds at least one byte to lock on Windows.
        {
            let mut f = OpenOptions::new()
                .read(true)
                .write(true)
                .create(true)
                .truncate(false)
                .open(&self.path)
                .map_err(|e| LockError(format!("Impossible d'ouvrir le fichier de verrou : {e}")))?;
            if f.metadata().map(|m| m.len() == 0).unwrap_or(true) {
                use std::io::Write;
                let _ = f.write_all(b"L");
            }
        }

        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .open(&self.path)
            .map_err(|e| LockError(format!("Impossible d'ouvrir le fichier de verrou : {e}")))?;

        let lock_result = match (blocking, shared) {
            (true, true) => file.lock_shared().map_err(|e| LockError(e.to_string())),
            (true, false) => file.lock().map_err(|e| LockError(e.to_string())),
            (false, true) => file.try_lock_shared().map_err(|e| LockError(e.to_string())),
            (false, false) => file.try_lock().map_err(|e| LockError(e.to_string())),
        };
        lock_result?;

        self.file = Some(file);
        self.depth = 1;
        Ok(())
    }

    pub fn release(&mut self) {
        if self.file.is_none() {
            return;
        }
        self.depth = self.depth.saturating_sub(1);
        if self.depth > 0 {
            return;
        }
        if let Some(file) = self.file.take() {
            let _ = file.unlock();
        }
    }
}

impl Drop for FileLock {
    fn drop(&mut self) {
        self.release();
    }
}

/// Acquire a non-blocking lock proving "the launcher is already running".
pub fn acquire_single_instance_lock(lock_dir: &Path) -> Result<FileLock, LockError> {
    let mut lock = FileLock::new(lock_dir.join("launcher.lock"));
    lock.acquire(false, false)?;
    Ok(lock)
}
