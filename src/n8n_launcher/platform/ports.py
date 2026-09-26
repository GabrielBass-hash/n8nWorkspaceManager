"""Port selection helpers."""

from __future__ import annotations

import socket


def is_port_available(port: int, host: str = "0.0.0.0") -> bool:
    """Return True when ``host:port`` can be bound (i.e. nothing is listening).

    The probe binds the wildcard address rather than the loopback one because
    BSD sockets (macOS) let a specific address bind succeed *next to* an
    existing wildcard listener when ``SO_REUSEADDR`` is set: docker-proxy
    publishes workspaces on ``0.0.0.0``, so a loopback-only check would report
    their ports as free and Docker would then refuse to allocate them.
    ``SO_REUSEADDR`` is kept so a TIME_WAIT connection does not make a port
    look busy — the wildcard bind still conflicts with every live listener.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def suggest_port(
    requested: int = 5678,
    *,
    reserved: set[int] | None = None,
    limit: int = 100,
) -> int:
    """Return the first free port at/after ``requested``, skipping ``reserved``."""
    reserved_ports = reserved or set()
    for port in range(requested, requested + limit):
        if port not in reserved_ports and is_port_available(port):
            return port
    raise OSError(f"No available port found in range {requested}-{requested + limit - 1}")
