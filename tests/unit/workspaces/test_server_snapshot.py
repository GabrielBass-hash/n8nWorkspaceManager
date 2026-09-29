"""Unit tests for the server snapshot (:mod:`n8n_launcher.workspaces.server_snapshot`).

The snapshot is what supervision reads, so its text is the product: these tests
pin the wording a user reads when the server is fine, degraded, unreachable,
half-read, or simply not configured. They run headless — the GUI used to own this
rendering, and nothing about it needed a toolkit.
"""

from __future__ import annotations

from n8n_launcher.remote import RemoteExecution, RemoteExecutionStatus, RemoteHealth
from n8n_launcher.workspaces.server_snapshot import (
    NO_SERVER_NOTE,
    SERVER_SNAPSHOT_TTL_SECONDS,
    SERVER_SUBJECT,
    ServerSnapshot,
    server_snapshot_text,
)


def _health(*, available: bool = True, healthy: bool = True) -> RemoteHealth:
    return RemoteHealth(
        available=available,
        healthy=healthy,
        services={"n8n": "running"} if available else {},
        health={} if not available else {"n8n": "healthy" if healthy else "unhealthy"},
        error=None if healthy else "Le service n8n n'est pas sain",
    )


def test_snapshot_without_health_is_not_healthy():
    # A read that never reached the server must not read as a healthy one: the
    # absence of evidence is the whole point of the ``healthy`` property.
    assert ServerSnapshot().healthy is False


def test_snapshot_healthy_when_the_stack_says_so():
    assert ServerSnapshot(health=_health()).healthy is True
    assert ServerSnapshot(health=_health(healthy=False)).healthy is False


def test_snapshot_ttl_is_short_enough_for_a_manual_refresh_to_win():
    # Four SSH commands are not free, so a view nobody looks at must not re-pay
    # for one on every tick; but 20s never contradicts what is on screen.
    assert SERVER_SNAPSHOT_TTL_SECONDS == 20.0


def test_healthy_view():
    snapshot = ServerSnapshot(
        health=_health(),
        logs="n8n ready",
        history=({"sha": "abcdef1234", "status": "ok", "at": "2026-09-25 10:00:00"},),
    )
    text = server_snapshot_text(snapshot)
    assert "Santé : ok." in text
    assert "n8n running/healthy" in text
    assert "2026-09-25 10:00:00 · ok · abcdef1" in text
    assert "n8n ready" in text


def test_degraded_view_lists_the_services_and_the_diagnostic():
    text = server_snapshot_text(ServerSnapshot(health=_health(healthy=False)))
    assert "Santé : dégradée." in text
    assert "Diagnostic : Le service n8n n'est pas sain" in text
    assert "Aucun déploiement enregistré" in text
    assert "Logs n8n (dernières lignes) :\n—" in text


def test_unavailable_server():
    health = _health(available=False, healthy=False)
    text = server_snapshot_text(ServerSnapshot(health=health))
    assert "serveur injoignable" in text
    assert ServerSnapshot(health=health).healthy is False


def test_partial_read_keeps_what_was_collected():
    # One failing read must not blank the others: the health that did arrive is
    # still the most useful thing on screen.
    text = server_snapshot_text(ServerSnapshot(health=_health(), error="ssh: timeout"))
    assert "Lecture partielle : ssh: timeout" in text
    assert "Santé : ok." in text


def test_read_that_nothing_reached_reports_unknown_health():
    text = server_snapshot_text(ServerSnapshot(error="ssh: timeout"))
    assert "Lecture partielle : ssh: timeout" in text
    assert "Santé : inconnue." in text


def test_note_short_circuits_the_whole_rendering():
    # A workspace with no server configured was never a failed read, so the note
    # replaces everything rather than standing next to an empty health section.
    text = server_snapshot_text(ServerSnapshot(note="Aucun serveur configuré."))
    assert text == "Aucun serveur configuré."
    assert "Santé" not in text


def test_history_is_newest_first_with_errors():
    snapshot = ServerSnapshot(
        history=(
            {"sha": "aaa1111", "status": "ok", "at": "t1"},
            {"sha": "bbb2222", "status": "error", "at": "t2", "error": "compose failed"},
        )
    )
    text = server_snapshot_text(snapshot)
    assert text.index("t2 · error · bbb2222 · compose failed") < text.index("t1 · ok · aaa1111")


def test_executions_section_is_omitted_when_it_was_not_read():
    assert "Exécutions n8n" not in server_snapshot_text(ServerSnapshot(health=_health()))


def test_executions_are_listed_newest_first():
    status = RemoteExecutionStatus(
        supported=True,
        executions=(
            RemoteExecution("9001", "error", "Sync", "2026-09-25T10:00:00Z", finished=True),
            RemoteExecution("9002", "success", "Import", "2026-09-25T11:00:00Z", finished=True),
        ),
    )
    text = server_snapshot_text(ServerSnapshot(health=_health(), executions=status))
    # n8n's own order is not a contract, so the display sorts on startedAt.
    assert text.index("#9002") < text.index("#9001")
    assert "2026-09-25T11:00:00Z · success · Import · #9002 · terminé" in text
    assert "2026-09-25T10:00:00Z · error · Sync · #9001 · terminé" in text


def test_executions_mark_running_and_unknown_states():
    status = RemoteExecutionStatus(
        supported=True,
        executions=(
            RemoteExecution("9003", "running", "Live", "2026-09-25T12:00:00Z", finished=False),
            RemoteExecution("9004", "success", "Future", "2026-09-25T13:00:00Z", finished=None),
            RemoteExecution("9005", "error", None, None, finished=True),
        ),
    )
    text = server_snapshot_text(ServerSnapshot(health=_health(), executions=status))
    assert "· #9003 · en cours" in text
    assert "· #9004 · état inconnu" in text
    # A summary without a workflow name or a timestamp still renders.
    assert "· — · #9005 · terminé" in text


def test_unsupported_execution_command_is_reported_not_raised():
    # A server deployed before the capability flag reports "unsupported": that is
    # an older deployment, not a broken read.
    status = RemoteExecutionStatus(supported=False, error="status distant non supporté")
    text = server_snapshot_text(ServerSnapshot(health=_health(), executions=status))
    assert "Exécutions n8n : non supporté par ce déploiement. status distant non supporté" in text


def test_empty_execution_history():
    text = server_snapshot_text(
        ServerSnapshot(health=_health(), executions=RemoteExecutionStatus(supported=True))
    )
    assert "Exécutions n8n : aucune exécution enregistrée." in text


def test_the_server_subject_names_the_deployment_not_the_reads() -> None:
    # The journal is scoped to what happened to the *deployment*: the reads the
    # view performs are not what a user watching it is looking for.
    assert SERVER_SUBJECT.label == "Serveur"
    assert SERVER_SUBJECT.tokens == ("Serveur", "Déploiement")


def test_no_server_note_says_how_to_configure_one() -> None:
    # It replaces the whole snapshot, so it has to be actionable on its own.
    assert "n'a pas de serveur configuré" in NO_SERVER_NOTE
    assert "Configurer le serveur" in NO_SERVER_NOTE
    assert server_snapshot_text(ServerSnapshot(note=NO_SERVER_NOTE)) == NO_SERVER_NOTE
