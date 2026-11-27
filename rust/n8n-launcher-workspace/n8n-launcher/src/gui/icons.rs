//! Icônes vectorielles embarquées dans le binaire.
//!
//! `include_bytes!` évite une dépendance au répertoire de travail au lancement :
//! le binaire reste autonome, y compris dans un bundle macOS. Les SVG sont
//! teintés par `theme::icon_style`, donc un seul jeu suffit pour tous les états.

use iced::widget::svg;

/// Jeu d'icônes utilisées par l'interface.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Icon {
    Logo,
    Plus,
    Search,
    Workspaces,
    Journal,
    Settings,
    Play,
    Stop,
    Trash,
    External,
    Database,
    Git,
    Update,
    Check,
    Warning,
    Folder,
    Close,
    Clock,
}

impl Icon {
    /// Octets du SVG correspondant, intégrés à l'exécutable.
    pub fn bytes(self) -> &'static [u8] {
        match self {
            Self::Logo => include_bytes!("../../assets/icons/logo.svg"),
            Self::Plus => include_bytes!("../../assets/icons/plus.svg"),
            Self::Search => include_bytes!("../../assets/icons/search.svg"),
            Self::Workspaces => include_bytes!("../../assets/icons/workspaces.svg"),
            Self::Journal => include_bytes!("../../assets/icons/journal.svg"),
            Self::Settings => include_bytes!("../../assets/icons/settings.svg"),
            Self::Play => include_bytes!("../../assets/icons/play.svg"),
            Self::Stop => include_bytes!("../../assets/icons/stop.svg"),
            Self::Trash => include_bytes!("../../assets/icons/trash.svg"),
            Self::External => include_bytes!("../../assets/icons/external.svg"),
            Self::Database => include_bytes!("../../assets/icons/database.svg"),
            Self::Git => include_bytes!("../../assets/icons/git.svg"),
            Self::Update => include_bytes!("../../assets/icons/update.svg"),
            Self::Check => include_bytes!("../../assets/icons/check.svg"),
            Self::Warning => include_bytes!("../../assets/icons/warning.svg"),
            Self::Folder => include_bytes!("../../assets/icons/folder.svg"),
            Self::Close => include_bytes!("../../assets/icons/close.svg"),
            Self::Clock => include_bytes!("../../assets/icons/clock.svg"),
        }
    }
}

/// Construit le widget SVG d'une icône à la taille demandée.
pub fn icon(name: Icon, size: f32) -> svg::Svg<'static> {
    svg(svg::Handle::from_memory(name.bytes()))
        .width(size)
        .height(size)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_every_icon_is_embedded() {
        let all = [
            Icon::Logo,
            Icon::Plus,
            Icon::Search,
            Icon::Workspaces,
            Icon::Journal,
            Icon::Settings,
            Icon::Play,
            Icon::Stop,
            Icon::Trash,
            Icon::External,
            Icon::Database,
            Icon::Git,
            Icon::Update,
            Icon::Check,
            Icon::Warning,
            Icon::Folder,
            Icon::Close,
            Icon::Clock,
        ];
        for item in all {
            let bytes = item.bytes();
            assert!(bytes.starts_with(b"<svg"), "{item:?} n'est pas un SVG");
        }
    }
}
