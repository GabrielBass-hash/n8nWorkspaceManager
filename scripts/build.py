"""Build a platform-specific desktop distribution with PyInstaller.

- macOS: windowed .app bundle, ad-hoc signed, packaged into a .dmg
- Linux / Windows: single-file windowed executable
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


APP_NAME = "n8n-launcher"
ENTRY_POINT = "src/n8n_launcher/__main__.py"
BUNDLE_ID = "com.gabrielbasso.n8n-launcher"


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def build_onedir():
    _run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--windowed",
            "--name",
            APP_NAME,
            "--osx-bundle-identifier",
            BUNDLE_ID,
            ENTRY_POINT,
        ]
    )


def build_onefile():
    _run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--onefile",
            "--windowed",
            "--name",
            APP_NAME,
            ENTRY_POINT,
        ]
    )


def build_macos_dmg() -> None:
    build_onedir()
    app = Path("dist") / f"{APP_NAME}.app"
    _run(["codesign", "--force", "--deep", "--sign", "-", str(app)])
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "stage"
        stage.mkdir()
        shutil.copytree(app, stage / f"{APP_NAME}.app")
        (stage / "Applications").symlink_to("/Applications")
        dmg = Path("dist") / f"{APP_NAME}-macos.dmg"
        _run(
            [
                "hdiutil",
                "create",
                "-volname",
                f"{APP_NAME}",
                "-srcfolder",
                str(stage),
                "-ov",
                "-format",
                "UDZO",
                str(dmg),
            ]
        )


def main() -> None:
    if platform.system() == "Darwin":
        build_macos_dmg()
    else:
        build_onefile()


if __name__ == "__main__":
    main()