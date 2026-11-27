//! Petits composants de présentation partagés entre les pages.
//!
//! Ils sont volontairement generiques sur le message : ils ne savent rien du
//! manager ni des règles métier, ils ne font que composer des widgets iced.

use iced::widget::{column, container, row, text};
use iced::{Color, Element, Length};

use crate::gui::icons::{self, Icon};
use crate::gui::theme;
use crate::workspaces::status::StatusTone;

/// Icône colorée, utilisée dans les boutons et les en-têtes.
pub fn icon<Message: Clone + 'static>(
    name: Icon,
    size: f32,
    color: Color,
) -> Element<'static, Message> {
    icons::icon(name, size)
        .style(theme::icon_style(color))
        .into()
}

/// Pastille de statut, colorée par le ton et non par une règle locale.
pub fn pill<Message: Clone + 'static>(label: String, tone: StatusTone) -> Element<'static, Message> {
    container(text(label).size(12))
        .padding([5, 11])
        .style(theme::pill(tone))
        .into()
}

/// En-tête d'une section : titre fort et sous-titre atténué.
pub fn section_header<'a, Message: Clone + 'a>(
    title: &'a str,
    subtitle: &'a str,
) -> Element<'a, Message> {
    column![
        text(title).size(22),
        text(subtitle).size(13).style(|_theme| iced::widget::text::Style {
            color: Some(theme::MUTED),
        }),
    ]
    .spacing(4)
    .into()
}

/// État vide : une explication, pas un simple texte perdu au milieu de la fenêtre.
pub fn empty_state<'a, Message: Clone + 'a>(title: &'a str, subtitle: &'a str) -> Element<'a, Message> {
    container(
        column![
            icons::icon(Icon::Workspaces, 42.0).style(theme::icon_style(theme::ACCENT)),
            text(title).size(18),
            text(subtitle).size(13).style(|_theme| iced::widget::text::Style {
                color: Some(theme::MUTED),
            }),
        ]
        .spacing(12)
        .align_x(iced::alignment::Horizontal::Center),
    )
    .padding(40)
    .width(Length::Fill)
    .align_x(iced::alignment::Horizontal::Center)
    .into()
}

/// Ligne « libellé discret + valeur » pour les panneaux de réglage.
pub fn labeled<'a, Message: Clone + 'a>(label: &'a str, value: String) -> Element<'a, Message> {
    row![
        text(label)
            .size(13)
            .width(Length::Fixed(110.0))
            .style(|_theme| iced::widget::text::Style {
                color: Some(theme::MUTED),
            }),
        text(value).size(13),
    ]
    .spacing(10)
    .into()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::workspaces::status::StatusTone;

    type TestMessage = ();

    #[test]
    fn test_widgets_can_be_built_without_a_renderer() {
        // Building elements must stay pure: no service, no renderer, no display.
        let _ = icon::<TestMessage>(Icon::Logo, 16.0, theme::ACCENT);
        let _ = pill::<TestMessage>("En marche".to_string(), StatusTone::Active);
        let _ = section_header::<TestMessage>("Titre", "Sous-titre");
        let _ = empty_state::<TestMessage>("Vide", "Rien à afficher");
        let _ = labeled::<TestMessage>("Libellé", "valeur".to_string());
    }
}
