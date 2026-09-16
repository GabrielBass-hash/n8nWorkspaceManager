from unittest.mock import patch

from n8n_launcher.platform.ports import suggest_port


def test_suggest_port_skips_reserved_and_busy_ports() -> None:
    with patch("n8n_launcher.platform.ports.is_port_available", side_effect=[False, True]):
        assert suggest_port(5678, reserved={5679}, limit=3) == 5680