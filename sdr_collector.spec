# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['sdr_collector.py'],
    pathex=[],
    binaries=[('librtlsdr.dll', '.'), ('libgcc_s_seh-1.dll', '.'), ('libiconv-2.dll', '.')],
    datas=[],
    hiddenimports=['scipy.signal', 'scipy._lib.messagestream', 'sqlalchemy.dialects.postgresql', 'psycopg2'],
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
    name='sdr_collector',
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
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='sdr_collector',
)
