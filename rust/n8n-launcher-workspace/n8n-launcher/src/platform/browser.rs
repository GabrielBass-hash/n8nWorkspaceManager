//! Ouverture d'une URL dans le navigateur par défaut du système.
//!
//! Un simple lancement de processus suffit : cela évite d'ajouter une crates
//! supplémentaire pour une seule action, et conserve le comportement natif de
//! chaque plateforme (onglet existant réutilisé par Chrome/Safari/Firefox).

use std::process::Command;

/// Ouvre *url* dans le navigateur par défaut.
pub fn open_url(url: &str) -> Result<(), String> {
    let (program, args) = open_command(url, std::env::consts::OS);
    let status = Command::new(program)
        .args(args)
        .status()
        .map_err(|e| format!("Impossible d'ouvrir le navigateur ({program}) : {e}"))?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("Le navigateur a refusé d'ouvrir {url}"))
    }
}

/// Commande à lancer pour *system*, sans rien exécuter.
///
/// Isolée de `open_url` pour pouvoir être testée sur n'importe quel hôte.
pub fn open_command<'a>(url: &'a str, system: &str) -> (&'static str, Vec<&'a str>) {
    match system {
        "windows" => ("cmd", vec!["/C", "start", "", url]),
        "macos" => ("open", vec![url]),
        _ => ("xdg-open", vec![url]),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_open_command_uses_native_launcher() {
        let url = "http://localhost:5678";
        assert_eq!(open_command(url, "linux"), ("xdg-open", vec![url]));
        assert_eq!(open_command(url, "macos"), ("open", vec![url]));
        let (program, args) = open_command(url, "windows");
        assert_eq!(program, "cmd");
        assert!(args.contains(&url));
    }
}
