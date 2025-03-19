import sys
from PyInstaller.utils.hooks import collect_data_files

# Get all image files
image_files = [('images/*.png', 'images')]

scipy_hidden_imports = [
    'scipy._lib.array_api_compat.numpy.fft',
    'scipy._lib.messagestream',
    'scipy.sparse.csgraph._validation',
    'scipy.special.cython_special',
]

# --------------------------------------------------

kingdom_console_a = Analysis(
    ["kingdom_scanner_console.py"],
    pathex=[],
    binaries=[],
    datas=image_files,
    hiddenimports=scipy_hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
kingdom_console_pyz = PYZ(kingdom_console_a.pure)

kingdom_console_exe = EXE(
    kingdom_console_pyz,
    kingdom_console_a.scripts,
    [],
    exclude_binaries=True,
    name="Kingdom Scanner (CLI)",
    icon="images/kingdom.png",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# --------------------------------------------------

kingdom_ui_a = Analysis(
    ["kingdom_scanner_ui.py"],
    pathex=[],
    binaries=[],
    datas=image_files,
    hiddenimports=["PyQt6"] + scipy_hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
kingdom_ui_pyz = PYZ(kingdom_ui_a.pure)

kingdom_ui_exe = EXE(
    kingdom_ui_pyz,
    kingdom_ui_a.scripts,
    [],
    exclude_binaries=True,
    name="Kingdom Scanner (GUI)",
    icon="images/kingdom.png",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# -----------------------------------------------------

unified_ui_a = Analysis(
    ["unified_scanner_ui.py"],
    pathex=[],
    binaries=[],
    datas=image_files,
    hiddenimports=["PyQt6"] + scipy_hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
unified_ui_pyz = PYZ(unified_ui_a.pure)

unified_ui_exe = EXE(
    unified_ui_pyz,
    unified_ui_a.scripts,
    [],
    exclude_binaries=True,
    name="Unified Scanner",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# -----------------------------------------------------

coll = COLLECT(
    kingdom_console_exe,
    kingdom_console_a.binaries,
    kingdom_console_a.datas,
    kingdom_ui_exe,
    kingdom_ui_a.binaries,
    kingdom_ui_a.datas,
    unified_ui_exe,
    unified_ui_a.binaries,
    unified_ui_a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="RoK Tracker",
)
