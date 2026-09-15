from urllib.parse import unquote

from n8n_launcher.models import DbConfig, DbMode, Workspace
from n8n_launcher.n8n_setup import build_external_connection_string, data_db_target


def test_build_external_connection_string_defaults_port(tmp_path) -> None:
    dsn = build_external_connection_string(
        host="db.example", port="", database="app", user="launcher", password="p$ss"
    )

    parsed_user, parsed_password = dsn.split("postgresql://", 1)[1].split("@")[0].split(":")
    assert parsed_user == "launcher"
    assert unquote(parsed_password) == "p$ss"
    assert dsn.endswith("@db.example:5432/app")


def test_build_external_connection_string_encodes_special_characters(tmp_path) -> None:
    dsn = build_external_connection_string(
        host="db.example", port="5433", database="app", user="user name", password="p@ss/word"
    )

    parsed = dsn.split("postgresql://", 1)[1]
    credentials, host_part = parsed.split("@")
    assert unquote(credentials.split(":", 1)[0]) == "user name"
    assert unquote(credentials.split(":", 1)[1]) == "p@ss/word"
    assert host_part == "db.example:5433/app"


def test_build_external_connection_string_accepts_int_port(tmp_path) -> None:
    dsn = build_external_connection_string(
        host="db.example", port=5544, database="app", user="user", password="pass"
    )

    assert "@db.example:5544/app" in dsn


def test_data_db_target_round_trips_percent_encoded_remote_credentials(tmp_path) -> None:
    workspace = Workspace(
        id="w1",
        name="Remote",
        workflows_dir=tmp_path,
        port=5678,
        db=DbConfig(
            DbMode.EXTERNAL,
            connection_string=build_external_connection_string(
                host="db.example", port="5432", database="app", user="user name", password="p@ss/word"
            ),
        ),
    )

    target = data_db_target(workspace)

    assert target is not None
    assert target.host == "db.example"
    assert target.port == 5432
    assert target.database == "app"
    assert target.user == "user name"
    assert target.password == "p@ss/word"