"""Build a platform-specific desktop distribution with PyInstaller.

macOS: windowed .app bundle with a real icon and a polished Info.plist,
ad-hoc signed, then packaged into a styled drag-and-drop .dmg rendered with
``dmgbuild`` (pure Python — writes the ``.DS_Store`` directly and works on
headless CI runners, no Finder required).

Linux / Windows: single-file windowed executable.
"""

from __future__ import annotations

import plistlib
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

# Repository root, resolved from this file so the build works from any CWD.
PROJECT_ROOT = Path(__file__).resolve().parents[1]

APP_NAME = "n8n-launcher"
DISPLAY_NAME = "n8n Launcher"
BUNDLE_ID = "io.launcher.n8n"
# Thin root entry point used instead of src/n8n_launcher/__main__.py:
# PyInstaller runs it correctly while the package keeps its relative imports.
ENTRY_POINT = PROJECT_ROOT / "run.py"
ICON_SOURCE = PROJECT_ROOT / "assets" / "icon.png"

DMG_WINDOW_RECT = ((60, 80), (720, 490))  # 660 x 410
DMG_BACKGROUND_SIZE = (660, 410)
DMG_ICON_POSITIONS = {"n8n-launcher.app": (450, 170), "Applications": (165, 170)}


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def project_version() -> str:
    """Read the version from the package's single source of truth."""
    src = str(PROJECT_ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from n8n_launcher import __version__

    return __version__


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def render_background(path: Path) -> None:
    """Write the subtle dark gradient shown behind the DMG icons."""
    width, height = DMG_BACKGROUND_SIZE

    def glow(x: int, y: int, cx: float, cy: float) -> float:
        falloff = 190.0
        dx, dy = x - cx, y - cy
        return max(0.0, 1.0 - (dx * dx + dy * dy) / (falloff * falloff))

    rows = []
    for y in range(height):
        row = []
        for x in range(width):
            top = (58, 65, 73)
            bottom = (24, 27, 32)
            mix = y / max(height - 1, 1)
            base = tuple(round(top[c] + (bottom[c] - top[c]) * mix) for c in range(3))
            horizontal = 0.82 + 0.18 * (x / max(width - 1, 1))
            left_glow = glow(x, y, 197, 204)
            right_glow = glow(x, y, 478, 208)
            lift = 26 * (left_glow + right_glow)
            color = tuple(round(min(max(c * horizontal + lift, 0), 255)) for c in base)
            row.append((*color, 255))
        rows.append(row)

    raw = b"".join(
        b"\x00" + b"".join(struct.pack("BBBB", *pixel) for pixel in row) for row in rows
    )
    png = b"\x89PNG\r\n\x1a\n"
    png += _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    png += _png_chunk(b"IDAT", zlib.compress(raw, 9))
    png += _png_chunk(b"IEND", b"")
    path.write_bytes(png)


def build_icns() -> Path:
    """Render ``assets/icon.png`` into a full-resolution ``.icns``."""
    scratch = PROJECT_ROOT / "build" / "icon"
    iconset = scratch / "icon.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir(parents=True)
    sizes = {
        "icon_16x16.png": 16,
        "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32,
        "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128,
        "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256,
        "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512,
        "icon_512x512@2x.png": 1024,
    }
    for name, size in sizes.items():
        _run(["sips", "-z", str(size), str(size), str(ICON_SOURCE), "--out", str(iconset / name)])
    icns = scratch / "icon.icns"
    _run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)])
    return icns


HIDDEN_IMPORTS = [
    "n8n_launcher.core",
    "n8n_launcher.core.config",
    "n8n_launcher.core.models",
    "n8n_launcher.core.paths",
    "n8n_launcher.database",
    "n8n_launcher.database.credentials",
    "n8n_launcher.database.layout",
    "n8n_launcher.database.migrations",
    "n8n_launcher.docker",
    "n8n_launcher.docker.compose",
    "n8n_launcher.docker.manager",
    "n8n_launcher.git",
    "n8n_launcher.git.manager",
    "n8n_launcher.gui",
    "n8n_launcher.gui.app",
    "n8n_launcher.gui.close",
    "n8n_launcher.gui.dialogs",
    "n8n_launcher.gui.display",
    "n8n_launcher.gui.first_launch",
    "n8n_launcher.gui.theme",
    "n8n_launcher.gui.update_flow",
    "n8n_launcher.n8n",
    "n8n_launcher.n8n.api",
    "n8n_launcher.n8n.owner",
    "n8n_launcher.n8n.workflows",
    "n8n_launcher.platform",
    "n8n_launcher.platform.browser",
    "n8n_launcher.platform.ports",
    "n8n_launcher.platform.shortcuts",
    "n8n_launcher.platform.updater",
    "n8n_launcher.workspaces",
    "n8n_launcher.workspaces.manager",
    "tkinter",
    "tkinter.ttk",
    "tkinter.filedialog",
    "tkinter.messagebox",
    "tkinter.simpledialog",
    "requests",
    "bcrypt",
    "platformdirs",
]


def _hidden_import_args() -> list[str]:
    args = []
    for imp in HIDDEN_IMPORTS:
        args.extend(["--hidden-import", imp])
    return args


def build_onedir() -> Path:
    app = PROJECT_ROOT / "dist" / f"{APP_NAME}.app"
    if app.exists():
        shutil.rmtree(app)
    _run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--windowed",
            "--name",
            APP_NAME,
            "--icon",
            str(build_icns()),
            "--osx-bundle-identifier",
            BUNDLE_ID,
            "--paths",
            str(PROJECT_ROOT / "src"),
            *_hidden_import_args(),
            str(ENTRY_POINT),
        ]
    )
    return app


def build_onefile() -> None:
    _run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--onefile",
            "--windowed",
            "--name",
            APP_NAME,
            "--paths",
            str(PROJECT_ROOT / "src"),
            *_hidden_import_args(),
            str(ENTRY_POINT),
        ]
    )


def patch_info_plist(app: Path) -> None:
    """Fill in the bundle metadata a well-behaved macOS app needs."""
    plist_path = app / "Contents" / "Info.plist"
    with plist_path.open("rb") as handle:
        info = plistlib.load(handle)
    version = project_version()
    info.update(
        {
            "CFBundleDisplayName": DISPLAY_NAME,
            "CFBundleName": DISPLAY_NAME,
            "CFBundleShortVersionString": version,
            "CFBundleVersion": version,
            "CFBundlePackageType": "APPL",
            "NSPrincipalClass": "NSApplication",
            "NSHighResolutionCapable": True,
            "CFBundleMinimumSystemVersion": "15.0",
            "LSApplicationCategoryType": "public.app-category.productivity",
        }
    )
    with plist_path.open("wb") as handle:
        plistlib.dump(info, handle)


def build_dmg(app: Path) -> None:
    """Package the signed .app into a styled drag-and-drop .dmg via dmgbuild."""
    scratch = PROJECT_ROOT / "build" / "dmg"
    scratch.mkdir(parents=True, exist_ok=True)
    icns = PROJECT_ROOT / "build" / "icon" / "icon.icns"
    background = scratch / "background.png"
    render_background(background)
    settings = scratch / "settings.py"
    left, top = DMG_WINDOW_RECT[0]
    right, bottom = DMG_WINDOW_RECT[1]
    locations = ", ".join(
        f"'{name}': ({x}, {y})" for name, (x, y) in DMG_ICON_POSITIONS.items()
    )
    settings.write_text(
        "app_name = '%s'\n"
        "files = ['%s']\n"
        "symlinks = {'Applications': '/Applications'}\n"
        "icon = '%s'\n"
        "background = '%s'\n"
        "window_rect = ((%d, %d), (%d, %d))\n"
        "icon_size = 110\n"
        "text_size = 12\n"
        "icon_locations = {%s}\n"
        "format = 'UDZO'\n"
        % (
            DISPLAY_NAME,
            app.resolve(),
            icns.resolve(),
            background.resolve(),
            left,
            top,
            right,
            bottom,
            locations,
        ),
        encoding="utf-8",
    )
    dmg = PROJECT_ROOT / "dist" / f"{APP_NAME}-macos.dmg"
    if dmg.exists():
        dmg.unlink()
    _run(
        [
            sys.executable,
            "-m",
            "dmgbuild",
            "-s",
            str(settings),
            "--no-hidpi",
            DISPLAY_NAME,
            str(dmg),
        ]
    )


def build_macos_dmg() -> None:
    app = build_onedir()
    patch_info_plist(app)
    _run(["codesign", "--force", "--deep", "--sign", "-", str(app)])
    _run(["codesign", "--verify", "--deep", "--strict", str(app)])
    build_dmg(app)


def main() -> None:
    if platform.system() == "Darwin":
        build_macos_dmg()
    else:
        build_onefile()


if __name__ == "__main__":
    main()