//! Cross-process advisory locking backed by a local lock file.
//! Port de `src/n8n_launcher/core/filelock.py`.

use std::fs::{File, OpenOptions};
use std::os::fd::{FromRawFd, IntoRawFd, RawFd};
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
    fd: Option<RawFd>,
    depth: usize,
}

impl FileLock {
    pub fn new<P: AsRef<Path>>(path: P) -> Self {
        Self {
            path: path.as_ref().to_path_buf().into_boxed_path(),
            fd: None,
            depth: 0,
        }
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn acquire(&mut self, _shared: bool, blocking: bool) -> Result<(), LockError> {
        if self.fd.is_some() {
            self.depth += 1;
            return Ok(());
        }

        if let Some(parent) = self.path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }

        // Ensure file holds at least one byte to lock on Windows.
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

        let fd = OpenOptions::new()
            .read(true)
            .write(true)
            .open(&self.path)
            .map_err(|e| LockError(format!("Impossible d'ouvrir le fichier de verrou : {e}")))?
            .into_raw_fd();

        #[cfg(unix)]
        {
            let result = flock(fd, blocking);
            if result.is_err() {
                let _ = unsafe { File::from_raw_fd(fd) };
                return result.map_err(|e| LockError(e.to_string()));
            }
        }

        #[cfg(windows)]
        {
            let _ = blocking;
            let result = lock_windows(fd);
            if result.is_err() {
                let _ = unsafe { File::from_raw_fd(fd) };
                return result.map_err(|e| LockError(e.to_string()));
            }
        }

        self.fd = Some(fd);
        self.depth = 1;
        Ok(())
    }

    pub fn release(&mut self) {
        if self.fd.is_none() {
            return;
        }
        self.depth = self.depth.saturating_sub(1);
        if self.depth > 0 {
            return;
        }
        let fd = self.fd.take().unwrap();
        #[cfg(unix)]
        let _ = unlock_flock(fd);
        #[cfg(windows)]
        let _ = unlock_windows(fd);
        let _ = unsafe { File::from_raw_fd(fd) };
    }
}

impl Drop for FileLock {
    fn drop(&mut self) {
        self.release();
    }
}

#[cfg(unix)]
fn flock(fd: RawFd, blocking: bool) -> std::io::Result<()> {
    use std::os::unix::io::AsRawFd as _;
    let file = unsafe { File::from_raw_fd(fd) };
    let raw = file.as_raw_fd();
    // Re-wrap so fd is not closed twice
    let _ = file.into_raw_fd();

    let flags = if blocking {
        libc::LOCK_EX
    } else {
        libc::LOCK_EX | libc::LOCK_NB
    };
    let rc = unsafe { libc::flock(raw, flags) };
    if rc == 0 {
        Ok(())
    } else {
        Err(std::io::Error::last_os_error())
    }
}

#[cfg(unix)]
fn unlock_flock(fd: RawFd) -> std::io::Result<()> {
    let rc = unsafe { libc::flock(fd, libc::LOCK_UN) };
    if rc == 0 {
        Ok(())
    } else {
        Err(std::io::Error::last_os_error())
    }
}

#[cfg(windows)]
fn lock_windows(_fd: RawFd) -> std::io::Result<()> {
    // Windows byte-range locking via msvcrt would need `windows-sys`.
    // For now, treat as acquired on non-Unix platforms.
    Ok(())
}

#[cfg(windows)]
fn unlock_windows(_fd: RawFd) -> std::io::Result<()> {
    Ok(())
}

/// Acquire a non-blocking lock proving "the launcher is already running".
pub fn acquire_single_instance_lock(lock_dir: &Path) -> Result<FileLock, LockError> {
    let mut lock = FileLock::new(lock_dir.join("launcher.lock"));
    lock.acquire(false, false)?;
    Ok(lock)
}
