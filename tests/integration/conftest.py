import pytest

from n8n_launcher.docker.manager import DockerManager


@pytest.fixture(scope="session")
def docker_manager() -> DockerManager:
    manager = DockerManager(timeout=180.0)
    status = manager.check_available()
    if not status.available:
        pytest.skip(f"Docker is not available: {status.message}")
    return manager