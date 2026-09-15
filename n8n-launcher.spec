# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['run.py'],
    pathex=['src'],
    binaries=[],
    datas=[],
    hiddenimports=['n8n_launcher.gui', 'n8n_launcher.api_client', 'n8n_launcher.browser', 'n8n_launcher.config', 'n8n_launcher.db_manager', 'n8n_launcher.docker_manager', 'n8n_launcher.models', 'n8n_launcher.sync_runner', 'n8n_launcher.workspace_info', 'n8n_launcher.workspace_manager', 'n8n_launcher.owner_setup', 'n8n_launcher.n8n_setup', 'n8n_launcher.paths', 'n8n_launcher.setup_wizard', 'n8n_launcher.shortcuts', 'n8n_launcher.database', 'n8n_launcher.git_manager', 'n8n_launcher.updater', 'tkinter', 'tkinter.ttk', 'tkinter.filedialog', 'tkinter.messagebox', 'tkinter.simpledialog', 'requests', 'bcrypt', 'platformdirs'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='n8n-launcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
