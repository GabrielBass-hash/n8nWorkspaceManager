//! What a card shows and which actions it offers.
//! Port de `src/n8n_launcher/workspaces/status.py`.

use crate::core::models::{DbMode, Workspace, WorkspaceState};

/// The colour family a status belongs to; the view maps it to a palette.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StatusTone {
    Neutral,
    Active,
    Pending,
    Error,
}

/// What a card can be asked to do; the view binds it to a gesture.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WorkspaceAction {
    Open,
    Stop,
    Delete,
}

/// One offered action: its label, and whether it currently applies.
///
/// The label lives here rather than in a menu builder so that what the user
/// reads and what the tests assert are the same string. ``destructive`` marks
/// the action a view must confirm before running.
#[derive(Debug, Clone)]
pub struct CardAction {
    pub action: WorkspaceAction,
    pub label: String,
    pub enabled: bool,
    pub destructive: bool,
}

/// Everything a row needs to describe and act on one lifecycle state.
#[derive(Debug, Clone)]
pub struct WorkspaceStatus {
    pub state: WorkspaceState,
    pub label: String,
    pub tone: StatusTone,
    pub can_start: bool,
    pub can_stop: bool,
    pub can_delete: bool,
    pub can_open: bool,
}

//: French label for each persisted state.
const _LABELS: &[(WorkspaceState, &str)] = &[
    (WorkspaceState::STOPPED, "Arrêté"),
    (WorkspaceState::STARTING, "Démarrage…"),
    (WorkspaceState::RUNNING, "En marche"),
    (WorkspaceState::STOPPING, "Arrêt…"),
    (WorkspaceState::ERROR, "Erreur"),
];

const _TONES: &[(WorkspaceState, StatusTone)] = &[
    (WorkspaceState::STOPPED, StatusTone::Neutral),
    (WorkspaceState::STARTING, StatusTone::Pending),
    (WorkspaceState::RUNNING, StatusTone::Active),
    (WorkspaceState::STOPPING, StatusTone::Pending),
    (WorkspaceState::ERROR, StatusTone::Error),
];

fn label_for(state: WorkspaceState) -> &'static str {
    _LABELS.iter().find(|(s, _)| *s == state).map(|(_, l)| *l).unwrap_or("Inconnu")
}

fn tone_for(state: WorkspaceState) -> StatusTone {
    _TONES.iter().find(|(s, _)| *s == state).map(|(_, t)| *t).unwrap_or(StatusTone::Neutral)
}

/// Return the human-readable French label for *state*.
pub fn state_label(state: WorkspaceState) -> String {
    label_for(state).to_string()
}

/// Return True when a workspace in *state* may be started.
///
/// An ``ERROR`` workspace can be retried; one that is already starting or
/// running cannot (a second ``docker up`` would race the first).
pub fn can_start(state: WorkspaceState) -> bool {
    matches!(state, WorkspaceState::STOPPED | WorkspaceState::ERROR)
}

/// Return True whenever something is still up that a teardown can remove.
///
/// Deliberately wider than ``state is RUNNING``: a failed start leaves the
/// stack running (the error lands *after* ``docker up``) and a crash-looping
/// container is reported as ``STARTING``, so both are states where the user is
/// looking at a live workspace with no way to bring it down. What is left out
/// is only what cannot be running: ``STOPPED`` and the transient ``STOPPING``.
///
/// The guard against stopping a start *this launcher* is in flight is not here
/// — it has no state to look at — it is the actions layer's busy lock, which
/// blocks the gesture for the whole duration of the task.
pub fn can_stop(state: WorkspaceState) -> bool {
    state != WorkspaceState::STOPPED && state != WorkspaceState::STOPPING
}

/// Return True when a workspace may be removed (only when stopped).
pub fn can_delete(state: WorkspaceState) -> bool {
    state == WorkspaceState::STOPPED
}

/// Return True when opening the workspace's n8n from its card makes sense.
///
/// A **running** workspace is the interesting case: opening it is not a start,
/// so it must never be gated behind ``can_start`` — Docker already holds a
/// running stack and a second ``docker up`` would only race the first. A
/// ``STARTING`` workspace is left alone because the start in flight owns the
/// outcome (it opens the instance itself when it completes), and a
/// ``STOPPING`` one because it is on its way out.
pub fn can_open(state: WorkspaceState) -> bool {
    matches!(
        state,
        WorkspaceState::STOPPED | WorkspaceState::ERROR | WorkspaceState::RUNNING
    )
}

/// Build the full :class:`WorkspaceStatus` for *state*.
pub fn status_for(state: WorkspaceState) -> WorkspaceStatus {
    WorkspaceStatus {
        state,
        label: label_for(state).to_string(),
        tone: tone_for(state),
        can_start: can_start(state),
        can_stop: can_stop(state),
        can_delete: can_delete(state),
        can_open: can_open(state),
    }
}

//: The action a card's status pill performs, per state. ``None`` means the pill
//: is a label only: a start or a stop already in flight owns the outcome, and a
//: second gesture would only race it.
const _PRIMARY_ACTIONS: &[(WorkspaceState, Option<WorkspaceAction>)] = &[
    (WorkspaceState::STOPPED, Some(WorkspaceAction::Open)),
    (WorkspaceState::STARTING, None),
    (WorkspaceState::RUNNING, Some(WorkspaceAction::Stop)),
    (WorkspaceState::STOPPING, None),
    // An errored workspace is offered *open* to stay consistent with what a
    // double-click already does on it; ``Arrêter`` stays one gesture away in the
    // card menu, where :func:`can_stop` enables it.
    (WorkspaceState::ERROR, Some(WorkspaceAction::Open)),
];

/// Return what a click on *state*'s status pill should do, or ``None``.
///
/// The pill is the one control a card already wears, so it carries the action
/// that needs no second gesture to reach. It is **stop** for a live workspace —
/// the teardown a user cannot reach any other way — and **open** otherwise,
/// matching the double-click rather than inventing a third rule.
pub fn primary_action(state: WorkspaceState) -> Option<WorkspaceAction> {
    _PRIMARY_ACTIONS
        .iter()
        .find(|(s, _)| *s == state)
        .map(|(_, a)| *a)
        .unwrap_or(None)
}

/// Return the actions offered on *state*'s card, in display order.
///
/// Every entry is returned whether or not it applies: a disabled item tells the
/// user the action exists and why it is not available right now, which a
/// missing item cannot. There is no separate "Démarrer" entry because opening
/// already starts a workspace Docker is not holding.
pub fn card_actions(state: WorkspaceState) -> Vec<CardAction> {
    let status = status_for(state);
    vec![
        CardAction {
            action: WorkspaceAction::Open,
            label: "Ouvrir".to_string(),
            enabled: status.can_open,
            destructive: false,
        },
        CardAction {
            action: WorkspaceAction::Stop,
            label: "Arrêter".to_string(),
            enabled: status.can_stop,
            destructive: false,
        },
        CardAction {
            action: WorkspaceAction::Delete,
            label: "Supprimer…".to_string(),
            enabled: status.can_delete,
            destructive: true,
        },
    ]
}

/// Return the one-line sub-title shown under a workspace's name.
pub fn summary_line(workspace: &Workspace) -> String {
    let database = if workspace.db.mode == DbMode::MANAGED {
        "Postgres managé"
    } else {
        "sans base locale"
    };
    let mut line = format!("Port {} · {}", workspace.port, database);
    if workspace.restart_required {
        line += " · redémarrage requis";
    }
    line
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_state_label() {
        assert_eq!(state_label(WorkspaceState::STOPPED), "Arrêté");
        assert_eq!(state_label(WorkspaceState::STARTING), "Démarrage…");
        assert_eq!(state_label(WorkspaceState::RUNNING), "En marche");
        assert_eq!(state_label(WorkspaceState::STOPPING), "Arrêt…");
        assert_eq!(state_label(WorkspaceState::ERROR), "Erreur");
    }

    #[test]
    fn test_status_for_stopped() {
        let s = status_for(WorkspaceState::STOPPED);
        assert_eq!(s.state, WorkspaceState::STOPPED);
        assert_eq!(s.label, "Arrêté");
        assert_eq!(s.tone, StatusTone::Neutral);
        assert!(s.can_start);
        assert!(!s.can_stop);
        assert!(s.can_delete);
        assert!(s.can_open);
    }

    #[test]
    fn test_status_for_running() {
        let s = status_for(WorkspaceState::RUNNING);
        assert_eq!(s.state, WorkspaceState::RUNNING);
        assert_eq!(s.label, "En marche");
        assert_eq!(s.tone, StatusTone::Active);
        assert!(!s.can_start);
        assert!(s.can_stop);
        assert!(!s.can_delete);
        assert!(s.can_open);
    }

    #[test]
    fn test_status_for_error() {
        let s = status_for(WorkspaceState::ERROR);
        assert_eq!(s.state, WorkspaceState::ERROR);
        assert_eq!(s.label, "Erreur");
        assert_eq!(s.tone, StatusTone::Error);
        assert!(s.can_start);
        assert!(s.can_stop);
        assert!(!s.can_delete);
        assert!(s.can_open);
    }

    #[test]
    fn test_status_for_starting() {
        let s = status_for(WorkspaceState::STARTING);
        assert!(!s.can_start);
        assert!(s.can_stop);
        assert!(!s.can_delete);
        assert!(!s.can_open);
    }

    #[test]
    fn test_status_for_stopping() {
        let s = status_for(WorkspaceState::STOPPING);
        assert!(!s.can_start);
        assert!(!s.can_stop);
        assert!(!s.can_delete);
        assert!(!s.can_open);
    }

    #[test]
    fn test_can_start() {
        assert!(can_start(WorkspaceState::STOPPED));
        assert!(can_start(WorkspaceState::ERROR));
        assert!(!can_start(WorkspaceState::RUNNING));
        assert!(!can_start(WorkspaceState::STARTING));
        assert!(!can_start(WorkspaceState::STOPPING));
    }

    #[test]
    fn test_can_stop() {
        assert!(!can_stop(WorkspaceState::STOPPED));
        assert!(!can_stop(WorkspaceState::STOPPING));
        assert!(can_stop(WorkspaceState::RUNNING));
        assert!(can_stop(WorkspaceState::STARTING));
        assert!(can_stop(WorkspaceState::ERROR));
    }

    #[test]
    fn test_can_delete() {
        assert!(can_delete(WorkspaceState::STOPPED));
        assert!(!can_delete(WorkspaceState::RUNNING));
        assert!(!can_delete(WorkspaceState::ERROR));
    }

    #[test]
    fn test_can_open() {
        assert!(can_open(WorkspaceState::STOPPED));
        assert!(can_open(WorkspaceState::ERROR));
        assert!(can_open(WorkspaceState::RUNNING));
        assert!(!can_open(WorkspaceState::STARTING));
        assert!(!can_open(WorkspaceState::STOPPING));
    }

    #[test]
    fn test_primary_action() {
        assert_eq!(
            primary_action(WorkspaceState::STOPPED),
            Some(WorkspaceAction::Open)
        );
        assert_eq!(primary_action(WorkspaceState::STARTING), None);
        assert_eq!(
            primary_action(WorkspaceState::RUNNING),
            Some(WorkspaceAction::Stop)
        );
        assert_eq!(primary_action(WorkspaceState::STOPPING), None);
        assert_eq!(
            primary_action(WorkspaceState::ERROR),
            Some(WorkspaceAction::Open)
        );
    }

    #[test]
    fn test_card_actions() {
        let actions = card_actions(WorkspaceState::STOPPED);
        assert_eq!(actions.len(), 3);
        assert_eq!(actions[0].label, "Ouvrir");
        assert!(actions[0].enabled);
        assert_eq!(actions[1].label, "Arrêter");
        assert!(!actions[1].enabled);
        assert_eq!(actions[2].label, "Supprimer…");
        assert!(actions[2].enabled);
        assert!(actions[2].destructive);
    }

    #[test]
    fn test_card_actions_running() {
        let actions = card_actions(WorkspaceState::RUNNING);
        assert_eq!(actions[1].label, "Arrêter");
        assert!(actions[1].enabled);
        assert!(!actions[2].enabled); // can't delete when running
    }

    #[test]
    fn test_summary_line() {
        let workspace = Workspace {
            id: "ws-1".to_string(),
            name: "test".to_string(),
            port: 5678,
            db: crate::core::models::DbConfig::new(crate::core::models::DbMode::MANAGED),
            restart_required: false,
            ..Default::default()
        };
        let line = summary_line(&workspace);
        assert!(line.contains("Port 5678"));
        assert!(line.contains("Postgres managé"));
        assert!(!line.contains("redémarrage requis"));

        let workspace = Workspace {
            restart_required: true,
            ..workspace
        };
        let line = summary_line(&workspace);
        assert!(line.contains("redémarrage requis"));
    }
}
