"""Build a platform-specific desktop distribution with PyInstaller.

macOS: windowed ``.app`` bundle with a real icon and a polished Info.plist,
ad-hoc signed deepest-first, then packaged into a styled drag-and-drop ``.dmg``
rendered with ``dmgbuild`` (pure Python — writes the ``.DS_Store`` directly and
works on headless CI runners, no Finder required).

Linux / Windows: a windowed **onedir** directory. A single-file build would
have to unpack Qt and its platform plugin into a temporary directory on every
launch — which is slow, breaks relative plugin paths and is what AppImage is
for anyway — so the shipped layout is a directory the packager wraps.
"""

from __future__ import annotations

import platform
import plistlib
import shutil
import struct
import subprocess
import sys
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

# macOS 12 is the floor: it is the oldest release where the Qt 6 Cocoa build
# still loads, and advertising a higher floor only refuses working machines.
MACOS_MIN_VERSION = "12.0"


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

    raw = b"".join(b"\x00" + b"".join(struct.pack("BBBB", *pixel) for pixel in row) for row in rows)
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
    "n8n_launcher.__main__",
    "n8n_launcher.core",
    "n8n_launcher.core.config",
    "n8n_launcher.core.filelock",
    "n8n_launcher.core.first_launch",
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
    "n8n_launcher.github",
    "n8n_launcher.github.api",
    "n8n_launcher.github.auth",
    "n8n_launcher.gui",
    "n8n_launcher.gui.actions",
    "n8n_launcher.gui.app",
    "n8n_launcher.gui.card_delegate",
    "n8n_launcher.gui.create_panel",
    "n8n_launcher.gui.first_launch",
    "n8n_launcher.gui.notifier",
    "n8n_launcher.gui.theme",
    "n8n_launcher.gui.window",
    "n8n_launcher.gui.workspace_model",
    "n8n_launcher.gui_utils.text",
    "n8n_launcher.monitoring.bootstrap",
    "n8n_launcher.monitoring.redaction",
    "n8n_launcher.monitoring.store",
    "n8n_launcher.n8n",
    "n8n_launcher.n8n.api",
    "n8n_launcher.n8n.owner",
    "n8n_launcher.n8n.workflows",
    "n8n_launcher.platform",
    "n8n_launcher.platform.ports",
    "n8n_launcher.platform.update_flow",
    "n8n_launcher.platform.updater",
    "n8n_launcher.remote.deploy",
    "n8n_launcher.remote.ssh",
    "n8n_launcher.workspaces",
    "n8n_launcher.workspaces.ci",
    "n8n_launcher.workspaces.close",
    "n8n_launcher.workspaces.dialogs",
    "n8n_launcher.workspaces.manager",
    "n8n_launcher.workspaces.status",
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
    """Build a windowed onedir distribution and return its root directory.

    PyInstaller's onedir layout does not require unpacking the Qt platform
    plugin on every launch (a single-file archive does, which is slow, brittle
    under AppImage and read-only mounts — and what AppImage is for anyway).

    PyInstaller lays it out as ``dist/<name>.app`` on macOS and ``dist/<name>``
    elsewhere, and the returned root is exactly that directory: the AppImage
    step wraps it, and ``--verify`` is pointed at the same path in CI.
    """
    system = platform.system()
    dist = PROJECT_ROOT / "dist"
    target = dist / f"{APP_NAME}.app" if system == "Darwin" else dist / APP_NAME
    if target.exists():
        shutil.rmtree(target)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--windowed",
        "--onedir",
        "--name",
        APP_NAME,
        "--paths",
        str(PROJECT_ROOT / "src"),
        *_hidden_import_args(),
    ]
    if system == "Darwin":
        command += ["--icon", str(build_icns()), "--osx-bundle-identifier", BUNDLE_ID]
    command.append(str(ENTRY_POINT))
    _run(command)

    if not target.exists():
        raise FileNotFoundError(f"PyInstaller did not produce {target}")
    return target


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
            "CFBundleMinimumSystemVersion": MACOS_MIN_VERSION,
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
    locations = ", ".join(f"'{name}': ({x}, {y})" for name, (x, y) in DMG_ICON_POSITIONS.items())
    settings.write_text(
        f"app_name = '{DISPLAY_NAME}'\n"
        f"files = ['{app.resolve()}']\n"
        "symlinks = {'Applications': '/Applications'}\n"
        f"icon = '{icns.resolve()}'\n"
        f"background = '{background.resolve()}'\n"
        f"window_rect = (({left}, {top}), ({right}, {bottom}))\n"
        "icon_size = 110\n"
        "text_size = 12\n"
        f"icon_locations = {{{locations}}}\n"
        "format = 'UDZO'\n",
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


def find_platform_plugin(root: Path) -> Path | None:
    """Locate the Qt platform-plugin directory inside a built distribution.

    PyInstaller names it ``platforms`` under the PySide6 Qt directory; its
    exact depth moved between hooks, so it is searched for rather than
    hard-coded. ``None`` means the distribution cannot open a window at all.
    """
    for candidate in sorted(root.rglob("platforms")):
        if candidate.is_dir() and any(candidate.iterdir()):
            return candidate
    return None


def verify_qt_bundle(root: Path) -> list[str]:
    """Return the reasons ``root`` would fail to start, empty when it is sound.

    A bundle missing its Qt platform plugin launches and dies silently: no
    window, no dialog, and nothing useful in a log — the first-run experience
    becomes "the app does nothing". Checking here turns that into a build
    failure that names the missing piece.
    """
    problems: list[str] = []
    if find_platform_plugin(root) is None:
        problems.append("Qt platform plugin (a non-empty 'platforms' directory)")
    return problems


def is_signable_bundle(path: Path) -> bool:
    """Return True when *path* is a directory ``codesign`` accepts as a bundle.

    A directory is only a bundle if it says so: an ``.app``/``.framework``
    suffix, or an ``Info.plist`` under ``Contents/``. ``codesign`` refuses
    anything else with "bundle format unrecognized, invalid, or unsuitable".
    """
    return path.suffix in {".app", ".framework"} or (path / "Contents" / "Info.plist").is_file()


def sign_macos_bundle(app: Path) -> None:
    """Ad-hoc sign the bundle, deepest nested code first.

    Apple's ``--deep`` is documented as unreliable for *signing* (it is fine
    for verifying): the recommended order is nested frameworks, then their
    dylibs, then the outer bundle. Qt alone ships a dozen dylibs and plugin
    bundles under ``Contents/Frameworks``, so signing them in one pass with
    ``--deep`` produced bundles that macOS refused to launch.
    """
    contents = app / "Contents"
    # dylibs and Qt plugin bundles live under Contents/Frameworks; sort by depth
    # descending so a bundle is signed after everything it contains.
    targets = sorted(
        (path for path in contents.rglob("*") if path.suffix in {".dylib", ".so"}),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    frameworks = contents / "Frameworks"
    if frameworks.is_dir():
        # PyInstaller puts plain directories in there too — the interpreter's
        # extension modules (``python3__dot__12``), the Qt plugin folders,
        # ``bcrypt`` — and codesign rejects a directory that is not a bundle.
        # Their Mach-O files are signed by the walk above and the outer seal
        # covers the tree, so only real bundles are sealed here.
        targets += sorted(
            (
                path
                for path in frameworks.iterdir()
                if not path.is_dir() or is_signable_bundle(path)
            ),
            key=lambda path: len(path.parts),
            reverse=True,
        )
    for path in targets:
        _run(["codesign", "--force", "--sign", "-", str(path)])
    _run(["codesign", "--force", "--sign", "-", str(app)])


def verify_or_fail(root: Path) -> None:
    """Raise with every reason ``root`` would fail to start, or confirm it is sound."""
    problems = verify_qt_bundle(root)
    if problems:
        raise RuntimeError(f"incomplete distribution {root}: " + ", ".join(problems))
    print(f"Qt bundle verified: {root}")


def build_macos_dmg() -> None:
    app = build_onedir()
    patch_info_plist(app)
    sign_macos_bundle(app)
    _run(["codesign", "--verify", "--deep", "--strict", str(app)])
    verify_or_fail(app)
    build_dmg(app)


def main() -> None:
    """Build the artifact for this platform, or verify one that already exists.

    ``--verify <dist-root>`` only runs the Qt completeness check, which is how
    CI asserts on a freshly built artifact (and on the AppDir copy of it)
    without rebuilding.
    """
    if len(sys.argv) == 3 and sys.argv[1] == "--verify":
        verify_or_fail(Path(sys.argv[2]))
        return
    if platform.system() == "Darwin":
        build_macos_dmg()
    else:
        root = build_onedir()
        verify_or_fail(root)
        print(f"built {root}")


if __name__ == "__main__":
    main()
