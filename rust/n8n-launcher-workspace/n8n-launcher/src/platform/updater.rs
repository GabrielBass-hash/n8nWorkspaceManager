//! Self-update: fetch the newest GitHub release.
//! Port de `src/n8n_launcher/platform/updater.rs`.

use reqwest::blocking::Client;
use serde_json::Value;
use std::path::{Path, PathBuf};
use std::time::Duration;

/// Dépôt GitHub qui publie les versions du launcher.
pub const REPO: &str = "GabrielBass-hash/n8nWorkspaceManager";

/// Erreur levée quand une version ne peut être récupérée ou téléchargée.
#[derive(Debug)]
pub struct UpdateError(pub String);

impl std::fmt::Display for UpdateError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "UpdateError: {}", self.0)
    }
}

impl std::error::Error for UpdateError {}

/// Triplet SemVer comparable MAJOR.MINOR.PATCH.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct Version {
    pub major: u64,
    pub minor: u64,
    pub patch: u64,
}

/// Artefact téléchargeable attaché à une version GitHub.
#[derive(Debug, Clone)]
pub struct Asset {
    pub name: String,
    pub url: String,
    pub size: u64,
}

/// Charge utile de ``releases/latest``, réduite à ce dont le updater a besoin.
#[derive(Debug, Clone)]
pub struct Release {
    pub tag_name: String,
    pub version: Version,
    pub assets: Vec<Asset>,
}

/// Analyse ``MAJOR[.MINOR[.PATCH]]``, tolérant un préfixe ``v``.
pub fn parse_version(text: &str) -> Result<Version, String> {
    let raw = text.trim();
    let raw = raw.trim_start_matches(['v', 'V']);
    let parts: Vec<&str> = raw.split('.').collect();
    if parts.is_empty() || parts.len() > 3 {
        return Err(format!("invalid version string: {text:?}"));
    }
    let major: u64 = parts[0]
        .parse()
        .map_err(|_| format!("invalid version string: {text:?}"))?;
    let minor: u64 = parts.get(1).and_then(|p| p.parse().ok()).unwrap_or(0);
    let patch: u64 = parts.get(2).and_then(|p| p.parse().ok()).unwrap_or(0);
    Ok(Version { major, minor, patch })
}

/// Retourne la version compilée du launcher en cours d'exécution.
pub fn current_version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

/// Vrai quand la version est strictement plus récente que la version en cours.
pub fn release_is_newer(release: &Release, current: &str) -> bool {
    match parse_version(current) {
        Ok(v) => release.version > v,
        Err(_) => false,
    }
}

/// URL de page publique où consulter et télécharger la dernière version.
pub fn release_page_url(owner_repo: &str) -> String {
    format!("https://github.com/{owner_repo}/releases/latest")
}

/// Récupère la dernière version non-préversion, non-brouillon depuis l'API GitHub.
pub fn fetch_latest_release(
    owner_repo: &str,
    client: Option<Client>,
    timeout_secs: u64,
) -> Result<Release, UpdateError> {
    let url = format!("https://api.github.com/repos/{owner_repo}/releases/latest");
    let http = client.unwrap_or_else(|| {
        Client::builder()
            .timeout(Duration::from_secs(timeout_secs))
            .build()
            .unwrap_or_default()
    });
    let response = http
        .get(&url)
        .header("Accept", "application/vnd.github+json")
        .header("User-Agent", "n8n-launcher")
        .send()
        .map_err(|e| UpdateError(format!("update check failed: {e}")))?;

    if response.status().as_u16() != 200 {
        return Err(UpdateError(format!(
            "update check returned HTTP {}",
            response.status().as_u16()
        )));
    }

    let payload: Value = response
        .json()
        .map_err(|e| UpdateError(format!("update check returned invalid JSON: {e}")))?;

    let tag_name = payload
        .get("tag_name")
        .and_then(|v| v.as_str())
        .ok_or_else(|| UpdateError("update check returned an unreadable release".to_string()))?;
    let version = parse_version(tag_name)
        .map_err(|_| UpdateError("update check returned an unreadable release".to_string()))?;

    let mut assets = Vec::new();
    if let Some(arr) = payload.get("assets").and_then(|v| v.as_array()) {
        for item in arr {
            if !item.is_object() {
                continue;
            }
            let name = match item.get("name").and_then(|v| v.as_str()) {
                Some(n) if !n.is_empty() => n.to_string(),
                _ => continue,
            };
            let url = item
                .get("browser_download_url")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let size = item.get("size").and_then(|v| v.as_u64()).unwrap_or(0);
            assets.push(Asset { name, url, size });
        }
    }

    Ok(Release {
        tag_name: tag_name.to_string(),
        version,
        assets,
    })
}

/// Sélectionne l'actif de release correspondant à la plateforme actuelle, si présent.
pub fn compatible_asset<'a>(release: &'a Release, system: Option<&str>) -> Option<&'a Asset> {
    let system = system.unwrap_or_else(|| platform_system());

    for name in _asset_names(system) {
        if let Some(asset) = release.assets.iter().find(|a| a.name == name) {
            return Some(asset);
        }
    }
    if system != "Darwin" && system != "Windows" {
        for asset in &release.assets {
            if asset.name.ends_with(".AppImage") {
                return Some(asset);
            }
        }
    }
    None
}

fn _asset_names(system: &str) -> Vec<&'static str> {
    match system {
        "Darwin" => vec!["n8n-launcher-macos.dmg"],
        "Windows" => vec!["n8n-launcher-windows.exe"],
        "Linux" => vec!["n8n-launcher-linux"],
        _ => vec![],
    }
}

/// Télécharge un actif de release dans ``dest`` via un fichier temporaire + renommage atomique.
pub fn download_asset(
    url: &str,
    dest: &Path,
    client: Option<Client>,
    expected_size: Option<u64>,
    progress: Option<&dyn Fn(u64)>,
) -> Result<(), UpdateError> {
    let http = client.unwrap_or_else(|| {
        Client::builder()
            .timeout(Duration::from_secs(70))
            .build()
            .unwrap_or_default()
    });
    if let Some(parent) = dest.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| UpdateError(format!("update download failed: {e}")))?;
    }
    let file_name: String = dest
        .file_name()
        .unwrap()
        .to_string_lossy()
        .into_owned();
    let temporary = dest
        .parent()
        .unwrap_or(Path::new(""))
        .join(format!("{file_name}.part"));
    let response = http
        .get(url)
        .send()
        .map_err(|e| UpdateError(format!("update download failed: {e}")))?;
    let status = response.status();
    if !status.is_success() {
        return Err(UpdateError(format!(
            "update download failed: HTTP {}",
            status.as_u16()
        )));
    }
    use std::io::Read;
    let mut handle = std::fs::File::create(&temporary)
        .map_err(|e| UpdateError(format!("update download failed: {e}")))?;
    let mut buf = [0u8; 1 << 16];
    let mut received: u64 = 0;
    let mut reader = response;
    loop {
        let n = reader.read(&mut buf).map_err(|e| {
            let _ = std::fs::remove_file(&temporary);
            UpdateError(format!("update download failed: {e}"))
        })?;
        if n == 0 {
            break;
        }
        std::io::Write::write_all(&mut handle, &buf[..n]).map_err(|e| {
            let _ = std::fs::remove_file(&temporary);
            UpdateError(format!("update download failed: {e}"))
        })?;
        received += n as u64;
        if let Some(cb) = &progress {
            cb(received);
        }
    }
    drop(handle);
    if let Some(expected) = expected_size {
        if received != expected {
            let _ = std::fs::remove_file(&temporary);
            return Err(UpdateError(format!(
                "update download is incomplete: {received}/{expected} bytes"
            )));
        }
    }
    std::fs::rename(&temporary, dest).map_err(|e| UpdateError(format!("update download failed: {e}")))?;
    Ok(())
}

/// Retourne le binaire ou le bundle que l'updater remplacerait.
/// ``None`` quand on exécute depuis l'arbre des sources (pas de binaire à échanger).
pub fn install_target() -> Option<PathBuf> {
    let frozen = std::env::var("N8N_LAUNCHER_FROZEN")
        .map(|v| v == "1" || v.eq_ignore_ascii_case("true"))
        .unwrap_or(!cfg!(debug_assertions));
    std::env::current_exe()
        .ok()
        .map(|p| p.to_path_buf())
        .and_then(|exe| install_target_in(&exe, platform_system(), frozen))
}

/// Variante injectable pour les tests : résout la cible avec un flag gelé explicite.
pub fn install_target_with_frozen(frozen: bool) -> Option<PathBuf> {
    std::env::current_exe()
        .ok()
        .map(|p| p.to_path_buf())
        .and_then(|exe| install_target_in(&exe, platform_system(), frozen))
}

/// Variante injectable pour les tests : résout le binaire cible selon la plateforme.
pub fn install_target_in(executable: &Path, system: &str, frozen: bool) -> Option<PathBuf> {
    if !frozen {
        return None;
    }
    if system == "Darwin" {
        for parent in executable.ancestors() {
            if parent.extension().map(|e| e == "app").unwrap_or(false) {
                return Some(parent.to_path_buf());
            }
        }
        return None;
    }
    Some(executable.to_path_buf())
}

/// Alias injectable pour les tests HTTP (fetch).
pub fn fetch_latest_release_in(
    owner_repo: &str,
    client: Option<Client>,
    timeout_secs: u64,
) -> Result<Release, UpdateError> {
    fetch_latest_release(owner_repo, client, timeout_secs)
}
/// Alias injectable pour les tests HTTP (fetch).
pub fn fetch_latest_release_with_client(
    owner_repo: &str,
    client: Option<Client>,
    timeout_secs: u64,
) -> Result<Release, UpdateError> {
    fetch_latest_release(owner_repo, client, timeout_secs)
}
/// Alias injectable pour les tests HTTP (fetch).
pub fn fetch_latest_release_with_session(
    owner_repo: &str,
    client: Option<Client>,
    timeout_secs: u64,
) -> Result<Release, UpdateError> {
    fetch_latest_release(owner_repo, client, timeout_secs)
}

/// Alias injectable pour les tests HTTP (download).
pub fn download_asset_in(
    url: &str,
    dest: &Path,
    client: Option<Client>,
    expected_size: Option<u64>,
    progress: Option<&dyn Fn(u64)>,
) -> Result<(), UpdateError> {
    download_asset(url, dest, client, expected_size, progress)
}
/// Alias injectable pour les tests HTTP (download).
pub fn download_asset_with_client(
    url: &str,
    dest: &Path,
    client: Option<Client>,
    expected_size: Option<u64>,
    progress: Option<&dyn Fn(u64)>,
) -> Result<(), UpdateError> {
    download_asset(url, dest, client, expected_size, progress)
}
/// Alias injectable pour les tests HTTP (download).
pub fn download_asset_with_session(
    url: &str,
    dest: &Path,
    client: Option<Client>,
    expected_size: Option<u64>,
    progress: Option<&dyn Fn(u64)>,
) -> Result<(), UpdateError> {
    download_asset(url, dest, client, expected_size, progress)
}

/// Alias injectable pour la cible d'installation.
pub fn install_target_with(
    executable: &Path,
    system: &str,
    frozen: bool,
) -> Option<PathBuf> {
    install_target_in(executable, system, frozen)
}
/// Alias injectable pour la cible d'installation.
pub fn install_target_for(
    executable: &Path,
    system: &str,
    frozen: bool,
) -> Option<PathBuf> {
    install_target_in(executable, system, frozen)
}

/// Retourne la plateforme logique du processus en cours.
pub fn platform_system() -> &'static str {
    match std::env::consts::OS {
        "macos" => "Darwin",
        "windows" => "Windows",
        _ => "Linux",
    }
}

/// Le système d'exploitation pour lequel un script d'aide est généré.
fn is_windows(system: &str) -> bool {
    system == "Windows"
}

/// Le système d'exploitation pour lequel un script d'aide est généré.
fn is_macos(system: &str) -> bool {
    system == "Darwin"
}

/// Suppression best-effort des restes d'un échange Windows échoué.
pub fn cleanup_stale() -> bool {
    let target = install_target();
    match target {
        Some(t) => cleanup_stale_in(&t, platform_system()),
        None => false,
    }
}

/// Variante injectable pour les tests : supprime le backup d'un échange Windows échoué.
pub fn cleanup_stale_in(target: &Path, system: &str) -> bool {
    if !is_windows(system) {
        return false;
    }
    let mut old_name = target.to_path_buf().into_os_string();
    old_name.push(".old");
    let old = std::path::PathBuf::from(old_name);
    if std::fs::remove_file(&old).is_err() {
        return false;
    }
    true
}

/// Nom du script d'aide détaché, par plateforme.
pub fn installer_script_name(system: &str) -> &'static str {
    if is_windows(system) {
        "apply_update.cmd"
    } else {
        "apply_update.sh"
    }
}

/// Écrit le script d'aide détaché et retourne son chemin.
pub fn installer_script(
    asset_path: &Path,
    target: &Path,
    script_dir: Option<&Path>,
) -> Result<PathBuf, UpdateError> {
    let system = platform_system();
    let pid = std::process::id();
    let directory = script_dir.map(|p| p.to_path_buf()).unwrap_or_else(|| {
        crate::core::paths::updates_dir().map(|d| d.join("updates")).unwrap_or_else(|_| std::path::PathBuf::from("~/.local/share/n8n-launcher/updates"))
    });
    let _ = std::fs::create_dir_all(&directory);
    let script = directory.join(installer_script_name(system));
    let content = installer_script_text(asset_path, target, system, pid);
    std::fs::write(&script, content).map_err(|e| UpdateError(format!("failed to write installer script: {e}")))?;
    #[cfg(unix)]
    if !is_windows(system) {
        use std::os::unix::fs::PermissionsExt;
        let mut perms = std::fs::metadata(&script).map(|m| m.permissions()).map_err(|e| UpdateError(format!("failed to write installer script: {e}")))?;
        perms.set_mode(0o755);
        std::fs::set_permissions(&script, perms).map_err(|e| UpdateError(format!("failed to chmod installer script: {e}")))?;
    }
    Ok(script)
}

/// Texte du script d'aide détaché pour la plateforme donnée.
pub fn installer_script_text(asset_path: &Path, target: &Path, system: &str, pid: u32) -> String {
    let asset = asset_path.to_string_lossy().to_string();
    let tgt = target.to_string_lossy().to_string();
    if is_windows(system) {
        windows_source(&asset, &tgt, pid)
    } else if is_macos(system) {
        macos_source(&asset, &tgt, pid)
    } else {
        linux_source(&asset, &tgt, pid)
    }
}

/// Construit la commande qui lance le script d'aide détaché.
pub fn installer_command(script: &Path, system: &str) -> Vec<String> {
    if is_windows(system) {
        vec!["cmd".to_string(), "/c".to_string(), script.to_string_lossy().to_string()]
    } else {
        vec!["/bin/sh".to_string(), script.to_string_lossy().to_string()]
    }
}

/// Lance l'aide entièrement détachée pour qu'elle survive au processus launcher.
pub fn spawn_installer(script: &Path, system: &str) -> Result<(), UpdateError> {
    let mut cmd = std::process::Command::new(if is_windows(system) { "cmd" } else { "/bin/sh" });
    if is_windows(system) {
        cmd.args(["/c", &script.to_string_lossy()]);
        #[cfg(target_os = "windows")]
        {
            use std::os::windows::process::CommandExt;
            cmd.creation_flags(std::os::windows::process::CreationFlags::CREATE_NEW_PROCESS_GROUP);
        }
    } else {
        cmd.args(["/bin/sh", &script.to_string_lossy()]);
    }
    cmd.stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        cmd.process_group(0);
    }
    cmd.spawn().map(|_| ()).map_err(|e| UpdateError(format!("spawn installer failed: {e}")))
}

fn macos_source(asset: &str, target: &str, pid: u32) -> String {
    let app = sh_quote_str(target);
    let dmg = sh_quote_str(asset);
    let mount_path = crate::core::paths::updates_dir()
        .map(|d| d.join("mount"))
        .map(|p| p.to_string_lossy().to_string())
        .unwrap_or_else(|_| "/tmp/n8n-launcher/updates/mount".to_string());
    let mount = sh_quote_str(&mount_path);
    let lines: Vec<String> = vec![
        "#!/bin/sh".to_string(),
        "# n8n Launcher auto-update helper (macOS).".to_string(),
        format!("APP={app}"),
        format!("DMG={dmg}"),
        format!("MNT={mount}"),
        format!("while kill -0 {pid} 2>/dev/null; do sleep 1; done"),
        "mkdir -p \"$MNT\"".to_string(),
        "hdiutil attach -nobrowse -noautoopen -mountpoint \"$MNT\" \"$DMG\" >/dev/null 2>&1".to_string(),
        "SRC=$(find \"$MNT\" -maxdepth 1 -name '*.app' -print -quit)".to_string(),
        "if [ -n \"$SRC\" ]; then".to_string(),
        "  rm -rf \"$APP\"".to_string(),
        "  ditto \"$SRC\" \"$APP\"".to_string(),
        "  xattr -dr com.apple.quarantine \"$APP\" 2>/dev/null || true".to_string(),
        "  codesign --verify --deep --strict \"$APP\"".to_string(),
        "fi".to_string(),
        "hdiutil detach \"$MNT\" >/dev/null 2>&1 || true".to_string(),
        "open \"$APP\"".to_string(),
        String::new(),
    ];
    lines.join("\n")
}

fn linux_source(asset: &str, target: &str, pid: u32) -> String {
    let old = sh_quote_str(target);
    let new = sh_quote_str(asset);
    let lines: Vec<String> = vec![
        "#!/bin/sh".to_string(),
        "# n8n Launcher auto-update helper (Linux).".to_string(),
        format!("OLD={old}"),
        format!("NEW={new}"),
        format!("while kill -0 {pid} 2>/dev/null; do sleep 1; done"),
        "TMP=\"$OLD.tmp\"".to_string(),
        "cp \"$NEW\" \"$TMP\"".to_string(),
        "chmod +x \"$TMP\"".to_string(),
        "mv -f \"$TMP\" \"$OLD\"".to_string(),
        format!("\"$OLD\" &"),
        String::new(),
    ];
    lines.join("\n")
}

fn windows_source(asset: &str, target: &str, pid: u32) -> String {
    let old = target.to_string();
    let new = asset.to_string();
    let lines: Vec<String> = vec![
        "@echo off".to_string(),
        "rem n8n Launcher auto-update helper (Windows).".to_string(),
        format!("set \"PID={pid}\""),
        format!("set \"OLD={old}\""),
        format!("set \"NEW={new}\""),
        "set \"OLD_BAK=%OLD%.old\"".to_string(),
        String::new(),
        ":wait".to_string(),
        "tasklist /FI \"PID eq %PID%\" 2>nul | findstr /C:\"%PID%\" >nul".to_string(),
        "if %errorlevel%==0 (".to_string(),
        "  timeout /t 1 /nobreak >nul".to_string(),
        "  goto :wait".to_string(),
        ")".to_string(),
        String::new(),
        "move /Y \"%OLD%\" \"%OLD_BAK%\" >nul 2>nul".to_string(),
        "if not exist \"%OLD_BAK%\" (".to_string(),
        "  start \"\" \"%OLD%\"".to_string(),
        "  exit /b 1".to_string(),
        ")".to_string(),
        "copy /Y \"%NEW%\" \"%OLD%\" >nul 2>nul".to_string(),
        "if not exist \"%OLD%\" (".to_string(),
        "  move /Y \"%OLD_BAK%\" \"%OLD%\"".to_string(),
        "  start \"\" \"%OLD%\"".to_string(),
        "  exit /b 1".to_string(),
        ")".to_string(),
        "del /Q \"%OLD_BAK%\" >nul 2>nul".to_string(),
        "start \"\" \"%OLD%\"".to_string(),
        String::new(),
    ];
    lines.join("\r\n")
}

fn sh_quote_str(value: &str) -> String {
    format!("'{}'", value.replace('\'', "'\\''"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_version() {
        assert_eq!(
            parse_version("4.0.2").unwrap(),
            Version { major: 4, minor: 0, patch: 2 }
        );
        assert_eq!(
            parse_version("v4.0.2").unwrap(),
            Version { major: 4, minor: 0, patch: 2 }
        );
        assert_eq!(
            parse_version("1").unwrap(),
            Version { major: 1, minor: 0, patch: 0 }
        );
        assert_eq!(
            parse_version("1.2").unwrap(),
            Version { major: 1, minor: 2, patch: 0 }
        );
        assert!(parse_version("invalid").is_err());
        assert!(parse_version("1.2.3.4").is_err());
    }

    #[test]
    fn test_release_is_newer() {
        let release = Release {
            tag_name: "v2.0.0".to_string(),
            version: Version { major: 2, minor: 0, patch: 0 },
            assets: vec![],
        };
        assert!(release_is_newer(&release, "1.0.0"));
        assert!(!release_is_newer(&release, "2.0.0"));
        assert!(!release_is_newer(&release, "3.0.0"));
    }

    #[test]
    fn test_install_target_in_macos_finds_app() {
        let dir = tempfile::tempdir().unwrap();
        let app = dir.path().join("n8n-launcher.app");
        let exe = app.join("Contents").join("MacOS").join("n8n-launcher");
        assert_eq!(install_target_in(&exe, "Darwin", true), Some(app.clone()));
    }

    #[test]
    fn test_install_target_in_not_frozen_returns_none() {
        let exe = std::path::Path::new("/tmp/n8n-launcher");
        assert!(install_target_in(exe, "Linux", false).is_none());
        assert_eq!(install_target_in(exe, "Linux", true), Some(exe.to_path_buf()));
    }

    #[test]
    fn test_installer_script_linux_writes_executable() {
        let dir = tempfile::tempdir().unwrap();
        let asset = dir.path().join("n8n-launcher.new");
        let target = dir.path().join("n8n-launcher");
        let script = installer_script(&asset, &target, Some(dir.path())).unwrap();
        assert_eq!(
            script.file_name().unwrap().to_string_lossy().to_string(),
            "apply_update.sh"
        );
        let text = std::fs::read_to_string(&script).unwrap();
        assert!(text.contains("kill -0"));
        assert!(text.contains("mv -f"));
        assert!(text.contains("\"$OLD\" &"));
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(&script).unwrap().permissions().mode();
            assert_eq!(mode & 0o111 != 0, true);
        }
    }

    #[test]
    fn test_installer_script_text_windows_uses_crlf() {
        let text = installer_script_text(
            std::path::Path::new("C:/a/n8n-launcher.new"),
            std::path::Path::new("C:/a/n8n-launcher.exe"),
            "Windows",
            4242,
        );
        assert!(text.contains("rem n8n Launcher auto-update helper"));
        assert!(text.contains("set \"PID=4242\""));
        assert!(text.contains("move /Y"));
    }

    #[test]
    fn test_installer_script_text_macos_swaps_bundle() {
        let text = installer_script_text(
            std::path::Path::new("/tmp/n8n-launcher-macos.dmg"),
            std::path::Path::new("/tmp/n8n-launcher.app"),
            "Darwin",
            7,
        );
        assert!(text.contains("hdiutil attach"));
        assert!(text.contains("ditto"));
        assert!(text.contains("codesign --verify"));
    }

    #[test]
    fn test_installer_command_uses_shell_per_platform() {
        let script = std::path::PathBuf::from("/tmp/apply_update.sh");
        assert_eq!(
            installer_command(&script, "Darwin"),
            vec!["/bin/sh".to_string(), "/tmp/apply_update.sh".to_string()]
        );
        assert_eq!(
            installer_command(&script, "Windows"),
            vec!["cmd".to_string(), "/c".to_string(), "/tmp/apply_update.sh".to_string()]
        );
    }

    #[test]
    fn test_cleanup_stale_in_windows_removes_backup() {
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("n8n-launcher.exe");
        std::fs::write(&target, b"old").unwrap();
        let mut old_name = target.clone().into_os_string();
        old_name.push(".old");
        let old = std::path::PathBuf::from(old_name);
        std::fs::write(&old, b"backup").unwrap();
        assert!(cleanup_stale_in(&target, "Windows"));
        assert!(!old.exists());
        assert!(!cleanup_stale_in(&target, "Linux"));
    }
}
