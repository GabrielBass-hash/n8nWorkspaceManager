"""Unit tests for scripts/bump_version.py version bumping."""

from __future__ import annotations

import pytest

import bump_version

PROJECT_TOML = """
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "n8n-launcher"
version = "0.3.0"
description = "test"
"""


def _bump(version: str, part: str, tmp_path) -> str:
    path = tmp_path / "pyproject.toml"
    path.write_text(PROJECT_TOML.replace("0.3.0", version), encoding="utf-8")
    bump_version.bump(path, part)
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("version "):
            return line.split('"')[1]
    raise AssertionError("version line missing after bump")


def test_minor_bump_from_legacy_zero_build(tmp_path) -> None:
    assert _bump("0.3.0", "minor", tmp_path) == "0.4.01"


def test_minor_bump_increments_build(tmp_path) -> None:
    assert _bump("0.4.01", "minor", tmp_path) == "0.4.02"


def test_minor_bump_crosses_double_digit_build(tmp_path) -> None:
    assert _bump("0.4.09", "minor", tmp_path) == "0.4.10"


def test_minor_bump_keeps_major(tmp_path) -> None:
    assert _bump("1.0.01", "minor", tmp_path) == "1.0.02"


def test_major_bump_resets_minor_and_build(tmp_path) -> None:
    assert _bump("0.4.02", "major", tmp_path) == "1.0.01"


def test_major_bump_increments_existing_major(tmp_path) -> None:
    assert _bump("1.0.01", "major", tmp_path) == "2.0.01"


def test_bump_invalid_part_raises(tmp_path) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text(PROJECT_TOML, encoding="utf-8")
    with pytest.raises(SystemExit, match="unknown bump type"):
        bump_version.bump(path, "patch")


def test_bump_missing_version_raises(tmp_path) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text("[project]\nname = \"n8n-launcher\"\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="cannot find version"):
        bump_version.bump(path, "minor")