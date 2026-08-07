# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for MarketForge.exe - ONE-FOLDER, deliberately.

One-file unpacks to temp on every launch and makes a stdlib server feel slow;
one-folder starts instantly and, more importantly, lets the app keep its whole
product thesis: panels/, chat-*.jsonl, memory.md and bot/ ship as PLAIN FILES
next to the exe, where any agent can read and write them. Nothing the user or
an agent touches lives inside the bundle.

Build with build.py, not by hand - it also assembles the sibling files and
refuses to ship bot/.env.
"""

a = Analysis(
    ['shell.py'],
    pathex=[],
    binaries=[],
    datas=[],            # resources ship as sibling files (build.py copies them)
    hiddenimports=[
        # everything the DISK-RUN engine needs - see frozen_requirements.py
        'frozen_requirements',
        # pywebview loads its platform backend dynamically; modulegraph
        # cannot see that import happen
        'webview.platforms.winforms',
        'webview.platforms.edgechromium',
        'clr_loader',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'pywebview.platforms.gtk', 'webview.platforms.cef',
              'webview.platforms.qt', 'PyQt5', 'PySide2', 'PySide6'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MarketForge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,               # .NET/WebView2 dlls do not enjoy being packed
    console=False,           # the whole point: no terminal window
    icon='build\\MarketForge.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='MarketForge',
)
