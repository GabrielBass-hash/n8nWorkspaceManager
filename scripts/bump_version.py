"""Bump the launcher version in its single source of truth.

The version lives in exactly one place — ``src/n8n_launcher/__init__.py``
(``__version__``). ``pyproject.toml`` reads it via
``[tool.setuptools.dynamic] version = {attr = "n8n_launcher.__version__"}``,
``scripts/build.py`` embeds it into the bundle and ``platform/updater.py``
compares it against GitHub releases, so bumping once keeps every consumer
consistent.

Version scheme: SemVer ``MAJOR.MINOR.PATCH``.

Usage::

    python scripts/bump_version.py patch          # 4.0.2 -> 4.0.3
    python scripts/bump_version.py minor          # 4.0.3 -> 4.1.0
    python scripts/bump_version.py major          # 4.1.0 -> 5.0.0
    python scripts/bump_version.py --to 4.2.0     # pin an exact version
    python scripts/bump_version.py --dry-run patch  # print what would change

Guards (bypassed with ``--force``): refuse to run on a dirty working tree so a
half-published bump cannot be committed by accident, and refuse to land on a
version whose ``v<version>`` tag already exists so a release is never
duplicated.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = PROJECT_ROOT / "src" / "n8n_launcher" / "__init__.py"

_VERSION_RE = re.compile(
    r'^__version__\s*=\s*"(?P<version>\d+\.\d+\.\d+)"\s*$',
    re.MULTILINE,
)
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
PARTS = ("patch", "minor", "major")


def semver_parts(text: str) -> tuple[int, int, int]:
    """Parse a strict ``MAJOR.MINOR.PATCH`` string into integers."""
    if not _SEMVER_RE.match(text):
        raise ValueError(f"invalid SemVer version: {text!r}")
    return tuple(int(part) for part in text.split("."))  # type: ignore[return-value]


def read_version(path: Path = VERSION_FILE) -> str:
    """Return the ``__version__`` currently declared in ``path``."""
    match = _VERSION_RE.search(path.read_text(encoding="utf-8"))
    if not match:
        raise SystemExit(f"cannot find __version__ in {path}")
    return match["version"]


def next_version(current: str, part: str) -> str:
    """Return the SemVer successor of ``current`` for the requested part."""
    if part not in PARTS:
        raise ValueError(
            f"unknown bump part {part!r} (expected {'|'.join(PARTS)})"
        )
    major, minor, patch = semver_parts(current)
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major + 1}.0.0"


def write_version(version: str, path: Path = VERSION_FILE) -> None:
    """Replace the ``__version__`` line in ``path`` with ``version``."""
    semver_parts(version)
    text = path.read_text(encoding="utf-8")
    updated, count = _VERSION_RE.subn(f'__version__ = "{version}"', text, count=1)
    if count != 1:
        raise SystemExit(f"cannot find __version__ in {path}")
    path.write_text(updated, encoding="utf-8")


def bump(path: Path, part: str, target: str | None = None) -> str:
    """Bump ``path`` and return the new version (or raise if unchanged).

    ``part`` may be ``patch|minor|major``, or ``target`` pins an explicit
    SemVer. Refusing a no-op release keeps CI from tagging twice.
    """
    current = read_version(path)
    new = target if target is not None else next_version(current, part)
    if new == current:
        raise ValueError(f"version already at {current}")
    write_version(new, path)
    return new


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), *args],
        check=check,
        text=True,
        capture_output=True,
    )


def _check_guards(new_version: str) -> None:
    """Refuse to bump when the tree is dirty or the tag already exists."""
    dirty = _git("status", "--porcelain", check=False).stdout.strip()
    if dirty:
        raise SystemExit(
            "working tree is dirty — commit or stash changes before bumping "
            "(override with --force)"
        )
    existing = _git("tag", "-l", f"v{new_version}", check=False).stdout.strip()
    if existing:
        raise SystemExit(
            f"tag v{new_version} already exists — this version was released "
            "(override with --force)"
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="bump_version.py",
        description="Bump the launcher SemVer in src/n8n_launcher/__init__.py.",
    )
    parser.add_argument(
        "part",
        nargs="?",
        choices=PARTS,
        help="which part of MAJOR.MINOR.PATCH to increment",
    )
    parser.add_argument(
        "--to",
        metavar="X.Y.Z",
        help="pin an exact SemVer version instead of a relative bump",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the new version, write nothing"
    )
    parser.add_argument(
        "--force", action="store_true", help="skip the dirty-tree and tag guards"
    )
    args = parser.parse_args(argv)

    if (args.part is None) == (args.to is None):
        parser.error("exactly one of <part> or --to is required")

    current = read_version()
    new = args.to if args.to else next_version(current, args.part)

    if not args.dry_run:
        if not args.force:
            _check_guards(new)
        if new == current:
            raise SystemExit(f"version already at {current} — nothing to do")
        write_version(new)
    print(f"{current} -> {new}")


if __name__ == "__main__":
    main(sys.argv[1:])