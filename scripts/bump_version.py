"""Bump the project version in pyproject.toml.

Usage: python scripts/bump_version.py <major|minor>

Version scheme: ``major.minor.build`` where ``build`` is zero-padded to two
digits and starts at ``01``.

Bump semantics (matching the dev/main CI flow):

- ``minor`` (dev pushes): increment only the build number — ``0.4.01`` ->
  ``0.4.02``. When the build is ``0`` (legacy single-digit state, e.g. the
  current ``0.3.0``), first move to the next minor and restart the build at
  ``01`` — ``0.3.0`` -> ``0.4.01``.
- ``major`` (main releases): increment the major, reset minor/build —
  ``0.4.02`` -> ``1.0.01``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_VERSION_RE = re.compile(
    r'^(?P<prefix>version\s*=\s*")(?P<major>\d+)\.(?P<minor>\d+)\.(?P<build>\d+)(")$',
    re.MULTILINE,
)


def bump(path: Path, part: str) -> str:
    text = path.read_text(encoding="utf-8")
    match = _VERSION_RE.search(text)
    if not match:
        raise SystemExit(f"cannot find version in {path}")

    major = int(match["major"])
    minor = int(match["minor"])
    build = int(match["build"])

    if part == "major":
        major += 1
        minor = 0
        build = 1
    elif part == "minor":
        if build == 0:
            minor += 1
        build += 1
    else:
        raise SystemExit(f"unknown bump type: {part!r} (expected major|minor)")

    # Rebuild the line as prefix + zero-padded version + closing quote.
    new_version = f"{major}.{minor}.{build:02d}"
    new_line = f'{match["prefix"]}{new_version}"'
    updated = "\n".join(
        new_line if line.startswith("version ") else line
        for line in text.splitlines()
    )
    path.write_text(updated, encoding="utf-8")
    print(f"bumped to {new_version}")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: bump_version.py <major|minor>")
    bump(Path("pyproject.toml"), sys.argv[1].lower())


if __name__ == "__main__":
    main()