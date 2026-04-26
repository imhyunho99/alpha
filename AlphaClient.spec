# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['alpha/main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('alpha/__init__.py', 'alpha'),
        ('alpha/core.py', 'alpha'),
        ('alpha/gui.py', 'alpha'),
        ('alpha/strategy_widgets.py', 'alpha'),
        ('alpha/analysis.py', 'alpha'),
        ('alpha/data_fetcher.py', 'alpha'),
        ('alpha/portfolio.py', 'alpha'),
        ('alpha/recommender.py', 'alpha'),
    ],
    hiddenimports=[
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'requests',
        'json',
        'alpha',
        'alpha.core',
        'alpha.gui',
        'alpha.strategy_widgets',
        'alpha.analysis',
        'alpha.data_fetcher',
        'alpha.portfolio',
        'alpha.recommender',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='AlphaClient',
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
    icon=None,
)

app = BUNDLE(
    exe,
    name='AlphaClient.app',
    icon=None,
    bundle_identifier='com.alpha.client',
    info_plist={
        'NSHighResolutionCapable': 'True',
        'LSBackgroundOnly': 'False',
    },
)
