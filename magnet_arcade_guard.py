import ctypes
import json
import os
import queue
import random
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Optional


def bootstrap_event(message: str) -> None:
    """Record packaged startup stages before the main logger is available."""
    if os.environ.get("MAGNET_GUARD_BOOT_TRACE") == "1":
        print(
            f"BOOTSTRAP frozen={getattr(sys, 'frozen', False)!r} "
            f"pid={os.getpid()} {message}",
            flush=True,
        )
    if not getattr(sys, "frozen", False):
        return
    try:
        local_app_data = Path(
            os.environ.get(
                "LOCALAPPDATA",
                str(Path.home() / "AppData" / "Local"),
            )
        )
        path = (
            local_app_data
            / "MagnetArcadeGuard"
            / "guard-bootstrap.log"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as log_file:
            log_file.write(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} | "
                f"pid={os.getpid()} | {message}\n"
            )
    except OSError:
        pass


bootstrap_event("loading Tk support")
import tkinter as tk
import tkinter.font as tkfont

bootstrap_event("loading serial support")
import serial
from serial import SerialException
from serial.tools import list_ports

bootstrap_event("loading image support")

try:
    from PIL import Image, ImageSequence, ImageTk

    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import av

    AV_AVAILABLE = True
except ImportError:
    AV_AVAILABLE = False

try:
    import pygame

    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False

bootstrap_event("loading Windows audio support")
try:
    from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume

    PYCAW_AVAILABLE = True
except ImportError:
    PYCAW_AVAILABLE = False

bootstrap_event("module imports complete")


DEFAULT_CONFIG = {
    "total_emeralds": 7,
    "auto_activate": False,
    "serial_port": "",
    "big_box_ready_delay_seconds": 1.5,
    "sensor_stable_ms": 60,
    "final_emerald_pause_ms": 150,
    "sound_effect_cooldown_ms": 350,
    "counter_flash_ms": 300,
    "robotnik_fade_ms": 250,
    "music_volume": 0.75,
    "sound_effect_volume": 1.0,
    "removal_sound_files": [
        "ohh-no-the-chaos-emerald.mp3",
        "ohh-no.mp3",
        "ohh-now-what.mp3",
        "stop.mp3",
    ],
    "last_emerald_removal_sound_file": (
        "no-he-s-got-the-last-emerald.mp3"
    ),
    "final_completion_sound_file": (
        "i-ll-show-you-what-the-chaos-emeralds-can-really-do.mp3"
    ),
    "default_mode": "story",
    "normal_warning_seconds": 10.0,
    "story_announcement_seconds": 2.5,
    "story_shutdown_seconds": 5.0,
    "story_question_seconds": 6.0,
    "story_eggman_seconds": 3.0,
    "cinematic_fade_seconds": 0.8,
    "ring_joystick_button": 10,
    "ring_debounce_ms": 90,
    "ring_game_commit_seconds": 3.0,
    "ring_announcement_seconds": 5.0,
    "cinematic_max_fps": 15,
    "cinematic_video_file": (
        "2015-02-19-SonictheHedgehogCD-Opening"
        "(SonicBoomNAVersion).mp4.7fb0570ab57510d4a7d54beb920f6517.mp4"
    ),
}


def application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def load_runtime_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    config_path = application_directory() / "guard-config.json"
    try:
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            config.update(loaded)
    except (FileNotFoundError, OSError, ValueError, TypeError):
        pass
    return config


RUNTIME_CONFIG = load_runtime_config()
try:
    TOTAL_EMERALDS = max(
        1,
        min(12, int(RUNTIME_CONFIG["total_emeralds"])),
    )
except (KeyError, TypeError, ValueError):
    TOTAL_EMERALDS = 7



def config_boolean(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "on", "1"}:
            return True
        if normalized in {"false", "no", "off", "0"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def config_number(
    value,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def joystick_button_mask(human_button_number: int) -> int:
    """Convert a 1-based Windows joystick button number to its bit mask."""
    return 1 << max(0, int(human_button_number) - 1)


AUTO_ACTIVATE = config_boolean(
    RUNTIME_CONFIG.get("auto_activate", False),
)
PREFERRED_SERIAL_PORT = str(
    RUNTIME_CONFIG.get("serial_port", "")
).strip()

# The display overlay is intentionally limited to the Big Box frontend. A
# MAME/GroovyMAME or RetroArch fullscreen surface can own the display mode,
# so the guard waits for Big Box to return to its menu before appearing.
BIG_BOX_PROCESS_NAME = "bigbox.exe"
EMULATOR_PROCESS_NAMES = {
    "mame.exe",
    "mame64.exe",
    "groovymame.exe",
    "retroarch.exe",
}

BAUD_RATE = 115200
# The serial reader thread timestamps raw heartbeats independently of the Tk
# UI thread. The extra margin also tolerates a slow Windows audio/session
# operation without declaring a healthy ESP32 disconnected.
CONNECTION_TIMEOUT_SECONDS = 5.0
RECONNECT_DELAY_SECONDS = 1.0
STABLE_COUNT_SECONDS = config_number(
    RUNTIME_CONFIG.get("sensor_stable_ms", 60),
    60,
    25,
    1000,
) / 1000.0
FINAL_EMERALD_PAUSE_SECONDS = config_number(
    RUNTIME_CONFIG.get("final_emerald_pause_ms", 150),
    150,
    0,
    3000,
) / 1000.0
SOUND_EFFECT_COOLDOWN_SECONDS = config_number(
    RUNTIME_CONFIG.get("sound_effect_cooldown_ms", 350),
    350,
    0,
    3000,
) / 1000.0
COUNTER_FLASH_MS = int(
    config_number(
        RUNTIME_CONFIG.get("counter_flash_ms", 300),
        300,
        50,
        2000,
    )
)
ROBOTNIK_FADE_MS = int(
    config_number(
        RUNTIME_CONFIG.get("robotnik_fade_ms", 250),
        250,
        0,
        3000,
    )
)
MUSIC_VOLUME = config_number(
    RUNTIME_CONFIG.get("music_volume", 0.75),
    0.75,
    0.0,
    1.0,
)
SOUND_EFFECT_VOLUME = config_number(
    RUNTIME_CONFIG.get("sound_effect_volume", 1.0),
    1.0,
    0.0,
    1.0,
)
NORMAL_WARNING_SECONDS = config_number(
    RUNTIME_CONFIG.get("normal_warning_seconds", 10.0),
    10.0,
    1.0,
    60.0,
)
STORY_ANNOUNCEMENT_SECONDS = config_number(
    RUNTIME_CONFIG.get("story_announcement_seconds", 2.5),
    2.5,
    0.5,
    15.0,
)
STORY_SHUTDOWN_SECONDS = config_number(
    RUNTIME_CONFIG.get("story_shutdown_seconds", 5.0),
    5.0,
    0.5,
    15.0,
)
STORY_QUESTION_SECONDS = config_number(
    RUNTIME_CONFIG.get("story_question_seconds", 6.0),
    6.0,
    1.0,
    30.0,
)
STORY_EGGMAN_SECONDS = config_number(
    RUNTIME_CONFIG.get("story_eggman_seconds", 3.0),
    3.0,
    1.0,
    15.0,
)
CINEMATIC_FADE_SECONDS = config_number(
    RUNTIME_CONFIG.get("cinematic_fade_seconds", 0.8),
    0.8,
    0.0,
    5.0,
)
RING_JOYSTICK_BUTTON = int(
    config_number(
        RUNTIME_CONFIG.get("ring_joystick_button", 10),
        10,
        1,
        32,
    )
)
RING_DEBOUNCE_SECONDS = config_number(
    RUNTIME_CONFIG.get("ring_debounce_ms", 90),
    90,
    30,
    500,
) / 1000.0
RING_GAME_COMMIT_SECONDS = config_number(
    RUNTIME_CONFIG.get("ring_game_commit_seconds", 3.0),
    3.0,
    1.0,
    15.0,
)
RING_ANNOUNCEMENT_SECONDS = config_number(
    RUNTIME_CONFIG.get("ring_announcement_seconds", 5.0),
    5.0,
    1.0,
    30.0,
)
RING_MILESTONE = 50
RING_MILESTONE_TITLE = "50 RINGS!"
RING_MILESTONE_MESSAGE = "Find Alex for your prize!"
RING_BURST_TITLE = "RING POWER!"
RING_BURST_MESSAGE = (
    "MASTER EMERALD ENERGY FULL!\n"
    "ONE GAME OF CHAOS POWER CHARGED!"
)
RING_BURST_ANNOUNCEMENT_SECONDS = 2.0
CINEMATIC_MAX_FPS = config_number(
    RUNTIME_CONFIG.get("cinematic_max_fps", 15),
    15,
    10,
    30,
)
CINEMATIC_FRAME_INTERVAL = 1.0 / CINEMATIC_MAX_FPS
CINEMATIC_QUEUE_SIZE = 12
CINEMATIC_PREBUFFER_FRAMES = 6
configured_default_mode = str(
    RUNTIME_CONFIG.get("default_mode", "story")
).strip().lower()
DEFAULT_GUARD_MODE = (
    configured_default_mode
    if configured_default_mode in {"story", "normal"}
    else "story"
)
try:
    BIG_BOX_READY_DELAY_SECONDS = max(
        0.5,
        float(RUNTIME_CONFIG.get("big_box_ready_delay_seconds", 1.5)),
    )
except (TypeError, ValueError):
    BIG_BOX_READY_DELAY_SECONDS = 1.5
COMPLETION_FALLBACK_SECONDS = 5.0
COMPLETION_MAX_SECONDS = 120.0
PROTOCOL_PREFIX = "MAGNET_LOCK:"

BACKGROUND_IMAGE_NAME = "egg-man-robotnik.gif"
COMPLETION_IMAGE_NAME = "sonic-sonic-the-hedgehog.gif"
SUPERSONIC_IMAGE_NAME = "supersonic.gif"
COMPLETION_AUDIO_NAME = "27. Sonic the Hedgehog Victory Theme.mp3"
MISSING_AUDIO_NAME = "Dr Robotniks Theme.mp3"
EMERALD_AUDIO_NAME = "emerald.mp3"
configured_removal_audio_names = RUNTIME_CONFIG.get(
    "removal_sound_files",
)
if not isinstance(configured_removal_audio_names, list):
    configured_removal_audio_names = [
        RUNTIME_CONFIG.get(
            "removal_sound_file",
            "ohh-no-the-chaos-emerald.mp3",
        )
    ]
REMOVAL_AUDIO_NAMES = tuple(
    dict.fromkeys(
        str(name).strip()
        for name in configured_removal_audio_names
        if str(name).strip()
    )
) or ("ohh-no-the-chaos-emerald.mp3",)
LAST_EMERALD_REMOVAL_AUDIO_NAME = str(
    RUNTIME_CONFIG.get(
        "last_emerald_removal_sound_file",
        "no-he-s-got-the-last-emerald.mp3",
    )
).strip() or "no-he-s-got-the-last-emerald.mp3"
FINAL_COMPLETION_AUDIO_NAME = str(
    RUNTIME_CONFIG.get(
        "final_completion_sound_file",
        "i-ll-show-you-what-the-chaos-emeralds-can-really-do.mp3",
    )
).strip() or "i-ll-show-you-what-the-chaos-emeralds-can-really-do.mp3"
EGGMAN_REVEAL_AUDIO_NAME = "so-egg-man-s-behind-this-huh.mp3"
CINEMATIC_VIDEO_NAME = (
    str(
        RUNTIME_CONFIG.get(
            "cinematic_video_file",
            DEFAULT_CONFIG["cinematic_video_file"],
        )
    ).strip()
    or DEFAULT_CONFIG["cinematic_video_file"]
)

SOURCE_ASSET_DIRECTORY = Path(
    os.environ.get(
        "MAGNET_GUARD_ASSET_DIR",
        str(Path(__file__).resolve().parents[2] / "Emerald"),
    )
)
ORIGINAL_BACKGROUND_IMAGE_PATH = (
    SOURCE_ASSET_DIRECTORY / BACKGROUND_IMAGE_NAME
)
ORIGINAL_COMPLETION_IMAGE_PATH = (
    SOURCE_ASSET_DIRECTORY / COMPLETION_IMAGE_NAME
)
ORIGINAL_SUPERSONIC_IMAGE_PATH = (
    SOURCE_ASSET_DIRECTORY / SUPERSONIC_IMAGE_NAME
)
ORIGINAL_COMPLETION_AUDIO_PATH = (
    SOURCE_ASSET_DIRECTORY / COMPLETION_AUDIO_NAME
)
ORIGINAL_MISSING_AUDIO_PATH = (
    SOURCE_ASSET_DIRECTORY / MISSING_AUDIO_NAME
)
ORIGINAL_EMERALD_AUDIO_PATH = (
    SOURCE_ASSET_DIRECTORY / EMERALD_AUDIO_NAME
)
ORIGINAL_REMOVAL_AUDIO_PATHS = tuple(
    SOURCE_ASSET_DIRECTORY / name
    for name in REMOVAL_AUDIO_NAMES
)
ORIGINAL_LAST_EMERALD_REMOVAL_AUDIO_PATH = (
    SOURCE_ASSET_DIRECTORY / LAST_EMERALD_REMOVAL_AUDIO_NAME
)
ORIGINAL_FINAL_COMPLETION_AUDIO_PATH = (
    SOURCE_ASSET_DIRECTORY / FINAL_COMPLETION_AUDIO_NAME
)
ORIGINAL_EGGMAN_REVEAL_AUDIO_PATH = (
    SOURCE_ASSET_DIRECTORY / EGGMAN_REVEAL_AUDIO_NAME
)
ORIGINAL_CINEMATIC_VIDEO_PATH = (
    SOURCE_ASSET_DIRECTORY / CINEMATIC_VIDEO_NAME
)

LOCK_MESSAGE = "ROBOTNIK STOLE THE CHAOS EMERALDS!"
COMPLETION_MESSAGE = (
    "Thank you, Sonic, for returning all the Chaos Emeralds!"
)
GAME_ON_MESSAGE = "EMERALDS FOUND! GAME ON!"
RESTORED_MESSAGE = "ALL CHAOS EMERALDS RESTORED!"
NORMAL_WARNING_MESSAGE = "Hey! Put that back! We already did the thing!"
STORY_REMOVAL_OVERLAY_TITLE = "A Chaos Emerald Was Stolen!"
STORY_SHUTDOWN_TITLE = "ROBOTNIK'S CHAOS HEIST!"
STORY_SHUTDOWN_MESSAGE = (
    "Robotnik has stolen the Chaos Emeralds and taken them back to his fortress!"
)
STORY_QUESTION_TITLE = "THE ARCADE HAS LOST ITS CHAOS ENERGY!"
STORY_QUESTION_MESSAGE = (
    "Where can we find someone fast enough to fight Robotnik\n"
    "and bring the Chaos Emeralds home?"
)
STORY_EGGMAN_TITLE = "SO EGGMAN'S BEHIND THIS, HUH?"
STORY_EGGMAN_MESSAGE = ""
ENERGY_ANIMATION_STEP_MS = 55
ENERGY_EMPHASIS_MS = 420

STORY_STOLEN_TEXT = {
    1: (
        "ONE CHAOS EMERALD STOLEN!",
        "ROBOTNIK HAS MADE OFF WITH THE FIRST EMERALD!",
    ),
    2: (
        "TWO CHAOS EMERALDS STOLEN!",
        "THE CHAOS HEIST IS UNDERWAY - THE SHRINE IS FADING!",
    ),
    3: (
        "THREE CHAOS EMERALDS STOLEN!",
        "ROBOTNIK'S FORTRESS IS DRAINING THE SHRINE!",
    ),
    4: (
        "FOUR CHAOS EMERALDS STOLEN!",
        "THE EGGMAN EMPIRE'S CHAOS POWER IS RISING!",
    ),
    5: (
        "FIVE CHAOS EMERALDS STOLEN!",
        "SONIC, THE SHRINE IS LOSING ITS POWER!",
    ),
    6: (
        "SIX CHAOS EMERALDS STOLEN!",
        "ONE MORE AND THE ARCADE WILL GO DARK!",
    ),
}

STORY_RETURNED_TEXT = {
    1: (
        "A CHAOS EMERALD RETURNS!",
        "THE SHRINE SPARKS BACK TO LIFE!",
    ),
    2: (
        "TWO CHAOS EMERALDS RESTORED!",
        "CHAOS ENERGY IS RUSHING BACK!",
    ),
    3: (
        "THREE CHAOS EMERALDS RESTORED!",
        "THE EGGMAN EMPIRE'S POWER IS FALTERING!",
    ),
    4: (
        "FOUR CHAOS EMERALDS RESTORED!",
        "THE SHRINE IS PUSHING BACK!",
    ),
    5: (
        "FIVE CHAOS EMERALDS RESTORED!",
        "ROBOTNIK'S CHAOS HEIST IS UNRAVELING!",
    ),
    6: (
        "SIX CHAOS EMERALDS RESTORED!",
        "ONE MORE AND THE ARCADE WILL AWAKEN!",
    ),
    7: (
        "ALL SEVEN CHAOS EMERALDS RESTORED!",
        "THE SHRINE IS FULLY CHARGED!",
    ),
}

HWND_TOPMOST = ctypes.c_void_p(-1)
HWND_NOTOPMOST = ctypes.c_void_p(-2)
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
SW_SHOWNOACTIVATE = 4
PROCESS_SUSPEND_RESUME = 0x0800
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
STILL_ACTIVE = 259
INFINITE = 0xFFFFFFFF
ERROR_ALREADY_EXISTS = 183
CREATE_NO_WINDOW = 0x08000000
SINGLE_INSTANCE_MUTEX_NAME = "Local\\MagnetArcadeGuard.SingleInstance"
JOYERR_NOERROR = 0
JOY_RETURNBUTTONS = 0x00000080


class Win32Rect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class Win32MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", Win32Rect),
        ("rcWork", Win32Rect),
        ("dwFlags", ctypes.c_ulong),
    ]


class Win32JoyInfoEx(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("dwXpos", ctypes.c_ulong),
        ("dwYpos", ctypes.c_ulong),
        ("dwZpos", ctypes.c_ulong),
        ("dwRpos", ctypes.c_ulong),
        ("dwUpos", ctypes.c_ulong),
        ("dwVpos", ctypes.c_ulong),
        ("dwButtons", ctypes.c_ulong),
        ("dwButtonNumber", ctypes.c_ulong),
        ("dwPOV", ctypes.c_ulong),
        ("dwReserved1", ctypes.c_ulong),
        ("dwReserved2", ctypes.c_ulong),
    ]


def configure_windows_runtime() -> None:
    """Configure DPI awareness and pointer-sized Win32 API signatures."""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    try:
        user32.SetProcessDpiAwarenessContext(
            ctypes.c_void_p(-4)  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        )
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            try:
                user32.SetProcessDPIAware()
            except (AttributeError, OSError):
                pass

    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.GetWindowThreadProcessId.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
    user32.GetWindowRect.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(Win32Rect),
    ]
    user32.GetWindowRect.restype = ctypes.c_bool
    user32.IsWindow.argtypes = [ctypes.c_void_p]
    user32.IsWindow.restype = ctypes.c_bool
    user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
    user32.IsWindowVisible.restype = ctypes.c_bool
    user32.IsIconic.argtypes = [ctypes.c_void_p]
    user32.IsIconic.restype = ctypes.c_bool
    user32.MonitorFromWindow.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
    ]
    user32.MonitorFromWindow.restype = ctypes.c_void_p
    user32.GetMonitorInfoW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(Win32MonitorInfo),
    ]
    user32.GetMonitorInfoW.restype = ctypes.c_bool
    user32.SetWindowPos.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    user32.SetWindowPos.restype = ctypes.c_bool
    user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    user32.SetForegroundWindow.restype = ctypes.c_bool
    user32.BringWindowToTop.argtypes = [ctypes.c_void_p]
    user32.BringWindowToTop.restype = ctypes.c_bool
    user32.SetActiveWindow.argtypes = [ctypes.c_void_p]
    user32.SetActiveWindow.restype = ctypes.c_void_p
    user32.SetFocus.argtypes = [ctypes.c_void_p]
    user32.SetFocus.restype = ctypes.c_void_p
    user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.ShowWindow.restype = ctypes.c_bool
    user32.AttachThreadInput.argtypes = [
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_bool,
    ]
    user32.AttachThreadInput.restype = ctypes.c_bool
    user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_int,
    ]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.MessageBoxW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_uint,
    ]
    user32.MessageBoxW.restype = ctypes.c_int

    kernel32.GetCurrentThreadId.argtypes = []
    kernel32.GetCurrentThreadId.restype = ctypes.c_ulong
    kernel32.OpenProcess.argtypes = [
        ctypes.c_ulong,
        ctypes.c_bool,
        ctypes.c_ulong,
    ]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool
    kernel32.GetExitCodeProcess.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    kernel32.GetExitCodeProcess.restype = ctypes.c_bool
    kernel32.WaitForSingleObject.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
    ]
    kernel32.WaitForSingleObject.restype = ctypes.c_ulong
    kernel32.CreateMutexW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_bool,
        ctypes.c_wchar_p,
    ]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.GetLastError.argtypes = []
    kernel32.GetLastError.restype = ctypes.c_ulong
    kernel32.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    kernel32.QueryFullProcessImageNameW.restype = ctypes.c_bool

    winmm = ctypes.windll.winmm
    winmm.joyGetNumDevs.argtypes = []
    winmm.joyGetNumDevs.restype = ctypes.c_uint
    winmm.joyGetPosEx.argtypes = [
        ctypes.c_uint,
        ctypes.POINTER(Win32JoyInfoEx),
    ]
    winmm.joyGetPosEx.restype = ctypes.c_uint


def run_resume_watchdog(
    parent_pid: int,
    big_box_pid: int,
    cancel_path_text: str,
) -> int:
    """Resume Big Box if the guard process dies while Big Box is suspended."""
    kernel32 = ctypes.windll.kernel32
    ntdll = ctypes.windll.ntdll
    cancel_path = Path(cancel_path_text)
    ntdll.NtResumeProcess.argtypes = [ctypes.c_void_p]
    ntdll.NtResumeProcess.restype = ctypes.c_long

    target_handle = kernel32.OpenProcess(
        PROCESS_SUSPEND_RESUME,
        False,
        big_box_pid,
    )
    if not target_handle:
        return 1

    parent_handle = kernel32.OpenProcess(SYNCHRONIZE, False, parent_pid)
    try:
        if cancel_path.exists():
            return 0

        # If the parent has already died, resume immediately. This closes the
        # small startup race between creating this helper and suspending Big
        # Box in the parent process.
        if parent_handle:
            while True:
                if cancel_path.exists():
                    return 0
                wait_result = kernel32.WaitForSingleObject(
                    parent_handle,
                    100,
                )
                if wait_result == 0:  # WAIT_OBJECT_0
                    break
                if wait_result != 258:  # WAIT_TIMEOUT
                    break

        if cancel_path.exists():
            return 0
        ntdll.NtResumeProcess(target_handle)
        return 0
    finally:
        kernel32.CloseHandle(target_handle)
        if parent_handle:
            kernel32.CloseHandle(parent_handle)
        try:
            cancel_path.unlink(missing_ok=True)
        except OSError:
            pass


def acquire_single_instance_mutex():
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(
        None,
        False,
        SINGLE_INSTANCE_MUTEX_NAME,
    )
    if not handle:
        return None, False

    already_running = kernel32.GetLastError() == ERROR_ALREADY_EXISTS
    return handle, not already_running


class MagnetArcadeGuard:
    def __init__(self, instance_mutex_handle=None) -> None:
        bootstrap_event("creating Tk root")
        self.instance_mutex_handle = instance_mutex_handle
        self.resume_watchdog_process = None
        self.resume_watchdog_cancel_path = None
        self.shutdown_started = False
        self.cleanup_complete = False
        self.root = tk.Tk()
        bootstrap_event("Tk root created")
        self.root.report_callback_exception = self.handle_tk_exception
        self.root.title("Magnetic Arcade Guard")
        self.root.configure(background="black")
        # Use an explicit borderless window instead of Tk's fullscreen mode.
        # Tk can retain a stale fullscreen size after a game changes display
        # modes, which can leave Big Box visible over part of the overlay.
        self.root.overrideredirect(True)
        self.root.attributes("-fullscreen", False)
        self.root.attributes("-topmost", True)
        self.root.configure(cursor="none")

        self.running = True
        self.guard_active = False
        self.guard_mode = DEFAULT_GUARD_MODE
        self.activation_generation = 0
        self.last_fault = ""
        self.overlay_visible = False
        self.overlay_kind: Optional[str] = None
        self.completion_in_progress = False
        self.completion_after_id = None
        self.final_emerald_after_id = None
        self.completion_audio_playing = False
        self.completion_started_at = 0.0
        self.completion_animation_finished = False
        self.final_completion_sound_started = False
        self.final_completion_sound_playing = False
        self.music_mode: Optional[str] = None
        self.emerald_sound = None
        self.removal_sound = None
        self.removal_sounds = []
        self.last_removal_sound_index = None
        self.last_emerald_removal_sound = None
        self.final_completion_sound = None
        self.eggman_reveal_sound = None
        self.event_channel = None
        self.event_audio_after_id = None
        self.last_event_sound_kind: Optional[str] = None
        self.last_event_sound_at = 0.0
        self.final_emerald_sound_started = False
        self.final_emerald_pause_started_at: Optional[float] = None
        self.counter_animation_after_id = None
        self.counter_animation_generation = 0
        self.energy_animation_after_id = None
        self.energy_animation_generation = 0
        self.energy_display_count = TOTAL_EMERALDS
        self.story_armed = False
        self.story_cycle_started = False
        self.story_intro_completed = False
        self.story_sequence_after_id = None
        self.announcement_after_id = None
        self.normal_warning_after_id = None
        self.normal_warning_trigger_count: Optional[int] = None
        self.pending_normal_warning: Optional[tuple[int, int]] = None
        self.cinematic_prepare_state = "unavailable"
        self.cinematic_prepare_error = ""
        self.cinematic_audio_pcm = b""
        self.cinematic_audio_rate = 44100
        self.cinematic_duration = 0.0
        self.cinematic_channel = None
        self.cinematic_sound = None
        self.cinematic_after_id = None
        self.cinematic_started_at = 0.0
        self.cinematic_generation = 0
        self.cinematic_cancel_event = threading.Event()
        self.cinematic_frame_queue = queue.Queue(
            maxsize=CINEMATIC_QUEUE_SIZE
        )
        self.cinematic_pending_frame = None
        self.cinematic_worker_done = False
        self.cinematic_worker_error = ""
        self.cinematic_photo = None
        self.return_window_handle = 0
        self.suspended_process_handle = None
        self.suspended_process_id = 0
        self.muted_audio_sessions = {}
        self.audio_muted = False
        self.audio_mute_error_reported = False
        self.audio_last_error = ""
        self.audio_watchdog_after_id = None
        self.ring_input_stop_event = threading.Event()
        self.ring_input_thread = None
        self.ring_input_backend = "Windows joystick"
        self.ring_joystick_error = ""
        self.ring_joystick_signature = ()
        self.ring_button_states = {}
        self.ring_last_press_at = {}
        self.ring_burst_active = False
        self.ring_burst_game_seen = False
        self.ring_burst_game_seen_since = 0.0
        self.pending_ring_milestone = False
        local_app_data = Path(
            os.environ.get(
                "LOCALAPPDATA",
                str(Path.home() / "AppData" / "Local"),
            )
        )
        self.status_path = (
            local_app_data
            / "MagnetArcadeGuard"
            / "guard-status.txt"
        )
        self.event_log_path = (
            local_app_data
            / "MagnetArcadeGuard"
            / "guard-events.log"
        )
        self.ring_counter_path = (
            local_app_data
            / "MagnetArcadeGuard"
            / "ring-counter.json"
        )
        self.ring_count, self.ring_milestones_shown = (
            self.load_ring_state()
        )
        self.reader_connected = False
        self.last_good_port = PREFERRED_SERIAL_PORT
        self.last_valid_message = 0.0
        self.last_serial_message_at = 0.0
        self.pending_count: Optional[int] = None
        self.pending_count_since = 0.0
        self.accepted_count: Optional[int] = None
        self.controller_lost = True
        self.pending_overlay_missing: Optional[int] = None
        self.overlay_gate_state = "DORMANT"
        self.foreground_process_name = "unknown"
        self.last_gate_log_key = ""
        self.big_box_candidate_window = 0
        self.big_box_candidate_since = 0.0
        self.big_box_candidate_monitor = None
        self.big_box_candidate_rect = None
        self.control_window = None
        self.control_state_var = None
        self.control_sensor_var = None
        self.control_details_var = None
        self.control_state_label = None
        self.control_activate_button = None
        self.control_deactivate_button = None
        self.control_story_button = None
        self.control_normal_button = None
        self.announcement_window = None
        self.announcement_title_label = None
        self.announcement_detail_label = None
        self.announcement_flash_window = None
        self.announcement_flash_after_id = None

        self.screen_width = self.root.winfo_screenwidth()
        self.screen_height = self.root.winfo_screenheight()
        self.overlay_monitor_bounds = (
            0,
            0,
            self.screen_width,
            self.screen_height,
        )
        self.title_font_size = self.fit_font_size(
            (LOCK_MESSAGE, COMPLETION_MESSAGE),
            max_size=min(34, max(18, int(self.screen_width * 0.04))),
            min_size=12,
        )
        self.count_font_size = self.fit_font_size(
            (
                self.missing_text(TOTAL_EMERALDS),
                self.missing_text(1),
                RESTORED_MESSAGE,
                GAME_ON_MESSAGE,
            ),
            max_size=min(64, max(36, int(self.screen_height * 0.10))),
            min_size=12,
        )

        self.background_label = tk.Label(
            self.root,
            background="black",
        )
        self.background_label.place(
            relx=0.5,
            rely=0.5,
            anchor="center",
        )
        self.background_frames = []
        self.background_delays = []
        self.background_frame_index = 0
        self.background_image_width = 0
        self.background_image_height = 0
        self.background_display_width = 0
        self.background_display_height = 0
        self.background_image_path = self.find_asset(
            BACKGROUND_IMAGE_NAME,
            ORIGINAL_BACKGROUND_IMAGE_PATH,
        )
        self.completion_image_path = self.find_asset(
            COMPLETION_IMAGE_NAME,
            ORIGINAL_COMPLETION_IMAGE_PATH,
        )
        self.supersonic_image_path = self.find_asset(
            SUPERSONIC_IMAGE_NAME,
            ORIGINAL_SUPERSONIC_IMAGE_PATH,
        )
        self.completion_audio_path = self.find_asset(
            COMPLETION_AUDIO_NAME,
            ORIGINAL_COMPLETION_AUDIO_PATH,
        )
        self.missing_audio_path = self.find_asset(
            MISSING_AUDIO_NAME,
            ORIGINAL_MISSING_AUDIO_PATH,
        )
        self.emerald_audio_path = self.find_asset(
            EMERALD_AUDIO_NAME,
            ORIGINAL_EMERALD_AUDIO_PATH,
        )
        self.removal_audio_paths = tuple(
            self.find_asset(name, original_path)
            for name, original_path in zip(
                REMOVAL_AUDIO_NAMES,
                ORIGINAL_REMOVAL_AUDIO_PATHS,
            )
        )
        self.last_emerald_removal_audio_path = self.find_asset(
            LAST_EMERALD_REMOVAL_AUDIO_NAME,
            ORIGINAL_LAST_EMERALD_REMOVAL_AUDIO_PATH,
        )
        self.final_completion_audio_path = self.find_asset(
            FINAL_COMPLETION_AUDIO_NAME,
            ORIGINAL_FINAL_COMPLETION_AUDIO_PATH,
        )
        self.eggman_reveal_audio_path = self.find_asset(
            EGGMAN_REVEAL_AUDIO_NAME,
            ORIGINAL_EGGMAN_REVEAL_AUDIO_PATH,
        )
        self.cinematic_video_path = self.find_asset(
            CINEMATIC_VIDEO_NAME,
            ORIGINAL_CINEMATIC_VIDEO_PATH,
        )
        self.active_frames = []
        self.active_delays = []
        self.active_loop = True
        self.active_on_complete = None
        self.animation_generation = 0
        self.completion_frames = []
        self.completion_delays = []
        self.supersonic_frames = []
        self.supersonic_delays = []
        self.completion_frame_index = 0
        self.load_background_image()
        self.load_completion_image()
        self.load_supersonic_image()
        bootstrap_event("animated images loaded")

        self.audio_ready = False
        if PYGAME_AVAILABLE:
            try:
                bootstrap_event("initializing audio mixer")
                pygame.mixer.init()
                pygame.mixer.set_num_channels(
                    max(8, pygame.mixer.get_num_channels())
                )
                pygame.mixer.set_reserved(2)
                self.event_channel = pygame.mixer.Channel(0)
                self.event_channel.set_volume(SOUND_EFFECT_VOLUME)
                self.cinematic_channel = pygame.mixer.Channel(1)
                self.cinematic_channel.set_volume(MUSIC_VOLUME)
                pygame.mixer.music.set_volume(MUSIC_VOLUME)
                self.audio_ready = True
                if self.emerald_audio_path.exists():
                    self.emerald_sound = pygame.mixer.Sound(
                        str(self.emerald_audio_path)
                    )
                    self.emerald_sound.set_volume(SOUND_EFFECT_VOLUME)

                for removal_audio_path in self.removal_audio_paths:
                    if not removal_audio_path.exists():
                        continue
                    try:
                        removal_sound = pygame.mixer.Sound(
                            str(removal_audio_path)
                        )
                        removal_sound.set_volume(SOUND_EFFECT_VOLUME)
                        self.removal_sounds.append(removal_sound)
                    except pygame.error:
                        continue

                if self.removal_sounds:
                    self.removal_sound = self.removal_sounds[0]

                if self.last_emerald_removal_audio_path.exists():
                    try:
                        self.last_emerald_removal_sound = (
                            pygame.mixer.Sound(
                                str(self.last_emerald_removal_audio_path)
                            )
                        )
                        self.last_emerald_removal_sound.set_volume(
                            SOUND_EFFECT_VOLUME
                        )
                    except pygame.error:
                        self.last_emerald_removal_sound = None

                if self.final_completion_audio_path.exists():
                    try:
                        self.final_completion_sound = pygame.mixer.Sound(
                            str(self.final_completion_audio_path)
                        )
                        self.final_completion_sound.set_volume(
                            SOUND_EFFECT_VOLUME
                        )
                    except pygame.error:
                        self.final_completion_sound = None

                if self.eggman_reveal_audio_path.exists():
                    try:
                        self.eggman_reveal_sound = pygame.mixer.Sound(
                            str(self.eggman_reveal_audio_path)
                        )
                        self.eggman_reveal_sound.set_volume(
                            SOUND_EFFECT_VOLUME
                        )
                    except pygame.error:
                        self.eggman_reveal_sound = None

            except pygame.error:
                self.audio_ready = False
        bootstrap_event("audio mixer initialization complete")

        self.title_label = tk.Label(
            self.root,
            text=LOCK_MESSAGE,
            font=("Arial", self.title_font_size, "bold"),
            foreground="white",
            background="#000000",
        )
        self.title_label.place(
            anchor="center",
        )
        self.position_title()

        self.count_label = tk.Label(
            self.root,
            text=self.missing_text(TOTAL_EMERALDS),
            font=("Arial", self.count_font_size, "bold"),
            foreground="white",
            background="#000000",
        )
        self.count_label.place(anchor="center")
        self.position_counter()

        self.energy_canvas = tk.Canvas(
            self.root,
            background="#000000",
            borderwidth=0,
            highlightthickness=0,
        )
        self.energy_canvas.place_forget()

        self.messages: queue.Queue[tuple[str, str, int]] = queue.Queue()
        self.create_announcement_window()

        if AV_AVAILABLE and self.cinematic_video_path.exists():
            self.cinematic_prepare_state = "preparing"
            threading.Thread(
                target=self.prepare_cinematic_audio,
                daemon=True,
            ).start()
        elif not AV_AVAILABLE:
            self.cinematic_prepare_state = "unavailable"
            self.cinematic_prepare_error = "PyAV unavailable"
        else:
            self.cinematic_prepare_state = "unavailable"
            self.cinematic_prepare_error = "cinematic file missing"

        self.root.protocol("WM_DELETE_WINDOW", lambda: None)

        # Emergency shutdown for testing/service:
        # Escape is intentionally not reserved because arcade games use it.
        self.root.bind_all("<Control-Shift-F12>", self.exit_program)
        self.root.bind_all(
            "<Control-Alt-F11>",
            self.deactivate_guard,
        )

        # Also poll the Windows key state so the service shortcut works even
        # if the overlay temporarily loses keyboard focus.
        threading.Thread(
            target=self.worker_entry,
            args=("keyboard shortcut", self.global_service_hotkey_worker),
            daemon=True,
        ).start()

        worker = threading.Thread(
            target=self.worker_entry,
            args=("ESP32 serial", self.serial_worker),
            daemon=True,
        )
        worker.start()

        self.ring_input_thread = threading.Thread(
            target=self.worker_entry,
            args=("ring input", self.ring_input_worker),
            daemon=True,
        )
        self.ring_input_thread.start()

        self.root.after(50, self.process_messages)
        self.root.after(250, self.connection_watchdog)
        self.root.after(250, self.foreground_watchdog)
        self.root.after(500, self.keep_window_on_top)
        self.root.after(100, self.status_heartbeat)

        # Start dormant. Ctrl + Alt + F10 selects and activates Story Mode;
        # Ctrl + Alt + F12 activates the currently selected mode.
        self.root.withdraw()
        self.create_control_panel()
        self.update_control_panel()
        self.root.after(250, self.control_panel_heartbeat)
        self.write_status("RUNNING | DORMANT")
        bootstrap_event("operator panel ready")

        if AUTO_ACTIVATE:
            self.root.after(500, self.activate_guard)

    def missing_text(self, missing_count: int) -> str:
        emerald_word = "Emerald" if missing_count == 1 else "Emeralds"
        return f"{missing_count} Chaos {emerald_word} Missing!"

    def cancel_counter_animation(self) -> None:
        self.counter_animation_generation += 1
        if self.counter_animation_after_id is not None:
            try:
                self.root.after_cancel(self.counter_animation_after_id)
            except tk.TclError:
                pass
            self.counter_animation_after_id = None

    def fitted_count_font_size(self) -> int:
        if not getattr(self, "count_label", None):
            return self.count_font_size
        _, _, monitor_width, _ = self.overlay_monitor_bounds
        return self.fit_font_size(
            (str(self.count_label.cget("text")),),
            max_size=self.count_font_size,
            min_size=12,
            available_width=monitor_width,
        )

    def reset_counter_style(self) -> None:
        self.cancel_counter_animation()
        try:
            font_size = self.fitted_count_font_size()
            self.count_label.configure(
                foreground="white",
                font=("Arial", font_size, "bold"),
            )
        except tk.TclError:
            pass

    def animate_counter(self, event_kind: str) -> None:
        colors = {
            "removed": "#ff5555",
            "returned": "#66ff99",
            "restored": "#99ff99",
        }
        color = colors.get(event_kind, "white")
        self.cancel_counter_animation()
        generation = self.counter_animation_generation
        base_size = self.fitted_count_font_size()
        _, _, monitor_width, _ = self.overlay_monitor_bounds
        enlarged_size = self.fit_font_size(
            (str(self.count_label.cget("text")),),
            max_size=base_size + max(
            2,
                min(6, base_size // 10),
            ),
            min_size=base_size,
            available_width=monitor_width,
        )

        try:
            self.count_label.configure(
                foreground=color,
                font=("Arial", enlarged_size, "bold"),
            )
        except tk.TclError:
            return

        def finish_animation() -> None:
            if generation != self.counter_animation_generation:
                return
            self.counter_animation_after_id = None
            try:
                font_size = self.fitted_count_font_size()
                self.count_label.configure(
                    foreground="white",
                    font=("Arial", font_size, "bold"),
                )
            except tk.TclError:
                pass

        self.counter_animation_after_id = self.root.after(
            COUNTER_FLASH_MS,
            finish_animation,
        )

    def cancel_energy_animation(self) -> None:
        self.energy_animation_generation += 1
        if self.energy_animation_after_id is not None:
            try:
                self.root.after_cancel(self.energy_animation_after_id)
            except tk.TclError:
                pass
            self.energy_animation_after_id = None

    def energy_meter_text(self, present_count: int) -> str:
        present_count = max(0, min(TOTAL_EMERALDS, int(present_count)))
        percent = round(present_count * 100 / TOTAL_EMERALDS)
        return f"MASTER EMERALD POWER  {percent}%"

    def energy_meter_color(self, present_count: int) -> str:
        present_count = max(0, min(TOTAL_EMERALDS, int(present_count)))
        if present_count == TOTAL_EMERALDS:
            return "#66ff99"
        if present_count >= TOTAL_EMERALDS // 2:
            return "#ffd766"
        return "#ff6666"

    def position_energy_meter(self) -> None:
        if not getattr(self, "energy_canvas", None):
            return

        _, _, monitor_width, monitor_height = self.overlay_monitor_bounds
        self.energy_canvas.update_idletasks()
        self.count_label.update_idletasks()
        count_info = self.count_label.place_info()
        try:
            count_y = float(count_info.get("y", monitor_height * 0.8))
        except (TypeError, ValueError):
            count_y = monitor_height * 0.8

        count_height = self.count_label.winfo_reqheight()
        energy_height = self.energy_canvas.winfo_reqheight()
        gap = max(5, int(monitor_height * 0.012))
        energy_y = count_y + count_height / 2 + gap + energy_height / 2
        energy_y = min(
            energy_y,
            monitor_height - energy_height / 2 - max(4, gap),
        )
        self.energy_canvas.place(
            x=monitor_width / 2,
            y=max(energy_height / 2, energy_y),
            anchor="center",
        )

    def set_energy_meter(
        self,
        present_count: int,
        *,
        emphasis: bool = False,
        visible: bool = True,
    ) -> None:
        if not getattr(self, "energy_canvas", None):
            return

        present_count = max(0, min(TOTAL_EMERALDS, int(present_count)))
        self.energy_display_count = present_count
        _, _, monitor_width, monitor_height = self.overlay_monitor_bounds
        text = self.energy_meter_text(present_count)
        meter_width = max(240, min(520, int(monitor_width * 0.72)))
        meter_height = max(42, min(62, int(monitor_height * 0.11)))
        base_size = max(10, min(18, int(monitor_height * 0.034)))
        if emphasis:
            base_size += 2
        font_size = self.fit_font_size(
            (text,),
            max_size=base_size,
            min_size=9,
            available_width=meter_width,
        )
        color = self.energy_meter_color(present_count)
        self.energy_canvas.configure(
            width=meter_width,
            height=meter_height,
            background="#000000",
        )
        self.energy_canvas.delete("all")
        self.energy_canvas.create_text(
            meter_width / 2,
            max(9, font_size * 0.65),
            text=text,
            fill=color,
            font=("Arial", font_size, "bold"),
        )

        horizontal_margin = max(8, int(meter_width * 0.035))
        segment_gap = max(2, int(meter_width * 0.007))
        bar_top = max(22, int(meter_height * 0.48))
        bar_bottom = meter_height - max(4, int(meter_height * 0.08))
        available_width = (
            meter_width
            - horizontal_margin * 2
            - segment_gap * (TOTAL_EMERALDS - 1)
        )
        segment_width = available_width / TOTAL_EMERALDS
        for index in range(TOTAL_EMERALDS):
            left = horizontal_margin + index * (segment_width + segment_gap)
            right = left + segment_width
            filled = index < present_count
            self.energy_canvas.create_rectangle(
                left,
                bar_top,
                right,
                bar_bottom,
                fill=color if filled else "#181818",
                outline="#ffffff" if emphasis and filled else "#606060",
                width=2 if emphasis and filled else 1,
            )
        if visible:
            self.position_energy_meter()
        else:
            self.energy_canvas.place_forget()

    def animate_energy_meter(
        self,
        previous_count: int,
        current_count: int,
    ) -> None:
        if not getattr(self, "energy_canvas", None):
            return

        previous_count = max(
            0,
            min(TOTAL_EMERALDS, int(previous_count)),
        )
        current_count = max(
            0,
            min(TOTAL_EMERALDS, int(current_count)),
        )
        self.cancel_energy_animation()
        generation = self.energy_animation_generation
        step = 1 if current_count >= previous_count else -1
        values = list(
            range(previous_count, current_count + step, step)
        )

        def show_step(index: int) -> None:
            if generation != self.energy_animation_generation:
                return
            self.energy_animation_after_id = None
            self.set_energy_meter(values[index], emphasis=True)
            if index + 1 < len(values):
                self.energy_animation_after_id = self.root.after(
                    ENERGY_ANIMATION_STEP_MS,
                    lambda: show_step(index + 1),
                )
                return

            self.energy_animation_after_id = self.root.after(
                ENERGY_EMPHASIS_MS,
                lambda: self.set_energy_meter(
                    current_count,
                    emphasis=False,
                ),
            )

        show_step(0)

    def hide_energy_meter(self) -> None:
        self.cancel_energy_animation()
        if getattr(self, "energy_canvas", None):
            self.energy_canvas.place_forget()

    def fit_font_size(
        self,
        text_options: tuple[str, ...],
        max_size: int,
        min_size: int,
        available_width: Optional[int] = None,
    ) -> int:
        # Leave extra horizontal margin for CRT overscan and the arcade
        # monitor's visible bezel area.
        target_width = available_width or self.screen_width
        available_width = max(
            1,
            int(target_width * 0.82),
        )

        text_lines = [
            line
            for text in text_options
            for line in str(text).splitlines()
        ] or [""]

        for size in range(max_size, min_size - 1, -1):
            font = tkfont.Font(
                root=self.root,
                family="Arial",
                size=size,
                weight="bold",
            )

            if max(font.measure(line) for line in text_lines) <= available_width:
                return size

        return min_size

    # --------------------------------------------------
    # Background image and layout
    # --------------------------------------------------

    def find_asset(
        self,
        asset_name: str,
        original_path: Path,
    ) -> Path:
        # A file beside the EXE overrides the bundled copy. This also lets an
        # operator add the optional removal sound later without rebuilding.
        external_path = application_directory() / asset_name
        if external_path.exists():
            return external_path

        bundled_directory = getattr(sys, "_MEIPASS", None)

        if bundled_directory:
            bundled_path = Path(bundled_directory) / asset_name
            if bundled_path.exists():
                return bundled_path

        local_path = Path(__file__).resolve().parent / asset_name
        if local_path.exists():
            return local_path

        return original_path

    def load_background_image(self) -> None:
        if not PIL_AVAILABLE:
            self.background_label.configure(
                text="Install Pillow to display the background image",
                foreground="white",
                font=("Arial", 24),
            )
            return

        try:
            resampling = getattr(Image, "Resampling", Image).LANCZOS

            with Image.open(self.background_image_path) as source_image:
                for frame in ImageSequence.Iterator(source_image):
                    frame_image = frame.convert("RGB")

                    if not self.background_frames:
                        self.background_image_width = frame_image.width
                        self.background_image_height = frame_image.height
                        self.calculate_background_display_size()

                    frame_image = frame_image.resize(
                        (
                            self.background_display_width,
                            self.background_display_height,
                        ),
                        resample=resampling,
                    )

                    self.background_frames.append(
                        ImageTk.PhotoImage(frame_image)
                    )

                    duration = frame.info.get(
                        "duration",
                        source_image.info.get("duration", 100),
                    )
                    self.background_delays.append(
                        max(20, int(duration or 100))
                    )

            if self.background_frames:
                self.switch_background(
                    self.background_frames,
                    self.background_delays,
                    loop=True,
                )

        except (FileNotFoundError, OSError):
            self.background_label.configure(
                text="Background image not found",
                foreground="white",
                font=("Arial", 24),
            )

    def load_completion_image(self) -> None:
        if not PIL_AVAILABLE or not self.background_display_width:
            return

        try:
            resampling = getattr(Image, "Resampling", Image).LANCZOS

            with Image.open(self.completion_image_path) as source_image:
                for frame in ImageSequence.Iterator(source_image):
                    frame_image = frame.convert("RGB")
                    frame_image = frame_image.resize(
                        (
                            self.background_display_width,
                            self.background_display_height,
                        ),
                        resample=resampling,
                    )

                    self.completion_frames.append(
                        ImageTk.PhotoImage(frame_image)
                    )

                    duration = frame.info.get(
                        "duration",
                        source_image.info.get("duration", 100),
                    )
                    self.completion_delays.append(
                        max(20, int(duration or 100))
                    )

        except (FileNotFoundError, OSError):
            self.completion_frames = []
            self.completion_delays = []

    def load_supersonic_image(self) -> None:
        if not PIL_AVAILABLE or not self.background_display_width:
            return

        try:
            resampling = getattr(Image, "Resampling", Image).LANCZOS

            with Image.open(self.supersonic_image_path) as source_image:
                for frame in ImageSequence.Iterator(source_image):
                    frame_image = frame.convert("RGB")
                    frame_image = frame_image.resize(
                        (
                            self.background_display_width,
                            self.background_display_height,
                        ),
                        resample=resampling,
                    )

                    self.supersonic_frames.append(
                        ImageTk.PhotoImage(frame_image)
                    )

                    duration = frame.info.get(
                        "duration",
                        source_image.info.get("duration", 100),
                    )
                    self.supersonic_delays.append(
                        max(20, int(duration or 100))
                    )

        except (FileNotFoundError, OSError):
            self.supersonic_frames = []
            self.supersonic_delays = []

    def calculate_background_display_size(self) -> None:
        title_space = int(self.title_font_size * 1.8) + 10
        counter_space = int(self.count_font_size * 1.4) + 10
        energy_space = max(48, int(self.screen_height * 0.12))
        vertical_gap = max(8, int(self.screen_height * 0.02))

        maximum_width = max(1, self.screen_width - 20)
        maximum_height = max(
            1,
            self.screen_height
            - title_space
            - counter_space
            - energy_space
            - vertical_gap,
        )

        scale = min(
            1.0,
            maximum_width / self.background_image_width,
            maximum_height / self.background_image_height,
        )

        self.background_display_width = max(
            1,
            int(self.background_image_width * scale),
        )
        self.background_display_height = max(
            1,
            int(self.background_image_height * scale),
        )

    def position_counter(self) -> None:
        _, _, monitor_width, monitor_height = self.overlay_monitor_bounds
        self.count_label.update_idletasks()
        count_height = self.count_label.winfo_reqheight()
        meter_height = max(42, min(62, int(monitor_height * 0.11)))
        bottom_margin = max(5, int(monitor_height * 0.012))
        meter_gap = max(5, int(monitor_height * 0.012))
        maximum_counter_y = (
            monitor_height
            - bottom_margin
            - meter_height
            - meter_gap
            - count_height / 2
        )
        if self.background_display_height:
            image_bottom = (
                monitor_height / 2
                + self.background_display_height / 2
            )
            counter_y = image_bottom + max(
                8,
                int(self.count_font_size * 0.4),
            )
            counter_y = min(
                counter_y,
                maximum_counter_y,
            )
        else:
            counter_y = min(monitor_height * 0.8, maximum_counter_y)

        self.count_label.place(
            x=monitor_width / 2,
            y=counter_y,
            anchor="center",
        )

    def position_title(self) -> None:
        _, _, monitor_width, monitor_height = self.overlay_monitor_bounds
        if self.background_display_height:
            image_top = (
                monitor_height / 2
                - self.background_display_height / 2
            )
            self.title_label.update_idletasks()
            title_height = self.title_label.winfo_reqheight()
            title_y = image_top - max(
                8,
                int(title_height / 2) + 4,
            )
            title_y = max(
                self.title_font_size,
                title_y,
            )
        else:
            title_y = monitor_height * 0.08

        self.title_label.place(
            x=monitor_width / 2,
            y=title_y,
            anchor="center",
        )

    def animate_background(self, generation: Optional[int] = None) -> None:
        if generation is not None and generation != self.animation_generation:
            return

        if not self.running or not self.active_frames:
            return

        self.background_label.configure(
            image=self.active_frames[self.background_frame_index]
        )

        delay = self.active_delays[self.background_frame_index]
        is_last_frame = (
            self.background_frame_index
            == len(self.active_frames) - 1
        )

        if is_last_frame and not self.active_loop:
            callback = self.active_on_complete
            self.active_on_complete = None

            if callback is not None:
                self.root.after(delay, callback)

            return

        self.background_frame_index = (
            self.background_frame_index + 1
        ) % len(self.active_frames)

        if generation is None:
            generation = self.animation_generation

        self.root.after(
            delay,
            lambda: self.animate_background(generation),
        )

    def switch_background(
        self,
        frames,
        delays,
        loop: bool = True,
        on_complete=None,
    ) -> None:
        if not frames:
            return

        self.active_frames = frames
        self.active_delays = delays
        self.active_loop = loop
        self.active_on_complete = on_complete
        self.background_frame_index = 0
        self.animation_generation += 1
        generation = self.animation_generation
        self.root.after(
            0,
            lambda: self.animate_background(generation),
        )

    # --------------------------------------------------
    # Non-blocking Story Mode announcements
    # --------------------------------------------------

    def create_announcement_window(self) -> None:
        self.announcement_window = tk.Toplevel(self.root)
        self.announcement_window.overrideredirect(True)
        self.announcement_window.configure(background="#090909")
        self.announcement_window.attributes("-topmost", True)
        try:
            self.announcement_window.attributes("-alpha", 0.94)
        except tk.TclError:
            pass

        self.announcement_title_label = tk.Label(
            self.announcement_window,
            text="",
            font=("Arial", 22, "bold"),
            foreground="#ff5555",
            background="#090909",
        )
        self.announcement_title_label.pack(pady=(10, 0))
        self.announcement_detail_label = tk.Label(
            self.announcement_window,
            text="",
            font=("Arial", 15, "bold"),
            foreground="white",
            background="#090909",
            justify="center",
        )
        self.announcement_detail_label.pack(pady=(2, 10))
        self.announcement_window.withdraw()

        self.announcement_flash_window = tk.Toplevel(self.root)
        self.announcement_flash_window.overrideredirect(True)
        self.announcement_flash_window.configure(background="#fff6b0")
        self.announcement_flash_window.attributes("-topmost", True)
        try:
            self.announcement_flash_window.attributes("-alpha", 0.78)
        except tk.TclError:
            pass
        self.announcement_flash_window.withdraw()

    def apply_announcement_window_style(self, window=None) -> None:
        window = window or self.announcement_window
        if not window:
            return
        try:
            window.update_idletasks()
            window_handle = window.winfo_id()
            user32 = ctypes.windll.user32
            get_style = getattr(
                user32,
                "GetWindowLongPtrW",
                user32.GetWindowLongW,
            )
            set_style = getattr(
                user32,
                "SetWindowLongPtrW",
                user32.SetWindowLongW,
            )
            style = get_style(window_handle, GWL_EXSTYLE)
            set_style(
                window_handle,
                GWL_EXSTYLE,
                style
                | WS_EX_TRANSPARENT
                | WS_EX_NOACTIVATE
                | WS_EX_TOOLWINDOW,
            )
        except (AttributeError, tk.TclError):
            pass

    def hide_announcement_flash(self) -> None:
        self.announcement_flash_after_id = None
        if self.announcement_flash_window:
            try:
                self.announcement_flash_window.withdraw()
            except tk.TclError:
                pass

    def flash_story_screen(self) -> None:
        if (
            not self.announcement_flash_window
            or not self.overlay_monitor_bounds
        ):
            return

        if self.announcement_flash_after_id is not None:
            try:
                self.root.after_cancel(self.announcement_flash_after_id)
            except tk.TclError:
                pass
            self.announcement_flash_after_id = None

        x, y, width, height = self.overlay_monitor_bounds
        self.announcement_flash_window.geometry(
            f"{width}x{height}{x:+d}{y:+d}"
        )
        self.apply_announcement_window_style(
            self.announcement_flash_window
        )
        self.announcement_flash_window.deiconify()
        try:
            window_handle = self.announcement_flash_window.winfo_id()
            ctypes.windll.user32.SetWindowPos(
                window_handle,
                HWND_TOPMOST,
                x,
                y,
                width,
                height,
                SWP_SHOWWINDOW | SWP_NOACTIVATE,
            )
            ctypes.windll.user32.ShowWindow(
                window_handle,
                SW_SHOWNOACTIVATE,
            )
        except (AttributeError, tk.TclError):
            pass

        self.announcement_flash_after_id = self.root.after(
            120,
            self.hide_announcement_flash,
        )

    def can_show_story_announcement(self) -> bool:
        try:
            foreground_window = ctypes.windll.user32.GetForegroundWindow()
        except AttributeError:
            return False

        if self.get_window_process_name(foreground_window) != BIG_BOX_PROCESS_NAME:
            return False

        monitor_bounds = self.get_monitor_bounds(foreground_window)
        window_rect = self.get_window_rect(foreground_window)
        if (
            monitor_bounds is None
            or window_rect is None
            or not self.window_covers_monitor(window_rect, monitor_bounds)
        ):
            return False

        self.overlay_monitor_bounds = monitor_bounds
        return True

    def show_story_announcement(
        self,
        present_count: int,
        event_kind: str,
        duration_seconds: Optional[float] = None,
    ) -> bool:
        if not self.can_show_story_announcement():
            return False

        self.hide_story_announcement()
        missing_count = TOTAL_EMERALDS - present_count
        energy_percent = round(present_count * 100 / TOTAL_EMERALDS)
        if event_kind == "removed":
            _, detail = STORY_STOLEN_TEXT.get(
                missing_count,
                (
                    f"{missing_count} CHAOS EMERALDS STOLEN!",
                    "ROBOTNIK'S CHAOS HEIST CONTINUES!",
                ),
            )
            title = STORY_REMOVAL_OVERLAY_TITLE
            detail = f"{detail}\nCHAOS ENERGY: {energy_percent}%"
            color = "#ff5555"
        elif event_kind == "normal":
            title = STORY_REMOVAL_OVERLAY_TITLE
            detail = (
                "Hey! Put that back!\n"
                "We already did the thing!\n"
                f"CHAOS ENERGY: {energy_percent}%"
            )
            color = "#ffcc66"
        else:
            title, detail = STORY_RETURNED_TEXT.get(
                present_count,
                (
                    f"{present_count} CHAOS EMERALDS RESTORED!",
                    "THE SHRINE IS RECLAIMING ITS POWER!",
                ),
            )
            detail = f"{detail}\nCHAOS ENERGY: {energy_percent}%"
            color = "#77ff99"

        x, y, width, height = self.overlay_monitor_bounds
        banner_width = max(1, int(width * 0.90))
        banner_height = max(88, min(180, int(height * 0.30)))
        banner_x = x + (width - banner_width) // 2
        banner_y = y + (height - banner_height) // 2
        title_size = self.fit_font_size(
            (title,),
            max_size=max(14, min(26, int(height * 0.052))),
            min_size=12,
            available_width=banner_width,
        )
        detail_size = self.fit_font_size(
            (detail,),
            max_size=max(11, min(19, int(height * 0.035))),
            min_size=10,
            available_width=banner_width,
        )

        self.announcement_title_label.configure(
            text=title,
            foreground=color,
            font=("Arial", title_size, "bold"),
            wraplength=max(1, int(banner_width * 0.82)),
            justify="center",
        )
        self.announcement_detail_label.configure(
            text=detail,
            font=("Arial", detail_size, "bold"),
            wraplength=max(1, int(banner_width * 0.82)),
            justify="center",
        )
        self.announcement_window.geometry(
            f"{banner_width}x{banner_height}{banner_x:+d}{banner_y:+d}"
        )
        self.apply_announcement_window_style()
        self.announcement_window.deiconify()
        try:
            window_handle = self.announcement_window.winfo_id()
            ctypes.windll.user32.SetWindowPos(
                window_handle,
                HWND_TOPMOST,
                banner_x,
                banner_y,
                banner_width,
                banner_height,
                SWP_SHOWWINDOW | SWP_NOACTIVATE,
            )
            ctypes.windll.user32.ShowWindow(
                window_handle,
                SW_SHOWNOACTIVATE,
            )
        except (AttributeError, tk.TclError):
            pass

        self.flash_story_screen()
        self.announcement_after_id = self.root.after(
            int(
                (duration_seconds or STORY_ANNOUNCEMENT_SECONDS)
                * 1000
            ),
            self.hide_story_announcement,
        )
        return True

    def hide_story_announcement(self) -> None:
        if self.announcement_after_id is not None:
            try:
                self.root.after_cancel(self.announcement_after_id)
            except tk.TclError:
                pass
            self.announcement_after_id = None
        self.hide_announcement_flash()
        if self.announcement_window:
            try:
                self.announcement_window.withdraw()
            except tk.TclError:
                pass

    # --------------------------------------------------
    # In-overlay cinematic playback
    # --------------------------------------------------

    def prepare_cinematic_audio(self) -> None:
        try:
            mixer_settings = pygame.mixer.get_init() if self.audio_ready else None
            sample_rate = mixer_settings[0] if mixer_settings else 44100
            channel_count = mixer_settings[2] if mixer_settings else 2
            layout = "mono" if channel_count == 1 else "stereo"
            bytes_per_sample = 2 * channel_count
            audio_chunks = []

            with av.open(str(self.cinematic_video_path)) as container:
                if container.duration:
                    self.cinematic_duration = float(
                        container.duration / av.time_base
                    )
                audio_stream = next(
                    (
                        stream
                        for stream in container.streams
                        if stream.type == "audio"
                    ),
                    None,
                )
                if audio_stream is not None:
                    resampler = av.AudioResampler(
                        format="s16",
                        layout=layout,
                        rate=sample_rate,
                    )
                    for input_frame in container.decode(audio_stream):
                        for output_frame in resampler.resample(input_frame):
                            required_bytes = (
                                output_frame.samples * bytes_per_sample
                            )
                            audio_chunks.append(
                                bytes(output_frame.planes[0])[
                                    :required_bytes
                                ]
                            )
                    for output_frame in resampler.resample(None):
                        required_bytes = (
                            output_frame.samples * bytes_per_sample
                        )
                        audio_chunks.append(
                            bytes(output_frame.planes[0])[:required_bytes]
                        )

            self.cinematic_audio_rate = sample_rate
            self.cinematic_audio_pcm = b"".join(audio_chunks)
            self.cinematic_prepare_state = "ready"
            self.cinematic_prepare_error = ""
        except Exception as error:
            self.cinematic_prepare_state = "error"
            self.cinematic_prepare_error = str(error).replace("\n", " ")[:160]

    def decode_cinematic_frames(self, generation: int) -> None:
        try:
            with av.open(str(self.cinematic_video_path)) as container:
                video_stream = next(
                    stream
                    for stream in container.streams
                    if stream.type == "video"
                )
                first_timestamp = None
                next_output_timestamp = 0.0
                _, _, monitor_width, monitor_height = (
                    self.overlay_monitor_bounds
                )

                for frame in container.decode(video_stream):
                    if (
                        self.cinematic_cancel_event.is_set()
                        or generation != self.cinematic_generation
                    ):
                        break

                    timestamp = (
                        float(frame.pts * frame.time_base)
                        if frame.pts is not None
                        else 0.0
                    )
                    if first_timestamp is None:
                        first_timestamp = timestamp
                    timestamp -= first_timestamp

                    # The arcade PC cannot reliably convert and upload all 30
                    # source frames every second. Keep timestamps tied to the
                    # original video but decode at a bounded display rate.
                    if timestamp + 0.0001 < next_output_timestamp:
                        continue
                    next_output_timestamp = timestamp + CINEMATIC_FRAME_INTERVAL

                    scale = min(
                        1.0,
                        monitor_width / frame.width,
                        monitor_height / frame.height,
                    )
                    target_size = (
                        max(1, int(frame.width * scale)),
                        max(1, int(frame.height * scale)),
                    )
                    # Let FFmpeg/PyAV scale directly into a tightly packed RGB
                    # frame. This avoids the padded-buffer artifacts seen as
                    # vertical black dashes and is much cheaper than a second
                    # full-frame Pillow resize.
                    frame_image = frame.reformat(
                        width=target_size[0],
                        height=target_size[1],
                        format="rgb24",
                    ).to_image()

                    while not self.cinematic_cancel_event.is_set():
                        try:
                            self.cinematic_frame_queue.put(
                                (timestamp, frame_image),
                                timeout=0.1,
                            )
                            break
                        except queue.Full:
                            if generation != self.cinematic_generation:
                                return

            if generation == self.cinematic_generation:
                self.cinematic_worker_done = True
        except Exception as error:
            if generation == self.cinematic_generation:
                self.cinematic_worker_error = (
                    str(error).replace("\n", " ")[:160]
                )
                self.cinematic_worker_done = True

    def start_story_cinematic(self) -> None:
        self.story_sequence_after_id = None
        if self.cinematic_prepare_state == "preparing":
            self.story_sequence_after_id = self.root.after(
                100,
                self.start_story_cinematic,
            )
            return
        if self.cinematic_prepare_state != "ready":
            self.fault_disable_guard(
                "Could not prepare Sonic cinematic: "
                + (self.cinematic_prepare_error or "unknown error")
            )
            return

        self.cancel_cinematic()
        self.overlay_kind = "cinematic"
        self.hide_energy_meter()
        self.title_label.configure(text="")
        self.count_label.configure(text="")
        self.animation_generation += 1
        self.active_frames = []
        self.active_delays = []
        self.background_label.configure(image="", background="black")
        self.stop_music()
        self.stop_event_sound()

        self.cinematic_generation += 1
        generation = self.cinematic_generation
        self.cinematic_cancel_event.clear()
        self.cinematic_frame_queue = queue.Queue(
            maxsize=CINEMATIC_QUEUE_SIZE
        )
        self.cinematic_pending_frame = None
        self.cinematic_worker_done = False
        self.cinematic_worker_error = ""
        self.cinematic_started_at = 0.0
        self.cinematic_photo = None
        threading.Thread(
            target=self.decode_cinematic_frames,
            args=(generation,),
            daemon=True,
        ).start()
        self.cinematic_after_id = self.root.after(
            10,
            self.poll_cinematic_playback,
        )

    def start_cinematic_audio(self) -> bool:
        if not self.cinematic_audio_pcm:
            return True
        if not self.audio_ready or self.cinematic_channel is None:
            return False
        try:
            self.cinematic_sound = pygame.mixer.Sound(
                buffer=self.cinematic_audio_pcm
            )
            self.cinematic_channel.set_volume(MUSIC_VOLUME)
            self.cinematic_channel.play(self.cinematic_sound)
            return True
        except pygame.error:
            self.cinematic_sound = None
            return False

    def display_cinematic_frame(
        self,
        frame_image,
        elapsed: float,
    ) -> None:
        if CINEMATIC_FADE_SECONDS > 0 and elapsed < CINEMATIC_FADE_SECONDS:
            alpha = max(0.0, min(1.0, elapsed / CINEMATIC_FADE_SECONDS))
            black_frame = Image.new("RGB", frame_image.size, "black")
            frame_image = Image.blend(black_frame, frame_image, alpha)

        if (
            self.cinematic_photo is None
            or self.cinematic_photo.width() != frame_image.width
            or self.cinematic_photo.height() != frame_image.height
        ):
            self.cinematic_photo = ImageTk.PhotoImage(frame_image)
            self.background_label.configure(
                image=self.cinematic_photo,
                background="black",
            )
        else:
            # Reuse one Tcl image instead of allocating and destroying a new
            # display object for every frame. This is faster and prevents stale
            # image fragments from surviving a frame swap on the CRT PC.
            self.cinematic_photo.paste(frame_image)

    def poll_cinematic_playback(self) -> None:
        self.cinematic_after_id = None
        if not (
            self.running
            and self.guard_active
            and self.overlay_visible
            and self.overlay_kind == "cinematic"
        ):
            return

        if self.cinematic_worker_error:
            self.fault_disable_guard(
                "Sonic cinematic failed: " + self.cinematic_worker_error
            )
            return

        if self.cinematic_pending_frame is None:
            try:
                self.cinematic_pending_frame = (
                    self.cinematic_frame_queue.get_nowait()
                )
            except queue.Empty:
                self.cinematic_pending_frame = None

        if self.cinematic_started_at == 0.0:
            buffered_frames = self.cinematic_frame_queue.qsize()
            if (
                self.cinematic_pending_frame is None
                or buffered_frames + 1 < CINEMATIC_PREBUFFER_FRAMES
            ):
                self.cinematic_after_id = self.root.after(
                    10,
                    self.poll_cinematic_playback,
                )
                return
            self.cinematic_started_at = time.monotonic()
            if not self.start_cinematic_audio():
                self.fault_disable_guard(
                    "Could not play Sonic cinematic audio"
                )
                return

        elapsed = time.monotonic() - self.cinematic_started_at
        latest_due_frame = None
        while (
            self.cinematic_pending_frame is not None
            and self.cinematic_pending_frame[0] <= elapsed + 0.02
        ):
            latest_due_frame = self.cinematic_pending_frame[1]
            try:
                self.cinematic_pending_frame = (
                    self.cinematic_frame_queue.get_nowait()
                )
            except queue.Empty:
                self.cinematic_pending_frame = None

        # If Tk ever falls behind, display only the newest due frame. Drawing
        # every stale frame creates a feedback loop where video slows down
        # while independently playing audio continues at full speed.
        if latest_due_frame is not None:
            self.display_cinematic_frame(latest_due_frame, elapsed)

        playback_finished = (
            self.cinematic_worker_done
            and self.cinematic_pending_frame is None
            and self.cinematic_frame_queue.empty()
            and elapsed >= max(0.0, self.cinematic_duration - 0.1)
        )
        if playback_finished:
            self.finish_story_cinematic()
            return

        self.cinematic_after_id = self.root.after(
            10,
            self.poll_cinematic_playback,
        )

    def cancel_cinematic(self) -> None:
        self.cinematic_generation += 1
        self.cinematic_cancel_event.set()
        if self.cinematic_after_id is not None:
            try:
                self.root.after_cancel(self.cinematic_after_id)
            except tk.TclError:
                pass
            self.cinematic_after_id = None
        if self.cinematic_channel is not None:
            try:
                self.cinematic_channel.stop()
            except pygame.error:
                pass
        self.cinematic_sound = None
        self.cinematic_pending_frame = None
        self.cinematic_photo = None

    def finish_story_cinematic(self) -> None:
        self.cancel_cinematic()
        self.show_story_robotnik_screen()

    # --------------------------------------------------
    # Overlay behavior
    # --------------------------------------------------

    def append_event_log(self, line: str) -> None:
        try:
            self.event_log_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            if (
                self.event_log_path.exists()
                and self.event_log_path.stat().st_size > 1_000_000
            ):
                old_log = self.event_log_path.with_suffix(".old.log")
                if old_log.exists():
                    old_log.unlink()
                self.event_log_path.replace(old_log)

            with self.event_log_path.open(
                "a",
                encoding="utf-8",
            ) as log_file:
                log_file.write(line + "\n")
        except OSError:
            pass

    def worker_entry(self, worker_name: str, worker) -> None:
        try:
            worker()
        except Exception as error:
            detail = "".join(
                traceback.format_exception(
                    type(error),
                    error,
                    error.__traceback__,
                )
            ).replace("\n", " ")[-800:]
            self.messages.put(
                (
                    "FAULT",
                    f"{worker_name} worker stopped: {detail}",
                    self.activation_generation,
                )
            )

    def write_status(self, message: str, event: bool = True) -> None:
        try:
            self.status_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            timestamp = time.strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            line = f"{timestamp} | {message}"
            self.status_path.write_text(
                line + "\n",
                encoding="utf-8",
            )
            if event:
                self.append_event_log(line)
        except OSError:
            pass

    def load_ring_state(self) -> tuple[int, set[int]]:
        """Load the persistent ring total without making startup fragile."""
        try:
            loaded = json.loads(
                self.ring_counter_path.read_text(encoding="utf-8")
            )
            total = max(0, int(loaded.get("total_rings", 0)))
            milestones = {
                int(value)
                for value in loaded.get("milestones_shown", [])
                if int(value) > 0
            }
            return total, milestones
        except (AttributeError, FileNotFoundError, OSError, TypeError, ValueError):
            return 0, set()

    def save_ring_state(self) -> None:
        try:
            self.ring_counter_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            temporary_path = self.ring_counter_path.with_suffix(".tmp")
            temporary_path.write_text(
                json.dumps(
                    {
                        "total_rings": self.ring_count,
                        "milestones_shown": sorted(
                            self.ring_milestones_shown
                        ),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            temporary_path.replace(self.ring_counter_path)
        except OSError:
            self.write_status(
                "RING COUNTER SAVE FAILED | total="
                + str(self.ring_count),
                event=False,
            )

    def ring_input_worker(self) -> None:
        """Watch every Windows joystick encoder for the configured coin input."""
        try:
            winmm = ctypes.windll.winmm
            joystick_slots = max(1, int(winmm.joyGetNumDevs()))
        except (AttributeError, OSError, ValueError) as error:
            self.ring_joystick_error = str(error).replace("\n", " ")[:120]
            return

        button_mask = joystick_button_mask(RING_JOYSTICK_BUTTON)
        while self.running and not self.ring_input_stop_event.is_set():
            try:
                now = time.monotonic()
                live_keys = set()

                for joystick_id in range(joystick_slots):
                    joystick_state = Win32JoyInfoEx()
                    joystick_state.dwSize = ctypes.sizeof(Win32JoyInfoEx)
                    joystick_state.dwFlags = JOY_RETURNBUTTONS
                    result = winmm.joyGetPosEx(
                        joystick_id,
                        ctypes.byref(joystick_state),
                    )
                    if result != JOYERR_NOERROR:
                        continue

                    live_keys.add(joystick_id)
                    pressed = bool(joystick_state.dwButtons & button_mask)
                    was_pressed = self.ring_button_states.get(joystick_id)
                    last_press_at = self.ring_last_press_at.get(
                        joystick_id,
                        0.0,
                    )
                    if (
                        pressed
                        and was_pressed is False
                        and now - last_press_at >= RING_DEBOUNCE_SECONDS
                    ):
                        self.ring_last_press_at[joystick_id] = now
                        self.messages.put(("RING", str(joystick_id), -1))
                    self.ring_button_states[joystick_id] = pressed

                for state_key in tuple(self.ring_button_states):
                    if state_key not in live_keys:
                        del self.ring_button_states[state_key]
                        self.ring_last_press_at.pop(state_key, None)

                self.ring_joystick_signature = tuple(
                    sorted(live_keys)
                )
                self.ring_joystick_error = ""
            except (AttributeError, OSError, ValueError) as error:
                self.ring_joystick_error = str(error)[:120]
                self.ring_button_states.clear()
                if self.ring_input_stop_event.wait(0.5):
                    break
                continue

            if self.ring_input_stop_event.wait(0.01):
                break

    def ring_burst_is_eligible(self) -> bool:
        if not self.guard_active:
            return False
        if self.overlay_kind == "robotnik":
            return True
        if self.guard_mode == "normal" and self.accepted_count == 0:
            return self.can_show_story_announcement()
        return False

    def handle_ring_entry(self) -> None:
        previous_total = self.ring_count
        self.ring_count += 1
        self.save_ring_state()
        self.write_status(
            f"RING ENTERED | total={self.ring_count}",
        )

        if (
            previous_total < RING_MILESTONE <= self.ring_count
            and RING_MILESTONE not in self.ring_milestones_shown
        ):
            self.ring_milestones_shown.add(RING_MILESTONE)
            self.save_ring_state()
            self.pending_ring_milestone = True
            self.maybe_show_pending_ring_milestone()

        if self.ring_burst_active or not self.ring_burst_is_eligible():
            return

        self.ring_burst_active = True
        self.ring_burst_game_seen = False
        self.ring_burst_game_seen_since = 0.0
        self.write_status("RING BURST ACTIVE | waiting for a game")

        if self.overlay_visible:
            self.hide_overlay()
        else:
            self.hide_story_announcement()
            self.cancel_normal_warning()

        if not self.pending_ring_milestone:
            self.show_plain_announcement(
                RING_BURST_TITLE,
                RING_BURST_MESSAGE,
                "#66ff99",
                RING_BURST_ANNOUNCEMENT_SECONDS,
            )

    def show_plain_announcement(
        self,
        title: str,
        detail: str,
        color: str,
        duration_seconds: float,
    ) -> bool:
        if not self.can_show_story_announcement():
            return False

        self.hide_story_announcement()
        x, y, width, height = self.overlay_monitor_bounds
        banner_width = max(1, int(width * 0.90))
        banner_height = max(88, min(200, int(height * 0.32)))
        banner_x = x + (width - banner_width) // 2
        banner_y = y + (height - banner_height) // 2
        title_size = self.fit_font_size(
            (title,),
            max_size=max(16, min(32, int(height * 0.065))),
            min_size=12,
            available_width=banner_width,
        )
        detail_size = self.fit_font_size(
            tuple(detail.splitlines()),
            max_size=max(12, min(24, int(height * 0.045))),
            min_size=10,
            available_width=banner_width,
        )
        self.announcement_title_label.configure(
            text=title,
            foreground=color,
            font=("Arial", title_size, "bold"),
            wraplength=max(1, int(banner_width * 0.82)),
            justify="center",
        )
        self.announcement_detail_label.configure(
            text=detail,
            font=("Arial", detail_size, "bold"),
            wraplength=max(1, int(banner_width * 0.82)),
            justify="center",
        )
        self.announcement_window.geometry(
            f"{banner_width}x{banner_height}{banner_x:+d}{banner_y:+d}"
        )
        self.apply_announcement_window_style()
        self.announcement_window.deiconify()
        try:
            window_handle = self.announcement_window.winfo_id()
            ctypes.windll.user32.SetWindowPos(
                window_handle,
                HWND_TOPMOST,
                banner_x,
                banner_y,
                banner_width,
                banner_height,
                SWP_SHOWWINDOW | SWP_NOACTIVATE,
            )
            ctypes.windll.user32.ShowWindow(
                window_handle,
                SW_SHOWNOACTIVATE,
            )
        except (AttributeError, tk.TclError):
            pass

        self.announcement_after_id = self.root.after(
            int(duration_seconds * 1000),
            self.hide_story_announcement,
        )
        return True

    def maybe_show_pending_ring_milestone(self) -> None:
        if not self.running or not self.pending_ring_milestone:
            return
        if not self.show_plain_announcement(
            RING_MILESTONE_TITLE,
            RING_MILESTONE_MESSAGE,
            "#ffdd55",
            RING_ANNOUNCEMENT_SECONDS,
        ):
            return
        self.pending_ring_milestone = False
        self.write_status("RING MILESTONE DISPLAYED | 50 rings")

    def consume_ring_burst_on_return(self) -> None:
        if not self.ring_burst_active:
            return

        self.ring_burst_active = False
        self.ring_burst_game_seen = False
        self.ring_burst_game_seen_since = 0.0
        self.write_status("RING BURST CONSUMED | Big Box returned")

        if not self.guard_active:
            return

        if self.guard_mode == "story" and self.story_intro_completed:
            if self.accepted_count == TOTAL_EMERALDS:
                self.show_missing_overlay(0)
                if self.guard_active and self.overlay_kind == "robotnik":
                    self.begin_final_emerald_transition()
                return
            self.pending_overlay_missing = (
                TOTAL_EMERALDS - (self.accepted_count or 0)
            )
            self.maybe_show_pending_overlay()

    def handle_ring_burst_foreground(self) -> None:
        if not self.ring_burst_active or not self.guard_active:
            return

        self.update_overlay_gate()
        now = time.monotonic()
        if self.foreground_process_name in EMULATOR_PROCESS_NAMES:
            if self.ring_burst_game_seen_since == 0.0:
                self.ring_burst_game_seen_since = now
                self.write_status("RING BURST GAME LAUNCH DETECTED")
            if (
                not self.ring_burst_game_seen
                and now - self.ring_burst_game_seen_since
                >= RING_GAME_COMMIT_SECONDS
            ):
                self.ring_burst_game_seen = True
                self.write_status("RING BURST GAME COMMITTED")
            return

        big_box_ready = (
            self.foreground_process_name == BIG_BOX_PROCESS_NAME
            and self.overlay_gate_state == "BIGBOX_READY"
        )
        if (
            big_box_ready
            and self.guard_mode == "story"
            and self.story_intro_completed
            and self.accepted_count == TOTAL_EMERALDS
        ):
            self.consume_ring_burst_on_return()
            return

        if (
            big_box_ready
            and not self.ring_burst_game_seen
            and self.ring_burst_game_seen_since != 0.0
        ):
            self.ring_burst_game_seen_since = 0.0
            self.write_status(
                "RING BURST GAME LAUNCH ABORTED | burst remains available"
            )

        if (
            self.ring_burst_game_seen
            and big_box_ready
        ):
            self.consume_ring_burst_on_return()

    def handle_tk_exception(self, exception_type, exception, trace) -> None:
        detail = "".join(
            traceback.format_exception(
                exception_type,
                exception,
                trace,
            )
        ).replace("\n", " ")[-1000:]
        self.write_status("UNHANDLED APP ERROR | " + detail)

        if self.guard_active or self.suspended_process_handle:
            self.fault_disable_guard("Application error")
        else:
            self.last_fault = "Application error — guard remains disabled"
            self.overlay_gate_state = "DISABLED_ERROR"

    def create_control_panel(self) -> None:
        self.control_window = tk.Toplevel(self.root)
        self.control_window.title("Magnetic Arcade Guard — Story/Normal Edition")
        self.control_window.geometry("680x410+40+40")
        self.control_window.minsize(620, 380)
        self.control_window.resizable(True, True)
        self.control_window.configure(background="#202020")
        try:
            self.control_window.attributes("-toolwindow", False)
        except tk.TclError:
            pass
        self.control_window.protocol(
            "WM_DELETE_WINDOW",
            self.exit_program,
        )

        self.control_state_var = tk.StringVar()
        self.control_sensor_var = tk.StringVar()
        self.control_details_var = tk.StringVar()

        tk.Label(
            self.control_window,
            text="MAGNETIC ARCADE GUARD",
            font=("Arial", 18, "bold"),
            foreground="white",
            background="#202020",
        ).pack(pady=(14, 4))

        self.control_state_label = tk.Label(
            self.control_window,
            textvariable=self.control_state_var,
            font=("Arial", 16, "bold"),
            foreground="#9e9e9e",
            background="#202020",
        )
        self.control_state_label.pack(pady=2)

        tk.Label(
            self.control_window,
            textvariable=self.control_sensor_var,
            font=("Arial", 13, "bold"),
            foreground="white",
            background="#202020",
        ).pack(pady=2)

        tk.Label(
            self.control_window,
            textvariable=self.control_details_var,
            justify="center",
            font=("Arial", 10),
            foreground="#c8c8c8",
            background="#202020",
        ).pack(pady=(3, 8))

        button_frame = tk.Frame(
            self.control_window,
            background="#202020",
        )
        button_frame.pack()

        self.control_story_button = tk.Button(
            button_frame,
            text="STORY MODE",
            width=24,
            command=self.select_story_mode,
        )
        self.control_story_button.grid(
            row=0,
            column=0,
            padx=6,
            pady=(0, 8),
        )

        self.control_normal_button = tk.Button(
            button_frame,
            text="NORMAL MODE",
            width=24,
            command=self.select_normal_mode,
        )
        self.control_normal_button.grid(
            row=0,
            column=1,
            padx=6,
            pady=(0, 8),
        )

        self.control_deactivate_button = tk.Button(
            button_frame,
            text="DEACTIVATE GUARD",
            width=24,
            command=self.deactivate_guard,
        )
        self.control_deactivate_button.grid(
            row=1,
            column=0,
            padx=6,
        )

        tk.Button(
            button_frame,
            text="CLOSE PROGRAM",
            width=24,
            command=self.exit_program,
        ).grid(
            row=1,
            column=1,
            padx=6,
        )

        tk.Label(
            self.control_window,
            text=(
                "Keyboard: Story Mode Ctrl+Alt+F10  |  "
                "Activate Ctrl+Alt+F12\n"
                "Deactivate Ctrl+Alt+F11  |  "
                "Close Ctrl+Shift+F12"
            ),
            font=("Arial", 9),
            foreground="#8f8f8f",
            background="#202020",
        ).pack(pady=(10, 0))

    def get_control_state(self) -> tuple[str, str]:
        mode_name = "STORY" if self.guard_mode == "story" else "NORMAL"
        if not self.guard_active:
            if self.last_fault:
                return "DISABLED — " + self.last_fault, "#ff9966"
            return f"{mode_name} MODE SELECTED — GUARD OFF", "#9e9e9e"

        if self.completion_in_progress:
            return "SONIC VICTORY SCREEN", "#66ccff"

        if self.overlay_visible:
            overlay_states = {
                "normal_warning": "NORMAL MODE WARNING ACTIVE",
                "story_shutdown": "STORY MODE ARCADE SHUTDOWN",
                "story_question": "STORY MODE HERO PROMPT",
                "story_eggman": "STORY MODE EGGMAN REVEAL",
                "cinematic": "SONIC CD CINEMATIC PLAYING",
                "robotnik": "ROBOTNIK LOCK SCREEN ACTIVE",
            }
            return (
                overlay_states.get(
                    self.overlay_kind,
                    "FULL-SCREEN OVERLAY ACTIVE",
                ),
                "#ff6666",
            )

        if self.accepted_count is None:
            return f"{mode_name} MODE — WAITING FOR SENSOR", "#ffcc66"

        if self.accepted_count < TOTAL_EMERALDS:
            if self.overlay_gate_state == "WAITING_FOR_BIGBOX_READY":
                return (
                    "ACTIVE — WAITING FOR BIG BOX TO SETTLE",
                    "#ffcc66",
                )
            if self.overlay_gate_state == "WAITING_FOR_BIGBOX":
                return "ACTIVE — WAITING FOR BIG BOX", "#ffcc66"
            return f"{mode_name} MODE — MONITORING", "#66dd88"

        return f"{mode_name} MODE — ALL EMERALDS PRESENT", "#66dd88"

    def update_control_panel(self) -> None:
        if not self.control_window or not self.control_window.winfo_exists():
            return

        state_text, state_color = self.get_control_state()
        sensor_text = (
            "Emeralds detected: ? / "
            f"{TOTAL_EMERALDS}"
            if self.accepted_count is None
            else (
                "Emeralds detected: "
                f"{self.accepted_count} / {TOTAL_EMERALDS}"
            )
        )
        if not self.guard_active:
            reader_text = "not checked (guard off)"
        else:
            reader_text = (
                "connected" if self.reader_connected else "not connected"
            )
        input_text = (
            f"blocked (Big Box PID {self.suspended_process_id})"
            if self.suspended_process_id
            else "normal"
        )
        if self.ring_joystick_error:
            ring_input_text = "ERROR: " + self.ring_joystick_error
        else:
            joystick_count = len(self.ring_joystick_signature)
            joystick_word = "joystick" if joystick_count == 1 else "joysticks"
            ring_input_text = (
                f"{joystick_count} {joystick_word}, button "
                f"{RING_JOYSTICK_BUTTON}, {round(RING_DEBOUNCE_SECONDS * 1000)}ms debounce"
            )
        details_text = (
            f"Reader: {reader_text}    "
            f"Foreground: {self.foreground_process_name}\n"
            f"Gate: {self.overlay_gate_state}    "
            f"Input: {input_text}\n"
            f"Rings entered: {self.ring_count}    "
            f"Ring input: {ring_input_text}\n"
            "Return SFX: ready    Removal SFX: "
            + ("ready" if self.removal_sound is not None else "not installed")
            + "    Cinematic: "
            + self.cinematic_prepare_state
        )

        self.control_state_var.set(state_text)
        self.control_sensor_var.set(sensor_text)
        self.control_details_var.set(details_text)
        self.control_state_label.configure(foreground=state_color)
        self.control_deactivate_button.configure(
            state=tk.NORMAL if self.guard_active else tk.DISABLED
        )
        story_selected = self.guard_mode == "story"
        self.control_story_button.configure(
            relief=tk.SUNKEN if story_selected else tk.RAISED,
            background="#d6b84b" if story_selected else "SystemButtonFace",
        )
        self.control_normal_button.configure(
            relief=tk.SUNKEN if not story_selected else tk.RAISED,
            background="#66b8e8" if not story_selected else "SystemButtonFace",
        )

    def set_control_panel_visible(self, visible: bool) -> None:
        # The operator panel now follows ordinary Windows behavior. Big Box
        # and the topmost takeover overlay naturally cover it, but the guard
        # never withdraws, restores, or unminimizes the panel on its own.
        return

    def update_control_panel_visibility(self) -> None:
        try:
            foreground_window = ctypes.windll.user32.GetForegroundWindow()
        except AttributeError:
            foreground_window = 0

        self.foreground_process_name = self.get_window_process_name(
            foreground_window
        )

    def control_panel_heartbeat(self) -> None:
        if self.running:
            self.update_control_panel_visibility()
            self.update_control_panel()
            self.root.after(250, self.control_panel_heartbeat)

    def status_heartbeat(self) -> None:
        self.update_control_panel()
        if self.guard_active:
            state = "ACTIVE"
        elif self.last_fault:
            state = "DISABLED_ERROR"
        else:
            state = "DORMANT"
        if not PYCAW_AVAILABLE:
            audio_state = "unavailable"
        elif self.audio_muted:
            audio_state = f"muted={len(self.muted_audio_sessions)}"
        elif self.audio_last_error:
            audio_state = "error"
        else:
            audio_state = "ready"

        self.write_status(
            f"RUNNING | {state} | "
            f"mode={self.guard_mode} | "
            f"connected={self.reader_connected} | "
            f"audio={audio_state} | "
            f"cinematic={self.cinematic_prepare_state} | "
            f"overlay_monitor={self.overlay_monitor_bounds} | "
            f"return_window={self.return_window_handle or 'none'} | "
            f"input_blocked_pid={self.suspended_process_id or 'none'} | "
            f"foreground={self.foreground_process_name} | "
            f"gate={self.overlay_gate_state} | "
            f"pending_missing={self.pending_overlay_missing}",
            event=False,
        )

        if self.running:
            self.root.after(2000, self.status_heartbeat)

    def get_window_process_name(self, window_handle: int) -> str:
        if not window_handle:
            return "unknown"

        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            # Ring Power resumes Big Box so it can launch an emulator. Once an
            # emulator owns the foreground, a delayed focus retry must never
            # pull focus back to Big Box or interfere with the display-mode
            # transition.
            if self.ring_burst_active:
                foreground_window = user32.GetForegroundWindow()
                foreground_process = self.get_window_process_name(
                    foreground_window
                )
                if foreground_process in EMULATOR_PROCESS_NAMES:
                    self.return_window_handle = 0
                    self.write_status(
                        "FOCUS RESTORE ENDED | emulator owns foreground"
                    )
                    return
            user32.GetWindowThreadProcessId.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_ulong),
            ]
            user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
            kernel32.OpenProcess.argtypes = [
                ctypes.c_ulong,
                ctypes.c_bool,
                ctypes.c_ulong,
            ]
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.QueryFullProcessImageNameW.argtypes = [
                ctypes.c_void_p,
                ctypes.c_ulong,
                ctypes.c_wchar_p,
                ctypes.POINTER(ctypes.c_ulong),
            ]
            kernel32.QueryFullProcessImageNameW.restype = ctypes.c_bool
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_bool
            process_id = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(
                window_handle,
                ctypes.byref(process_id),
            )
            if not process_id.value:
                return "unknown"

            process_handle = kernel32.OpenProcess(
                0x1000,  # PROCESS_QUERY_LIMITED_INFORMATION
                False,
                process_id.value,
            )
            if not process_handle:
                return "unknown"

            try:
                path_buffer = ctypes.create_unicode_buffer(1024)
                path_length = ctypes.c_ulong(len(path_buffer))
                if not kernel32.QueryFullProcessImageNameW(
                    process_handle,
                    0,
                    path_buffer,
                    ctypes.byref(path_length),
                ):
                    return "unknown"

                return Path(path_buffer.value).name.lower()
            finally:
                kernel32.CloseHandle(process_handle)

        except (AttributeError, OSError, ValueError):
            return "unknown"

    def get_window_process_id(self, window_handle: int) -> int:
        if not window_handle:
            return 0

        try:
            process_id = ctypes.c_ulong()
            ctypes.windll.user32.GetWindowThreadProcessId(
                window_handle,
                ctypes.byref(process_id),
            )
            return process_id.value
        except (AttributeError, OSError, ValueError):
            return 0

    def get_window_rect(self, window_handle: int):
        if not window_handle:
            return None

        try:
            rect = Win32Rect()
            if not ctypes.windll.user32.GetWindowRect(
                window_handle,
                ctypes.byref(rect),
            ):
                return None

            return (
                rect.left,
                rect.top,
                rect.right,
                rect.bottom,
            )
        except (AttributeError, OSError, ValueError):
            return None

    def window_covers_monitor(self, window_rect, monitor_bounds) -> bool:
        if window_rect is None or monitor_bounds is None:
            return False

        left, top, right, bottom = window_rect
        monitor_x, monitor_y, monitor_width, monitor_height = monitor_bounds
        monitor_right = monitor_x + monitor_width
        monitor_bottom = monitor_y + monitor_height
        intersection_width = max(
            0,
            min(right, monitor_right) - max(left, monitor_x),
        )
        intersection_height = max(
            0,
            min(bottom, monitor_bottom) - max(top, monitor_y),
        )
        monitor_area = max(1, monitor_width * monitor_height)
        coverage = (
            intersection_width * intersection_height / monitor_area
        )
        return coverage >= 0.95

    def reset_big_box_readiness(self) -> None:
        self.big_box_candidate_window = 0
        self.big_box_candidate_since = 0.0
        self.big_box_candidate_monitor = None
        self.big_box_candidate_rect = None

    def launch_resume_watchdog(self, process_id: int) -> bool:
        if not self.stop_resume_watchdog():
            return False
        try:
            cancel_path = (
                self.status_path.parent
                / f"watchdog-{os.getpid()}-{time.time_ns()}.cancel"
            )
            self.resume_watchdog_cancel_path = cancel_path
            if getattr(sys, "frozen", False):
                command = [
                    sys.executable,
                    "--resume-watchdog",
                    str(os.getpid()),
                    str(process_id),
                    str(cancel_path),
                ]
            else:
                command = [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--resume-watchdog",
                    str(os.getpid()),
                    str(process_id),
                    str(cancel_path),
                ]

            watchdog_environment = os.environ.copy()
            if getattr(sys, "frozen", False):
                # This is a new independent copy of the one-file EXE. Tell
                # PyInstaller not to treat it as an internal child process.
                watchdog_environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"

            self.resume_watchdog_process = subprocess.Popen(
                command,
                creationflags=CREATE_NO_WINDOW,
                close_fds=True,
                env=watchdog_environment,
            )
            time.sleep(0.10)
            if self.resume_watchdog_process.poll() is not None:
                self.resume_watchdog_process = None
                self.resume_watchdog_cancel_path = None
                self.write_status(
                    "RESUME WATCHDOG FAILED | helper exited during startup"
                )
                return False
            return True
        except (OSError, ValueError) as error:
            self.resume_watchdog_process = None
            self.resume_watchdog_cancel_path = None
            self.write_status(
                "RESUME WATCHDOG FAILED | "
                + str(error).replace("\n", " ")[:120]
            )
            return False

    def stop_resume_watchdog(self) -> bool:
        watchdog = self.resume_watchdog_process
        cancel_path = self.resume_watchdog_cancel_path
        self.resume_watchdog_process = None
        self.resume_watchdog_cancel_path = None
        if watchdog is None:
            return True

        if watchdog.poll() is not None:
            if cancel_path is not None:
                try:
                    cancel_path.unlink(missing_ok=True)
                except OSError:
                    pass
            return True

        try:
            cancel_path.parent.mkdir(parents=True, exist_ok=True)
            cancel_path.write_text("cancel\n", encoding="utf-8")
        except (AttributeError, OSError):
            self.resume_watchdog_process = watchdog
            self.resume_watchdog_cancel_path = cancel_path
            self.write_status(
                "RESUME WATCHDOG CANCEL FAILED | closing guard is required"
            )
            self.last_fault = "Crash helper could not be cancelled"
            self.guard_active = False
            if self.running and not self.shutdown_started:
                try:
                    self.root.after(0, self.exit_program)
                except tk.TclError:
                    pass
            return False

        def reap_watchdog() -> None:
            try:
                watchdog.wait(timeout=30.0)
            except (OSError, subprocess.TimeoutExpired):
                return
            try:
                cancel_path.unlink(missing_ok=True)
            except OSError:
                pass

        threading.Thread(target=reap_watchdog, daemon=True).start()
        return True

    def suspend_return_process(self) -> bool:
        """Stop Big Box from handling controller input while the overlay is up."""
        if self.suspended_process_handle:
            return True
        if not self.return_window_handle:
            self.write_status("INPUT BLOCK FAILED | no return window")
            return False

        if (
            self.get_window_process_name(self.return_window_handle)
            != BIG_BOX_PROCESS_NAME
        ):
            self.write_status(
                "INPUT BLOCK FAILED | return window is not Big Box"
            )
            return False

        process_id = self.get_window_process_id(
            self.return_window_handle
        )
        if not process_id:
            self.write_status("INPUT BLOCK FAILED | no return process")
            return False

        if not self.launch_resume_watchdog(process_id):
            return False

        try:
            kernel32 = ctypes.windll.kernel32
            ntdll = ctypes.windll.ntdll
            ntdll.NtSuspendProcess.argtypes = [ctypes.c_void_p]
            ntdll.NtSuspendProcess.restype = ctypes.c_long

            process_handle = kernel32.OpenProcess(
                PROCESS_SUSPEND_RESUME,
                False,
                process_id,
            )
            if not process_handle:
                self.write_status(
                    "INPUT BLOCK FAILED | "
                    f"pid={process_id} | access denied"
                )
                self.stop_resume_watchdog()
                return False

            status = ntdll.NtSuspendProcess(process_handle)
            if status != 0:
                kernel32.CloseHandle(process_handle)
                self.write_status(
                    "INPUT BLOCK FAILED | "
                    f"pid={process_id} | status={status}"
                )
                self.stop_resume_watchdog()
                return False

            self.suspended_process_handle = process_handle
            self.suspended_process_id = process_id
            self.write_status(
                "CONTROLLER INPUT BLOCKED | "
                f"suspended Big Box pid={process_id}"
            )
            return True
        except (AttributeError, OSError, ValueError) as error:
            self.stop_resume_watchdog()
            self.write_status(
                "INPUT BLOCK FAILED | "
                + str(error).replace("\n", " ")[:120]
            )
            return False

    def resume_return_process(self) -> bool:
        process_handle = self.suspended_process_handle
        process_id = self.suspended_process_id

        if not process_handle:
            self.stop_resume_watchdog()
            return True

        try:
            ntdll = ctypes.windll.ntdll
            kernel32 = ctypes.windll.kernel32
            ntdll.NtResumeProcess.argtypes = [ctypes.c_void_p]
            ntdll.NtResumeProcess.restype = ctypes.c_long
            exit_code = ctypes.c_ulong()
            if (
                kernel32.GetExitCodeProcess(
                    process_handle,
                    ctypes.byref(exit_code),
                )
                and exit_code.value != STILL_ACTIVE
            ):
                kernel32.CloseHandle(process_handle)
                self.suspended_process_handle = None
                self.suspended_process_id = 0
                self.stop_resume_watchdog()
                return True

            status = ntdll.NtResumeProcess(process_handle)
            if status == 0:
                kernel32.CloseHandle(process_handle)
                self.suspended_process_handle = None
                self.suspended_process_id = 0
                self.stop_resume_watchdog()
                self.write_status(
                    "CONTROLLER INPUT RESTORED | "
                    f"Big Box pid={process_id}"
                )
                return True
            else:
                self.write_status(
                    "INPUT RESTORE FAILED | "
                    f"pid={process_id} | status={status}"
                )
                return False
        except (AttributeError, OSError, ValueError) as error:
            self.write_status(
                "INPUT RESTORE FAILED | "
                + str(error).replace("\n", " ")[:120]
            )
            return False

    def update_overlay_gate(self) -> bool:
        try:
            user32 = ctypes.windll.user32
            foreground_window = user32.GetForegroundWindow()
        except AttributeError:
            user32 = None
            foreground_window = 0

        process_name = self.get_window_process_name(foreground_window)
        self.foreground_process_name = process_name

        if process_name == BIG_BOX_PROCESS_NAME:
            monitor_bounds = self.get_monitor_bounds(foreground_window)
            window_rect = self.get_window_rect(foreground_window)
            window_ready = bool(
                user32
                and user32.IsWindowVisible(foreground_window)
                and not user32.IsIconic(foreground_window)
            )

            if (
                not window_ready
                or monitor_bounds is None
                or window_rect is None
                or window_rect[2] <= window_rect[0]
                or window_rect[3] <= window_rect[1]
                or not self.window_covers_monitor(
                    window_rect,
                    monitor_bounds,
                )
            ):
                self.reset_big_box_readiness()
                gate_state = "WAITING_FOR_BIGBOX_READY"
                allowed = False
                self.overlay_gate_state = gate_state
                return allowed

            candidate_changed = (
                foreground_window != self.big_box_candidate_window
                or monitor_bounds != self.big_box_candidate_monitor
                or window_rect != self.big_box_candidate_rect
            )

            if candidate_changed:
                self.big_box_candidate_window = foreground_window
                self.big_box_candidate_since = time.monotonic()
                self.big_box_candidate_monitor = monitor_bounds
                self.big_box_candidate_rect = window_rect
                gate_state = "WAITING_FOR_BIGBOX_READY"
                allowed = False
                self.overlay_gate_state = gate_state
                return allowed

            ready_for = time.monotonic() - self.big_box_candidate_since
            if ready_for < BIG_BOX_READY_DELAY_SECONDS:
                gate_state = "WAITING_FOR_BIGBOX_READY"
                allowed = False
                self.overlay_gate_state = gate_state
                return allowed

            gate_state = "BIGBOX_READY"
            allowed = True
        elif process_name in EMULATOR_PROCESS_NAMES:
            self.reset_big_box_readiness()
            gate_state = "WAITING_FOR_BIGBOX"
            allowed = False
        else:
            self.reset_big_box_readiness()
            gate_state = "WAITING_FOR_BIGBOX"
            allowed = False

        self.overlay_gate_state = gate_state
        return allowed

    def request_missing_overlay(self, missing_count: int) -> None:
        self.cancel_final_emerald_transition()
        self.pending_overlay_missing = missing_count

        # Once the guard is already visible over Big Box, update the counter
        # immediately as sensors change. The Big Box gate only applies before
        # the overlay has appeared.
        if self.overlay_visible:
            self.pending_overlay_missing = None
            self.show_missing_overlay(missing_count)
            return

        self.maybe_show_pending_overlay()

    def maybe_show_pending_overlay(self) -> None:
        if (
            not self.running
            or not self.guard_active
            or self.overlay_visible
            or self.completion_in_progress
            or self.pending_overlay_missing is None
        ):
            return

        if not self.update_overlay_gate():
            log_key = (
                f"{self.overlay_gate_state}|"
                f"{self.foreground_process_name}|"
                f"{self.pending_overlay_missing}"
            )
            if log_key != self.last_gate_log_key:
                self.last_gate_log_key = log_key
                self.write_status(
                    "OVERLAY WAITING FOR BIG BOX | "
                    f"foreground={self.foreground_process_name} | "
                    f"missing={self.pending_overlay_missing}"
                )
            return

        missing_count = self.pending_overlay_missing
        self.pending_overlay_missing = None
        self.last_gate_log_key = ""
        if (
            self.guard_mode == "story"
            and self.accepted_count == 0
            and not self.story_intro_completed
        ):
            self.start_story_shutdown_sequence()
        else:
            self.show_missing_overlay(missing_count)

    def foreground_watchdog(self) -> None:
        if self.running and self.guard_active:
            self.handle_ring_burst_foreground()
            # Keep the Big Box readiness timer warm even before a sensor event.
            # That makes announcements and takeovers feel immediate while still
            # retaining the settle delay after an emulator closes.
            if (
                not self.overlay_visible
                and self.pending_overlay_missing is None
            ):
                self.update_overlay_gate()
            self.maybe_show_pending_overlay()

        if self.running:
            self.maybe_show_pending_ring_milestone()

        if self.running:
            self.root.after(250, self.foreground_watchdog)

    def capture_return_window(self) -> None:
        self.return_window_handle = 0
        try:
            foreground_window = ctypes.windll.user32.GetForegroundWindow()
            own_window = self.root.winfo_id()

            if (
                foreground_window
                and foreground_window != own_window
                and self.get_window_process_name(foreground_window)
                == BIG_BOX_PROCESS_NAME
            ):
                self.return_window_handle = foreground_window
                monitor_bounds = self.get_monitor_bounds(
                    foreground_window
                )
                if monitor_bounds is not None:
                    self.overlay_monitor_bounds = monitor_bounds
                self.write_status(
                    "CAPTURED WINDOW | "
                    + self.describe_window(foreground_window)
                    + " | monitor="
                    + str(self.overlay_monitor_bounds)
                )
        except (AttributeError, tk.TclError):
            self.return_window_handle = 0

    def get_monitor_bounds(self, window_handle: int):
        try:
            user32 = ctypes.windll.user32
            monitor_handle = user32.MonitorFromWindow(
                window_handle,
                2,  # MONITOR_DEFAULTTONEAREST
            )
            if not monitor_handle:
                return None

            monitor_info = Win32MonitorInfo()
            monitor_info.cbSize = ctypes.sizeof(Win32MonitorInfo)
            if not user32.GetMonitorInfoW(
                monitor_handle,
                ctypes.byref(monitor_info),
            ):
                return None

            monitor_rect = monitor_info.rcMonitor
            return (
                monitor_rect.left,
                monitor_rect.top,
                monitor_rect.right - monitor_rect.left,
                monitor_rect.bottom - monitor_rect.top,
            )
        except AttributeError:
            return None

    def prepare_overlay_monitor(self) -> None:
        x, y, width, height = self.overlay_monitor_bounds

        if (
            width != self.screen_width
            or height != self.screen_height
        ):
            self.screen_width = width
            self.screen_height = height
            self.title_font_size = self.fit_font_size(
                (LOCK_MESSAGE, COMPLETION_MESSAGE),
                max_size=min(34, max(18, int(width * 0.04))),
                min_size=12,
            )
            self.count_font_size = self.fit_font_size(
                (
                    self.missing_text(TOTAL_EMERALDS),
                    self.missing_text(1),
                    RESTORED_MESSAGE,
                    GAME_ON_MESSAGE,
                ),
                max_size=min(64, max(36, int(height * 0.10))),
                min_size=12,
            )
            self.title_label.configure(
                font=("Arial", self.title_font_size, "bold")
            )
            self.count_label.configure(
                font=("Arial", self.count_font_size, "bold")
            )

            if PIL_AVAILABLE:
                self.background_frames = []
                self.background_delays = []
                self.completion_frames = []
                self.completion_delays = []
                self.supersonic_frames = []
                self.supersonic_delays = []
                self.background_display_width = 0
                self.background_display_height = 0
                self.load_background_image()
                self.load_completion_image()
                self.load_supersonic_image()

            self.position_title()
            self.position_counter()

        try:
            self.root.overrideredirect(True)
            self.root.geometry(
                f"{width}x{height}{x:+d}{y:+d}"
            )
            self.apply_overlay_bounds(show=False)
        except (AttributeError, tk.TclError):
            pass

    def apply_overlay_bounds(self, show: bool = False) -> bool:
        """Force the borderless overlay to cover the selected monitor."""
        x, y, width, height = self.overlay_monitor_bounds

        try:
            self.root.geometry(
                f"{width}x{height}{x:+d}{y:+d}"
            )
            self.root.update_idletasks()
            window_handle = self.root.winfo_id()
            user32 = ctypes.windll.user32
            positioned = user32.SetWindowPos(
                window_handle,
                HWND_TOPMOST,
                x,
                y,
                width,
                height,
                SWP_SHOWWINDOW if show else 0,
            )
            return bool(positioned)
        except (AttributeError, tk.TclError):
            return False

    def reveal_overlay_window(self) -> bool:
        try:
            self.root.deiconify()
            self.root.overrideredirect(True)
            if not self.apply_overlay_bounds(show=True):
                return False
            self.root.attributes("-topmost", True)
            self.root.lift()
            self.root.update_idletasks()

            window_handle = self.root.winfo_id()
            window_rect = self.get_window_rect(window_handle)
            user32 = ctypes.windll.user32
            if (
                not user32.IsWindowVisible(window_handle)
                or window_rect is None
                or not self.window_covers_monitor(
                    window_rect,
                    self.overlay_monitor_bounds,
                )
            ):
                return False

            self.force_overlay_focus()
            return True
        except (AttributeError, tk.TclError):
            return False

    def describe_window(self, window_handle: int) -> str:
        try:
            user32 = ctypes.windll.user32
            title_length = user32.GetWindowTextLengthW(window_handle)
            title_buffer = ctypes.create_unicode_buffer(title_length + 1)
            user32.GetWindowTextW(
                window_handle,
                title_buffer,
                title_length + 1,
            )
            process_id = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(
                window_handle,
                ctypes.byref(process_id),
            )
            title = title_buffer.value or "(untitled)"
            return (
                f"handle={window_handle} | pid={process_id.value} | "
                f"title={title}"
            )
        except (AttributeError, ValueError):
            return f"handle={window_handle}"

    def force_overlay_focus(self) -> None:
        try:
            self.root.update_idletasks()
            self.set_overlay_z_order(True)
            self.root.focus_force()
            ctypes.windll.user32.SetForegroundWindow(
                self.root.winfo_id()
            )
        except (AttributeError, tk.TclError):
            pass

    def set_overlay_z_order(self, topmost: bool) -> None:
        try:
            user32 = ctypes.windll.user32
            hwnd_topmost = HWND_TOPMOST if topmost else HWND_NOTOPMOST
            flags = SWP_NOMOVE | SWP_NOSIZE
            if topmost:
                flags |= SWP_SHOWWINDOW
            user32.SetWindowPos(
                self.root.winfo_id(),
                hwnd_topmost,
                0,
                0,
                0,
                0,
                flags,
            )
        except (AttributeError, tk.TclError):
            pass

    def restore_return_window(self, attempt: int = 0) -> None:
        window_handle = self.return_window_handle

        if not window_handle:
            return

        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            if not user32.IsWindow(window_handle):
                self.return_window_handle = 0
                self.write_status(
                    "FOCUS RESTORE SKIPPED | Big Box window no longer exists"
                )
                if self.guard_active:
                    self.fault_disable_guard(
                        "Big Box window disappeared during cleanup"
                    )
                return

            # Restore only a genuinely minimized window. Calling SW_RESTORE
            # on a fullscreen MAME window can force it back to windowed mode.
            if user32.IsIconic(window_handle):
                user32.ShowWindow(window_handle, 9)

            target_thread = user32.GetWindowThreadProcessId(
                window_handle,
                None,
            )
            current_thread = kernel32.GetCurrentThreadId()
            attached = False

            if target_thread and target_thread != current_thread:
                attached = bool(
                    user32.AttachThreadInput(
                        current_thread,
                        target_thread,
                        True,
                    )
                )

            try:
                user32.BringWindowToTop(window_handle)
                user32.SetActiveWindow(window_handle)
                user32.SetForegroundWindow(window_handle)
                user32.SetFocus(window_handle)
            finally:
                if attached:
                    user32.AttachThreadInput(
                        current_thread,
                        target_thread,
                        False,
                    )

            restored_window = user32.GetForegroundWindow()
            if restored_window == window_handle:
                self.write_status(
                    "RESTORED WINDOW | "
                    + self.describe_window(window_handle)
                )
                self.return_window_handle = 0
            elif attempt < (3 if self.ring_burst_active else 20):
                # Windows may reject the first foreground request when the
                # overlay has just closed. Retry while retaining the original
                # handle instead of falling back to whichever window happens
                # to be active on another monitor.
                if self.running:
                    self.root.after(
                        100,
                        lambda: self.restore_return_window(attempt + 1),
                    )
                else:
                    time.sleep(0.05)
                    self.restore_return_window(attempt + 1)
            else:
                self.write_status(
                    "FOCUS RESTORE FAILED | "
                    + self.describe_window(window_handle)
                )
                self.return_window_handle = 0
                if self.guard_active and not self.ring_burst_active:
                    self.fault_disable_guard(
                        "Could not restore Big Box keyboard focus"
                    )
        except (AttributeError, OSError, ValueError):
            self.return_window_handle = 0
            if self.guard_active and not self.ring_burst_active:
                self.fault_disable_guard(
                    "Could not restore Big Box keyboard focus"
                )

    def resume_and_restore_return_window(self, attempt: int = 0) -> None:
        if self.resume_return_process():
            try:
                self.root.after(100, self.restore_return_window)
            except tk.TclError:
                self.restore_return_window()
            return

        if self.running and attempt < 10:
            self.root.after(
                100,
                lambda: self.resume_and_restore_return_window(attempt + 1),
            )
            return

        self.write_status(
            "FATAL CLEANUP ERROR | could not resume Big Box"
        )
        self.last_fault = "Could not resume Big Box — closing guard"
        if self.running:
            self.root.after(0, self.exit_program)

    def mute_other_audio(self) -> bool:
        if not PYCAW_AVAILABLE:
            if not self.audio_mute_error_reported:
                self.write_status(
                    "AUDIO MUTING UNAVAILABLE | "
                    "install pycaw and comtypes before rebuilding"
                )
                self.audio_mute_error_reported = True
            return False

        try:
            sessions = AudioUtilities.GetAllSessions()
            target_process_id = self.suspended_process_id
            if not target_process_id:
                self.audio_last_error = "no suspended Big Box process"
                self.write_status(
                    "AUDIO MUTE ERROR | " + self.audio_last_error
                )
                return False

            target_session_failures = 0
            for session in sessions:
                try:
                    process = session.Process
                    if (
                        process is None
                        or process.pid != target_process_id
                    ):
                        continue

                    session_id = str(
                        getattr(session, "Identifier", "")
                        or f"{target_process_id}:{id(session._ctl)}"
                    )
                    if session_id in self.muted_audio_sessions:
                        continue

                    volume = session._ctl.QueryInterface(
                        ISimpleAudioVolume
                    )
                    old_mute = bool(volume.GetMute())
                    old_volume = float(volume.GetMasterVolume())
                    volume.SetMute(1, None)
                    self.muted_audio_sessions[session_id] = (
                        volume,
                        old_mute,
                        old_volume,
                    )
                except Exception:
                    try:
                        process = session.Process
                        if (
                            process is not None
                            and process.pid == target_process_id
                        ):
                            target_session_failures += 1
                    except Exception:
                        pass

            if target_session_failures:
                self.audio_last_error = (
                    f"could not mute {target_session_failures} Big Box "
                    "audio session(s)"
                )
                self.write_status(
                    "AUDIO MUTE ERROR | " + self.audio_last_error
                )
                return False

            self.audio_muted = bool(self.muted_audio_sessions)
            self.audio_last_error = ""
            return True

        except Exception as error:
            self.audio_last_error = str(error).replace("\n", " ")[:120]
            self.write_status(
                "AUDIO MUTE ERROR | "
                + self.audio_last_error
            )
            return False

    def restore_other_audio(self) -> bool:
        if not self.muted_audio_sessions:
            self.audio_muted = False
            return True

        restored_count = 0
        failed_sessions = {}
        for session_id, values in tuple(self.muted_audio_sessions.items()):
            volume, old_mute, old_volume = values
            try:
                volume.SetMasterVolume(old_volume, None)
                volume.SetMute(old_mute, None)
                restored_count += 1
            except Exception:
                failed_sessions[session_id] = values

        self.muted_audio_sessions = failed_sessions
        self.audio_muted = bool(failed_sessions)
        self.write_status(
            f"AUDIO RESTORED | sessions={restored_count} | "
            f"failed={len(failed_sessions)}"
        )
        return not failed_sessions

    def restore_other_audio_with_retries(self, attempts: int = 3) -> bool:
        for attempt in range(max(1, attempts)):
            if self.restore_other_audio():
                return True
            if attempt + 1 < attempts:
                time.sleep(0.05)
        return False

    def cancel_audio_watchdog(self) -> None:
        if self.audio_watchdog_after_id is None:
            return

        try:
            self.root.after_cancel(self.audio_watchdog_after_id)
        except tk.TclError:
            pass
        self.audio_watchdog_after_id = None

    def schedule_audio_watchdog(self) -> None:
        if self.audio_watchdog_after_id is None and self.running:
            self.audio_watchdog_after_id = self.root.after(
                1000,
                self.audio_watchdog,
            )

    def audio_watchdog(self) -> None:
        self.audio_watchdog_after_id = None
        if self.running and self.guard_active and self.overlay_visible:
            if not self.mute_other_audio():
                self.fault_disable_guard("Could not mute background audio")
                return
            self.schedule_audio_watchdog()

    def show_text_takeover(
        self,
        title: str,
        message: str,
        overlay_kind: str,
    ) -> bool:
        self.hide_story_announcement()
        if not self.overlay_visible:
            self.capture_return_window()
            self.prepare_overlay_monitor()
            if not self.suspend_return_process():
                self.fault_disable_guard("Could not safely pause Big Box")
                return False

        if not self.mute_other_audio():
            self.fault_disable_guard("Could not mute background audio")
            return False

        self.cancel_completion()
        self.stop_music()
        self.stop_event_sound()
        self.schedule_audio_watchdog()
        self.animation_generation += 1
        self.active_frames = []
        self.active_delays = []
        self.background_label.configure(image="", background="black")
        self.overlay_visible = True
        self.overlay_kind = overlay_kind
        self.reset_counter_style()
        self.hide_energy_meter()

        _, _, monitor_width, monitor_height = self.overlay_monitor_bounds
        story_takeover = overlay_kind in {
            "story_shutdown",
            "story_question",
            "story_eggman",
        }
        title_max_size = self.title_font_size
        message_max_size = self.count_font_size
        title_min_size = 12
        message_min_size = 12
        if story_takeover:
            # The story cards are meant to be read from across the arcade.
            # Their limits are based on the actual target monitor, then the
            # normal width fitting still prevents any clipping.
            title_max_size = max(
                title_max_size,
                int(monitor_height * 0.12),
            )
            message_max_size = max(
                message_max_size,
                int(monitor_height * 0.11),
            )
            title_min_size = 14
            message_min_size = 14

        title_size = self.fit_font_size(
            tuple(title.splitlines()) or (title,),
            max_size=title_max_size,
            min_size=title_min_size,
            available_width=monitor_width,
        )
        message_size = self.fit_font_size(
            tuple(message.splitlines()) or (message,),
            max_size=message_max_size,
            min_size=message_min_size,
            available_width=monitor_width,
        )
        self.title_label.configure(
            text=title,
            foreground="white",
            font=("Arial", title_size, "bold"),
            wraplength=max(1, int(monitor_width * 0.80)),
            justify="center",
        )
        self.count_label.configure(
            text=message,
            foreground="white",
            font=("Arial", message_size, "bold"),
            wraplength=max(1, int(monitor_width * 0.80)),
            justify="center",
        )
        self.title_label.update_idletasks()
        self.count_label.update_idletasks()
        title_height = self.title_label.winfo_reqheight()
        message_height = self.count_label.winfo_reqheight()
        vertical_gap = max(12, int(monitor_height * 0.035))
        total_height = title_height + message_height + vertical_gap
        top_y = max(10, (monitor_height - total_height) / 2)
        self.title_label.place(
            x=monitor_width / 2,
            y=top_y + title_height / 2,
            anchor="center",
        )
        self.count_label.place(
            x=monitor_width / 2,
            y=top_y + title_height + vertical_gap + message_height / 2,
            anchor="center",
        )

        if not self.reveal_overlay_window():
            self.fault_disable_guard("Overlay could not cover the display")
            return False
        return True

    def cancel_story_sequence(self) -> None:
        if self.story_sequence_after_id is not None:
            try:
                self.root.after_cancel(self.story_sequence_after_id)
            except tk.TclError:
                pass
            self.story_sequence_after_id = None
        self.cancel_cinematic()

    def start_story_shutdown_sequence(self) -> None:
        self.pending_overlay_missing = None
        self.story_cycle_started = True
        self.story_intro_completed = False
        if not self.show_text_takeover(
            STORY_SHUTDOWN_TITLE,
            STORY_SHUTDOWN_MESSAGE,
            "story_shutdown",
        ):
            return
        self.play_last_emerald_removal_sound()
        self.story_sequence_after_id = self.root.after(
            int(STORY_SHUTDOWN_SECONDS * 1000),
            self.show_story_question,
        )

    def show_story_question(self) -> None:
        self.story_sequence_after_id = None
        if not (
            self.guard_active
            and self.guard_mode == "story"
            and self.overlay_visible
        ):
            return
        if not self.show_text_takeover(
            STORY_QUESTION_TITLE,
            STORY_QUESTION_MESSAGE,
            "story_question",
        ):
            return
        self.story_sequence_after_id = self.root.after(
            int(STORY_QUESTION_SECONDS * 1000),
            self.show_story_eggman,
        )

    def show_story_eggman(self) -> None:
        self.story_sequence_after_id = None
        if not (
            self.guard_active
            and self.guard_mode == "story"
            and self.overlay_visible
        ):
            return
        if not self.show_text_takeover(
            STORY_EGGMAN_TITLE,
            STORY_EGGMAN_MESSAGE,
            "story_eggman",
        ):
            return
        self.play_event_sound(
            self.eggman_reveal_sound,
            "story_eggman",
            force=True,
            duck_music=False,
        )
        self.story_sequence_after_id = self.root.after(
            int(STORY_EGGMAN_SECONDS * 1000),
            self.start_story_cinematic,
        )

    def story_recovery_message(self, present_count: int) -> str:
        energy_percent = round(present_count * 100 / TOTAL_EMERALDS)
        title, detail = STORY_RETURNED_TEXT.get(
            present_count,
            (
                f"{present_count} CHAOS EMERALDS RESTORED!",
                "THE SHRINE IS RECLAIMING ITS POWER!",
            ),
        )
        return f"{title}  {detail}  CHAOS ENERGY: {energy_percent}%"

    def show_story_robotnik_screen(self) -> None:
        if not self.guard_active or self.guard_mode != "story":
            return
        self.story_intro_completed = True
        present_count = self.accepted_count or 0
        missing_count = TOTAL_EMERALDS - present_count
        self.show_missing_overlay(max(1, missing_count))
        if not self.guard_active:
            return
        if present_count > 0:
            self.set_robotnik_title(
                self.story_recovery_message(present_count)
            )
        if present_count == TOTAL_EMERALDS:
            self.begin_final_emerald_transition()

    def cancel_normal_warning(self) -> None:
        if self.normal_warning_after_id is not None:
            try:
                self.root.after_cancel(self.normal_warning_after_id)
            except tk.TclError:
                pass
            self.normal_warning_after_id = None
        self.normal_warning_trigger_count = None

    def show_normal_warning(
        self,
        previous_count: int,
        current_count: int,
    ) -> bool:
        if not self.can_show_story_announcement():
            return False
        self.cancel_normal_warning()
        if not self.show_story_announcement(
            current_count,
            "normal",
            duration_seconds=NORMAL_WARNING_SECONDS,
        ):
            return False
        self.normal_warning_trigger_count = current_count
        self.normal_warning_after_id = self.root.after(
            int(NORMAL_WARNING_SECONDS * 1000),
            self.finish_normal_warning,
        )
        return True

    def finish_normal_warning(self) -> None:
        self.normal_warning_after_id = None
        self.normal_warning_trigger_count = None
        self.hide_story_announcement()
        if self.overlay_kind == "normal_warning":
            self.hide_overlay()

    def set_robotnik_title(self, text: str) -> None:
        _, _, monitor_width, _ = self.overlay_monitor_bounds
        title_size = self.fit_font_size(
            tuple(text.splitlines()) or (text,),
            max_size=self.title_font_size,
            min_size=12,
            available_width=monitor_width,
        )
        self.title_label.configure(
            text=text,
            foreground="white",
            font=("Arial", title_size, "bold"),
            wraplength=max(1, int(monitor_width * 0.82)),
            justify="center",
        )

    def show_missing_overlay(self, missing_count: int) -> None:
        self.set_control_panel_visible(False)

        if not self.overlay_visible:
            self.capture_return_window()
            self.prepare_overlay_monitor()
            if not self.suspend_return_process():
                self.fault_disable_guard(
                    "Could not safely pause Big Box"
                )
                return

        if not self.mute_other_audio():
            self.fault_disable_guard("Could not mute background audio")
            return
        self.cancel_completion()
        self.completion_in_progress = False
        if not self.play_missing_music():
            self.fault_disable_guard("Could not play Robotnik music")
            return
        self.schedule_audio_watchdog()
        if self.active_frames is not self.background_frames:
            self.switch_background(
                self.background_frames,
                self.background_delays,
                loop=True,
            )
        self.overlay_visible = True
        self.overlay_kind = "robotnik"
        _, _, monitor_width, _ = self.overlay_monitor_bounds
        count_size = self.fit_font_size(
            (self.missing_text(missing_count),),
            max_size=self.count_font_size,
            min_size=12,
            available_width=monitor_width,
        )
        self.set_robotnik_title(LOCK_MESSAGE)
        self.reset_counter_style()
        self.count_label.configure(
            text=self.missing_text(missing_count),
            font=("Arial", count_size, "bold"),
            wraplength=max(1, int(monitor_width * 0.82)),
            justify="center",
        )
        self.set_energy_meter(TOTAL_EMERALDS - missing_count)
        self.position_title()
        self.position_counter()
        self.position_energy_meter()

        if not self.reveal_overlay_window():
            self.fault_disable_guard("Overlay could not cover the display")
            return

    def hide_overlay(self, stop_music: bool = True) -> None:
        self.overlay_visible = False
        self.overlay_kind = None
        self.cancel_audio_watchdog()
        self.hide_energy_meter()
        self.set_control_panel_visible(False)
        self.reset_counter_style()

        if stop_music:
            self.stop_music()
            self.stop_event_sound()

        try:
            self.root.attributes("-topmost", False)
        except tk.TclError:
            pass

        self.set_overlay_z_order(False)
        try:
            self.root.withdraw()
        except tk.TclError:
            pass
        if not self.restore_other_audio_with_retries():
            if not self.last_fault:
                self.last_fault = "Could not restore background audio"
            self.guard_active = False
            self.overlay_gate_state = "DISABLED_ERROR"
            self.write_status(
                "GUARD DISABLED | could not restore background audio"
            )
        self.resume_and_restore_return_window()

    def show_completion_message(self) -> None:
        if self.completion_in_progress:
            return

        self.set_control_panel_visible(False)
        self.completion_in_progress = True
        self.completion_animation_finished = False
        self.final_completion_sound_started = False
        self.final_completion_sound_playing = False
        self.reset_counter_style()
        self.hide_energy_meter()

        if self.completion_frames:
            self.switch_background(
                self.completion_frames,
                self.completion_delays,
                loop=False,
                on_complete=self.start_supersonic_animation,
            )
        else:
            self.start_supersonic_animation()

        self.overlay_visible = True
        self.overlay_kind = "completion"
        self.title_label.configure(
            text=COMPLETION_MESSAGE,
            foreground="white",
            font=("Arial", self.title_font_size, "bold"),
            wraplength=0,
        )
        self.count_label.configure(
            text=GAME_ON_MESSAGE,
            wraplength=0,
        )
        self.position_title()
        self.position_counter()

        if not self.reveal_overlay_window():
            self.fault_disable_guard("Overlay could not cover the display")
            return
        if not self.mute_other_audio():
            self.fault_disable_guard("Could not mute background audio")
            return
        self.schedule_audio_watchdog()

        self.completion_started_at = time.monotonic()
        self.stop_music()
        if not self.play_completion_audio():
            self.fault_disable_guard("Could not play victory music")
            return

        self.completion_after_id = self.root.after(
            100,
            self.wait_for_completion_audio,
        )

    def start_supersonic_animation(self) -> None:
        if not self.completion_in_progress:
            return

        self.completion_animation_finished = True
        self.switch_background(
            self.supersonic_frames,
            self.supersonic_delays,
            loop=True,
        )

    def play_completion_audio(self) -> bool:
        self.completion_audio_playing = False

        if not self.audio_ready or not self.completion_audio_path.exists():
            return False

        try:
            pygame.mixer.music.load(str(self.completion_audio_path))
            pygame.mixer.music.set_volume(MUSIC_VOLUME)
            pygame.mixer.music.play()
            self.completion_audio_playing = True
            self.music_mode = "completion"
            return True
        except pygame.error:
            self.completion_audio_playing = False
            return False

    def play_missing_music(self) -> bool:
        if not self.audio_ready or not self.missing_audio_path.exists():
            return False

        if self.music_mode == "missing":
            return True

        try:
            pygame.mixer.music.load(str(self.missing_audio_path))
            pygame.mixer.music.set_volume(MUSIC_VOLUME)
            pygame.mixer.music.play(-1)
            self.music_mode = "missing"
            return True
        except pygame.error:
            self.music_mode = None
            return False

    def event_channel_busy(self) -> bool:
        if self.event_channel is None:
            return False
        try:
            return bool(self.event_channel.get_busy())
        except pygame.error:
            return False

    def play_event_sound(
        self,
        sound,
        event_kind: str,
        *,
        force: bool = False,
        duck_music: bool = True,
    ) -> bool:
        if not self.audio_ready or self.event_channel is None or sound is None:
            return False

        now = time.monotonic()
        same_recent_event = (
            event_kind == self.last_event_sound_kind
            and now - self.last_event_sound_at
            < SOUND_EFFECT_COOLDOWN_SECONDS
        )

        try:
            if self.event_channel.get_busy():
                if not force and (
                    same_recent_event
                    or event_kind == self.last_event_sound_kind
                ):
                    return False
                # A different event replaces the old effect cleanly instead
                # of allowing two sounds to overlap.
                self.event_channel.stop()
            elif not force and same_recent_event:
                return False

            self.event_channel.set_volume(SOUND_EFFECT_VOLUME)
            self.event_channel.play(sound)
            self.last_event_sound_kind = event_kind
            self.last_event_sound_at = now

            if duck_music and self.music_mode == "missing":
                pygame.mixer.music.set_volume(MUSIC_VOLUME * 0.45)
            self.schedule_event_audio_watchdog()
            return True
        except pygame.error:
            return False

    def play_emerald_sound(self, *, final: bool = False) -> bool:
        return self.play_event_sound(
            self.emerald_sound,
            "final_return" if final else "returned",
            force=final,
            duck_music=not final,
        )

    def play_removal_sound(self) -> bool:
        sounds = list(getattr(self, "removal_sounds", ()) or ())
        if not sounds and self.removal_sound is not None:
            sounds = [self.removal_sound]
        if not sounds:
            return False

        previous_index = getattr(
            self,
            "last_removal_sound_index",
            None,
        )
        if len(sounds) == 1:
            sound_index = 0
        else:
            sound_index = random.choice(
                [index for index in range(len(sounds)) if index != previous_index]
            )

        sound = sounds[sound_index]
        played = self.play_event_sound(
            sound,
            "removed",
            force=True,
            duck_music=True,
        )
        if played:
            self.last_removal_sound_index = sound_index
            self.removal_sound = sound
        return played

    def play_last_emerald_removal_sound(self) -> bool:
        return self.play_event_sound(
            self.last_emerald_removal_sound,
            "last_removed",
            force=True,
            duck_music=False,
        )

    def cancel_event_audio_watchdog(self) -> None:
        if self.event_audio_after_id is None:
            return
        try:
            self.root.after_cancel(self.event_audio_after_id)
        except tk.TclError:
            pass
        self.event_audio_after_id = None

    def schedule_event_audio_watchdog(self) -> None:
        if self.event_audio_after_id is None and self.running:
            self.event_audio_after_id = self.root.after(
                50,
                self.event_audio_watchdog,
            )

    def event_audio_watchdog(self) -> None:
        self.event_audio_after_id = None
        if self.event_channel_busy():
            self.schedule_event_audio_watchdog()
            return

        if self.audio_ready and self.music_mode == "missing":
            try:
                pygame.mixer.music.set_volume(MUSIC_VOLUME)
            except pygame.error:
                pass

    def fade_missing_music(self) -> None:
        if not self.audio_ready or self.music_mode != "missing":
            return
        try:
            pygame.mixer.music.fadeout(ROBOTNIK_FADE_MS)
        except pygame.error:
            pass
        self.music_mode = None

    def stop_event_sound(self) -> None:
        self.cancel_event_audio_watchdog()
        if self.event_channel is not None:
            try:
                self.event_channel.stop()
            except pygame.error:
                pass

    def cancel_final_emerald_transition(self) -> None:
        if self.final_emerald_after_id is not None:
            try:
                self.root.after_cancel(self.final_emerald_after_id)
            except tk.TclError:
                pass
            self.final_emerald_after_id = None

        if (
            self.last_event_sound_kind == "final_return"
            and self.event_channel_busy()
        ):
            try:
                self.event_channel.stop()
            except pygame.error:
                pass

        self.final_emerald_sound_started = False
        self.final_emerald_pause_started_at = None

    def begin_final_emerald_transition(self) -> None:
        self.cancel_final_emerald_transition()
        self.count_label.configure(text=RESTORED_MESSAGE)
        self.animate_counter("restored")
        self.animate_energy_meter(
            self.energy_display_count,
            TOTAL_EMERALDS,
        )
        self.fade_missing_music()
        self.final_emerald_sound_started = self.play_emerald_sound(
            final=True
        )
        self.final_emerald_pause_started_at = None
        self.final_emerald_after_id = self.root.after(
            50,
            self.wait_for_final_emerald_transition,
        )

    def wait_for_final_emerald_transition(self) -> None:
        self.final_emerald_after_id = None

        if not (
            self.guard_active
            and self.overlay_visible
            and self.accepted_count == TOTAL_EMERALDS
            and not self.controller_lost
        ):
            return

        if self.final_emerald_sound_started and self.event_channel_busy():
            self.final_emerald_after_id = self.root.after(
                50,
                self.wait_for_final_emerald_transition,
            )
            return

        now = time.monotonic()
        if self.final_emerald_pause_started_at is None:
            self.final_emerald_pause_started_at = now

        remaining_seconds = (
            FINAL_EMERALD_PAUSE_SECONDS
            - (now - self.final_emerald_pause_started_at)
        )
        if remaining_seconds > 0:
            self.final_emerald_after_id = self.root.after(
                max(10, int(remaining_seconds * 1000)),
                self.wait_for_final_emerald_transition,
            )
            return

        self.finish_final_emerald_transition()

    def finish_final_emerald_transition(self) -> None:
        self.final_emerald_after_id = None

        if (
            self.guard_active
            and self.overlay_visible
            and self.accepted_count == TOTAL_EMERALDS
            and not self.controller_lost
        ):
            self.show_completion_message()

    def stop_music(self) -> None:
        if self.audio_ready:
            try:
                pygame.mixer.music.stop()
            except pygame.error:
                pass

        self.music_mode = None

    def wait_for_completion_audio(self) -> None:
        self.completion_after_id = None

        if not self.completion_in_progress:
            return

        elapsed = time.monotonic() - self.completion_started_at
        audio_finished = False

        if self.completion_audio_playing:
            try:
                audio_finished = not pygame.mixer.music.get_busy()
            except pygame.error:
                audio_finished = True

        victory_audio_done = (
            (
                self.completion_audio_playing
                and audio_finished
            )
            or (
                not self.completion_audio_playing
                and elapsed >= COMPLETION_FALLBACK_SECONDS
            )
            or elapsed >= COMPLETION_MAX_SECONDS
        )
        if victory_audio_done and self.completion_animation_finished:
            if not self.final_completion_sound_started:
                self.final_completion_sound_started = True
                self.final_completion_sound_playing = (
                    self.play_event_sound(
                        self.final_completion_sound,
                        "final_completion",
                        force=True,
                        duck_music=False,
                    )
                )

            if self.final_completion_sound_playing:
                if self.event_channel_busy():
                    self.completion_after_id = self.root.after(
                        100,
                        self.wait_for_completion_audio,
                    )
                    return
                self.final_completion_sound_playing = False

            self.finish_completion()
            return

        self.completion_after_id = self.root.after(
            100,
            self.wait_for_completion_audio,
        )

    def finish_completion(self) -> None:
        self.completion_after_id = None

        if (
            self.accepted_count == TOTAL_EMERALDS
            and not self.controller_lost
            and self.completion_animation_finished
        ):
            completed_story = (
                self.guard_mode == "story"
                and self.story_cycle_started
            )
            self.completion_in_progress = False
            self.final_completion_sound_started = False
            self.final_completion_sound_playing = False
            self.hide_overlay()
            if completed_story:
                self.guard_mode = "normal"
                self.story_armed = False
                self.story_cycle_started = False
                self.story_intro_completed = False
                self.write_status(
                    "STORY COMPLETE | switched automatically to Normal Mode"
                )

    def cancel_completion(self) -> None:
        self.cancel_final_emerald_transition()

        if self.completion_after_id is not None:
            try:
                self.root.after_cancel(self.completion_after_id)
            except tk.TclError:
                pass

            self.completion_after_id = None

        if self.completion_audio_playing and self.audio_ready:
            try:
                pygame.mixer.music.stop()
            except pygame.error:
                pass

        self.completion_audio_playing = False
        if self.final_completion_sound_playing:
            self.stop_event_sound()
        self.final_completion_sound_started = False
        self.final_completion_sound_playing = False

    def keep_window_on_top(self) -> None:
        if self.running and self.overlay_visible:
            try:
                self.root.overrideredirect(True)
                x, y, width, height = self.overlay_monitor_bounds
                expected_rect = (x, y, x + width, y + height)
                if self.get_window_rect(self.root.winfo_id()) != expected_rect:
                    self.apply_overlay_bounds(show=True)
                self.root.attributes("-topmost", True)
                self.set_overlay_z_order(True)
                self.root.lift()
            except tk.TclError:
                pass

        if self.running:
            self.root.after(500, self.keep_window_on_top)

    # --------------------------------------------------
    # Serial connection
    # --------------------------------------------------

    def get_available_ports(self) -> list[str]:
        if PREFERRED_SERIAL_PORT:
            return [PREFERRED_SERIAL_PORT]

        ports = [port.device for port in list_ports.comports()]
        if self.last_good_port in ports:
            ports.remove(self.last_good_port)
            ports.insert(0, self.last_good_port)
        return ports

    def identify_reader(
        self,
        port_name: str,
        generation: int,
    ) -> Optional[serial.Serial]:
        try:
            device = serial.Serial(
                port=port_name,
                baudrate=BAUD_RATE,
                timeout=0.5,
                write_timeout=0.5,
            )
        except (SerialException, OSError):
            return None

        try:
            for _ in range(20):
                if (
                    not self.running
                ):
                    device.close()
                    return None
                time.sleep(0.1)

            deadline = time.monotonic() + 4.0

            while time.monotonic() < deadline:
                if (
                    not self.running
                ):
                    device.close()
                    return None

                raw_line = device.readline()
                if not raw_line:
                    continue

                line = raw_line.decode(
                    "utf-8",
                    errors="ignore",
                ).strip()

                if line.startswith(PROTOCOL_PREFIX):
                    self.last_good_port = port_name
                    self.messages.put(("SERIAL", line, generation))
                    return device

            device.close()
            return None

        except (SerialException, OSError):
            try:
                device.close()
            except Exception:
                pass

            return None

    def serial_worker(self) -> None:
        while self.running:
            generation = self.activation_generation
            device: Optional[serial.Serial] = None

            try:
                available_ports = self.get_available_ports()
            except Exception as error:
                self.messages.put(
                    (
                        "FAULT",
                        "Serial port scan failed: "
                        + str(error).replace("\n", " ")[:120],
                        generation,
                    )
                )
                time.sleep(RECONNECT_DELAY_SECONDS)
                continue

            for port_name in available_ports:
                if (
                    not self.running
                ):
                    break

                candidate = self.identify_reader(port_name, generation)
                if candidate is not None:
                    device = candidate
                    break

            if device is None:
                if (
                    self.guard_active
                    and generation == self.activation_generation
                ):
                    self.messages.put(
                        (
                            "DISCONNECTED",
                            "ESP32 sensor controller not found",
                            generation,
                        )
                    )
                time.sleep(RECONNECT_DELAY_SECONDS)
                continue

            try:
                while (
                    self.running
                ):
                    raw_line = device.readline()
                    if not raw_line:
                        continue

                    line = raw_line.decode(
                        "utf-8",
                        errors="ignore",
                    ).strip()

                    if line.startswith(PROTOCOL_PREFIX):
                        self.last_serial_message_at = time.monotonic()
                        self.messages.put(
                            (
                                "SERIAL",
                                line,
                                self.activation_generation,
                            )
                        )

            except (SerialException, OSError) as error:
                self.messages.put(
                    (
                        "DISCONNECTED",
                        "ESP32 connection lost: "
                        + str(error).replace("\n", " ")[:120],
                        generation,
                    )
                )

            finally:
                try:
                    device.close()
                except Exception:
                    pass

            time.sleep(RECONNECT_DELAY_SECONDS)

    # --------------------------------------------------
    # Count state and fail-safe behavior
    # --------------------------------------------------

    def handle_serial_message(self, message: str) -> None:
        if not self.guard_active:
            return

        if message == "MAGNET_LOCK:READY":
            self.reader_connected = True
            self.controller_lost = False
            now = time.monotonic()
            self.last_valid_message = now
            self.last_serial_message_at = now
            return

        prefix = "MAGNET_LOCK:COUNT:"
        if not message.startswith(prefix):
            return

        try:
            count = int(message[len(prefix):])
        except ValueError:
            return

        if not 0 <= count <= TOTAL_EMERALDS:
            return

        self.reader_connected = True
        self.controller_lost = False
        now = time.monotonic()
        self.last_valid_message = now
        self.last_serial_message_at = now
        self.overlay_gate_state = "MONITORING"

        if count != self.pending_count:
            self.pending_count = count
            self.pending_count_since = now

    def accept_stable_count(self) -> None:
        if self.pending_count is None:
            return

        if (
            time.monotonic() - self.pending_count_since
            < STABLE_COUNT_SECONDS
        ):
            return

        count = self.pending_count
        if count == self.accepted_count:
            return

        previous_count = self.accepted_count
        self.accepted_count = count

        # The first accepted reading establishes a baseline. In particular,
        # launching while an emerald is already absent must never create a
        # warning or begin the story on its own.
        if previous_count is None:
            if self.guard_mode == "story" and count == TOTAL_EMERALDS:
                self.story_armed = True
            return

        if self.guard_mode == "story":
            self.handle_story_count_change(previous_count, count)
        else:
            self.handle_normal_count_change(previous_count, count)

    def handle_story_count_change(
        self,
        previous_count: int,
        current_count: int,
    ) -> None:
        if current_count == previous_count:
            return

        # Ring Power intentionally hides the blocking screen while a game is
        # launched. Keep accepting sensor counts, but defer all Story Mode UI
        # and sounds until Big Box safely returns.
        if self.story_intro_completed and self.ring_burst_active:
            return

        # Once the intro has finished, every recovery update belongs on the
        # blocking Robotnik screen. Removing one again during the victory
        # sequence safely cancels that sequence and returns to Robotnik.
        if self.story_intro_completed and self.overlay_kind in {
            "robotnik",
            "completion",
        }:
            if current_count == TOTAL_EMERALDS:
                if (
                    current_count > previous_count
                    and self.overlay_kind == "robotnik"
                ):
                    self.begin_final_emerald_transition()
                return

            self.show_missing_overlay(TOTAL_EMERALDS - current_count)
            if not self.guard_active:
                return
            if current_count > 0:
                self.set_robotnik_title(
                    self.story_recovery_message(current_count)
                )
            self.animate_energy_meter(previous_count, current_count)
            if current_count < previous_count:
                if current_count == 0:
                    self.play_last_emerald_removal_sound()
                else:
                    self.play_removal_sound()
                self.animate_counter("removed")
            else:
                self.play_emerald_sound()
                self.animate_counter("returned")
            return

        # Sensor changes during the shutdown narration or cinematic are
        # remembered, then reflected when the Robotnik screen appears.
        if self.overlay_kind in {
            "story_shutdown",
            "story_question",
            "story_eggman",
            "cinematic",
        }:
            return

        # Story Mode arms only after all seven have been observed together.
        # This preserves the fail-open baseline behavior when the guard starts
        # with one or more emeralds already absent.
        if current_count == TOTAL_EMERALDS:
            self.story_armed = True
            self.pending_overlay_missing = None
            if previous_count < current_count:
                if self.show_story_announcement(current_count, "returned"):
                    self.play_emerald_sound()
            return

        if not self.story_armed:
            return

        if current_count < previous_count:
            if current_count == 0:
                self.hide_story_announcement()
                self.pending_overlay_missing = TOTAL_EMERALDS
                self.maybe_show_pending_overlay()
            elif self.show_story_announcement(current_count, "removed"):
                self.play_removal_sound()
            return

        # Returning an emerald before the complete theft cancels a pending
        # shutdown and gives a non-blocking energy update over Big Box.
        self.pending_overlay_missing = None
        if self.show_story_announcement(current_count, "returned"):
            self.play_emerald_sound()

    def handle_normal_count_change(
        self,
        previous_count: int,
        current_count: int,
    ) -> None:
        if current_count == previous_count:
            return

        if current_count > previous_count:
            if (
                self.overlay_kind == "normal_warning"
                or getattr(self, "normal_warning_trigger_count", None)
                is not None
            ):
                self.finish_normal_warning()
            self.play_emerald_sound()
            return

        if (
            self.overlay_kind == "normal_warning"
            or getattr(self, "normal_warning_trigger_count", None)
            is not None
        ):
            # A second real removal is a new event, so keep the warning up
            # for a full interval from the newest removal.
            if self.show_normal_warning(previous_count, current_count):
                self.play_removal_sound()
            return

        # Normal Mode reacts only to a downward edge observed while Big Box is
        # the usable full-screen foreground. A missing baseline or a steady
        # missing count never opens the warning.
        if current_count < previous_count:
            if self.show_normal_warning(previous_count, current_count):
                self.play_removal_sound()

    def handle_disconnect(self, reason: str) -> None:
        if not self.guard_active:
            return

        self.reader_connected = False
        self.controller_lost = True
        self.pending_count = None
        self.last_serial_message_at = 0.0
        self.fault_disable_guard(reason or "ESP32 disconnected")

    def process_messages(self) -> None:
        try:
            while True:
                message_type, value, generation = self.messages.get_nowait()

                if message_type == "CONTROL":
                    if value == "EXIT":
                        self.exit_program()
                        return
                    elif value == "STORY_MODE":
                        self.select_story_mode()
                    elif value == "DEACTIVATE":
                        self.deactivate_guard()
                    elif value == "ACTIVATE":
                        self.activate_guard()
                    continue

                if message_type == "RING":
                    self.handle_ring_entry()
                    continue

                if generation != self.activation_generation:
                    continue

                if message_type == "SERIAL":
                    self.handle_serial_message(value)

                elif message_type == "DISCONNECTED":
                    self.handle_disconnect(value)

                elif message_type == "FAULT":
                    self.fault_disable_guard(value)

        except queue.Empty:
            pass

        self.accept_stable_count()

        if self.running:
            self.root.after(50, self.process_messages)

    def connection_watchdog(self) -> None:
        if self.guard_active and self.reader_connected:
            elapsed = time.monotonic() - self.last_serial_message_at

            if elapsed > CONNECTION_TIMEOUT_SECONDS:
                self.handle_disconnect("ESP32 heartbeat timed out")

        if self.running:
            self.root.after(250, self.connection_watchdog)

    # --------------------------------------------------
    # Program control
    # --------------------------------------------------

    def guard_readiness_error(self) -> str:
        problems = []

        if self.guard_mode == "story":
            if not PIL_AVAILABLE:
                problems.append("Pillow image support unavailable")
            elif not self.background_frames:
                problems.append("Robotnik GIF unavailable")
            elif not self.completion_frames:
                problems.append("Sonic GIF unavailable")
            elif not self.supersonic_frames:
                problems.append("Super Sonic GIF unavailable")

            if not PYGAME_AVAILABLE or not self.audio_ready:
                problems.append("audio system unavailable")
            else:
                if not self.missing_audio_path.exists():
                    problems.append("Robotnik music unavailable")
                if not self.completion_audio_path.exists():
                    problems.append("victory music unavailable")
                if self.emerald_sound is None:
                    problems.append("emerald sound unavailable")

            if not AV_AVAILABLE:
                problems.append("cinematic decoder unavailable")
            elif not self.cinematic_video_path.exists():
                problems.append("Sonic CD cinematic unavailable")
            elif self.cinematic_prepare_state in {"error", "unavailable"}:
                problems.append(
                    "cinematic preparation failed: "
                    + (self.cinematic_prepare_error or "unknown error")
                )

        if not PYCAW_AVAILABLE:
            problems.append("background-audio muting unavailable")

        return "; ".join(problems)

    def select_story_mode(self) -> None:
        self.select_guard_mode("story")

    def select_normal_mode(self) -> None:
        self.select_guard_mode("normal")

    def select_guard_mode(self, mode: str) -> None:
        if mode not in {"story", "normal"} or not self.running:
            return
        if self.guard_active:
            self._deactivate_guard(None)
        self.guard_mode = mode
        self.last_fault = ""
        self.write_status(f"MODE SELECTED | {mode.upper()}")
        self.activate_guard()

    def activate_guard(self) -> None:
        if self.guard_active or not self.running:
            return

        self.activation_generation += 1
        readiness_error = self.guard_readiness_error()
        if readiness_error:
            self.last_fault = readiness_error[:160]
            self.overlay_gate_state = "DISABLED_ERROR"
            self.write_status("GUARD DISABLED | " + readiness_error)
            return

        self.guard_active = True
        self.last_fault = ""
        self.pending_count = None
        self.last_serial_message_at = 0.0
        self.accepted_count = None
        self.pending_overlay_missing = None
        self.pending_normal_warning = None
        self.story_armed = False
        self.story_cycle_started = False
        self.story_intro_completed = False
        self.ring_burst_active = False
        self.ring_burst_game_seen = False
        self.ring_burst_game_seen_since = 0.0
        self.controller_lost = True
        self.reader_connected = False
        self.overlay_gate_state = "WAITING_FOR_SENSOR"
        self.reset_big_box_readiness()
        self.write_status(f"ACTIVATING GUARD | mode={self.guard_mode}")

    def deactivate_guard(self, event=None) -> str:
        if not self.running:
            return "break"

        self._deactivate_guard(None)
        return "break"

    def fault_disable_guard(self, reason: str) -> None:
        if not self.running:
            return

        clean_reason = reason.replace("\n", " ")[:160]
        self._deactivate_guard(clean_reason)

    def _deactivate_guard(self, fault_reason: Optional[str]) -> None:
        self.activation_generation += 1
        if fault_reason:
            self.last_fault = fault_reason
            self.write_status("GUARD DISABLED | " + fault_reason)
        else:
            self.last_fault = ""
            self.write_status("DEACTIVATING GUARD")

        self.guard_active = False
        self.reader_connected = False
        self.controller_lost = True
        self.pending_count = None
        self.accepted_count = None
        self.pending_overlay_missing = None
        self.pending_normal_warning = None
        self.hide_story_announcement()
        self.cancel_story_sequence()
        self.cancel_normal_warning()
        self.story_armed = False
        self.story_cycle_started = False
        self.story_intro_completed = False
        self.ring_burst_active = False
        self.ring_burst_game_seen = False
        self.ring_burst_game_seen_since = 0.0
        self.cancel_completion()
        self.completion_in_progress = False
        self.reset_big_box_readiness()
        self.overlay_gate_state = (
            "DISABLED_ERROR" if fault_reason else "DORMANT"
        )
        self.hide_overlay()

    def global_service_hotkey_worker(self) -> None:
        try:
            user32 = ctypes.windll.user32
        except AttributeError:
            return

        exit_combo = (0x11, 0x10, 0x7B)  # Ctrl, Shift, F12
        deactivate_combo = (0x11, 0x12, 0x7A)  # Ctrl, Alt, F11
        story_mode_combo = (0x11, 0x12, 0x79)  # Ctrl, Alt, F10
        keyboard_activate_combo = (0x11, 0x12, 0x7B)
        was_exit_pressed = False
        was_deactivate_pressed = False
        was_story_mode_pressed = False
        was_activate_pressed = False

        while self.running:
            keyboard_combo_pressed = all(
                user32.GetAsyncKeyState(key) & 0x8000
                for key in exit_combo
            )
            ctrl_alt_f11_pressed = all(
                user32.GetAsyncKeyState(key) & 0x8000
                for key in deactivate_combo
            )
            ctrl_alt_f10_pressed = all(
                user32.GetAsyncKeyState(key) & 0x8000
                for key in story_mode_combo
            )
            ctrl_alt_f12_pressed = all(
                user32.GetAsyncKeyState(key) & 0x8000
                for key in keyboard_activate_combo
            )
            exit_pressed = keyboard_combo_pressed
            deactivate_pressed = (
                ctrl_alt_f11_pressed
                and self.guard_active
            )
            story_mode_pressed = ctrl_alt_f10_pressed
            activate_pressed = (
                ctrl_alt_f12_pressed and not self.guard_active
            )

            if exit_pressed and not was_exit_pressed:
                self.messages.put(("CONTROL", "EXIT", -1))

            if deactivate_pressed and not was_deactivate_pressed:
                self.messages.put(("CONTROL", "DEACTIVATE", -1))

            if story_mode_pressed and not was_story_mode_pressed:
                self.messages.put(("CONTROL", "STORY_MODE", -1))

            if activate_pressed and not was_activate_pressed:
                self.messages.put(("CONTROL", "ACTIVATE", -1))

            was_exit_pressed = exit_pressed
            was_deactivate_pressed = deactivate_pressed
            was_story_mode_pressed = story_mode_pressed
            was_activate_pressed = activate_pressed
            time.sleep(0.05)

    def exit_program(self, event=None) -> str:
        if self.shutdown_started:
            return "break"

        self.shutdown_started = True
        self.write_status("STOPPING")
        self.cleanup_runtime()

        try:
            self.root.destroy()
        except tk.TclError:
            pass
        return "break"

    def cleanup_runtime(self) -> None:
        """Release every lock-screen side effect, even after a partial failure."""
        if self.cleanup_complete:
            return

        self.cleanup_complete = True
        self.running = False
        self.ring_input_stop_event.set()
        if (
            self.ring_input_thread
            and self.ring_input_thread.is_alive()
            and self.ring_input_thread is not threading.current_thread()
        ):
            self.ring_input_thread.join(timeout=0.75)
        self.guard_active = False
        self.activation_generation += 1
        self.overlay_visible = False

        try:
            self.hide_story_announcement()
            self.cancel_story_sequence()
            self.cancel_normal_warning()
            self.cancel_completion()
            self.cancel_audio_watchdog()
            self.stop_music()
        except Exception as error:
            self.write_status(
                "CLEANUP WARNING | media stop failed | "
                + str(error).replace("\n", " ")[:120]
            )

        try:
            self.root.attributes("-topmost", False)
            self.set_overlay_z_order(False)
            self.root.withdraw()
        except (AttributeError, tk.TclError):
            pass

        if not self.restore_other_audio_with_retries(attempts=10):
            self.write_status(
                "CLEANUP WARNING | background audio restore failed"
            )

        resumed = False
        for attempt in range(10):
            if self.resume_return_process():
                resumed = True
                break
            if attempt < 9:
                time.sleep(0.05)

        if resumed:
            self.restore_return_window()
        else:
            # Keep the independent watchdog alive. It will resume Big Box as
            # soon as this process exits. Close only our duplicate handle.
            self.write_status(
                "CLEANUP WARNING | watchdog will resume Big Box on exit"
            )
            if self.suspended_process_handle:
                try:
                    ctypes.windll.kernel32.CloseHandle(
                        self.suspended_process_handle
                    )
                except (AttributeError, OSError, ValueError):
                    pass
                self.suspended_process_handle = None
                self.suspended_process_id = 0

        if self.audio_ready:
            try:
                pygame.mixer.quit()
            except pygame.error:
                pass

        if self.instance_mutex_handle:
            try:
                ctypes.windll.kernel32.CloseHandle(
                    self.instance_mutex_handle
                )
            except (AttributeError, OSError, ValueError):
                pass
            self.instance_mutex_handle = None

    def run(self) -> None:
        try:
            self.root.mainloop()
        finally:
            self.cleanup_runtime()


def show_message_box(message: str, title: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
    except AttributeError:
        pass


def main() -> int:
    bootstrap_event("main entry")
    configure_windows_runtime()
    bootstrap_event("Windows runtime configured")

    if len(sys.argv) >= 2 and sys.argv[1] == "--resume-watchdog":
        if len(sys.argv) != 5:
            return 2
        try:
            parent_pid = int(sys.argv[2])
            big_box_pid = int(sys.argv[3])
        except ValueError:
            return 2
        return run_resume_watchdog(
            parent_pid,
            big_box_pid,
            sys.argv[4],
        )

    mutex_handle, is_first_instance = acquire_single_instance_mutex()
    if not mutex_handle:
        show_message_box(
            "The guard could not create its safety lock and did not start.",
            "Magnetic Arcade Guard",
        )
        return 1
    if not is_first_instance:
        ctypes.windll.kernel32.CloseHandle(mutex_handle)
        show_message_box(
            "Magnetic Arcade Guard is already running.",
            "Magnetic Arcade Guard",
        )
        return 0

    app = None
    try:
        bootstrap_event("single-instance lock acquired")
        app = MagnetArcadeGuard(mutex_handle)
        bootstrap_event("entering main loop")
        app.run()
        return 0
    except Exception as error:
        bootstrap_event(
            "fatal startup/runtime error: "
            + repr(error).replace("\n", " ")[:500]
        )
        if app is not None:
            app.write_status(
                "FATAL APP ERROR | "
                + "".join(
                    traceback.format_exception(
                        type(error),
                        error,
                        error.__traceback__,
                    )
                ).replace("\n", " ")[-1000:]
            )
            app.cleanup_runtime()
        else:
            ctypes.windll.kernel32.CloseHandle(mutex_handle)
        show_message_box(
            "The guard stopped because of an error. The guard is disabled, "
            "and Big Box should remain usable.\n\n"
            + str(error),
            "Magnetic Arcade Guard",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
