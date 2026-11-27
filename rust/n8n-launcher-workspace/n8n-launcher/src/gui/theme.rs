//! Palette et styles de l'interface.
//!
//! Les couleurs vivent ici (et nulle part dans la vue) pour qu'une carte, une
//! pastille d'état ou un bouton partagent exactement les mêmes tokens. Les
//! functions de style sont des functions pures : iced les appelle à chaque
//! rendu, elles ne doivent donc jamais lire d'état global.

use iced::theme::Palette;
use iced::widget::{button, container, svg, text_input};
use iced::{Background, Color, Gradient, Shadow, Theme};

pub use iced::color;

use crate::workspaces::status::StatusTone;

/// Fond général de la fenêtre.
pub const BG: Color = color!(0x0B1020);
/// Fond de la barre latérale.
pub const SIDEBAR: Color = color!(0x0E1424);
/// Surface des cartes et panneaux.
pub const SURFACE: Color = color!(0x141B2E);
/// Surface au survol.
pub const SURFACE_HOVER: Color = color!(0x1B2440);
/// Bordure subtile des surfaces.
pub const BORDER: Color = color!(0x24304D);
/// Texte principal.
pub const TEXT: Color = color!(0xE8ECF8);
/// Texte secondaire.
pub const MUTED: Color = color!(0x93A0BD);
/// Première couleur de l'accent.
pub const ACCENT: Color = color!(0x7C6CF6);
/// Seconde couleur de l'accent (dégradé).
pub const ACCENT_2: Color = color!(0xA855F7);
/// État actif / succès.
pub const SUCCESS: Color = color!(0x34D399);
/// État transitoire.
pub const WARNING: Color = color!(0xFBBF24);
/// État d'erreur.
pub const DANGER: Color = color!(0xF87171);
/// État neutre.
pub const NEUTRAL: Color = color!(0x64748B);

/// Construit le thème applicatif à partir de notre palette.
///
/// Un thème nommé évite que les styles par défaut d'iced (gris neutre) ne
/// réapparaissent sur un widget que nous n'aurions pas stylé explicitement.
pub fn theme() -> Theme {
    Theme::custom(
        "n8n-launcher premium".to_string(),
        Palette {
            background: BG,
            text: TEXT,
            primary: ACCENT,
            success: SUCCESS,
            danger: DANGER,
        },
    )
}

/// Couleur d'un ton d'état ; la vue ne décide jamais de cette correspondance.
pub fn tone_color(tone: StatusTone) -> Color {
    match tone {
        StatusTone::Neutral => NEUTRAL,
        StatusTone::Active => SUCCESS,
        StatusTone::Pending => WARNING,
        StatusTone::Error => DANGER,
    }
}

/// Dégradé d'accent du bouton principal et de la barre de marque.
pub fn accent_background() -> Background {
    let linear = iced::gradient::Linear::new(0.35_f32)
        .add_stop(0.0, ACCENT)
        .add_stop(1.0, ACCENT_2);
    Background::Gradient(Gradient::Linear(linear))
}

/// Fond général : un dégradé très désaturé donne de la profondeur sans nuire à la lisibilité.
pub fn app_background(_theme: &Theme) -> container::Style {
    let linear = iced::gradient::Linear::new(1.57_f32)
        .add_stop(0.0, color!(0x0D1426))
        .add_stop(1.0, BG);
    container::Style {
        background: Some(Background::Gradient(Gradient::Linear(linear))),
        text_color: Some(TEXT),
        ..Default::default()
    }
}

/// Fond de la barre latérale.
pub fn sidebar(_theme: &Theme) -> container::Style {
    container::Style {
        background: Some(Background::Color(SIDEBAR)),
        border: iced::Border {
            color: BORDER,
            width: 1.0,
            radius: 0.0.into(),
        },
        ..Default::default()
    }
}

/// Surface simple (en-tête, pied de page, panneau de réglage).
pub fn surface(_theme: &Theme) -> container::Style {
    container::Style {
        background: Some(Background::Color(SURFACE)),
        border: iced::Border {
            color: BORDER,
            width: 1.0,
            radius: 12.0.into(),
        },
        ..Default::default()
    }
}

/// Carte workspace : bordure visible et ombre douce.
pub fn card(_theme: &Theme) -> container::Style {
    container::Style {
        background: Some(Background::Color(SURFACE)),
        border: iced::Border {
            color: BORDER,
            width: 1.0,
            radius: 16.0.into(),
        },
        shadow: Shadow {
            color: color!(0x000000, 0.35),
            offset: iced::Vector::new(0.0, 6.0),
            blur_radius: 18.0,
        },
        ..Default::default()
    }
}

/// Pastille d'état : le ton pilote la couleur, jamais le texte du libellé.
pub fn pill(tone: StatusTone) -> impl Fn(&Theme) -> container::Style {
    let base = tone_color(tone);
    move |_theme| container::Style {
        background: Some(Background::Color(Color { a: 0.16, ..base })),
        border: iced::Border {
            color: Color { a: 0.45, ..base },
            width: 1.0,
            radius: 999.0.into(),
        },
        text_color: Some(base),
        ..Default::default()
    }
}

/// Bouton principal (accent en dégradé).
pub fn primary_button(_theme: &Theme, status: button::Status) -> button::Style {
    let (alpha, offset) = match status {
        button::Status::Hovered => (0.92, -1.0),
        button::Status::Pressed => (0.82, 0.0),
        button::Status::Disabled => (0.35, 0.0),
        button::Status::Active => (1.0, 0.0),
    };
    button::Style {
        background: Some(accent_background().scale_alpha(alpha)),
        text_color: Color::WHITE,
        border: iced::Border {
            color: Color { a: 0.0, ..ACCENT },
            width: 0.0,
            radius: 10.0.into(),
        },
        shadow: Shadow {
            color: color!(0x7C6CF6, 0.28),
            offset: iced::Vector::new(0.0, offset),
            blur_radius: 14.0,
        },
    }
}

/// Bouton secondaire, discret sur fond sombre.
pub fn ghost_button(_theme: &Theme, status: button::Status) -> button::Style {
    let background = match status {
        button::Status::Hovered => Background::Color(SURFACE_HOVER),
        button::Status::Pressed => Background::Color(BORDER),
        button::Status::Disabled => Background::Color(Color { a: 0.4, ..SURFACE }),
        button::Status::Active => Background::Color(Color { a: 0.6, ..SURFACE }),
    };
    button::Style {
        background: Some(background),
        text_color: if matches!(status, button::Status::Disabled) {
            MUTED
        } else {
            TEXT
        },
        border: iced::Border {
            color: BORDER,
            width: 1.0,
            radius: 9.0.into(),
        },
        ..Default::default()
    }
}

/// Bouton de suppression : couleur dangereuse explicite.
pub fn danger_button(_theme: &Theme, status: button::Status) -> button::Style {
    let base = match status {
        button::Status::Hovered => color!(0xF87171, 0.24),
        button::Status::Pressed => color!(0xF87171, 0.34),
        button::Status::Disabled => color!(0xF87171, 0.08),
        button::Status::Active => color!(0xF87171, 0.14),
    };
    button::Style {
        background: Some(Background::Color(base)),
        text_color: DANGER,
        border: iced::Border {
            color: color!(0xF87171, 0.4),
            width: 1.0,
            radius: 9.0.into(),
        },
        ..Default::default()
    }
}

/// Entrée de texte cohérente avec les surfaces.
pub fn input_style(_theme: &Theme, status: text_input::Status) -> text_input::Style {
    let focused = matches!(status, text_input::Status::Focused);
    text_input::Style {
        background: Background::Color(color!(0x0E1424)),
        border: iced::Border {
            color: if focused { ACCENT } else { BORDER },
            width: if focused { 2.0 } else { 1.0 },
            radius: 9.0.into(),
        },
        icon: MUTED,
        placeholder: color!(0x6B7796),
        value: TEXT,
        selection: color!(0x7C6CF6, 0.4),
    }
}

/// Colore une icône vectorielle ; les SVG utilisent des pixels opaques.
pub fn icon_style(color: Color) -> impl Fn(&Theme, svg::Status) -> svg::Style {
    move |_theme, _status| svg::Style { color: Some(color) }
}

/// Fond semi-transparent des modales.
pub fn scrim(_theme: &Theme) -> container::Style {
    container::Style {
        background: Some(Background::Color(color!(0x05080F, 0.78))),
        ..Default::default()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_tone_colors_are_distinct() {
        assert_eq!(tone_color(StatusTone::Neutral), NEUTRAL);
        assert_eq!(tone_color(StatusTone::Active), SUCCESS);
        assert_eq!(tone_color(StatusTone::Pending), WARNING);
        assert_eq!(tone_color(StatusTone::Error), DANGER);
    }

    #[test]
    fn test_theme_uses_our_palette() {
        let theme = theme();
        assert_eq!(theme.palette().background, BG);
        assert_eq!(theme.palette().primary, ACCENT);
    }

    #[test]
    fn test_accent_is_a_gradient() {
        assert!(matches!(accent_background(), Background::Gradient(_)));
    }
}
