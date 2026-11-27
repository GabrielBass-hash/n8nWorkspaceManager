//! SSH primitives for remote deployment and observability.
//! Port de `src/n8n_launcher/remote/ssh.rs`.

use crate::core::models::ServerConfig;
use std::io::Write;
use std::process::{Command, Stdio};
use std::time::Duration;

pub const MAX_REMOTE_OUTPUT_BYTES: usize = 4 * 1024 * 1024;

/// Exécute une commande sur un hôte distant via SSH.
pub fn ssh_run(cfg: &ServerConfig, command: &str) -> Result<String, String> {
    ssh_run_opts(cfg, command, None, 30, true)
}

/// Variante avec stdin, timeout et contrôle du code de retour.
pub fn ssh_run_opts(
    cfg: &ServerConfig,
    command: &str,
    stdin: Option<&str>,
    timeout_secs: u64,
    check: bool,
) -> Result<String, String> {
    let mut argv: Vec<String> = vec![
        "ssh".to_string(),
        "-p".to_string(),
        cfg.ssh_port.to_string(),
        "-o".to_string(),
        "BatchMode=yes".to_string(),
        "-o".to_string(),
        "StrictHostKeyChecking=accept-new".to_string(),
    ];
    if let Some(key) = &cfg.key_path {
        argv.push("-o".to_string());
        argv.push("IdentitiesOnly=yes".to_string());
        argv.push("-i".to_string());
        argv.push(key.clone());
    }
    argv.push(format!("{}@{}", cfg.user, cfg.host));
    argv.push(command.to_string());

    let mut cmd = Command::new(&argv[0]);
    for a in &argv[1..] {
        cmd.arg(a);
    }
    cmd.stdout(Stdio::piped());
    cmd.stderr(Stdio::piped());
    if stdin.is_some() {
        cmd.stdin(Stdio::piped());
    } else {
        cmd.stdin(Stdio::null());
    }

    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => return Err(format!("ssh n'a pas pu être exécuté : {e}")),
    };

    if let Some(input) = stdin {
        if let Some(mut s) = child.stdin.take() {
            let _ = s.write_all(input.as_bytes());
            let _ = s.flush();
            drop(s);
        }
    }

    // Wait with timeout
    let start = std::time::Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) => {
                if start.elapsed() >= Duration::from_secs(timeout_secs) {
                    let _ = child.kill();
                    return Err(format!(
                        "Connexion SSH à {} expirée (>{}s)",
                        cfg.host, timeout_secs
                    ));
                }
                std::thread::sleep(Duration::from_millis(50));
            }
            Err(e) => return Err(format!("ssh erreur d'attente : {e}")),
        }
    }

    let output = child.wait_with_output().map_err(|e| e.to_string())?;
    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    if check && !output.status.success() {
        let detail = if !stderr.trim().is_empty() {
            stderr.trim().to_string()
        } else if !stdout.trim().is_empty() {
            stdout.trim().to_string()
        } else {
            format!("code {}", output.status.code().unwrap_or(-1))
        };
        return Err(format!("Commande SSH sur {} échouée : {}", cfg.host, detail));
    }
    Ok(stdout)
}

/// Crée un répertoire distant.
pub fn mkdir_remote(cfg: &ServerConfig, path: &str, parents: bool) -> Result<(), String> {
    validate_remote_path(path)?;
    let args = if parents { "-p" } else { "" };
    ssh_run(cfg, &format!("mkdir {} {}", args, sh_quote(path))).map(|_| ())
}

/// Écrit un fichier sur un serveur distant de manière atomique.
pub fn write_remote_file(cfg: &ServerConfig, path: &str, content: &str) -> Result<(), String> {
    validate_remote_path(path)?;
    let quoted = sh_quote(path);
    ssh_run_opts(
        cfg,
        &format!("cat > {quoted}.tmp && mv -f {quoted}.tmp {quoted}"),
        Some(content),
        60,
        true,
    )
    .map(|_| ())
}

/// Modifie les permissions d'un fichier distant.
pub fn chmod_remote(cfg: &ServerConfig, path: &str, mode: &str) -> Result<(), String> {
    validate_remote_path(path)?;
    if !valid_mode(mode) {
        return Err("Mode de fichier distant invalide".to_string());
    }
    let quoted = sh_quote(path);
    ssh_run(cfg, &format!("chmod {} {}", mode, quoted)).map(|_| ())
}

/// Sonde les prérequis serveur.
pub fn test_connection(cfg: &ServerConfig) -> Result<String, String> {
    let probe = [
        "command -v docker >/dev/null 2>&1 || { echo 'docker introuvable sur le serveur'; exit 1; }",
        "docker compose version",
        "command -v git >/dev/null 2>&1 || { echo 'git introuvable sur le serveur'; exit 1; }",
        "git --version",
        "command -v python3 >/dev/null 2>&1 || { echo 'python3 introuvable sur le serveur'; exit 1; }",
        "python3 --version",
    ]
    .join("\n");
    ssh_run(cfg, &probe)
}

fn validate_remote_path(path: &str) -> Result<(), String> {
    if path.is_empty() || path.contains('\0') {
        return Err("Chemin distant invalide".to_string());
    }
    Ok(())
}

pub fn sh_quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "'\\''"))
}

fn valid_mode(mode: &str) -> bool {
    let re_octal = mode.chars().all(|c| c.is_ascii_digit()) && (mode.len() == 3 || mode.len() == 4);
    let re_symbolic = (mode.starts_with('+') || mode.starts_with('-') || mode.starts_with('='))
        && mode[1..].chars().all(|c| c.is_ascii_alphabetic());
    re_octal || re_symbolic
}

/// Copie un fichier local vers un hôte distant via SCP (stub conservé).
pub fn scp(_host: &str, _local: &std::path::Path, _remote: &str) -> Result<(), String> {
    Err("Not implemented".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::models::ServerConfig;

    fn cfg() -> ServerConfig {
        ServerConfig {
            enabled: true,
            host: "localhost".to_string(),
            ssh_port: 22,
            user: "user".to_string(),
            key_path: None,
            base_dir: "base".to_string(),
            n8n_port: 5678,
        }
    }

    #[test]
    fn test_sh_quote() {
        assert_eq!(sh_quote("a'b"), "'a'\\''b'");
    }

    #[test]
    fn test_valid_mode() {
        assert!(valid_mode("755"));
        assert!(valid_mode("+x"));
        assert!(!valid_mode("abc"));
    }

    #[test]
    fn test_ssh_run_unreachable() {
        // No ssh server; should error rather than panic
        let c = cfg();
        let res = ssh_run(&c, "echo hi");
        assert!(res.is_err());
    }
}
