"""Desktop launcher for isolated n8n workspaces."""

#: Single source of truth for the launcher version (SemVer ``MAJOR.MINOR.PATCH``).
#: ``pyproject.toml`` declares ``dynamic = ["version"]`` and reads it from here,
#: ``scripts/build.py`` embeds it into the bundle and ``platform/updater.py``
#: compares it against GitHub releases. Edit it by hand before a release.
__version__ = "4.0.2"
