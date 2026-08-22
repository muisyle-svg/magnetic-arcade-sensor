# -*- mode: python ; coding: utf-8 -*-
import os
import sys
import json
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

python_home = Path(sys.executable).resolve().parent
spec_directory = Path(SPECPATH).resolve()
if spec_directory.is_file():
    spec_directory = spec_directory.parent
asset_directory = Path(
    os.environ.get(
        'MAGNET_GUARD_ASSET_DIR',
        str(spec_directory.parent.parent / 'Emerald'),
    )
).resolve()
runtime_config = json.loads(
    (spec_directory / 'guard-config.json').read_text(encoding='utf-8')
)
cinematic_name = runtime_config.get(
    'cinematic_video_file',
    'sonic-cd-opening.mp4',
)
removal_names = runtime_config.get(
    'removal_sound_files',
    ['ohh-no-the-chaos-emerald.mp3'],
)
if not isinstance(removal_names, list):
    removal_names = [
        runtime_config.get(
            'removal_sound_file',
            'ohh-no-the-chaos-emerald.mp3',
        )
    ]
last_removal_name = runtime_config.get(
    'last_emerald_removal_sound_file',
    'no-he-s-got-the-last-emerald.mp3',
)
final_completion_name = runtime_config.get(
    'final_completion_sound_file',
    'i-ll-show-you-what-the-chaos-emeralds-can-really-do.mp3',
)
asset_names = [
    'egg-man-robotnik.gif',
    'sonic-sonic-the-hedgehog.gif',
    'supersonic.gif',
    '27. Sonic the Hedgehog Victory Theme.mp3',
    'Dr Robotniks Theme.mp3',
    'emerald.mp3',
    *removal_names,
    last_removal_name,
    final_completion_name,
    'so-egg-man-s-behind-this-huh.mp3',
    cinematic_name,
]
missing_assets = [
    name for name in asset_names
    if not (asset_directory / name).is_file()
]
if missing_assets:
    raise FileNotFoundError(
        'Missing guard assets in '
        + str(asset_directory)
        + ': '
        + ', '.join(missing_assets)
    )

datas = [
    (str(asset_directory / asset_name), '.')
    for asset_name in asset_names
]
binaries = []
hiddenimports = []
debug_build = os.environ.get('MAGNET_GUARD_DEBUG_BUILD') == '1'

tkinter_directory = python_home / 'Lib' / 'tkinter'
datas += [(str(tkinter_directory), 'tkinter')]
datas += [
    (str(python_home / 'tcl' / 'tcl8.6'), 'tcl/tcl8.6'),
    (str(python_home / 'tcl' / 'tk8.6'), 'tcl/tk8.6'),
]
binaries += [
    (str(python_home / 'DLLs' / '_tkinter.pyd'), '.'),
    (str(python_home / 'DLLs' / 'tcl86t.dll'), '.'),
    (str(python_home / 'DLLs' / 'tk86t.dll'), '.'),
]

tmp_ret = collect_all('PIL')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pygame')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pycaw')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('comtypes')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('av')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['magnet_arcade_guard.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['tk_runtime_hook.py'],
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
    name=(
        'MagnetArcadeGuardStoryModeDebug'
        if debug_build
        else 'MagnetArcadeGuardStoryMode'
    ),
    debug='all' if debug_build else False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=debug_build,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
