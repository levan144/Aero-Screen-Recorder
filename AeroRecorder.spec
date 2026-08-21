from pathlib import Path


project_root = Path(SPECPATH)
portable_binaries = []
portable_data = []
ffmpeg = project_root / "tools" / "ffmpeg.exe"
if ffmpeg.exists():
    portable_binaries.append((str(ffmpeg), "tools"))
ffmpeg_license = project_root / "tools" / "FFMPEG-LICENSE.txt"
if ffmpeg_license.exists():
    portable_data.append((str(ffmpeg_license), "tools"))

a = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root)],
    binaries=portable_binaries,
    datas=portable_data,
    hiddenimports=["pyaudiowpatch"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AeroRecorder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(project_root / "assets" / "AeroRecorder.ico"),
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    manifest=str(project_root / "packaging" / "app.manifest"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="AeroRecorder",
)
