# -*- mode: python ; coding: utf-8 -*-
"""Reproducible folder build: pyinstaller video_studio_backend.spec

Output is dist/video_studio_backend/ (the exe plus an _internal folder). A folder
build starts faster than one-file (nothing is unpacked to a temp dir on every
launch) and is flagged far less often by antivirus heuristics.

.env is deliberately NOT bundled: it holds machine-specific paths. The exe reads
an optional .env placed next to video_studio_backend.exe instead.
"""

datas = [('vs_app/web/video_studio_app.html', 'web')]

hiddenimports = [
    'aiosqlite',
    'anyio._backends._asyncio',
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.loops.asyncio',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.http.h11_impl',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
]

a = Analysis(
    ['video_studio_backend.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='video_studio_backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='video_studio_backend',
)
