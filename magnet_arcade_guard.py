import ctypes
import json
import os
import queue
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
    "sensor_stable_ms": 100,
    "final_emerald_pause_ms": 150,
    "sound_effect_cooldown_ms": 350,
    "counter_flash_ms": 300,
    "robotnik_fade_ms": 250,
    "music_volume": 0.75,
    "sound_effect_volume": 1.0,
    "removal_sound_file": "emerald-removed.mp3",
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
CONNECTION_TIMEOUT_SECONDS = 3.0
RECONNECT_DELAY_SECONDS = 1.0
STABLE_COUNT_SECONDS = config_number(
    RUNTIME_CONFIG.get("sensor_stable_ms", 100),
    100,
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
REMOVAL_AUDIO_NAME = (
    str(
        RUNTIME_CONFIG.get(
            "removal_sound_file",
            "emerald-removed.mp3",
        )
    ).strip()
    or "emerald-removed.mp3"
)
SOURCE_ASSET_DIRECTORY = Path(
    os.environ.get(
        "MAGNET_GUARD_ASSET_DIR",
        str(Path(__file__).resolve().parent.parent / "Emerald"),
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
ORIGINAL_REMOVAL_AUDIO_PATH = (
    SOURCE_ASSET_DIRECTORY / REMOVAL_AUDIO_NAME
)

LOCK_MESSAGE = "Dr. Robotnik has stolen the Chaos Emeralds!"
COMPLETION_MESSAGE = (
    "Thank you, Sonic, for returning all the Chaos Emeralds!"
)
GAME_ON_MESSAGE = "EMERALDS FOUND! GAME ON!"
RESTORED_MESSAGE = "ALL CHAOS EMERALDS RESTORED!"
HWND_TOPMOST = ctypes.c_void_p(-1)
HWND_NOTOPMOST = ctypes.c_void_p(-2)
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_SHOWWINDOW = 0x0040
PROCESS_SUSPEND_RESUME = 0x0800
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
STILL_ACTIVE = 259
INFINITE = 0xFFFFFFFF
ERROR_ALREADY_EXISTS = 183
CREATE_NO_WINDOW = 0x08000000
SINGLE_INSTANCE_MUTEX_NAME = "Local\\MagnetArcadeGuard.SingleInstance"


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
        self.activation_generation = 0
        self.last_fault = ""
        self.overlay_visible = False
        self.completion_in_progress = False
        self.completion_after_id = None
        self.final_emerald_after_id = None
        self.completion_audio_playing = False
        self.completion_started_at = 0.0
        self.completion_animation_finished = False
        self.music_mode: Optional[str] = None
        self.emerald_sound = None
        self.removal_sound = None
        self.event_channel = None
        self.event_audio_after_id = None
        self.last_event_sound_kind: Optional[str] = None
        self.last_event_sound_at = 0.0
        self.final_emerald_sound_started = False
        self.final_emerald_pause_started_at: Optional[float] = None
        self.counter_animation_after_id = None
        self.counter_animation_generation = 0
        self.return_window_handle = 0
        self.suspended_process_handle = None
        self.suspended_process_id = 0
        self.muted_audio_sessions = {}
        self.audio_muted = False
        self.audio_mute_error_reported = False
        self.audio_last_error = ""
        self.audio_watchdog_after_id = None
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
        self.reader_connected = False
        self.last_good_port = PREFERRED_SERIAL_PORT
        self.last_valid_message = 0.0
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
        self.removal_audio_path = self.find_asset(
            REMOVAL_AUDIO_NAME,
            ORIGINAL_REMOVAL_AUDIO_PATH,
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
                pygame.mixer.set_reserved(1)
                self.event_channel = pygame.mixer.Channel(0)
                self.event_channel.set_volume(SOUND_EFFECT_VOLUME)
                pygame.mixer.music.set_volume(MUSIC_VOLUME)
                self.audio_ready = True
                if self.emerald_audio_path.exists():
                    self.emerald_sound = pygame.mixer.Sound(
                        str(self.emerald_audio_path)
                    )
                    self.emerald_sound.set_volume(SOUND_EFFECT_VOLUME)

                if self.removal_audio_path.exists():
                    try:
                        self.removal_sound = pygame.mixer.Sound(
                            str(self.removal_audio_path)
                        )
                        self.removal_sound.set_volume(
                            SOUND_EFFECT_VOLUME
                        )
                    except pygame.error:
                        # This sound is optional; the visual and LED removal
                        # feedback still work if it has not been added yet.
                        self.removal_sound = None

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

        self.messages: queue.Queue[tuple[str, str, int]] = queue.Queue()

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

        self.root.after(50, self.process_messages)
        self.root.after(250, self.connection_watchdog)
        self.root.after(250, self.foreground_watchdog)
        self.root.after(500, self.keep_window_on_top)
        self.root.after(100, self.status_heartbeat)

        # Start dormant. Ctrl + Alt + F12 enables the guard.
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

    def reset_counter_style(self) -> None:
        self.cancel_counter_animation()
        try:
            self.count_label.configure(
                foreground="white",
                font=("Arial", self.count_font_size, "bold"),
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
        enlarged_size = self.count_font_size + max(
            2,
            min(6, self.count_font_size // 10),
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
                self.count_label.configure(
                    foreground="white",
                    font=("Arial", self.count_font_size, "bold"),
                )
            except tk.TclError:
                pass

        self.counter_animation_after_id = self.root.after(
            COUNTER_FLASH_MS,
            finish_animation,
        )

    def fit_font_size(
        self,
        text_options: tuple[str, ...],
        max_size: int,
        min_size: int,
    ) -> int:
        # Leave extra horizontal margin for CRT overscan and the arcade
        # monitor's visible bezel area.
        available_width = max(
            1,
            int(self.screen_width * 0.82),
        )

        for size in range(max_size, min_size - 1, -1):
            font = tkfont.Font(
                root=self.root,
                family="Arial",
                size=size,
                weight="bold",
            )

            if max(font.measure(text) for text in text_options) <= available_width:
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
        vertical_gap = max(8, int(self.screen_height * 0.02))

        maximum_width = max(1, self.screen_width - 20)
        maximum_height = max(
            1,
            self.screen_height
            - title_space
            - counter_space
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
        if self.background_display_height:
            image_bottom = (
                self.screen_height / 2
                + self.background_display_height / 2
            )
            counter_y = image_bottom + max(
                8,
                int(self.count_font_size * 0.4),
            )
            counter_y = min(
                counter_y,
                self.screen_height - int(self.count_font_size * 0.7),
            )
        else:
            counter_y = self.screen_height * 0.8

        self.count_label.place(
            x=self.screen_width / 2,
            y=counter_y,
            anchor="center",
        )

    def position_title(self) -> None:
        if self.background_display_height:
            image_top = (
                self.screen_height / 2
                - self.background_display_height / 2
            )
            title_y = image_top - max(
                8,
                int(self.title_font_size * 0.8),
            )
            title_y = max(
                self.title_font_size,
                title_y,
            )
        else:
            title_y = self.screen_height * 0.08

        self.title_label.place(
            x=self.screen_width / 2,
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
        self.control_window.title("Magnetic Arcade Guard")
        self.control_window.geometry("620x340+40+40")
        self.control_window.minsize(520, 300)
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

        self.control_activate_button = tk.Button(
            button_frame,
            text="ACTIVATE GUARD",
            width=18,
            command=self.activate_guard,
        )
        self.control_activate_button.grid(
            row=0,
            column=0,
            padx=4,
        )

        self.control_deactivate_button = tk.Button(
            button_frame,
            text="DEACTIVATE GUARD",
            width=18,
            command=self.deactivate_guard,
        )
        self.control_deactivate_button.grid(
            row=0,
            column=1,
            padx=4,
        )

        tk.Button(
            button_frame,
            text="CLOSE PROGRAM",
            width=18,
            command=self.exit_program,
        ).grid(
            row=0,
            column=2,
            padx=4,
        )

        tk.Label(
            self.control_window,
            text=(
                "Keyboard: Activate Ctrl+Alt+F12  |  "
                "Deactivate Ctrl+Alt+F11  |  "
                "Close Ctrl+Shift+F12"
            ),
            font=("Arial", 9),
            foreground="#8f8f8f",
            background="#202020",
        ).pack(pady=(10, 0))

    def get_control_state(self) -> tuple[str, str]:
        if not self.guard_active:
            if self.last_fault:
                return "DISABLED — " + self.last_fault, "#ff9966"
            return "DORMANT — GUARD OFF", "#9e9e9e"

        if self.completion_in_progress:
            return "SONIC VICTORY SCREEN", "#66ccff"

        if self.overlay_visible:
            return "ROBOTNIK LOCK SCREEN ACTIVE", "#ff6666"

        if self.accepted_count is None:
            return "ACTIVE — WAITING FOR SENSOR", "#ffcc66"

        if self.accepted_count < TOTAL_EMERALDS:
            if self.overlay_gate_state == "WAITING_FOR_BIGBOX_READY":
                return (
                    "ACTIVE — WAITING FOR BIG BOX TO SETTLE",
                    "#ffcc66",
                )
            if self.overlay_gate_state == "WAITING_FOR_BIGBOX":
                return "ACTIVE — WAITING FOR BIG BOX", "#ffcc66"
            return "ACTIVE — MONITORING", "#66dd88"

        return "ACTIVE — ALL EMERALDS PRESENT", "#66dd88"

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
        details_text = (
            f"Reader: {reader_text}    "
            f"Foreground: {self.foreground_process_name}\n"
            f"Gate: {self.overlay_gate_state}    "
            f"Input: {input_text}\n"
            "Return SFX: ready    Removal SFX: "
            + ("ready" if self.removal_sound is not None else "not installed")
        )

        self.control_state_var.set(state_text)
        self.control_sensor_var.set(sensor_text)
        self.control_details_var.set(details_text)
        self.control_state_label.configure(foreground=state_color)
        self.control_activate_button.configure(
            state=tk.DISABLED if self.guard_active else tk.NORMAL
        )
        self.control_deactivate_button.configure(
            state=tk.NORMAL if self.guard_active else tk.DISABLED
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
            f"connected={self.reader_connected} | "
            f"audio={audio_state} | "
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
        self.show_missing_overlay(missing_count)

    def foreground_watchdog(self) -> None:
        if self.running and self.guard_active:
            self.maybe_show_pending_overlay()

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
            elif attempt < 20:
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
                if self.guard_active:
                    self.fault_disable_guard(
                        "Could not restore Big Box keyboard focus"
                    )
        except (AttributeError, OSError, ValueError):
            self.return_window_handle = 0
            if self.guard_active:
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
        self.title_label.configure(
            text=LOCK_MESSAGE,
        )
        self.reset_counter_style()
        self.count_label.configure(
            text=self.missing_text(missing_count),
        )
        if not self.reveal_overlay_window():
            self.fault_disable_guard("Overlay could not cover the display")
            return

    def hide_overlay(self, stop_music: bool = True) -> None:
        self.overlay_visible = False
        self.cancel_audio_watchdog()
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
        self.reset_counter_style()

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
        self.title_label.configure(
            text=COMPLETION_MESSAGE,
        )
        self.count_label.configure(
            text=GAME_ON_MESSAGE,
        )
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
        return self.play_event_sound(
            self.removal_sound,
            "removed",
            force=False,
            duck_music=True,
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

        if (
            (self.completion_audio_playing and audio_finished)
            or (
                not self.completion_audio_playing
                and elapsed >= COMPLETION_FALLBACK_SECONDS
            )
            or elapsed >= COMPLETION_MAX_SECONDS
        ) and self.completion_animation_finished:
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
            self.completion_in_progress = False
            self.hide_overlay()

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
                    or not self.guard_active
                    or generation != self.activation_generation
                ):
                    device.close()
                    return None
                time.sleep(0.1)

            deadline = time.monotonic() + 4.0

            while time.monotonic() < deadline:
                if (
                    not self.running
                    or not self.guard_active
                    or generation != self.activation_generation
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
            if not self.guard_active:
                time.sleep(0.10)
                continue

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
                    or not self.guard_active
                    or generation != self.activation_generation
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
                    and self.guard_active
                    and generation == self.activation_generation
                ):
                    raw_line = device.readline()
                    if not raw_line:
                        continue

                    line = raw_line.decode(
                        "utf-8",
                        errors="ignore",
                    ).strip()

                    if line.startswith(PROTOCOL_PREFIX):
                        self.messages.put(("SERIAL", line, generation))

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
            self.last_valid_message = time.monotonic()
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
        self.last_valid_message = time.monotonic()
        self.overlay_gate_state = "MONITORING"
        now = time.monotonic()

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

        if count < TOTAL_EMERALDS:
            self.request_missing_overlay(TOTAL_EMERALDS - count)
            # Event sounds are deliberately limited to an active overlay.
            # Changes during MAME/RetroArch are remembered silently and the
            # correct count appears after Big Box has safely returned.
            if self.overlay_visible and previous_count is not None:
                if count < previous_count:
                    self.play_removal_sound()
                    self.animate_counter("removed")
                elif count > previous_count:
                    self.play_emerald_sound()
                    self.animate_counter("returned")
            return

        if (
            previous_count is not None
            and previous_count < TOTAL_EMERALDS
            and not self.controller_lost
        ):
            if self.overlay_visible:
                self.begin_final_emerald_transition()
            else:
                self.pending_overlay_missing = None
        else:
            self.pending_overlay_missing = None
            if self.overlay_visible:
                self.hide_overlay()

    def handle_disconnect(self, reason: str) -> None:
        if not self.guard_active:
            return

        self.reader_connected = False
        self.controller_lost = True
        self.pending_count = None
        self.fault_disable_guard(reason or "ESP32 disconnected")

    def process_messages(self) -> None:
        try:
            while True:
                message_type, value, generation = self.messages.get_nowait()

                if message_type == "CONTROL":
                    if value == "EXIT":
                        self.exit_program()
                        return
                    elif value == "DEACTIVATE":
                        self.deactivate_guard()
                    elif value == "ACTIVATE":
                        self.activate_guard()
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
            elapsed = time.monotonic() - self.last_valid_message

            if elapsed > CONNECTION_TIMEOUT_SECONDS:
                self.handle_disconnect("ESP32 heartbeat timed out")

        if self.running:
            self.root.after(250, self.connection_watchdog)

    # --------------------------------------------------
    # Program control
    # --------------------------------------------------

    def guard_readiness_error(self) -> str:
        problems = []

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

        if not PYCAW_AVAILABLE:
            problems.append("background-audio muting unavailable")

        return "; ".join(problems)

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
        self.accepted_count = None
        self.pending_overlay_missing = None
        self.controller_lost = True
        self.reader_connected = False
        self.overlay_gate_state = "WAITING_FOR_SENSOR"
        self.reset_big_box_readiness()
        self.write_status("ACTIVATING GUARD")

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
        keyboard_activate_combo = (0x11, 0x12, 0x7B)
        was_exit_pressed = False
        was_deactivate_pressed = False
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
            ctrl_alt_f12_pressed = all(
                user32.GetAsyncKeyState(key) & 0x8000
                for key in keyboard_activate_combo
            )
            exit_pressed = keyboard_combo_pressed
            deactivate_pressed = (
                ctrl_alt_f11_pressed
                and self.guard_active
            )
            activate_pressed = (
                ctrl_alt_f12_pressed and not self.guard_active
            )

            if exit_pressed and not was_exit_pressed:
                self.messages.put(("CONTROL", "EXIT", -1))

            if deactivate_pressed and not was_deactivate_pressed:
                self.messages.put(("CONTROL", "DEACTIVATE", -1))

            if activate_pressed and not was_activate_pressed:
                self.messages.put(("CONTROL", "ACTIVATE", -1))

            was_exit_pressed = exit_pressed
            was_deactivate_pressed = deactivate_pressed
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
        self.guard_active = False
        self.activation_generation += 1
        self.overlay_visible = False

        try:
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
