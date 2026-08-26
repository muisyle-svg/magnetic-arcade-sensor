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
asset_directory_text = os.environ.get(
    'CHAOS_HEIST_ASSET_DIR',
    os.environ.get('MAGNET_GUARD_ASSET_DIR', ''),
).strip()
if not asset_directory_text:
    raise EnvironmentError(
        'CHAOS_HEIST_ASSET_DIR must name the media asset directory. '
        'Use build_chaos_heist.ps1, which sets it automatically.'
    )
asset_directory = Path(asset_directory_text).resolve()
if not asset_directory.is_dir():
    raise FileNotFoundError(
        'ChaosHeist asset directory does not exist: ' + str(asset_directory)
    )
runtime_config = json.loads(
    (spec_directory / 'chaos-heist-config.json').read_text(encoding='utf-8')
)
if not isinstance(runtime_config, dict):
    raise ValueError('chaos-heist-config.json must contain a JSON object')
if runtime_config.get('total_emeralds') != 7:
    raise ValueError('total_emeralds must be 7 for the production firmware')


def filename_setting(key, default):
    value = runtime_config.get(key, default)
    if (
        not isinstance(value, str)
        or not value.strip()
        or Path(value.strip()).name != value.strip()
    ):
        raise ValueError(key + ' must be a non-empty filename')
    return value.strip()


cinematic_name = filename_setting(
    'cinematic_video_file',
    'sonic-cd-opening.mp4',
)
removal_names = runtime_config.get(
    'removal_sound_files',
    ['ohh-no-the-chaos-emerald.mp3'],
)
if (
    not isinstance(removal_names, list)
    or not removal_names
    or any(
        not isinstance(name, str)
        or not name.strip()
        or Path(name.strip()).name != name.strip()
        for name in removal_names
    )
):
    raise ValueError('removal_sound_files must contain at least one filename')
removal_names = [name.strip() for name in removal_names]
last_removal_name = filename_setting(
    'last_emerald_removal_sound_file',
    'no-he-s-got-the-last-emerald.mp3',
)
story_shutdown_name = filename_setting(
    'story_shutdown_sound_file',
    'i-m-afraid-our-little-game-ends-now.mp3',
)
power_loss_audio_names = [
    filename_setting(
        'power_loss_lights_sound_file',
        'flourescent-lights-buzzing.mp3',
    ),
    filename_setting(
        'power_loss_buzz_fades_sound_file',
        'lantern-buzzes-fades.mp3',
    ),
    filename_setting(
        'power_loss_buzz_dies_sound_file',
        'lantern-whines-buzzing-dies.mp3',
    ),
    filename_setting(
        'power_loss_tv_off_sound_file',
        'tv-off.mp3',
    ),
]
ring_sound_name = filename_setting(
    'ring_sound_file',
    'ring.mp3',
)
act_clear_sound_name = filename_setting(
    'act_clear_sound_file',
    '16-act-clear.mp3',
)
final_completion_name = filename_setting(
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
    ring_sound_name,
    act_clear_sound_name,
    *removal_names,
    last_removal_name,
    story_shutdown_name,
    *power_loss_audio_names,
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
        'Missing ChaosHeist assets in '
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
debug_build = (
    os.environ.get('CHAOS_HEIST_DEBUG_BUILD') == '1'
    or os.environ.get('MAGNET_GUARD_DEBUG_BUILD') == '1'
)

tkinter_directory = python_home / 'Lib' / 'tkinter'
required_tk_paths = [
    tkinter_directory,
    python_home / 'tcl' / 'tcl8.6',
    python_home / 'tcl' / 'tk8.6',
    python_home / 'DLLs' / '_tkinter.pyd',
    python_home / 'DLLs' / 'tcl86t.dll',
    python_home / 'DLLs' / 'tk86t.dll',
]
missing_tk_paths = [
    str(path) for path in required_tk_paths if not path.exists()
]
if missing_tk_paths:
    raise FileNotFoundError(
        'Python Tk runtime is incomplete: ' + ', '.join(missing_tk_paths)
    )
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
    ['chaos_heist.py'],
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
        'ChaosHeistDebug'
        if debug_build
        else 'ChaosHeist'
    ),
    version=str(spec_directory / 'ChaosHeist.version'),
    debug='all' if debug_build else False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=debug_build,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
