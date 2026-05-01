# -*- mode: python ; coding: utf-8 -*-
import os

block_cipher = None

# AlphaServer를 클라이언트 번들 안에 임베드한다.
# AlphaServer.spec 빌드가 먼저 끝나서 dist/AlphaServer/ 가 존재해야 한다.
# 빌드 누락 시 빠르게 실패시켜 release 파이프라인에서 잡히게 한다.
_server_dist = os.path.abspath('dist/AlphaServer')
if not os.path.isdir(_server_dist):
    raise SystemExit(
        "AlphaClient.spec: dist/AlphaServer 가 없습니다. "
        "AlphaServer.spec을 먼저 빌드하세요 (pyinstaller AlphaServer.spec)."
    )

a = Analysis(
    ['alpha/main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('alpha/__init__.py', 'alpha'),
        ('alpha/core.py', 'alpha'),
        ('alpha/gui.py', 'alpha'),
        ('alpha/strategy_widgets.py', 'alpha'),
        ('alpha/server_launcher.py', 'alpha'),
        ('alpha/analysis.py', 'alpha'),
        ('alpha/data_fetcher.py', 'alpha'),
        ('alpha/portfolio.py', 'alpha'),
        ('alpha/recommender.py', 'alpha'),
        # 서버 바이너리 + 의존성 트리 전체를 임베드.
        # 사용자는 AlphaClient.app 만 끌어다 놓으면 서버까지 같이 동작.
        (_server_dist, 'AlphaServer'),
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
        'alpha.server_launcher',
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

# onedir 모드 — onefile 은 .app 번들과 결합 시 deprecated 이고, 200MB+ 서버를
# 매 실행마다 _MEIPASS 로 압축 해제하므로 런타임이 느려진다. 폴더 구조면 .app
# 안에 풀린 파일이 그대로 사용되어 즉시 기동된다.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AlphaClient',
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
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AlphaClient',
)

app = BUNDLE(
    coll,
    name='AlphaClient.app',
    icon=None,
    bundle_identifier='com.alpha.client',
    info_plist={
        'NSHighResolutionCapable': 'True',
        'LSBackgroundOnly': 'False',
    },
)
