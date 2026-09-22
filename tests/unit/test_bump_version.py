"""Unit tests for scripts/bump_version.py SemVer bumping."""

from __future__ import annotations

import bump_version
import pytest

INIT_STUB = """\"\"\"pkg.\"\"\"

__version__ = "%s"
"""


def _make_version_file(tmp_path, version: str):
    """Write a fake package `__init__.py` declaring `__version__`."""
    path = tmp_path / "n8n_launcher" / "__init__.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(INIT_STUB % version, encoding="utf-8")
    return path


def _bump(tmp_path, version: str, part: str = "patch", target: str | None = None) -> str:
    path = _make_version_file(tmp_path, version)
    new = bump_version.bump(path, part, target=target)
    assert bump_version.read_version(path) == new
    return new


# --- version helpers ---------------------------------------------------------


def test_semver_parts_parses_full_triplet() -> None:
    assert bump_version.semver_parts("4.0.2") == (4, 0, 2)


def test_semver_parts_rejects_bad_input() -> None:
    for bad in ("4.0", "4.0.2.1", "v4.0.2", "4.0.x", ""):
        with pytest.raises(ValueError):
            bump_version.semver_parts(bad)


def test_read_version_missing_raises(tmp_path) -> None:
    path = tmp_path / "pkg.py"
    path.write_text("no version here\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="cannot find __version__"):
        bump_version.read_version(path)


def test_next_version_patch_increments_patch() -> None:
    assert bump_version.next_version("4.0.2", "patch") == "4.0.3"


def test_next_version_minor_resets_patch() -> None:
    assert bump_version.next_version("4.9.7", "minor") == "4.10.0"


def test_next_version_major_resets_minor_and_patch() -> None:
    assert bump_version.next_version("4.3.2", "major") == "5.0.0"


def test_next_version_unknown_part_raises() -> None:
    with pytest.raises(ValueError, match="unknown bump part"):
        bump_version.next_version("4.0.2", "build")


# --- bump behaviour ----------------------------------------------------------


def test_bump_patch(tmp_path) -> None:
    assert _bump(tmp_path, "4.0.2", "patch") == "4.0.3"


def test_bump_minor(tmp_path) -> None:
    assert _bump(tmp_path, "4.0.9", "minor") == "4.1.0"


def test_bump_major(tmp_path) -> None:
    assert _bump(tmp_path, "4.3.2", "major") == "5.0.0"


def test_bump_target_pins_exact_version(tmp_path) -> None:
    assert _bump(tmp_path, "4.0.2", target="4.5.0") == "4.5.0"


def test_bump_target_noop_raises(tmp_path) -> None:
    path = _make_version_file(tmp_path, "4.0.2")
    with pytest.raises(ValueError, match="already at"):
        bump_version.bump(path, "patch", target="4.0.2")


def test_write_version_rejects_invalid(tmp_path) -> None:
    path = _make_version_file(tmp_path, "4.0.2")
    with pytest.raises(ValueError):
        bump_version.write_version("not-a-version", path)


def test_package_version_is_valid_semver() -> None:
    from n8n_launcher import __version__

    assert bump_version.semver_parts(__version__) == tuple(
        int(part) for part in __version__.split(".")
    )


def test_package_version_has_no_padding() -> None:
    from n8n_launcher import __version__

    assert __version__ == ".".join(str(int(part)) for part in __version__.split("."))
