import pytest

from n8n_launcher.docker.manager import DockerManager, resolve_docker_command


@pytest.fixture(scope="session")
def docker_manager() -> DockerManager:
    manager = DockerManager(command=resolve_docker_command(), timeout=180.0)
    status = manager.check_available()
    if not status.available:
        pytest.skip(f"Docker is not available: {status.message}")
    return manager
