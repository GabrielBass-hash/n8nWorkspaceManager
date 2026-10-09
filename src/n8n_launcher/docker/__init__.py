"""Docker and Docker Compose infrastructure for isolated n8n workspaces.

Entry: :class:`DockerManager` (``manager.py``) for the lifecycle,
``render_compose`` (``compose.py``) for the YAML, ``resolve_docker_command()``
for the CLI path.
Gotcha: ``docker ps`` Labels is a flat ``k=v,k=v`` string since Compose 2.35 —
see ``manager.parse_container_labels``.
Map: ``docs/architecture.md``.
"""
