# -*- mode: python ; coding: utf-8 -*-
# Onedir build. A onefile build extracts python3xx.dll to %TEMP% at launch,
# which is blocked on machines with an Application Control policy (WDAC /
# Smart App Control). Onedir keeps the DLLs beside the exe in a normal folder,
# so the app runs there and can be copied anywhere -- including OneDrive.


a = Analysis(
    ['mouse-jiggler.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['pystray._win32', 'PIL._tkinter_finder', 'PIL.ImageTk'],
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
    [],
    exclude_binaries=True,
    name='ZenMouseJiggler',
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

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ZenMouseJiggler',
)
