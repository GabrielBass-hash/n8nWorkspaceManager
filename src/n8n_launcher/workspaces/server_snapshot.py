"""Read-only view of a deployed workspace on its remote server.

:class:`ServerSnapshot` is the value every read returns and
:func:`server_snapshot_text` renders it. The snapshot is the *contract* that
makes supervision cheap: every field is already redacted and bounded by the
:mod:`~n8n_launcher.remote` layer, so whoever builds one never has to think
about presentation, and whoever renders one never has to think about SSH.

Both halves live together on purpose, and neither needs a display — the headless
CLI renders the same snapshot the GUI does. The *reader* that fills one in (four
independent SSH reads whose failures each degrade their own section rather than
raise) is built by the application layer, and the freshness policy is a separate
concern too: it lives with the other caches, in
:class:`n8n_launcher.core.snapshot_cache.SnapshotCache`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..core.subjects import PageSubject
from ..remote import RemoteExecutionStatus, RemoteHealth

# The journal subject the server view filters on while it is focused: the deploy
# itself, not the reads the view performs.
SERVER_SUBJECT = PageSubject("Serveur", ("Serveur", "Déploiement"))

# Shown instead of a snapshot when the workspace has no server configured. It is
# a *note* and not a failure: nothing to supervise is a configuration, and the
# view has to say how to fix it rather than look broken.
NO_SERVER_NOTE = (
    "Ce workspace n'a pas de serveur configuré. Définissez-en un via "
    "« Configurer le serveur… » pour superviser son déploiement et ses exécutions."
)

# How long a collected snapshot stays fresh. A read is four SSH commands, so a
# view nobody is looking at must not re-pay for one on every tick; 20s is short
# enough that a manual refresh never contradicts what is on screen.
SERVER_SNAPSHOT_TTL_SECONDS = 20.0


@dataclass(frozen=True)
class ServerSnapshot:
    """One read-only view of a deployed workspace on the remote server.

    Every field is already redacted and bounded by the ``remote`` layer, so the
    view only has to render it. ``error`` carries a whole failed read (SSH
    unreachable, deployment not confirmed yet) while the other fields keep
    whatever was collected before the failure. ``note`` is the opposite: the read
    was skipped on purpose (no server configured for this workspace), so the view
    says why it is empty instead of reporting a failure.
    """

    health: RemoteHealth | None = None
    logs: str = ""
    marker: dict[str, object] | None = None
    history: tuple[dict[str, object], ...] = ()
    executions: RemoteExecutionStatus | None = None
    error: str | None = None
    note: str | None = None

    @property
    def healthy(self) -> bool:
        """Return whether the remote stack reported itself healthy."""
        return self.health is not None and self.health.healthy


def _deploy_history_text(history: Sequence[dict[str, object]]) -> str:
    """Render the newest deploy entries, one compact line each."""
    if not history:
        return "Aucun déploiement enregistré sur le serveur."
    lines = []
    for entry in reversed(history):
        sha = str(entry.get("sha", ""))[:7]
        status = entry.get("status", "?")
        at = entry.get("at", "?")
        detail = entry.get("error")
        line = f"{at} · {status} · {sha or '—'}"
        if detail:
            line = f"{line} · {detail}"
        lines.append(line)
    return "\n".join(lines)


def _executions_text(status: RemoteExecutionStatus) -> str:
    """Render the remote executions, newest first.

    The remote script answers in n8n's own order and the contract only promises
    "up to *limit* entries", so the display sorts on ``startedAt`` itself rather
    than trusting the order of the wire payload.
    """
    if not status.supported:
        return "Exécutions n8n : non supporté par ce déploiement. " + (status.error or "")
    if not status.executions:
        return "Exécutions n8n : aucune exécution enregistrée."
    ordered = sorted(status.executions, key=lambda item: item.started_at or "", reverse=True)
    lines = []
    for item in ordered:
        # ``finished`` is derived from the status server-side; None means the
        # status was one the launcher does not know, so say so instead of lying.
        if item.finished is True:
            state = "terminé"
        elif item.finished is False:
            state = "en cours"
        else:
            state = "état inconnu"
        name = item.workflow_name or "—"
        lines.append(
            f"{item.started_at or '—'} · {item.status} · {name} · #{item.execution_id} · {state}"
        )
    return "Exécutions n8n :\n" + "\n".join(lines)


def server_snapshot_text(snapshot: ServerSnapshot) -> str:
    """Render the whole server snapshot as plain text (health, deploy, logs).

    A ``note`` short-circuits the whole rendering: there is no server to
    describe, and an empty health section next to a "lecture partielle" line
    would read like a broken deployment.
    """
    if snapshot.note:
        return snapshot.note
    parts: list[str] = []
    health = snapshot.health
    if health is None:
        parts.append("Santé : inconnue.")
    else:
        headline = (
            "Santé : serveur injoignable."
            if not health.available
            else f"Santé : {'ok' if health.healthy else 'dégradée'}."
        )
        parts.append(headline)
        if health.services:
            services = " · ".join(
                f"{name} {value}{'/' + health.health[name] if name in health.health else ''}"
                for name, value in health.services.items()
            )
            parts.append(f"Services : {services}")
        if health.error:
            parts.append(f"Diagnostic : {health.error}")
    parts.append("Dernier déploiement :\n" + _deploy_history_text(snapshot.history[-1:]))
    if len(snapshot.history) > 1:
        parts.append("Historique :\n" + _deploy_history_text(snapshot.history))
    if snapshot.executions is not None:
        parts.append(_executions_text(snapshot.executions))
    parts.append("Logs n8n (dernières lignes) :\n" + (snapshot.logs.strip() or "—"))
    if snapshot.error:
        # Last, and as a footnote: a read that partly failed still reports what it
        # did collect, and the failure must not read like an empty deployment.
        parts.append(f"Lecture partielle : {snapshot.error}")
    return "\n\n".join(parts)


__all__ = [
    "NO_SERVER_NOTE",
    "SERVER_SNAPSHOT_TTL_SECONDS",
    "SERVER_SUBJECT",
    "ServerSnapshot",
    "server_snapshot_text",
]
