"""Unit tests for port selection helpers."""

from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

from n8n_launcher.platform.ports import is_port_available, suggest_port


def _fake_socket() -> tuple[MagicMock, MagicMock]:
    """Return a ``socket.socket()`` context mock and the socket it yields.

    The production code does ``with socket.socket(...) as sock:``, so the calls
    land on the ``__enter__`` result, not on the context object itself.
    """
    context = MagicMock(spec=socket.socket)
    return context, context.__enter__.return_value


def test_suggest_port_skips_reserved_and_busy_ports() -> None:
    with patch("n8n_launcher.platform.ports.is_port_available", side_effect=[False, True]):
        assert suggest_port(5678, reserved={5679}, limit=3) == 5680


def test_suggest_port_raises_when_the_whole_range_is_busy() -> None:
    with patch("n8n_launcher.platform.ports.is_port_available", return_value=False):
        try:
            suggest_port(5678, limit=2)
        except OSError as exc:
            assert "5678-5679" in str(exc)
        else:
            raise AssertionError("a fully booked range must raise")


def test_is_port_available_binds_the_wildcard_address() -> None:
    """A loopback bind would miss a docker-proxy listener on 0.0.0.0.

    On BSD sockets, with ``SO_REUSEADDR`` set, binding ``127.0.0.1:P`` beside a
    wildcard ``0.0.0.0:P`` listener succeeds, so only the wildcard address is a
    faithful "is Docker free to publish this?" probe.
    """
    context, sock = _fake_socket()
    with patch("n8n_launcher.platform.ports.socket.socket", return_value=context):
        assert is_port_available(5678) is True

    sock.bind.assert_called_once_with(("0.0.0.0", 5678))
    sock.setsockopt.assert_called_once_with(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)


def test_is_port_available_reports_a_bound_port_as_busy() -> None:
    context, sock = _fake_socket()
    sock.bind.side_effect = OSError("Address already in use")
    with patch("n8n_launcher.platform.ports.socket.socket", return_value=context):
        assert is_port_available(5678) is False
