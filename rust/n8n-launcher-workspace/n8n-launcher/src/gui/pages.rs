//! Pages de l'interface : barre latérale, en-tête, cartes, journal, réglages.
//!
//! Cette couche ne prend aucune décision métier : elle affiche les libellés,
//! tons et actions renvoyés par `workspaces::status` et traduit un `CardAction`
//! en message iced via `app::action_message`.

use iced::widget::{Space, button, checkbox, column, container, row, scrollable, stack, text, text_input};
use iced::{Element, Length, Theme};

use crate::app::{action_message, status_line, LauncherState, Message, Nav};
use crate::core::models::{DbMode, Workspace, WorkspaceState};
use crate::gui::icons::Icon;
use crate::gui::{theme, widgets};
use crate::platform::updater;
use crate::workspaces::status::{self, WorkspaceAction};

/// Construit la fenêtre complète (barre latérale + zone principale).
pub fn view(state: &LauncherState) -> Element<'_, Message> {
    let main = column![header(state), content(state), footer(state)]
        .spacing(0)
        .width(Length::Fill);
    let shell = row![sidebar(state), main].spacing(0);
    container(shell)
        .width(Length::Fill)
        .height(Length::Fill)
        .style(theme::app_background)
        .into()
}

fn sidebar(state: &LauncherState) -> Element<'_, Message> {
    let brand = row![
        widgets::icon::<Message>(Icon::Logo, 28.0, theme::ACCENT),
        column![
            text("n8n-launcher").size(17),
            text("Workspace manager").size(11).style(muted_style),
        ]
        .spacing(0),
    ]
    .spacing(10)
    .align_y(iced::alignment::Vertical::Center);

    let nav_items = [
        (Icon::Workspaces, "Workspaces", Nav::Workspaces),
        (Icon::Journal, "Journal", Nav::Journal),
        (Icon::Settings, "Réglages", Nav::Settings),
    ];

    let mut nav = column![brand].spacing(6).padding([22, 16]);
    nav = nav.push(Space::with_height(18));
    for (icon, label, target) in nav_items {
        let active = state.nav == target;
        let color = if active { theme::ACCENT } else { theme::MUTED };
        nav = nav.push(
            button(
                row![
                    widgets::icon::<Message>(icon, 18.0, color),
                    text(label).size(14),
                ]
                .spacing(12)
                .align_y(iced::alignment::Vertical::Center),
            )
            .on_press(Message::Navigate(target))
            .width(Length::Fill)
            .padding([11, 12])
            .style(move |_theme, status| nav_style(active, status)),
        );
    }

    let footer = column![
        iced::widget::horizontal_rule(1).style(|_theme| iced::widget::rule::Style {
            color: theme::BORDER,
            width: 1,
            radius: 0.0.into(),
            fill_mode: iced::widget::rule::FillMode::Full,
        }),
        text(format!("version {}", state.update_current)).size(11).style(muted_style),
        text(state.status.clone()).size(11).style(muted_style),
    ]
    .spacing(6);

    let column = column![nav, Space::with_height(Length::Fill), container(footer).padding([0, 16])]
        .width(Length::Fixed(232.0))
        .height(Length::Fill);
    container(column).style(theme::sidebar).into()
}

fn nav_style(active: bool, status: button::Status) -> button::Style {
    let background = if active {
        theme::color!(0x7C6CF6, 0.18)
    } else {
        match status {
            button::Status::Hovered => theme::color!(0x1B2440, 0.9),
            button::Status::Pressed => theme::SURFACE_HOVER,
            _ => iced::Color { a: 0.0, ..theme::SURFACE },
        }
    };
    button::Style {
        background: Some(iced::Background::Color(background)),
        text_color: if active { theme::TEXT } else { theme::MUTED },
        border: iced::Border {
            color: if active { theme::ACCENT } else { iced::Color { a: 0.0, ..theme::BORDER } },
            width: if active { 1.0 } else { 0.0 },
            radius: 10.0.into(),
        },
        ..Default::default()
    }
}

fn muted_style(_theme: &Theme) -> iced::widget::text::Style {
    iced::widget::text::Style {
        color: Some(theme::MUTED),
    }
}

fn header(state: &LauncherState) -> Element<'_, Message> {
    let (title, subtitle) = match state.nav {
        Nav::Workspaces => ("Vos workspaces", "Gérez vos instances n8n isolées, leur base et leur synchronisation."),
        Nav::Journal => ("Journal", "Événements récents du lanceur et des workspaces."),
        Nav::Settings => ("Réglages", "Identité, token GitHub et mises à jour."),
    };

    let heading = column![
        text(title).size(27),
        text(subtitle).size(13).style(muted_style),
    ]
    .spacing(4);

    let action = if state.nav == Nav::Workspaces {
        button(
            row![
                widgets::icon::<Message>(Icon::Plus, 16.0, iced::Color::WHITE),
                text("Nouveau").size(14),
            ]
            .spacing(7)
            .align_y(iced::alignment::Vertical::Center),
        )
        .on_press(Message::NewWorkspace)
        .padding([11, 16])
        .style(theme::primary_button)
    } else {
        button(text("Actualiser").size(13))
            .on_press(if state.nav == Nav::Journal {
                Message::JournalRefresh
            } else {
                Message::CheckUpdates
            })
            .padding([11, 16])
            .style(theme::ghost_button)
    };

    let top = row![heading, Space::with_width(Length::Fill), action]
        .spacing(20)
        .align_y(iced::alignment::Vertical::Center);

    let controls: Element<'_, Message> = if state.nav == Nav::Workspaces {
        column![search_and_filters(state), counters(state)].spacing(14).into()
    } else {
        Space::with_height(0).into()
    };

    container(column![top, controls].spacing(18))
        .padding([24, 28])
        .into()
}

fn search_and_filters(state: &LauncherState) -> Element<'_, Message> {
    let search = row![
        widgets::icon::<Message>(Icon::Search, 16.0, theme::MUTED),
        text_input("Rechercher un workspace…", &state.search)
            .on_input(Message::SearchChanged)
            .size(14)
            .padding(11)
            .style(theme::input_style),
    ]
    .spacing(8)
    .align_y(iced::alignment::Vertical::Center)
    .width(Length::Fixed(300.0));

    let filters = [
        ("Tous", None),
        ("Arrêtés", Some(WorkspaceState::STOPPED)),
        ("En marche", Some(WorkspaceState::RUNNING)),
        ("Démarrage", Some(WorkspaceState::STARTING)),
        ("Erreurs", Some(WorkspaceState::ERROR)),
    ];
    let mut row = row![search].spacing(8).align_y(iced::alignment::Vertical::Center);
    for (label, state_filter) in filters {
        let active = state.filter == state_filter;
        row = row.push(
            button(text(label).size(12))
                .on_press(Message::FilterChanged(state_filter))
                .padding([8, 12])
                .style(move |_theme, status| filter_style(active, status)),
        );
    }
    row.into()
}

fn filter_style(active: bool, status: button::Status) -> button::Style {
    let background = if active {
        iced::Background::Color(theme::color!(0x7C6CF6, 0.24))
    } else {
        iced::Background::Color(match status {
            button::Status::Hovered => theme::SURFACE_HOVER,
            _ => theme::SURFACE,
        })
    };
    button::Style {
        background: Some(background),
        text_color: if active { theme::TEXT } else { theme::MUTED },
        border: iced::Border {
            color: if active { theme::ACCENT } else { theme::BORDER },
            width: 1.0,
            radius: 8.0.into(),
        },
        ..Default::default()
    }
}

fn counters(state: &LauncherState) -> Element<'_, Message> {
    let running = state
        .workspaces
        .iter()
        .filter(|ws| ws.state == WorkspaceState::RUNNING)
        .count();
    let stopped = state
        .workspaces
        .iter()
        .filter(|ws| ws.state == WorkspaceState::STOPPED)
        .count();
    let errors = state
        .workspaces
        .iter()
        .filter(|ws| ws.state == WorkspaceState::ERROR)
        .count();

    row![
        stat_tile("Total", state.workspaces.len(), theme::ACCENT),
        stat_tile("En marche", running, theme::SUCCESS),
        stat_tile("Arrêtés", stopped, theme::NEUTRAL),
        stat_tile("Erreurs", errors, theme::DANGER),
    ]
    .spacing(10)
    .into()
}

fn stat_tile<'a>(label: &'a str, value: usize, color: iced::Color) -> Element<'a, Message> {
    container(
        column![
            text(value.to_string()).size(21),
            text(label).size(11).style(muted_style),
        ]
        .spacing(2),
    )
    .padding([10, 16])
    .width(Length::Fixed(130.0))
    .style(move |_theme| container::Style {
        background: Some(iced::Background::Color(theme::SURFACE)),
        border: iced::Border {
            color: theme::BORDER,
            width: 1.0,
            radius: 10.0.into(),
        },
        shadow: iced::Shadow {
            color: iced::Color { a: 0.13, ..color },
            offset: iced::Vector::new(0.0, 2.0),
            blur_radius: 8.0,
        },
        ..Default::default()
    })
    .into()
}

/// Workspaces correspondant à la recherche et au filtre courants.
fn visible_workspaces(state: &LauncherState) -> Vec<&Workspace> {
    let needle = state.search.trim().to_lowercase();
    state
        .workspaces
        .iter()
        .filter(|ws| state.filter.is_none_or(|filter| ws.state == filter))
        .filter(|ws| {
            needle.is_empty()
                || ws.name.to_lowercase().contains(&needle)
                || ws.id.to_lowercase().contains(&needle)
        })
        .collect()
}

fn content(state: &LauncherState) -> Element<'_, Message> {
    let inner: Element<'_, Message> = match state.nav {
        Nav::Workspaces => workspaces_page(state),
        Nav::Journal => journal_page(state),
        Nav::Settings => settings_page(state),
    };
    let base: Element<'_, Message> = scrollable(
        container(inner)
            .width(Length::Fill)
            .padding([16, 28]),
    )
    .height(Length::Fill)
    .into();

    if let Some(modal) = modal_layer(state) {
        stack![base, modal].into()
    } else {
        base
    }
}

fn workspaces_page(state: &LauncherState) -> Element<'_, Message> {
    let visible = visible_workspaces(state);
    if visible.is_empty() {
        return if state.workspaces.is_empty() {
            widgets::empty_state(
                "Aucun workspace pour le moment",
                "Cliquez sur « Nouveau » pour créer votre première instance n8n.",
            )
        } else {
            widgets::empty_state(
                "Aucun résultat",
                "Essayez un autre terme de recherche ou un autre filtre.",
            )
        };
    }

    let mut rows: Vec<Element<'_, Message>> = Vec::new();
    for chunk in visible.chunks(2) {
        let mut cards: Vec<Element<'_, Message>> = Vec::new();
        for ws in chunk {
            cards.push(workspace_card(state, ws));
        }
        rows.push(row(cards).spacing(16).into());
    }
    column(rows).spacing(16).into()
}

fn workspace_card<'a>(state: &'a LauncherState, ws: &'a Workspace) -> Element<'a, Message> {
    let info = status::status_for(ws.state);
    let busy = state.busy.contains(&ws.id);

    let status_pill: Element<'a, Message> = match status::primary_action(ws.state) {
        Some(action) if !busy => button(widgets::pill(info.label.clone(), info.tone))
            .on_press(action_message(action, &ws.id))
            .style(bare_button)
            .into(),
        _ => widgets::pill(info.label.clone(), info.tone),
    };

    let summary = status::summary_line(ws);
    let title = column![
        row![
            text(ws.name.clone()).size(17),
            Space::with_width(Length::Fill),
            status_pill,
        ]
        .spacing(10)
        .align_y(iced::alignment::Vertical::Center),
        text(summary).size(12).style(muted_style),
    ]
    .spacing(6);

    let badges: Element<'a, Message> = row![
        badge(Icon::Database, "Postgres managé", ws.db.mode == DbMode::MANAGED),
        badge(Icon::Git, "Git", ws.git.enabled),
        badge(Icon::Update, "CI", ws.git.ci_enabled),
        badge(Icon::External, "Serveur", ws.server.enabled),
    ]
    .spacing(6)
    .into();

    let url = format!("http://localhost:{}", ws.port);
    let link: Element<'a, Message> = if busy {
        row![
            widgets::icon::<Message>(Icon::Clock, 14.0, theme::WARNING),
            text("Opération en cours…").size(12).style(|_theme| iced::widget::text::Style {
                color: Some(theme::WARNING),
            }),
        ]
        .spacing(6)
        .into()
    } else {
        button(
            row![
                widgets::icon::<Message>(Icon::External, 13.0, theme::ACCENT),
                text(url).size(12).style(|_theme| iced::widget::text::Style {
                    color: Some(theme::ACCENT),
                }),
            ]
            .spacing(6),
        )
        .on_press(Message::OpenUrl(format!("http://localhost:{}", ws.port)))
        .style(bare_button)
        .into()
    };

    let mut actions: Vec<Element<'a, Message>> = Vec::new();
    for card_action in status::card_actions(ws.state) {
        let enabled = card_action.enabled && !busy;
        let (glyph, color) = match card_action.action {
            WorkspaceAction::Open => (Icon::Play, theme::SUCCESS),
            WorkspaceAction::Stop => (Icon::Stop, theme::WARNING),
            WorkspaceAction::Delete => (Icon::Trash, theme::DANGER),
        };
        let kind = card_action.destructive;
        actions.push(
            button(
                row![
                    widgets::icon::<Message>(glyph, 13.0, color),
                    text(card_action.label).size(12),
                ]
                .spacing(6),
            )
            .on_press_maybe(if enabled {
                Some(action_message(card_action.action, &ws.id))
            } else {
                None
            })
            .padding([7, 10])
            .style(move |_theme, status| {
                if kind {
                    theme::danger_button(_theme, status)
                } else {
                    theme::ghost_button(_theme, status)
                }
            })
            .into(),
        );
    }

    container(
        column![title, badges, link, row(actions).spacing(8)]
            .spacing(14),
    )
    .padding(18)
    .width(Length::FillPortion(1))
    .style(theme::card)
    .into()
}

fn badge<'a, Message: Clone + 'static>(glyph: Icon, label: &'a str, enabled: bool) -> Element<'a, Message> {
    let color = if enabled { theme::MUTED } else { theme::color!(0x4B5570) };
    container(
        row![
            widgets::icon::<Message>(glyph, 12.0, color),
            text(label).size(11).style(move |_theme| iced::widget::text::Style { color: Some(color) }),
        ]
        .spacing(5),
    )
    .padding([5, 8])
    .style(|_theme| container::Style {
        background: Some(iced::Background::Color(theme::color!(0x0E1424, 0.8))),
        border: iced::Border {
            color: theme::BORDER,
            width: 1.0,
            radius: 7.0.into(),
        },
        ..Default::default()
    })
    .into()
}

fn bare_button(_theme: &Theme, status: button::Status) -> button::Style {
    button::Style {
        background: Some(iced::Background::Color(match status {
            button::Status::Hovered => theme::SURFACE_HOVER,
            _ => iced::Color { a: 0.0, ..theme::SURFACE },
        })),
        text_color: theme::TEXT,
        border: iced::Border {
            color: iced::Color { a: 0.0, ..theme::BORDER },
            width: 0.0,
            radius: 7.0.into(),
        },
        ..Default::default()
    }
}

// ---------------------------------------------------------------------------
// Journal
// ---------------------------------------------------------------------------

fn journal_page(state: &LauncherState) -> Element<'_, Message> {
    let search = row![
        widgets::icon::<Message>(Icon::Search, 16.0, theme::MUTED),
        text_input("Rechercher dans le journal…", &state.journal_query)
            .on_input(Message::JournalQueryChanged)
            .size(14)
            .padding(11)
            .style(theme::input_style),
    ]
    .spacing(8)
    .width(Length::Fixed(360.0));

    let rows: Vec<Element<'_, Message>> = state
        .events
        .iter()
        .map(|event| {
            let color = if event.level.eq_ignore_ascii_case("ERREUR")
                || event.level.eq_ignore_ascii_case("ERROR")
            {
                theme::DANGER
            } else {
                theme::SUCCESS
            };
            container(
                column![
                    row![
                        text(short_timestamp(&event.timestamp)).size(11).style(muted_style),
                        widgets::pill(event.level.clone(), if color == theme::DANGER {
                            status::StatusTone::Error
                        } else {
                            status::StatusTone::Active
                        }),
                        Space::with_width(Length::Fill),
                        text(event.name.clone()).size(11).style(muted_style),
                    ]
                    .spacing(10)
                    .align_y(iced::alignment::Vertical::Center),
                    text(event.message.clone()).size(14),
                ]
                .spacing(8),
            )
            .padding([12, 14])
            .style(theme::surface)
            .into()
        })
        .collect();

    let list: Element<'_, Message> = if rows.is_empty() {
        widgets::empty_state(
            "Journal vide",
            "Les actions du lanceur apparaîtront ici au fur et à mesure.",
        )
    } else {
        column(rows).spacing(8).into()
    };

    column![row![search, Space::with_width(Length::Fill)].spacing(12), Space::with_height(12), list]
        .spacing(4)
        .into()
}

/// Formatte un horodatage RFC 3339 en une ligne courte pour le journal.
fn short_timestamp(raw: &str) -> String {
    chrono::DateTime::parse_from_rfc3339(raw)
        .map(|dt| dt.format("%d/%m %H:%M:%S").to_string())
        .unwrap_or_else(|_| raw.to_string())
}

// ---------------------------------------------------------------------------
// Réglages
// ---------------------------------------------------------------------------

fn settings_page(state: &LauncherState) -> Element<'_, Message> {
    let email = state
        .store
        .as_ref()
        .and_then(|store| store.load().ok())
        .map(|config| config.owner_email)
        .unwrap_or_else(|| "—".to_string());

    let token_hint = if state.token_present {
        "Un token est enregistré (masqué)."
    } else {
        "Aucun token enregistré. La découverte automatique n'est pas disponible."
    };

    let token_row = row![
        text_input("Token GitHub (ghp_…)", &state.token_input)
            .on_input(Message::TokenChanged)
            .secure(true)
            .size(14)
            .padding(11)
            .style(theme::input_style),
        button(text("Enregistrer").size(13))
            .on_press(Message::SaveToken)
            .padding([11, 14])
            .style(theme::primary_button),
        button(text("Effacer").size(13))
            .on_press(Message::ClearToken)
            .padding([11, 14])
            .style(theme::ghost_button),
    ]
    .spacing(8)
    .align_y(iced::alignment::Vertical::Center);

    let latest = state.update_latest.as_deref().unwrap_or("—");
    let update_status = if let Some(err) = &state.update_error {
        format!("Vérification impossible : {err}")
    } else if state.update_latest.is_some() {
        format!("Dernière version connue : {latest}")
    } else {
        "Aucune vérification effectuée.".to_string()
    };

    column![
        section(
            "Identité",
            column![
                widgets::labeled::<Message>("Email propriétaire", email),
                widgets::labeled::<Message>("Version", state.update_current.clone()),
            ]
            .spacing(8)
            .into(),
        ),
        section(
            "GitHub",
            column![
                text(token_hint).size(12).style(muted_style),
                token_row,
            ]
            .spacing(10)
            .into(),
        ),
        section(
            "Mises à jour",
            column![
                widgets::labeled::<Message>("Version installée", state.update_current.clone()),
                widgets::labeled::<Message>("Dernière version", latest.to_string()),
                text(update_status).size(12).style(muted_style),
                row![
                    button(
                        row![
                            widgets::icon::<Message>(Icon::Update, 15.0, iced::Color::WHITE),
                            text(if state.checking_updates { "Vérification…" } else { "Vérifier" }).size(13),
                        ]
                        .spacing(7),
                    )
                    .on_press_maybe(if state.checking_updates { None } else { Some(Message::CheckUpdates) })
                    .padding([10, 14])
                    .style(theme::primary_button),
                    button(
                        row![
                            widgets::icon::<Message>(Icon::External, 14.0, theme::ACCENT),
                            text("Voir la release").size(13),
                        ]
                        .spacing(7),
                    )
                    .on_press(Message::OpenUrl(updater::release_page_url(updater::REPO)))
                    .padding([10, 14])
                    .style(theme::ghost_button),
                ]
                .spacing(8),
                checkbox(" Installer uniquement depuis une release signée par le projet", true)
                    .on_toggle(|_| {
                        Message::Notice(
                            "L'installation automatique n'est pas activée.".to_string(),
                            false,
                        )
                    })
                    .size(13),
            ]
            .spacing(10)
            .into(),
        ),
    ]
    .spacing(14)
    .into()
}

fn section<'a>(title: &'a str, body: Element<'a, Message>) -> Element<'a, Message> {
    container(
        column![text(title).size(17), Space::with_height(4), body].spacing(6),
    )
    .padding(18)
    .width(Length::Fill)
    .style(theme::surface)
    .into()
}

// ---------------------------------------------------------------------------
// Modales et pied de page
// ---------------------------------------------------------------------------

fn modal_layer(state: &LauncherState) -> Option<Element<'_, Message>> {
    if let Some(id) = &state.pending_delete {
        let name = state
            .workspaces
            .iter()
            .find(|ws| &ws.id == id)
            .map(|ws| ws.name.clone())
            .unwrap_or_else(|| id.clone());
        return Some(
            container(
                container(
                    column![
                        row![
                            widgets::icon::<Message>(Icon::Warning, 22.0, theme::DANGER),
                            text("Supprimer ce workspace ?").size(19),
                        ]
                        .spacing(10),
                        text(format!(
                            "« {name} » sera retiré du lanceur. Ses workflows exportés restent sur le disque."
                        ))
                        .size(13)
                        .style(muted_style),
                        Space::with_height(8),
                        row![
                            button(text("Annuler").size(13))
                                .on_press(Message::CancelDelete)
                                .padding([10, 16])
                                .style(theme::ghost_button),
                            button(text("Supprimer").size(13))
                                .on_press(Message::ConfirmDelete(id.clone()))
                                .padding([10, 16])
                                .style(theme::danger_button),
                        ]
                        .spacing(10),
                    ]
                    .spacing(14),
                )
                .padding(26)
                .width(Length::Fixed(460.0))
                .style(theme::card),
            )
            .width(Length::Fill)
            .height(Length::Fill)
            .center_x(Length::Fill)
            .center_y(Length::Fill)
            .style(theme::scrim)
            .into(),
        );
    }

    if state.show_create {
        return Some(
            container(
                container(
                    column![
                        row![
                            widgets::icon::<Message>(Icon::Plus, 22.0, theme::ACCENT),
                            text("Nouveau workspace").size(19),
                        ]
                        .spacing(10),
                        text("Nom").size(12).style(muted_style),
                        text_input("Mon projet", &state.create_name)
                            .on_input(Message::CreateNameChanged)
                            .size(14)
                            .padding(11)
                            .style(theme::input_style),
                        text("Port n8n (par défaut 5678)").size(12).style(muted_style),
                        text_input("5678", &state.create_port)
                            .on_input(Message::CreatePortChanged)
                            .size(14)
                            .padding(11)
                            .style(theme::input_style),
                        text("Dossier des workflows (optionnel)").size(12).style(muted_style),
                        text_input("Laissé vide = dossier du lanceur", &state.create_dir)
                            .on_input(Message::CreateDirChanged)
                            .size(14)
                            .padding(11)
                            .style(theme::input_style),
                        checkbox("Base Postgres managée", state.create_managed)
                            .on_toggle(Message::CreateManagedChanged)
                            .size(14),
                        Space::with_height(6),
                        row![
                            button(text("Annuler").size(13))
                                .on_press(Message::CancelCreate)
                                .padding([10, 16])
                                .style(theme::ghost_button),
                            button(text(if state.creating { "Création…" } else { "Créer" }).size(13))
                                .on_press_maybe(if state.creating { None } else { Some(Message::SubmitCreate) })
                                .padding([10, 16])
                                .style(theme::primary_button),
                        ]
                        .spacing(10),
                    ]
                    .spacing(10),
                )
                .padding(26)
                .width(Length::Fixed(480.0))
                .style(theme::card),
            )
            .width(Length::Fill)
            .height(Length::Fill)
            .center_x(Length::Fill)
            .center_y(Length::Fill)
            .style(theme::scrim)
            .into(),
        );
    }
    None
}

fn footer(state: &LauncherState) -> Element<'_, Message> {
    let notice: Element<'_, Message> = match &state.notice {
        Some(notice) => row![
            widgets::icon::<Message>(
                if notice.error { Icon::Warning } else { Icon::Check },
                14.0,
                if notice.error { theme::DANGER } else { theme::SUCCESS },
            ),
            text(notice.text.clone()).size(12).style(move |_theme| iced::widget::text::Style {
                color: Some(if notice.error { theme::DANGER } else { theme::SUCCESS }),
            }),
        ]
        .spacing(7)
        .into(),
        None => text("Prêt").size(12).style(muted_style).into(),
    };

    container(
        row![
            text(status_line(state)).size(12).style(muted_style),
            Space::with_width(Length::Fill),
            notice,
        ]
        .spacing(16)
        .align_y(iced::alignment::Vertical::Center),
    )
    .padding([9, 28])
    .style(|_theme| container::Style {
        background: Some(iced::Background::Color(theme::SIDEBAR)),
        border: iced::Border {
            color: theme::BORDER,
            width: 1.0,
            radius: 0.0.into(),
        },
        ..Default::default()
    })
    .into()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::models::{DbConfig, Workspace};

    fn workspace(id: &str, name: &str, state: WorkspaceState) -> Workspace {
        Workspace {
            id: id.to_string(),
            name: name.to_string(),
            db: DbConfig::new(DbMode::NONE),
            state,
            ..Default::default()
        }
    }

    #[test]
    fn test_visible_workspaces_filter_and_search() {
        let mut state = LauncherState::default();
        state.workspaces = vec![
            workspace("ws-1", "Alpha", WorkspaceState::RUNNING),
            workspace("ws-2", "Beta", WorkspaceState::STOPPED),
        ];
        assert_eq!(visible_workspaces(&state).len(), 2);
        state.search = "alp".to_string();
        assert_eq!(visible_workspaces(&state).len(), 1);
        state.search.clear();
        state.filter = Some(WorkspaceState::STOPPED);
        let visible = visible_workspaces(&state);
        assert_eq!(visible.len(), 1);
        assert_eq!(visible[0].id, "ws-2");
    }

    #[test]
    fn test_view_builds_for_every_page() {
        let state = LauncherState::default();
        for page in [Nav::Workspaces, Nav::Journal, Nav::Settings] {
            let mut state = LauncherState::default();
            state.nav = page;
            let _ = view(&state);
        }
        let _ = view(&state);
    }

    #[test]
    fn test_short_timestamp() {
        assert_eq!(
            short_timestamp("2026-11-27T10:05:07Z"),
            "27/11 10:05:07"
        );
        assert_eq!(short_timestamp("pas une date"), "pas une date");
    }
}
