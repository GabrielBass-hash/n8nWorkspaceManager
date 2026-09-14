"""Build the desktop executable with PyInstaller."""

from __future__ import annotations

import subprocess
import sys


def main() -> None:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--windowed",
        "--name",
        "n8n-launcher",
        "src/n8n_launcher/__main__.py",
    ]
    subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
