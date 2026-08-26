import ctypes
import json
import math
import os
import queue
import random
import shutil
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Optional


APP_NAME = "ChaosHeist"
APP_DISPLAY_NAME = "Chaos Heist"
APP_VERSION = "1.0.0"
APP_DATA_DIRECTORY_NAME = "ChaosHeist"
LEGACY_APP_DATA_DIRECTORY_NAME = "MagnetArcadeGuard"
CONFIG_FILENAME = "chaos-heist-config.json"
LEGACY_CONFIG_FILENAME = "guard-config.json"
LEGACY_MIGRATION_MARKER_FILENAME = "legacy-migration-v1.json"


def bootstrap_event(message: str) -> None:
    """Record packaged startup stages before the main logger is available."""
    trace_enabled = (
        os.environ.get("CHAOS_HEIST_BOOT_TRACE") == "1"
        or os.environ.get("MAGNET_GUARD_BOOT_TRACE") == "1"
    )
    if trace_enabled:
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
            / APP_DATA_DIRECTORY_NAME
            / "chaos-heist-bootstrap.log"
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
from tkinter import messagebox

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


FIRMWARE_TOTAL_EMERALDS = 7
DEFAULT_EMULATOR_PROCESS_NAMES = (
    "mame.exe",
    "mame64.exe",
    "groovymame.exe",
    "retroarch.exe",
)

# ChaosHeist never uses Windows file associations for sound playback. These
# are only a containment list for the rare case where Groove Music or its
# legacy executable steals the foreground while the guard is presenting an
# active sound/overlay.
EXTERNAL_AUDIO_PLAYER_PROCESS_NAMES = frozenset(
    {
        "groovemusic.exe",
        "music.ui.exe",
    }
)


DEFAULT_CONFIG = {
    "total_emeralds": FIRMWARE_TOTAL_EMERALDS,
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
    "ring_sound_file": "ring.mp3",
    "act_clear_sound_file": "16-act-clear.mp3",
    "removal_sound_files": [
        "ohh-no-the-chaos-emerald.mp3",
        "ohh-no.mp3",
        "ohh-now-what.mp3",
        "stop.mp3",
        "you-must-be-kidding.mp3",
        "you-can-t-get-away-with-this.mp3",
        "something-horrible-is-happening-amy.mp3",
        "scream-4-tails.mp3",
        "problem-tails.mp3",
        "oh-no-tails.mp3",
        "oh-no (2)-sonic.mp3",
        "oh-no (1)-knuckles.mp3",
        "no-amy.mp3",
        "hey-amy.mp3",
        "hey-2-knuckles.mp3",
    ],
    "last_emerald_removal_sound_file": (
        "no-he-s-got-the-last-emerald.mp3"
    ),
    "story_shutdown_sound_file": (
        "i-m-afraid-our-little-game-ends-now.mp3"
    ),
    "power_loss_lights_sound_file": (
        "flourescent-lights-buzzing.mp3"
    ),
    "power_loss_buzz_fades_sound_file": (
        "lantern-buzzes-fades.mp3"
    ),
    "power_loss_buzz_dies_sound_file": (
        "lantern-whines-buzzing-dies.mp3"
    ),
    "power_loss_tv_off_sound_file": "tv-off.mp3",
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
    "ring_power_selection_seconds": 35.0,
    "ring_announcement_seconds": 3.0,
    "ring_milestone_announcement_seconds": 10.0,
    "emulator_process_names": list(DEFAULT_EMULATOR_PROCESS_NAMES),
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


def local_app_data_directory(directory_name: str) -> Path:
    local_app_data = Path(
        os.environ.get(
            "LOCALAPPDATA",
            str(Path.home() / "AppData" / "Local"),
        )
    )
    return local_app_data / directory_name


def write_json_atomic(destination: Path, payload: dict) -> None:
    """Atomically replace a small JSON state file."""
    temporary_path = destination.with_name(destination.name + ".tmp")
    temporary_path.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(destination)


def normalized_ring_state_payload(
    state: tuple[int, set[int], set[int]],
) -> dict:
    total, shown, pending = state
    return {
        "version": RING_STATE_VERSION,
        "total_rings": total,
        "milestones_shown": sorted(shown),
        "milestones_pending": sorted(pending),
    }


def migrate_legacy_persistent_state(target_directory: Path) -> str:
    """Import one validated legacy ring state exactly once."""
    legacy_directory = local_app_data_directory(
        LEGACY_APP_DATA_DIRECTORY_NAME
    )
    if legacy_directory == target_directory:
        return ""

    marker_path = target_directory / LEGACY_MIGRATION_MARKER_FILENAME
    if marker_path.is_file():
        return ""

    try:
        target_directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return f"Could not create ChaosHeist data directory: {error}"

    target_primary = target_directory / "ring-counter.json"
    target_backup = target_directory / "ring-counter.backup.json"
    migration_result = "no legacy ring state found"

    # Any new-format state wins. This prevents a deleted backup or an
    # operator reset from resurrecting an obsolete legacy count later.
    if target_primary.exists() or target_backup.exists():
        migration_result = "existing ChaosHeist ring state retained"
    elif legacy_directory.is_dir():
        legacy_state = None
        legacy_source = None
        for filename in ("ring-counter.json", "ring-counter.backup.json"):
            legacy_path = legacy_directory / filename
            if not legacy_path.is_file():
                continue
            try:
                loaded = json.loads(legacy_path.read_text(encoding="utf-8"))
                legacy_state = parse_ring_state_payload(loaded)
                legacy_source = filename
                break
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                continue

        if legacy_state is None:
            migration_result = "legacy ring state was unreadable"
        else:
            payload = normalized_ring_state_payload(legacy_state)
            try:
                write_json_atomic(target_primary, payload)
                write_json_atomic(target_backup, payload)
            except OSError as error:
                return "Legacy ring-state migration failed: " + str(error)
            migration_result = f"migrated from {legacy_source}"

    marker_payload = {
        "version": 1,
        "completed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "result": migration_result,
    }
    try:
        write_json_atomic(marker_path, marker_payload)
    except OSError as error:
        return "Legacy ring-state migration marker failed: " + str(error)

    if migration_result.startswith("migrated"):
        return "Migrated legacy ring state to ChaosHeist."
    if migration_result == "legacy ring state was unreadable":
        return (
            "Legacy ring state was unreadable; ChaosHeist started with its "
            "own ring state."
        )
    return ""


def load_runtime_config_details(
    config_directory: Optional[Path] = None,
) -> tuple[dict, Optional[Path], list[str], list[str]]:
    """Load one config file and retain actionable diagnostics."""
    config = dict(DEFAULT_CONFIG)
    directory = config_directory or application_directory()
    errors = []
    warnings = []
    active_path = None

    for config_name in (CONFIG_FILENAME, LEGACY_CONFIG_FILENAME):
        config_path = directory / config_name
        if not config_path.exists():
            continue
        active_path = config_path
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as error:
            errors.append(
                f"{config_name} could not be read: "
                + str(error).replace("\n", " ")[:160]
            )
            break
        if not isinstance(loaded, dict):
            errors.append(f"{config_name} must contain a JSON object")
            break

        unknown_keys = sorted(set(loaded) - set(DEFAULT_CONFIG))
        if unknown_keys:
            warnings.append(
                f"{config_name} has unknown settings: "
                + ", ".join(unknown_keys)
            )
        config.update(loaded)
        if config_name == LEGACY_CONFIG_FILENAME:
            warnings.append(
                f"Using legacy {LEGACY_CONFIG_FILENAME}; rename it to "
                f"{CONFIG_FILENAME}."
            )
        break

    return config, active_path, errors, warnings


def load_runtime_config() -> dict:
    return load_runtime_config_details()[0]


(
    RUNTIME_CONFIG,
    ACTIVE_CONFIG_PATH,
    CONFIG_LOAD_ERRORS,
    CONFIG_WARNINGS,
) = load_runtime_config_details()
CONFIG_VALIDATION_ERRORS = list(CONFIG_LOAD_ERRORS)


def validate_runtime_config_shape(config: dict) -> tuple[list[str], list[str]]:
    """Catch unsafe types while leaving harmless tuning values fail-soft."""
    errors = []
    warnings = []
    required_filename_keys = (
        "ring_sound_file",
        "act_clear_sound_file",
        "last_emerald_removal_sound_file",
        "story_shutdown_sound_file",
        "power_loss_lights_sound_file",
        "power_loss_buzz_fades_sound_file",
        "power_loss_buzz_dies_sound_file",
        "power_loss_tv_off_sound_file",
        "final_completion_sound_file",
        "cinematic_video_file",
    )
    for key in required_filename_keys:
        value = config.get(key)
        if (
            not isinstance(value, str)
            or not value.strip()
            or Path(value.strip()).name != value.strip()
        ):
            errors.append(f"{key} must be a non-empty filename")

    removal_files = config.get("removal_sound_files")
    if (
        not isinstance(removal_files, list)
        or not removal_files
        or any(
            not isinstance(value, str) or not value.strip()
            or Path(value.strip()).name != value.strip()
            for value in removal_files
        )
    ):
        errors.append(
            "removal_sound_files must be a non-empty list of filenames"
        )

    default_mode_value = config.get("default_mode")
    if (
        not isinstance(default_mode_value, str)
        or default_mode_value.strip().lower() not in {"story", "normal"}
    ):
        warnings.append("default_mode is invalid; using story")
    if not isinstance(config.get("auto_activate"), (bool, str, int, float)):
        warnings.append("auto_activate is invalid; using false")
    if not isinstance(config.get("serial_port"), str):
        warnings.append("serial_port is invalid; using automatic detection")
    if not isinstance(config.get("emulator_process_names"), list):
        warnings.append(
            "emulator_process_names is invalid; using built-in emulator names"
        )
    return errors, warnings


CONFIG_SHAPE_ERRORS, CONFIG_SHAPE_WARNINGS = (
    validate_runtime_config_shape(RUNTIME_CONFIG)
)
CONFIG_VALIDATION_ERRORS.extend(CONFIG_SHAPE_ERRORS)
CONFIG_WARNINGS.extend(CONFIG_SHAPE_WARNINGS)
try:
    configured_total_value = RUNTIME_CONFIG["total_emeralds"]
    if isinstance(configured_total_value, bool):
        raise ValueError
    configured_total_emeralds = int(configured_total_value)
    if (
        isinstance(configured_total_value, float)
        and not configured_total_value.is_integer()
    ):
        raise ValueError
except (KeyError, TypeError, ValueError):
    configured_total_emeralds = None

if configured_total_emeralds != FIRMWARE_TOTAL_EMERALDS:
    CONFIG_VALIDATION_ERRORS.append(
        "total_emeralds must be 7 to match the installed firmware and sensors"
    )
TOTAL_EMERALDS = FIRMWARE_TOTAL_EMERALDS



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
    if not math.isfinite(number):
        return default
    return max(minimum, min(maximum, number))


def config_process_names(value, defaults) -> frozenset[str]:
    """Normalize configured Windows executable names with safe defaults."""
    if not isinstance(value, list):
        return frozenset(defaults)

    normalized = set()
    for candidate in value:
        if not isinstance(candidate, str):
            continue
        process_name = Path(candidate.strip()).name.lower()
        if not process_name:
            continue
        if "." not in process_name:
            process_name += ".exe"
        normalized.add(process_name)

    return frozenset(normalized or defaults)


def is_external_audio_player_process(process_name: str) -> bool:
    """Identify a Windows media player that should not cover the guard."""
    if not isinstance(process_name, str):
        return False
    return Path(process_name.strip()).name.lower() in (
        EXTERNAL_AUDIO_PLAYER_PROCESS_NAMES
    )


def joystick_button_mask(human_button_number: int) -> int:
    """Convert a 1-based Windows joystick button number to its bit mask."""
    return 1 << max(0, int(human_button_number) - 1)


def joystick_direction_active(joystick_state) -> bool:
    """Return true when a physical stick or POV hat is being held."""
    axis_moved = (
        abs(int(joystick_state.dwXpos) - JOYSTICK_AXIS_CENTER)
        >= JOYSTICK_AXIS_THRESHOLD
        or abs(int(joystick_state.dwYpos) - JOYSTICK_AXIS_CENTER)
        >= JOYSTICK_AXIS_THRESHOLD
    )
    pov_active = int(joystick_state.dwPOV) != JOY_POVCENTERED
    return axis_moved or pov_active


def ring_press_is_accepted(
    now: float,
    device_last_press_at: float,
    global_last_press_at: float,
) -> bool:
    """Debounce switch chatter and duplicate reports across encoders."""
    return (
        now - device_last_press_at >= RING_DEBOUNCE_SECONDS
        and now - global_last_press_at >= RING_DEBOUNCE_SECONDS
    )


AUTO_ACTIVATE = config_boolean(
    RUNTIME_CONFIG.get("auto_activate", False),
)
configured_serial_port = RUNTIME_CONFIG.get("serial_port", "")
PREFERRED_SERIAL_PORT = (
    configured_serial_port.strip()
    if isinstance(configured_serial_port, str)
    else ""
)

# The display overlay is intentionally limited to the Big Box frontend. A
# MAME/GroovyMAME or RetroArch fullscreen surface can own the display mode,
# so the guard waits for Big Box to return to its menu before appearing.
BIG_BOX_PROCESS_NAME = "bigbox.exe"
EMULATOR_PROCESS_NAMES = config_process_names(
    RUNTIME_CONFIG.get("emulator_process_names"),
    DEFAULT_EMULATOR_PROCESS_NAMES,
)

BAUD_RATE = 115200
# The timeout margin tolerates a slow Windows audio/session operation without
# declaring a healthy ESP32 disconnected. Only validated READY/COUNT messages
# refresh it; malformed protocol-like traffic must not defeat fail-open.
CONNECTION_TIMEOUT_SECONDS = 5.0
INITIAL_CONNECTION_TIMEOUT_SECONDS = 15.0
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
RING_POWER_SELECTION_SECONDS = config_number(
    RUNTIME_CONFIG.get("ring_power_selection_seconds", 35.0),
    35.0,
    5.0,
    300.0,
)
RING_POWER_SEGMENT_SECONDS = (
    RING_POWER_SELECTION_SECONDS / TOTAL_EMERALDS
)
RING_ANNOUNCEMENT_SECONDS = config_number(
    RUNTIME_CONFIG.get("ring_announcement_seconds", 3.0),
    3.0,
    1.0,
    30.0,
)
RING_MILESTONE_ANNOUNCEMENT_SECONDS = config_number(
    RUNTIME_CONFIG.get("ring_milestone_announcement_seconds", 10.0),
    10.0,
    10.0,
    60.0,
)
RING_MILESTONE = 50
RING_MILESTONE_TITLE = "50 Rings!"
RING_MILESTONE_MESSAGE = (
    "Sonic holds the KEY to your prize... if it hasn't already been taken!"
)
RING_COUNT_TITLE = "RING COLLECTED!"
RING_BURST_TITLE = "RING POWER!"
RING_BURST_MESSAGE = "This won't last long, quick play a game!"
RING_BURST_ANNOUNCEMENT_SECONDS = 2.0
RING_STATE_VERSION = 2
CINEMATIC_MAX_FPS = config_number(
    RUNTIME_CONFIG.get("cinematic_max_fps", 15),
    15,
    10,
    30,
)
CINEMATIC_FRAME_INTERVAL = 1.0 / CINEMATIC_MAX_FPS
CINEMATIC_QUEUE_SIZE = 12
CINEMATIC_PREBUFFER_FRAMES = 6
CINEMATIC_PREPARE_TIMEOUT_SECONDS = 180.0
CINEMATIC_START_TIMEOUT_SECONDS = 20.0
CINEMATIC_FINISH_GRACE_SECONDS = 20.0
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
EVENT_SOUND_MAX_SECONDS = 15.0
PROTOCOL_PREFIX = "MAGNET_LOCK:"


def parse_magnet_protocol_message(
    message: str,
) -> Optional[tuple[str, Optional[int]]]:
    """Return a validated protocol event, or None for malformed traffic."""
    if message == PROTOCOL_PREFIX + "READY":
        return "ready", None

    count_prefix = PROTOCOL_PREFIX + "COUNT:"
    if not message.startswith(count_prefix):
        return None
    count_text = message[len(count_prefix):]
    if not count_text.isdecimal():
        return None
    count = int(count_text)
    if not 0 <= count <= TOTAL_EMERALDS:
        return None
    return "count", count


def parse_ring_state_payload(payload) -> tuple[int, set[int], set[int]]:
    """Validate both current and legacy persistent ring-counter payloads."""
    if not isinstance(payload, dict):
        raise ValueError("ring state must be a JSON object")

    version_value = payload.get("version", 1)
    if isinstance(version_value, bool):
        raise ValueError("ring state version must be an integer")
    if isinstance(version_value, float) and not version_value.is_integer():
        raise ValueError("ring state version must be an integer")
    try:
        version = int(version_value)
    except (TypeError, ValueError) as error:
        raise ValueError("ring state version must be an integer") from error
    if version not in {1, RING_STATE_VERSION}:
        raise ValueError(f"unsupported ring state version: {version}")

    total_value = payload.get("total_rings", 0)
    if isinstance(total_value, bool):
        raise ValueError("total_rings must be a non-negative integer")
    if isinstance(total_value, float) and not total_value.is_integer():
        raise ValueError("total_rings must be a non-negative integer")
    try:
        total = int(total_value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            "total_rings must be a non-negative integer"
        ) from error
    if total < 0:
        raise ValueError("total_rings must be a non-negative integer")

    def milestone_values(field_name: str) -> set[int]:
        raw_values = payload.get(field_name, [])
        if not isinstance(raw_values, list):
            raise ValueError(f"{field_name} must be a list")
        result = set()
        for raw_value in raw_values:
            if isinstance(raw_value, bool):
                raise ValueError(f"{field_name} contains an invalid value")
            if isinstance(raw_value, float) and not raw_value.is_integer():
                raise ValueError(f"{field_name} contains an invalid value")
            try:
                value = int(raw_value)
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"{field_name} contains an invalid value"
                ) from error
            if value <= 0:
                raise ValueError(
                    f"{field_name} contains an invalid value"
                )
            result.add(value)
        return result

    shown = milestone_values("milestones_shown")
    pending = milestone_values("milestones_pending")
    pending.difference_update(shown)
    return total, shown, pending

BACKGROUND_IMAGE_NAME = "egg-man-robotnik.gif"
COMPLETION_IMAGE_NAME = "sonic-sonic-the-hedgehog.gif"
SUPERSONIC_IMAGE_NAME = "supersonic.gif"
COMPLETION_AUDIO_NAME = "27. Sonic the Hedgehog Victory Theme.mp3"
MISSING_AUDIO_NAME = "Dr Robotniks Theme.mp3"
EMERALD_AUDIO_NAME = "emerald.mp3"
RING_AUDIO_NAME = str(
    RUNTIME_CONFIG.get("ring_sound_file", "ring.mp3")
).strip() or "ring.mp3"
ACT_CLEAR_AUDIO_NAME = str(
    RUNTIME_CONFIG.get("act_clear_sound_file", "16-act-clear.mp3")
).strip() or "16-act-clear.mp3"
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
STORY_SHUTDOWN_AUDIO_NAME = str(
    RUNTIME_CONFIG.get(
        "story_shutdown_sound_file",
        "i-m-afraid-our-little-game-ends-now.mp3",
    )
).strip() or "i-m-afraid-our-little-game-ends-now.mp3"
POWER_LOSS_AUDIO_DEFAULTS = {
    "lights": "flourescent-lights-buzzing.mp3",
    "buzz_fades": "lantern-buzzes-fades.mp3",
    "buzz_dies": "lantern-whines-buzzing-dies.mp3",
    "tv_off": "tv-off.mp3",
}
POWER_LOSS_AUDIO_NAMES = {
    key: (
        str(
            RUNTIME_CONFIG.get(
                f"power_loss_{key}_sound_file",
                default_name,
            )
        ).strip()
        or default_name
    )
    for key, default_name in POWER_LOSS_AUDIO_DEFAULTS.items()
}
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
        "CHAOS_HEIST_ASSET_DIR",
        os.environ.get(
            "MAGNET_GUARD_ASSET_DIR",
            str(Path(__file__).resolve().parents[2] / "Emerald"),
        ),
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
ORIGINAL_RING_AUDIO_PATH = SOURCE_ASSET_DIRECTORY / RING_AUDIO_NAME
ORIGINAL_ACT_CLEAR_AUDIO_PATH = (
    SOURCE_ASSET_DIRECTORY / ACT_CLEAR_AUDIO_NAME
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
ORIGINAL_STORY_SHUTDOWN_AUDIO_PATH = (
    SOURCE_ASSET_DIRECTORY / STORY_SHUTDOWN_AUDIO_NAME
)
ORIGINAL_POWER_LOSS_AUDIO_PATHS = {
    key: SOURCE_ASSET_DIRECTORY / name
    for key, name in POWER_LOSS_AUDIO_NAMES.items()
}
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
NORMAL_WARNING_MESSAGE = "Hey! Put that back!"
NORMAL_ALL_MISSING_MESSAGE = (
    "You'll need a Ring if there's no Chaos Energy!"
)
STORY_ROBOTNIK_MESSAGE = (
    "You'll need a ring to play until full energy is restored!"
)
NORMAL_RESTORED_TITLE = "EMERALD RESTORED!"
STORY_REMOVAL_OVERLAY_TITLE = "A Chaos Emerald Was Stolen!"
STORY_SHUTDOWN_TITLE = "ROBOTNIK'S CHAOS HEIST!"
STORY_SHUTDOWN_MESSAGE = (
    "Robotnik has stolen the Chaos Emeralds\n"
    "and taken them back to his fortress!"
)
STORY_QUESTION_TITLE = "THE ARCADE HAS LOST ITS CHAOS ENERGY!"
STORY_QUESTION_MESSAGE = "Only Sonic can save us!"
STORY_EGGMAN_TITLE = "SO EGGMAN'S BEHIND THIS, HUH?"
STORY_EGGMAN_MESSAGE = ""
POWER_LOSS_AUDIO_STEPS = (
    ("lights", 2347),
    ("buzz_fades", 3968),
    ("buzz_dies", 2005),
    ("tv_off", 2051),
)
POWER_LOSS_CRT_START_MS = sum(
    duration_ms
    for _name, duration_ms in POWER_LOSS_AUDIO_STEPS[:3]
)
STORY_POWER_LOSS_TOTAL_MS = sum(
    duration_ms
    for _name, duration_ms in POWER_LOSS_AUDIO_STEPS
)
STORY_POWER_LOSS_SECONDS = STORY_POWER_LOSS_TOTAL_MS / 1000.0
STORY_POWER_LOSS_BLACKOUT_MS = 400
STORY_POWER_LOSS_TICK_MS = 45
POWER_LOSS_CRT_COLLAPSE_MS = 1800
ENERGY_ANIMATION_STEP_MS = 55
ENERGY_EMPHASIS_MS = 420
RING_POWER_COUNTDOWN_TICK_MS = 250
RING_POWER_METER_STEP_MS = 70

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
    7: (
        "SEVEN CHAOS EMERALDS STOLEN!",
        "CHAOS ENERGY GONE! IT'S GOING DOWN!",
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
SW_MINIMIZE = 6
CURSOR_SHOWING = 0x00000001
PROCESS_SUSPEND_RESUME = 0x0800
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
STILL_ACTIVE = 259
INFINITE = 0xFFFFFFFF
ERROR_ALREADY_EXISTS = 183
CREATE_NO_WINDOW = 0x08000000
# Hold both identities during the product rename. This is intentionally not a
# clean break: an old MagnetArcadeGuard executable left in Windows Startup must
# never run beside ChaosHeist and compete for serial, joystick, audio, or Big
# Box control.
SINGLE_INSTANCE_MUTEX_NAMES = (
    "Local\\MagnetArcadeGuard.SingleInstance",
    "Local\\ChaosHeist.SingleInstance",
)
JOYERR_NOERROR = 0
JOY_RETURNX = 0x00000001
JOY_RETURNY = 0x00000002
JOY_RETURNPOV = 0x00000004
JOY_RETURNBUTTONS = 0x00000080
JOY_POVCENTERED = 0xFFFF
JOYSTICK_AXIS_CENTER = 32768
JOYSTICK_AXIS_THRESHOLD = 12000


class Win32Rect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class Win32Point(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_long),
        ("y", ctypes.c_long),
    ]


class Win32CursorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("flags", ctypes.c_ulong),
        ("hCursor", ctypes.c_void_p),
        ("ptScreenPos", Win32Point),
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


def release_mutex_handles(handles) -> None:
    kernel32 = ctypes.windll.kernel32
    for handle in tuple(handles or ()):
        if not handle:
            continue
        try:
            kernel32.CloseHandle(handle)
        except (AttributeError, OSError, ValueError):
            pass


def acquire_single_instance_mutex():
    kernel32 = ctypes.windll.kernel32
    handles = []
    for mutex_name in SINGLE_INSTANCE_MUTEX_NAMES:
        handle = kernel32.CreateMutexW(None, False, mutex_name)
        if not handle:
            release_mutex_handles(handles)
            return None, False
        already_running = kernel32.GetLastError() == ERROR_ALREADY_EXISTS
        if already_running:
            kernel32.CloseHandle(handle)
            release_mutex_handles(handles)
            return (), False
        handles.append(handle)
    return tuple(handles), True


class ChaosHeistApp:
    def __init__(self, instance_mutex_handles=()) -> None:
        bootstrap_event("creating Tk root")
        self.instance_mutex_handles = tuple(instance_mutex_handles or ())
        self.resume_watchdog_process = None
        self.resume_watchdog_cancel_path = None
        self.shutdown_started = False
        self.cleanup_complete = False
        self.root = tk.Tk()
        bootstrap_event("Tk root created")
        self.root.report_callback_exception = self.handle_tk_exception
        self.root.title(APP_DISPLAY_NAME)
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
        self.last_fault = "; ".join(CONFIG_VALIDATION_ERRORS)[:160]
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
        self.final_completion_sound_started_at = 0.0
        self.music_mode: Optional[str] = None
        self.emerald_sound = None
        self.ring_sound = None
        self.act_clear_sound = None
        self.removal_sound = None
        self.removal_sounds = []
        self.last_removal_sound_index = None
        self.last_emerald_removal_sound = None
        self.final_completion_sound = None
        self.story_shutdown_sound = None
        self.power_loss_sounds = {}
        self.eggman_reveal_sound = None
        self.event_channel = None
        self.event_audio_after_id = None
        self.last_event_sound_kind: Optional[str] = None
        self.last_event_sound_at = 0.0
        self.final_emerald_sound_started = False
        self.final_emerald_pause_started_at: Optional[float] = None
        self.final_emerald_wait_started_at = 0.0
        self.counter_animation_after_id = None
        self.counter_animation_generation = 0
        self.energy_animation_after_id = None
        self.energy_animation_generation = 0
        self.energy_display_count = TOTAL_EMERALDS
        self.story_armed = False
        self.story_cycle_started = False
        self.story_intro_completed = False
        # One-shot operator aid for testing: skip the cinematic during the
        # next Story Mode heist without skipping the narration or Robotnik
        # lock screen.
        self.skip_cinematic_requested = False
        self.story_sequence_after_id = None
        self.power_loss_after_id = None
        self.power_loss_audio_after_id = None
        self.power_loss_generation = 0
        self.power_loss_active = False
        self.power_loss_started_at = 0.0
        self.power_loss_cursor_hidden = False
        self.announcement_after_id = None
        self.normal_warning_after_id = None
        self.normal_warning_trigger_count: Optional[int] = None
        self.pending_normal_warning: Optional[tuple[int, int]] = None
        self.cinematic_prepare_state = "unavailable"
        self.cinematic_prepare_error = ""
        self.cinematic_prepare_started_at = 0.0
        self.cinematic_story_wait_started_at = 0.0
        self.cinematic_audio_pcm = b""
        self.cinematic_audio_rate = 44100
        self.cinematic_duration = 0.0
        self.cinematic_channel = None
        self.cinematic_sound = None
        self.cinematic_after_id = None
        self.cinematic_started_at = 0.0
        self.cinematic_wait_started_at = 0.0
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
        self.audio_restore_retry_after_id = None
        self.audio_restore_retry_attempt = 0
        self.ring_input_stop_event = threading.Event()
        self.ring_input_thread = None
        self.ring_input_restart_after_id = None
        self.ring_input_restart_count = 0
        self.ring_input_backend = "Windows joystick"
        self.ring_joystick_error = ""
        self.ring_joystick_signature = ()
        self.joystick_button_states = {}
        self.joystick_direction_states = {}
        self.ring_last_press_at = {}
        self.ring_global_last_press_at = 0.0
        self.ring_persistence_queue = queue.Queue(maxsize=1)
        self.ring_persistence_stop_event = threading.Event()
        self.ring_persistence_thread = None
        self.ring_burst_active = False
        self.ring_burst_game_seen = False
        self.ring_burst_game_seen_since = 0.0
        self.ring_burst_origin: Optional[str] = None
        self.ring_burst_selection_deadline: Optional[float] = None
        self.ring_burst_selection_expired = False
        self.ring_power_selection_after_id = None
        self.ring_power_countdown_after_id = None
        self.ring_power_last_countdown_seconds: Optional[int] = None
        self.ring_power_selection_started_at = 0.0
        self.ring_power_meter_animation_after_id = None
        self.ring_power_meter_animation_generation = 0
        self.ring_power_meter_filling = False
        self.ring_power_meter_blink_on = True
        self.ring_power_meter_visible = False
        self.announcement_energy_animation_after_id = None
        self.announcement_energy_animation_generation = 0
        self.normal_ring_lock_active = False
        self.ring_power_announcement_visible = False
        self.ring_power_ignore_until = 0.0
        self.joystick_press_sequence = 0
        self.ring_power_ignore_press_sequence = 0
        self.ring_count_ignore_press_sequence = 0
        self.pending_ring_milestone = False
        self.pending_ring_announcement: Optional[str] = None
        self.active_ring_announcement_kind: Optional[str] = None
        self.milestone_deferred_count_change = False
        self.milestone_deferred_previous_count: Optional[int] = None
        self.ring_state_warning = ""
        self.service_warning = "; ".join(CONFIG_WARNINGS)[:160]
        self.serial_worker_failed = False
        self.pending_guard_activation = False
        self.activation_retry_after_id = None
        app_data_directory = local_app_data_directory(
            APP_DATA_DIRECTORY_NAME
        )
        migration_message = migrate_legacy_persistent_state(
            app_data_directory
        )
        if migration_message:
            self.service_warning = "; ".join(
                part
                for part in (self.service_warning, migration_message)
                if part
            )[:160]
        self.status_path = (
            app_data_directory / "chaos-heist-status.txt"
        )
        self.event_log_path = (
            app_data_directory / "chaos-heist-events.log"
        )
        self.ring_counter_path = app_data_directory / "ring-counter.json"
        self.ring_counter_backup_path = (
            app_data_directory / "ring-counter.backup.json"
        )
        (
            self.ring_count,
            self.ring_milestones_shown,
            self.ring_milestones_pending,
        ) = self.load_ring_state()
        self.pending_ring_milestone = (
            RING_MILESTONE in self.ring_milestones_pending
        )
        if self.pending_ring_milestone:
            self.pending_ring_announcement = "milestone"
        if self.ring_state_warning:
            self.write_status(
                "RING COUNTER WARNING | " + self.ring_state_warning
            )
        self.reader_connected = False
        self.last_good_port = PREFERRED_SERIAL_PORT
        self.last_valid_message = 0.0
        self.last_serial_message_at = 0.0
        self.activation_started_at = 0.0
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
        self.control_reset_rings_button = None
        self.story_question_message_frame = None
        self.story_question_prefix_label = None
        self.story_question_sonic_label = None
        self.story_question_suffix_label = None
        self.announcement_window = None
        self.announcement_title_label = None
        self.announcement_detail_label = None
        self.announcement_energy_canvas = None
        self.ring_power_meter_window = None
        self.ring_power_meter_canvas = None
        self.announcement_flash_window = None
        self.announcement_flash_after_id = None
        self.power_loss_crt_window = None
        self.power_loss_crt_canvas = None

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
        self.ring_audio_path = self.find_asset(
            RING_AUDIO_NAME,
            ORIGINAL_RING_AUDIO_PATH,
        )
        self.act_clear_audio_path = self.find_optional_audio_asset(
            ACT_CLEAR_AUDIO_NAME,
            ORIGINAL_ACT_CLEAR_AUDIO_PATH,
            ("act", "clear"),
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
        self.story_shutdown_audio_path = self.find_asset(
            STORY_SHUTDOWN_AUDIO_NAME,
            ORIGINAL_STORY_SHUTDOWN_AUDIO_PATH,
        )
        self.power_loss_audio_paths = {
            key: self.find_asset(
                POWER_LOSS_AUDIO_NAMES[key],
                ORIGINAL_POWER_LOSS_AUDIO_PATHS[key],
            )
            for key in POWER_LOSS_AUDIO_NAMES
        }
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

                if self.ring_audio_path.exists():
                    self.ring_sound = pygame.mixer.Sound(
                        str(self.ring_audio_path)
                    )
                    self.ring_sound.set_volume(SOUND_EFFECT_VOLUME)

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

                if self.story_shutdown_audio_path.exists():
                    try:
                        self.story_shutdown_sound = pygame.mixer.Sound(
                            str(self.story_shutdown_audio_path)
                        )
                        self.story_shutdown_sound.set_volume(
                            SOUND_EFFECT_VOLUME
                        )
                    except pygame.error:
                        self.story_shutdown_sound = None

                for power_loss_key, power_loss_audio_path in (
                    self.power_loss_audio_paths.items()
                ):
                    if not power_loss_audio_path.exists():
                        continue
                    try:
                        power_loss_sound = pygame.mixer.Sound(
                            str(power_loss_audio_path)
                        )
                        power_loss_sound.set_volume(SOUND_EFFECT_VOLUME)
                        self.power_loss_sounds[power_loss_key] = (
                            power_loss_sound
                        )
                    except pygame.error:
                        continue

                if self.act_clear_audio_path.exists():
                    try:
                        self.act_clear_sound = pygame.mixer.Sound(
                            str(self.act_clear_audio_path)
                        )
                        self.act_clear_sound.set_volume(
                            SOUND_EFFECT_VOLUME
                        )
                    except pygame.error:
                        self.act_clear_sound = None

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

        # The question card uses three labels so only the word "Sonic" can
        # be colored blue while the rest of the story message remains white.
        self.story_question_message_frame = tk.Frame(
            self.root,
            background="#000000",
            takefocus=0,
        )
        self.story_question_prefix_label = tk.Label(
            self.story_question_message_frame,
            text="Only ",
            foreground="white",
            background="#000000",
            takefocus=0,
        )
        self.story_question_sonic_label = tk.Label(
            self.story_question_message_frame,
            text="Sonic",
            foreground="#2f8cff",
            background="#000000",
            takefocus=0,
        )
        self.story_question_suffix_label = tk.Label(
            self.story_question_message_frame,
            text=" can save us!",
            foreground="white",
            background="#000000",
            takefocus=0,
        )
        for label in (
            self.story_question_prefix_label,
            self.story_question_sonic_label,
            self.story_question_suffix_label,
        ):
            label.pack(side="left", padx=0, pady=0)
        self.story_question_message_frame.place_forget()

        self.energy_canvas = tk.Canvas(
            self.root,
            background="#000000",
            borderwidth=0,
            highlightthickness=0,
        )
        self.energy_canvas.place_forget()

        self.messages: queue.Queue[tuple[str, str, int]] = queue.Queue()
        self.create_announcement_window()

        self.ring_persistence_thread = threading.Thread(
            target=self.worker_entry,
            args=(
                "ring persistence",
                self.ring_persistence_worker,
                False,
            ),
            daemon=True,
        )
        self.ring_persistence_thread.start()

        if AV_AVAILABLE and self.cinematic_video_path.exists():
            self.cinematic_prepare_state = "preparing"
            self.cinematic_prepare_started_at = time.monotonic()
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
            args=(
                "keyboard shortcut",
                self.global_service_hotkey_worker,
                False,
            ),
            daemon=True,
        ).start()

        worker = threading.Thread(
            target=self.worker_entry,
            args=("ESP32 serial", self.serial_worker, True),
            daemon=True,
        )
        worker.start()

        self.ring_input_thread = threading.Thread(
            target=self.worker_entry,
            args=("ring input", self.ring_input_worker, False),
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
        self.write_status(
            "CONFIG LOADED | "
            + (
                str(ACTIVE_CONFIG_PATH)
                if ACTIVE_CONFIG_PATH is not None
                else "built-in defaults"
            )
        )
        for warning in CONFIG_WARNINGS:
            self.write_status("CONFIG WARNING | " + warning)
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

    def energy_meter_text(
        self,
        present_count: int,
        *,
        display_percent: Optional[float] = None,
    ) -> str:
        present_count = max(0, min(TOTAL_EMERALDS, int(present_count)))
        if display_percent is None:
            percent = round(present_count * 100 / TOTAL_EMERALDS)
        else:
            try:
                percent = round(
                    max(0.0, min(100.0, float(display_percent)))
                )
            except (TypeError, ValueError):
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

    def render_segmented_energy_meter(
        self,
        canvas,
        present_count: int,
        *,
        emphasis: bool = False,
        meter_width: Optional[int] = None,
        meter_height: Optional[int] = None,
        blink_segment_index: Optional[int] = None,
        blink_on: bool = True,
        display_percent: Optional[float] = None,
    ) -> tuple[int, int]:
        """Draw the same labeled, color-coded meter on any Tk canvas.

        ``blink_segment_index`` is used by Ring Power's countdown. The
        selected segment alternates between its normal filled color and the
        empty-segment appearance while all other segments remain stable.
        """
        present_count = max(0, min(TOTAL_EMERALDS, int(present_count)))
        _, _, monitor_width, monitor_height = self.overlay_monitor_bounds
        meter_width = meter_width or max(
            240,
            min(520, int(monitor_width * 0.72)),
        )
        meter_height = meter_height or max(
            42,
            min(62, int(monitor_height * 0.11)),
        )
        text = self.energy_meter_text(
            present_count,
            display_percent=display_percent,
        )
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
        canvas.configure(
            width=meter_width,
            height=meter_height,
            background="#000000",
        )
        canvas.delete("all")
        canvas.create_text(
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
            if index == blink_segment_index and not blink_on:
                filled = False
            canvas.create_rectangle(
                left,
                bar_top,
                right,
                bar_bottom,
                fill=color if filled else "#181818",
                outline="#ffffff" if emphasis and filled else "#606060",
                width=2 if emphasis and filled else 1,
            )
        return meter_width, meter_height

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
        self.render_segmented_energy_meter(
            self.energy_canvas,
            present_count,
            emphasis=emphasis,
        )
        if visible:
            self.position_energy_meter()
        else:
            self.energy_canvas.place_forget()

    def set_announcement_energy_meter(
        self,
        present_count: int,
        *,
        visible: bool,
        emphasis: bool = False,
    ) -> None:
        """Show the Robotnik-style meter inside a temporary banner."""
        canvas = getattr(self, "announcement_energy_canvas", None)
        if canvas is None:
            return
        try:
            canvas.pack_forget()
        except tk.TclError:
            pass
        if not visible:
            return
        self.render_segmented_energy_meter(
            canvas,
            present_count,
            emphasis=emphasis,
            meter_width=max(
                220,
                min(500, int(self.overlay_monitor_bounds[2] * 0.70)),
            ),
            meter_height=max(
                42,
                min(62, int(self.overlay_monitor_bounds[3] * 0.10)),
            ),
        )
        canvas.pack(pady=(0, 10))

    def ring_power_meter_size(self) -> tuple[int, int]:
        """Return a compact meter size that remains usable on arcade modes."""
        _x, _y, monitor_width, monitor_height = self.overlay_monitor_bounds
        return (
            max(220, min(500, int(monitor_width * 0.70))),
            max(42, min(62, int(monitor_height * 0.10))),
        )

    def show_ring_power_meter_overlay(self, present_count: int) -> None:
        """Show Ring Power's independent, non-dismissable energy display."""
        window = getattr(self, "ring_power_meter_window", None)
        canvas = getattr(self, "ring_power_meter_canvas", None)
        if window is None or canvas is None:
            return

        present_count = max(
            0,
            min(TOTAL_EMERALDS, int(present_count)),
        )
        meter_width, meter_height = self.ring_power_meter_size()
        x, y, monitor_width, monitor_height = self.overlay_monitor_bounds
        meter_x = x + max(0, (monitor_width - meter_width) // 2)
        meter_y = y + max(
            4,
            min(
                int(monitor_height * 0.76),
                monitor_height - meter_height - max(4, int(monitor_height * 0.04)),
            ),
        )
        try:
            self.render_segmented_energy_meter(
                canvas,
                present_count,
                emphasis=True,
                meter_width=meter_width,
                meter_height=meter_height,
            )
            window.geometry(
                f"{meter_width}x{meter_height}{meter_x:+d}{meter_y:+d}"
            )
            self.apply_announcement_window_style(window)
            window.deiconify()
            window.update_idletasks()
            window_handle = window.winfo_id()
            ctypes.windll.user32.SetWindowPos(
                window_handle,
                HWND_TOPMOST,
                meter_x,
                meter_y,
                meter_width,
                meter_height,
                SWP_SHOWWINDOW | SWP_NOACTIVATE,
            )
            ctypes.windll.user32.ShowWindow(
                window_handle,
                SW_SHOWNOACTIVATE,
            )
            self.ring_power_meter_visible = True
        except (AttributeError, tk.TclError):
            self.ring_power_meter_visible = False

    def hide_ring_power_meter_overlay(self) -> None:
        """Hide Ring Power's meter without affecting other announcements."""
        self.cancel_ring_power_meter_animation()
        window = getattr(self, "ring_power_meter_window", None)
        if window is not None:
            try:
                window.withdraw()
            except tk.TclError:
                pass
        self.ring_power_meter_visible = False

    def cancel_ring_power_meter_animation(self) -> None:
        self.ring_power_meter_animation_generation = (
            getattr(self, "ring_power_meter_animation_generation", 0) + 1
        )
        after_id = getattr(
            self,
            "ring_power_meter_animation_after_id",
            None,
        )
        if after_id is not None:
            try:
                self.root.after_cancel(after_id)
            except (AttributeError, tk.TclError):
                pass
        self.ring_power_meter_animation_after_id = None
        self.ring_power_meter_filling = False

    def render_ring_power_countdown_meter(self, now=None) -> None:
        """Render the Ring Power countdown with a linear numeric percentage.

        The segment display is intentionally discrete, but the percentage is
        calculated directly from the selection deadline. Keeping those two
        values separate prevents the numeric readout from inheriting any
        segment-boundary timing or rounding behavior.
        """
        if not (
            getattr(self, "ring_burst_active", False)
            and getattr(self, "ring_burst_selection_deadline", None) is not None
            and not getattr(self, "ring_burst_selection_expired", False)
            and getattr(self, "ring_burst_game_seen_since", 0.0) == 0.0
        ):
            return
        canvas = getattr(self, "ring_power_meter_canvas", None)
        if canvas is None:
            return

        started_at = getattr(self, "ring_power_selection_started_at", 0.0)
        if not started_at:
            deadline = self.ring_burst_selection_deadline
            started_at = deadline - RING_POWER_SELECTION_SECONDS
        if now is None:
            now = time.monotonic()
        now = float(now)
        elapsed = max(0.0, now - started_at)
        segment_seconds = max(0.001, RING_POWER_SEGMENT_SECONDS)
        elapsed_energy = min(
            float(TOTAL_EMERALDS),
            elapsed / segment_seconds,
        )
        phase = min(
            TOTAL_EMERALDS,
            int(elapsed_energy),
        )
        remaining_segments = TOTAL_EMERALDS - phase
        total_seconds = max(0.001, float(RING_POWER_SELECTION_SECONDS))
        remaining_seconds = max(
            0.0,
            min(
                total_seconds,
                float(self.ring_burst_selection_deadline) - now,
            ),
        )
        remaining_percent = remaining_seconds * 100.0 / total_seconds
        self.render_segmented_energy_meter(
            canvas,
            remaining_segments,
            emphasis=True,
            meter_width=self.ring_power_meter_size()[0],
            meter_height=self.ring_power_meter_size()[1],
            blink_segment_index=(
                remaining_segments - 1
                if remaining_segments > 0
                else None
            ),
            blink_on=getattr(self, "ring_power_meter_blink_on", True),
            display_percent=remaining_percent,
        )

    def animate_ring_power_meter_fill(
        self,
        previous_count: int,
        current_count: int,
    ) -> None:
        """Quickly fill the independent meter before its timed drain begins."""
        canvas = getattr(self, "ring_power_meter_canvas", None)
        if canvas is None:
            return
        previous_count = max(
            0,
            min(TOTAL_EMERALDS, int(previous_count)),
        )
        current_count = max(
            0,
            min(TOTAL_EMERALDS, int(current_count)),
        )
        self.cancel_ring_power_meter_animation()
        self.ring_power_meter_filling = True
        generation = self.ring_power_meter_animation_generation
        step = 1 if current_count >= previous_count else -1
        values = list(range(previous_count, current_count + step, step))
        if not values:
            values = [current_count]

        def show_step(index: int) -> None:
            if generation != self.ring_power_meter_animation_generation:
                return
            if not (
                getattr(self, "ring_burst_active", False)
                and getattr(self, "ring_burst_selection_deadline", None)
                is not None
            ):
                self.ring_power_meter_animation_after_id = None
                self.ring_power_meter_filling = False
                return

            self.ring_power_meter_animation_after_id = None
            last_step = index + 1 >= len(values)
            self.show_ring_power_meter_overlay(values[index])
            if last_step:
                self.ring_power_meter_filling = False
                self.render_ring_power_countdown_meter()
                return
            self.ring_power_meter_animation_after_id = self.root.after(
                RING_POWER_METER_STEP_MS,
                lambda: show_step(index + 1),
            )

        show_step(0)

    def update_ring_power_meter(self) -> None:
        """Toggle the active drain segment and redraw the independent meter."""
        if getattr(self, "ring_power_meter_filling", False):
            return
        self.ring_power_meter_blink_on = not getattr(
            self,
            "ring_power_meter_blink_on",
            True,
        )
        self.render_ring_power_countdown_meter()

    def cancel_announcement_energy_animation(self) -> None:
        self.announcement_energy_animation_generation = (
            getattr(self, "announcement_energy_animation_generation", 0) + 1
        )
        after_id = getattr(
            self,
            "announcement_energy_animation_after_id",
            None,
        )
        if after_id is not None:
            try:
                self.root.after_cancel(after_id)
            except (AttributeError, tk.TclError):
                pass
        self.announcement_energy_animation_after_id = None

    def animate_announcement_energy_meter(
        self,
        previous_count: int,
        current_count: int,
    ) -> None:
        """Rapidly fill the temporary Ring Power meter to full energy."""
        if not getattr(self, "announcement_energy_canvas", None):
            return
        previous_count = max(
            0,
            min(TOTAL_EMERALDS, int(previous_count)),
        )
        current_count = max(
            0,
            min(TOTAL_EMERALDS, int(current_count)),
        )
        self.cancel_announcement_energy_animation()
        generation = self.announcement_energy_animation_generation
        step = 1 if current_count >= previous_count else -1
        values = list(range(previous_count, current_count + step, step))
        if not values:
            values = [current_count]

        def show_step(index: int) -> None:
            if generation != self.announcement_energy_animation_generation:
                return
            if (
                getattr(self, "active_ring_announcement_kind", None)
                != "burst"
                or not getattr(
                    self,
                    "ring_power_announcement_visible",
                    False,
                )
            ):
                self.announcement_energy_animation_after_id = None
                return

            self.announcement_energy_animation_after_id = None
            last_step = index + 1 >= len(values)
            self.set_announcement_energy_meter(
                values[index],
                visible=True,
                emphasis=not last_step,
            )
            if last_step:
                return
            self.announcement_energy_animation_after_id = self.root.after(
                RING_POWER_METER_STEP_MS,
                lambda: show_step(index + 1),
            )

        show_step(0)

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
        width_fraction: float = 0.82,
    ) -> int:
        # Leave extra horizontal margin for CRT overscan and the arcade
        # monitor's visible bezel area.
        target_width = available_width or self.screen_width
        available_width = max(
            1,
            int(target_width * max(0.10, min(0.98, width_fraction))),
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

    def find_optional_audio_asset(
        self,
        asset_name: str,
        original_path: Path,
        keywords: tuple[str, ...],
    ) -> Path:
        """Find an optional audio asset, tolerating a descriptive filename."""
        exact_path = self.find_asset(asset_name, original_path)
        if exact_path.exists():
            return exact_path

        directories = [application_directory()]
        bundled_directory = getattr(sys, "_MEIPASS", None)
        if bundled_directory:
            directories.append(Path(bundled_directory))
        directories.extend(
            [Path(__file__).resolve().parent, original_path.parent]
        )
        seen_directories = set()
        for directory in directories:
            directory_key = str(directory).lower()
            if directory_key in seen_directories or not directory.is_dir():
                continue
            seen_directories.add(directory_key)
            try:
                candidates = sorted(
                    path
                    for path in directory.iterdir()
                    if path.is_file()
                    and path.suffix.lower() in {".mp3", ".wav", ".ogg"}
                    and all(
                        keyword.lower() in path.stem.lower()
                        for keyword in keywords
                    )
                )
            except OSError:
                continue
            if candidates:
                candidate = candidates[0]
                return self.find_asset(candidate.name, candidate)

        return exact_path

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
        self.announcement_energy_canvas = tk.Canvas(
            self.announcement_window,
            background="#000000",
            borderwidth=0,
            highlightthickness=0,
        )
        self.announcement_window.withdraw()

        # Ring Power's meter has its own non-activating surface. The text
        # banner can be dismissed for convenience, but this meter remains
        # visible until a game starts or Ring Power expires.
        self.ring_power_meter_window = tk.Toplevel(self.root)
        self.ring_power_meter_window.overrideredirect(True)
        self.ring_power_meter_window.configure(
            background="#000000",
            cursor="none",
        )
        self.ring_power_meter_window.attributes("-topmost", True)
        try:
            self.ring_power_meter_window.attributes("-alpha", 0.94)
        except tk.TclError:
            pass
        self.ring_power_meter_canvas = tk.Canvas(
            self.ring_power_meter_window,
            background="#000000",
            borderwidth=0,
            highlightthickness=0,
        )
        self.ring_power_meter_canvas.pack(fill="both", expand=True)
        self.ring_power_meter_window.withdraw()

        self.announcement_flash_window = tk.Toplevel(self.root)
        self.announcement_flash_window.overrideredirect(True)
        self.announcement_flash_window.configure(background="#fff6b0")
        self.announcement_flash_window.configure(cursor="none")
        self.announcement_flash_window.attributes("-topmost", True)
        try:
            self.announcement_flash_window.attributes("-alpha", 0.78)
        except tk.TclError:
            pass
        self.announcement_flash_window.withdraw()

        # Separate non-activating surface for the glitch bars and the final
        # CRT line-to-dot collapse. Keeping it independent from the full-screen
        # filter lets the frozen Big Box menu remain visible through the
        # flicker while still producing a clean white line at the end.
        self.power_loss_crt_window = tk.Toplevel(self.root)
        self.power_loss_crt_window.overrideredirect(True)
        self.power_loss_crt_window.configure(background="#050505")
        self.power_loss_crt_window.configure(cursor="none")
        self.power_loss_crt_window.attributes("-topmost", True)
        try:
            self.power_loss_crt_window.attributes("-alpha", 0.18)
        except tk.TclError:
            pass
        self.power_loss_crt_canvas = tk.Canvas(
            self.power_loss_crt_window,
            background="#050505",
            borderwidth=0,
            highlightthickness=0,
        )
        self.power_loss_crt_canvas.pack(fill="both", expand=True)
        self.power_loss_crt_window.withdraw()

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

    def hide_power_loss_crt(self) -> None:
        if self.power_loss_crt_window:
            try:
                self.power_loss_crt_window.withdraw()
            except tk.TclError:
                pass
        if self.power_loss_crt_canvas:
            try:
                self.power_loss_crt_canvas.delete("all")
            except tk.TclError:
                pass

    def position_power_loss_window(
        self,
        window,
        width: int,
        height: int,
        alpha: float,
        centered: bool = False,
    ) -> None:
        if not window:
            return

        x, y, monitor_width, monitor_height = self.overlay_monitor_bounds
        if centered:
            x += max(0, (monitor_width - width) // 2)
            y += max(0, (monitor_height - height) // 2)
        try:
            window.geometry(f"{width}x{height}{x:+d}{y:+d}")
            window.attributes(
                "-alpha",
                max(0.0, min(1.0, float(alpha))),
            )
            self.apply_announcement_window_style(window)
            window.deiconify()
            window_handle = window.winfo_id()
            ctypes.windll.user32.SetWindowPos(
                window_handle,
                HWND_TOPMOST,
                x,
                y,
                width,
                height,
                SWP_SHOWWINDOW | SWP_NOACTIVATE,
            )
        except (AttributeError, tk.TclError):
            pass

    def show_power_loss_glitches(
        self,
        tick: int,
        alpha: float,
    ) -> None:
        """Draw irregular scanline glitches over the dimming menu."""
        if not self.power_loss_crt_window or not self.power_loss_crt_canvas:
            return

        _x, _y, width, height = self.overlay_monitor_bounds
        width = max(1, int(width))
        height = max(1, int(height))
        canvas = self.power_loss_crt_canvas
        try:
            canvas.configure(background="#050505")
            canvas.delete("all")
            bar_colors = ("#ffffff", "#ff3344", "#111111", "#fff6b0")
            for index in range(9):
                bar_y = (tick * (31 + index * 7) + index * 97) % height
                bar_height = 2 + ((tick + index * 3) % 15)
                color = bar_colors[(tick + index * 2) % len(bar_colors)]
                canvas.create_rectangle(
                    0,
                    bar_y,
                    width,
                    min(height, bar_y + bar_height),
                    fill=color,
                    outline="",
                )
            canvas.update_idletasks()
        except tk.TclError:
            return

        self.position_power_loss_window(
            self.power_loss_crt_window,
            width,
            height,
            alpha,
        )

    def show_power_loss_crt_line(
        self,
        line_width: int,
        line_height: int,
        alpha: float,
    ) -> None:
        """Show the final horizontal CRT collapse, shrinking toward a dot."""
        if not self.power_loss_crt_window or not self.power_loss_crt_canvas:
            return

        line_width = max(2, int(line_width))
        line_height = max(2, int(line_height))
        try:
            self.power_loss_crt_window.configure(background="#ffffff")
            self.power_loss_crt_canvas.configure(background="#ffffff")
            self.power_loss_crt_canvas.delete("all")
        except tk.TclError:
            return

        self.position_power_loss_window(
            self.power_loss_crt_window,
            line_width,
            line_height,
            alpha,
            centered=True,
        )

    def hide_power_loss_cursor(self) -> None:
        """Hide the real Windows pointer while the menu is losing power."""
        if self.power_loss_cursor_hidden:
            return
        try:
            cursor_info = Win32CursorInfo()
            cursor_info.cbSize = ctypes.sizeof(Win32CursorInfo)
            user32 = ctypes.windll.user32
            if not user32.GetCursorInfo(ctypes.byref(cursor_info)):
                return
            if cursor_info.flags & CURSOR_SHOWING:
                user32.ShowCursor(False)
                self.power_loss_cursor_hidden = True
        except (AttributeError, OSError, TypeError):
            pass

    def restore_power_loss_cursor(self) -> None:
        """Restore the pointer only if this effect hid an initially visible one."""
        if not self.power_loss_cursor_hidden:
            return
        try:
            ctypes.windll.user32.ShowCursor(True)
        except (AttributeError, OSError, TypeError):
            pass
        finally:
            self.power_loss_cursor_hidden = False

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

    def configure_announcement_content(
        self,
        title: str,
        detail: str,
        color: str,
        *,
        title_max_size: int,
        title_min_size: int,
        detail_max_size: int,
        detail_min_size: int,
    ) -> tuple[int, int, int, int]:
        """Fit announcement text horizontally and vertically before showing it."""
        x, y, monitor_width, monitor_height = self.overlay_monitor_bounds
        banner_width = max(1, int(monitor_width * 0.90))
        wraplength = max(1, int(banner_width * 0.82))
        title_size = self.fit_font_size(
            tuple(title.splitlines()) or (title,),
            max_size=title_max_size,
            min_size=title_min_size,
            available_width=banner_width,
        )
        detail_size = self.fit_font_size(
            tuple(detail.splitlines()) or (detail,),
            max_size=detail_max_size,
            min_size=detail_min_size,
            available_width=banner_width,
        )

        # The old fixed 30%-of-screen banner could clip packed labels at the
        # top and bottom on short arcade displays. Allow a taller centered
        # banner, then reduce fonts if the packed content still needs room.
        max_banner_height = max(
            88,
            monitor_height - max(12, int(monitor_height * 0.04)),
        )
        required_height = max_banner_height
        for _ in range(40):
            self.announcement_title_label.configure(
                text=title,
                foreground=color,
                font=("Arial", title_size, "bold"),
                wraplength=wraplength,
                justify="center",
            )
            self.announcement_detail_label.configure(
                text=detail,
                font=("Arial", detail_size, "bold"),
                wraplength=wraplength,
                justify="center",
            )
            self.announcement_window.update_idletasks()
            required_height = (
                self.announcement_title_label.winfo_reqheight()
                + self.announcement_detail_label.winfo_reqheight()
                + (
                    self.announcement_energy_canvas.winfo_reqheight()
                    if (
                        getattr(self, "announcement_energy_canvas", None)
                        and self.announcement_energy_canvas.winfo_manager()
                        == "pack"
                    )
                    else 0
                )
                # Include a real safety margin below the packed meter. Tk's
                # requested height can otherwise be a few pixels shorter
                # than the final rendered canvas on arcade resolutions.
                + 34
            )
            if required_height <= max_banner_height:
                break
            if detail_size > detail_min_size:
                detail_size -= 1
            elif title_size > title_min_size:
                title_size -= 1
            else:
                break

        banner_height = max(
            88,
            min(max_banner_height, required_height),
        )
        banner_x = x + (monitor_width - banner_width) // 2
        banner_y = y + (monitor_height - banner_height) // 2
        return banner_x, banner_y, banner_width, banner_height

    def emulator_owns_foreground(self) -> bool:
        """Fail safely when deciding whether a small overlay may be created."""
        try:
            foreground_window = ctypes.windll.user32.GetForegroundWindow()
        except AttributeError:
            return True
        return (
            self.get_window_process_name(foreground_window)
            in EMULATOR_PROCESS_NAMES
        )

    def show_story_announcement(
        self,
        present_count: int,
        event_kind: str,
        duration_seconds: Optional[float] = None,
    ) -> bool:
        if getattr(self, "active_ring_announcement_kind", None) == "milestone":
            # The 50-ring prize owns the presentation until its guaranteed
            # display interval completes.
            return False
        if not self.can_show_story_announcement():
            return False

        self.hide_story_announcement()
        missing_count = TOTAL_EMERALDS - present_count
        # Every temporary emerald status banner uses the same segmented meter
        # as the full Robotnik screen, in both Story and Normal Mode.
        uses_energy_meter = event_kind in {
            "removed",
            "returned",
            "normal",
            "restored_normal",
        }
        if event_kind == "removed":
            _, detail = STORY_STOLEN_TEXT.get(
                missing_count,
                (
                    f"{missing_count} CHAOS EMERALDS STOLEN!",
                    "ROBOTNIK'S CHAOS HEIST CONTINUES!",
                ),
            )
            title = STORY_REMOVAL_OVERLAY_TITLE
            color = "#ff5555"
        elif event_kind == "normal":
            title = STORY_REMOVAL_OVERLAY_TITLE
            detail = NORMAL_WARNING_MESSAGE
            color = "#ffcc66"
        elif event_kind == "restored_normal":
            title = NORMAL_RESTORED_TITLE
            detail = "THE SHRINE IS RECLAIMING ITS POWER!"
            color = "#77ff99"
        else:
            title, detail = STORY_RETURNED_TEXT.get(
                present_count,
                (
                    f"{present_count} CHAOS EMERALDS RESTORED!",
                    "THE SHRINE IS RECLAIMING ITS POWER!",
                ),
            )
            color = "#77ff99"

        self.set_announcement_energy_meter(
            present_count,
            visible=uses_energy_meter,
        )

        banner_x, banner_y, banner_width, banner_height = (
            self.configure_announcement_content(
                title,
                detail,
                color,
                title_max_size=max(
                    14,
                    min(
                        26,
                        int(self.overlay_monitor_bounds[3] * 0.052),
                    ),
                ),
                title_min_size=10,
                detail_max_size=max(
                    11,
                    min(
                        19,
                        int(self.overlay_monitor_bounds[3] * 0.035),
                    ),
                ),
                detail_min_size=8,
            )
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

        if not self.announcement_window_matches_bounds(
            banner_x,
            banner_y,
            banner_width,
            banner_height,
        ):
            try:
                self.announcement_window.withdraw()
            except tk.TclError:
                pass
            self.write_status(
                "EMERALD ANNOUNCEMENT SKIPPED | window did not render correctly"
            )
            return False

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
        if getattr(self, "active_ring_announcement_kind", None) == "milestone":
            # Do not let sensor changes, later rings, or a delayed callback
            # interrupt the prize announcement. Keep it active and re-queue
            # the durable milestone state if a caller tried to hide it.
            self.pending_ring_milestone = True
            self.pending_ring_announcement = "milestone"
            self.ring_milestones_pending.add(RING_MILESTONE)
            self.save_ring_state()
            self.write_status(
                "RING MILESTONE INTERRUPT IGNORED | kept visible"
            )
            return
        previous_ring_kind = getattr(
            self,
            "active_ring_announcement_kind",
            None,
        )
        if previous_ring_kind == "milestone":
            # Only the dedicated timeout callback acknowledges delivery. Any
            # other hide (game launch, mode switch, emerald event, shutdown)
            # means the camper did not receive the promised ten seconds.
            self.pending_ring_milestone = True
            self.pending_ring_announcement = "milestone"
            self.ring_milestones_pending.add(RING_MILESTONE)
            self.save_ring_state()
            self.write_status(
                "RING MILESTONE INTERRUPTED | queued for safe menu"
            )
        self.active_ring_announcement_kind = None
        self.ring_power_announcement_visible = False
        self.cancel_announcement_energy_animation()
        self.set_announcement_energy_meter(0, visible=False)
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

        if (
            previous_ring_kind is not None
            and getattr(self, "pending_ring_announcement", None)
            and getattr(self, "root", None) is not None
        ):
            try:
                self.root.after_idle(
                    self.maybe_show_pending_ring_announcement
                )
            except (AttributeError, tk.TclError):
                pass

    def defer_announcement_for_emulator(self) -> None:
        """Remove every small topmost window before an emulator takes over."""
        active_kind = getattr(
            self,
            "active_ring_announcement_kind",
            None,
        )
        if active_kind == "milestone":
            # The prize has not received its guaranteed safe-menu display.
            # Keep it durable and replay the full announcement after the game.
            self.pending_ring_milestone = True
            self.pending_ring_announcement = "milestone"
            self.ring_milestones_pending.add(RING_MILESTONE)
            self.save_ring_state()

        self.active_ring_announcement_kind = None
        self.ring_power_announcement_visible = False
        self.hide_ring_power_meter_overlay()
        self.cancel_announcement_energy_animation()
        if self.announcement_after_id is not None:
            try:
                self.root.after_cancel(self.announcement_after_id)
            except (AttributeError, tk.TclError):
                pass
            self.announcement_after_id = None
        self.hide_announcement_flash()
        self.set_announcement_energy_meter(0, visible=False)
        if self.announcement_window:
            try:
                self.announcement_window.withdraw()
            except tk.TclError:
                pass
        self.stop_event_sound()
        suffix = (
            " | 50-ring prize re-queued"
            if active_kind == "milestone"
            else ""
        )
        self.write_status(
            "ANNOUNCEMENT DEFERRED | emulator foreground" + suffix
        )

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

    def prepare_cinematic_frame_image(self, frame, target_size: tuple[int, int]):
        """Detach a decoded frame before scaling it for Tk.

        PyAV frames can expose padded YUV/RGB planes whose lifetime and stride
        are tied to the decoder. The RGB plane is therefore copied row by row
        into a tightly packed Pillow image before Tk sees it. This explicitly
        removes any decoder padding instead of relying on the version-specific
        behavior of PyAV's ``to_image`` conversion. BILINEAR is intentionally
        used here: the cinematic is prebuffered and the arcade PC needs
        predictable frame time more than an expensive resize filter.
        """
        try:
            rgb_frame = frame.reformat(format="rgb24")
            rgb_plane = rgb_frame.planes[0]
            row_width = rgb_frame.width * 3
            row_stride = rgb_plane.line_size
            plane_bytes = bytes(rgb_plane)
            packed_bytes = b"".join(
                plane_bytes[row * row_stride:row * row_stride + row_width]
                for row in range(rgb_frame.height)
            )
            source_image = Image.frombytes(
                "RGB",
                (rgb_frame.width, rgb_frame.height),
                packed_bytes,
            )
        except (AttributeError, IndexError, TypeError, ValueError):
            # Keep compatibility with unusual PyAV frame implementations
            # while still detaching their memory before any resize.
            source_image = frame.to_image().convert("RGB").copy()
        if source_image.size == target_size:
            source_image.load()
            return source_image

        resampling = getattr(Image, "Resampling", Image).BILINEAR
        display_image = source_image.resize(
            target_size,
            resample=resampling,
        )
        display_image.load()
        return display_image.copy()

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
                    # Detach from PyAV's frame buffers before Tk sees the
                    # image. This avoids stride/padding artifacts and keeps
                    # decoder-owned memory from being reused underneath a
                    # queued display frame.
                    frame_image = self.prepare_cinematic_frame_image(
                        frame,
                        target_size,
                    )

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
        if getattr(self, "active_ring_announcement_kind", None) == "milestone":
            self.milestone_deferred_count_change = True
            return
        if self.skip_cinematic_requested:
            self.skip_cinematic_requested = False
            self.write_status("CINEMATIC SKIPPED | ROBOTNIK SCREEN ACTIVE")
            self.show_story_robotnik_screen()
            return
        if self.cinematic_prepare_state == "preparing":
            if self.cinematic_story_wait_started_at == 0.0:
                self.cinematic_story_wait_started_at = time.monotonic()
            if (
                time.monotonic() - self.cinematic_story_wait_started_at
                >= CINEMATIC_START_TIMEOUT_SECONDS
            ):
                self.fault_disable_guard(
                    "Sonic cinematic preparation timed out"
                )
                return
            self.story_sequence_after_id = self.root.after(
                100,
                self.start_story_cinematic,
            )
            return
        self.cinematic_story_wait_started_at = 0.0
        if self.cinematic_prepare_state != "ready":
            self.fault_disable_guard(
                "Could not prepare Sonic cinematic: "
                + (self.cinematic_prepare_error or "unknown error")
            )
            return

        self.cancel_cinematic()
        self.overlay_kind = "cinematic"
        self.hide_energy_meter()
        self.hide_story_question_message()
        # No power-loss filter or CRT/glitch surface may survive into the
        # video. Those windows are independent of the full-screen root, so
        # explicitly withdraw them at the handoff as well as during normal
        # power-loss cleanup.
        self.hide_announcement_flash()
        self.hide_power_loss_crt()
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
        self.cinematic_wait_started_at = time.monotonic()
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

        # Install a fresh Tk photo for every frame. Reusing PhotoImage.paste()
        # was efficient on a desktop display but can leave partial dirty
        # rectangles on the arcade CRT path; the symptom is two solid,
        # partial-height black bars over otherwise clean video. The source
        # frame is already bounded to the configured cinematic FPS, so this
        # more conservative swap is preferable to risking stale pixels.
        self.cinematic_photo = ImageTk.PhotoImage(frame_image)
        self.background_label.configure(
            image=self.cinematic_photo,
            background="black",
        )

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
            now = time.monotonic()
            buffered_frames = self.cinematic_frame_queue.qsize()
            start_timed_out = (
                self.cinematic_wait_started_at > 0.0
                and now - self.cinematic_wait_started_at
                >= CINEMATIC_START_TIMEOUT_SECONDS
            )
            if self.cinematic_pending_frame is None:
                if self.cinematic_worker_done or start_timed_out:
                    self.fault_disable_guard(
                        "Sonic cinematic produced no playable video frames"
                    )
                    return
                self.cinematic_after_id = self.root.after(
                    10,
                    self.poll_cinematic_playback,
                )
                return
            if (
                buffered_frames + 1 < CINEMATIC_PREBUFFER_FRAMES
                and not self.cinematic_worker_done
                and not start_timed_out
            ):
                self.cinematic_after_id = self.root.after(
                    10,
                    self.poll_cinematic_playback,
                )
                return
            self.cinematic_started_at = now
            if not self.start_cinematic_audio():
                self.fault_disable_guard(
                    "Could not play Sonic cinematic audio"
                )
                return

        elapsed = time.monotonic() - self.cinematic_started_at
        if elapsed >= max(
            30.0,
            self.cinematic_duration + CINEMATIC_FINISH_GRACE_SECONDS,
        ):
            self.fault_disable_guard(
                "Sonic cinematic playback timed out"
            )
            return
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
        self.cinematic_wait_started_at = 0.0
        self.cinematic_story_wait_started_at = 0.0
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

    def worker_entry(
        self,
        worker_name: str,
        worker,
        fault_disables_guard: bool = True,
    ) -> None:
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
                    (
                        "CORE_SERVICE_FAULT"
                        if fault_disables_guard
                        else "SERVICE_FAULT"
                    ),
                    f"{worker_name} worker stopped: {detail}",
                    self.activation_generation,
                )
            )

    def schedule_ring_input_restart(self) -> None:
        if (
            not self.running
            or self.ring_input_stop_event.is_set()
            or self.ring_input_restart_after_id is not None
        ):
            return
        delay_ms = min(
            30000,
            1000 * (2 ** min(self.ring_input_restart_count, 5)),
        )
        try:
            self.ring_input_restart_after_id = self.root.after(
                delay_ms,
                self.restart_ring_input_worker,
            )
        except (AttributeError, tk.TclError):
            self.ring_input_restart_after_id = None

    def restart_ring_input_worker(self) -> None:
        self.ring_input_restart_after_id = None
        if not self.running or self.ring_input_stop_event.is_set():
            return
        if self.ring_input_thread and self.ring_input_thread.is_alive():
            return
        self.ring_input_restart_count += 1
        self.joystick_button_states.clear()
        self.joystick_direction_states.clear()
        self.ring_last_press_at.clear()
        self.ring_global_last_press_at = 0.0
        self.ring_input_thread = threading.Thread(
            target=self.worker_entry,
            args=("ring input", self.ring_input_worker, False),
            daemon=True,
        )
        self.ring_input_thread.start()
        self.write_status(
            "RING INPUT RESTARTED | "
            f"attempt={self.ring_input_restart_count}"
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

    def preserve_corrupt_ring_state(self, source_path: Path) -> Optional[Path]:
        """Keep an inspectable copy instead of silently discarding bad data."""
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        for suffix in range(100):
            suffix_text = "" if suffix == 0 else f"-{suffix + 1}"
            preserved_path = source_path.with_name(
                f"{source_path.stem}.corrupt-{timestamp}{suffix_text}"
                f"{source_path.suffix}"
            )
            if preserved_path.exists():
                continue
            try:
                shutil.copy2(source_path, preserved_path)
                return preserved_path
            except OSError:
                return None
        return None

    def read_ring_state_file(
        self,
        state_path: Path,
    ) -> tuple[int, set[int], set[int]]:
        loaded = json.loads(state_path.read_text(encoding="utf-8"))
        return parse_ring_state_payload(loaded)

    def load_ring_state(self) -> tuple[int, set[int], set[int]]:
        """Load the primary counter, recovering visibly from its backup."""
        self.ring_state_warning = ""
        primary_exists = self.ring_counter_path.exists()
        if primary_exists:
            try:
                primary_state = self.read_ring_state_file(
                    self.ring_counter_path
                )
                try:
                    self.read_ring_state_file(self.ring_counter_backup_path)
                except (
                    FileNotFoundError,
                    OSError,
                    TypeError,
                    ValueError,
                    json.JSONDecodeError,
                ):
                    if self.ring_counter_backup_path.exists():
                        self.preserve_corrupt_ring_state(
                            self.ring_counter_backup_path
                        )
                    try:
                        self.write_ring_state_file(
                            self.ring_counter_backup_path,
                            normalized_ring_state_payload(primary_state),
                        )
                        self.ring_state_warning = (
                            "Ring-counter backup was repaired from the "
                            "valid primary."
                        )
                    except OSError:
                        self.ring_state_warning = (
                            "Ring-counter backup is unavailable."
                        )
                return primary_state
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                preserved = self.preserve_corrupt_ring_state(
                    self.ring_counter_path
                )
                preservation_text = (
                    f" preserved as {preserved.name}." if preserved else ""
                )
                self.ring_state_warning = (
                    "Primary ring counter was unreadable;" + preservation_text
                )

        backup_exists = self.ring_counter_backup_path.exists()
        if backup_exists:
            try:
                recovered = self.read_ring_state_file(
                    self.ring_counter_backup_path
                )
                reason = (
                    "primary was unreadable"
                    if primary_exists
                    else "primary was missing"
                )
                self.ring_state_warning += (
                    f" Recovered from backup because the {reason}."
                )
                recovered_total, recovered_shown, recovered_pending = (
                    recovered
                )
                recovered_payload = {
                    "version": RING_STATE_VERSION,
                    "total_rings": recovered_total,
                    "milestones_shown": sorted(recovered_shown),
                    "milestones_pending": sorted(recovered_pending),
                }
                try:
                    self.write_ring_state_file(
                        self.ring_counter_path,
                        recovered_payload,
                    )
                    self.ring_state_warning += " Primary file repaired."
                except OSError:
                    self.ring_state_warning += (
                        " Primary file could not be repaired."
                    )
                return recovered
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                self.preserve_corrupt_ring_state(
                    self.ring_counter_backup_path
                )
                self.ring_state_warning += (
                    " Backup ring counter was also unreadable."
                )

        temporary_path = self.ring_counter_path.with_name(
            self.ring_counter_path.name + ".tmp"
        )
        if temporary_path.is_file():
            try:
                recovered = self.read_ring_state_file(temporary_path)
                recovered_payload = normalized_ring_state_payload(recovered)
                self.write_ring_state_file(
                    self.ring_counter_path,
                    recovered_payload,
                )
                self.write_ring_state_file(
                    self.ring_counter_backup_path,
                    recovered_payload,
                )
                temporary_path.unlink(missing_ok=True)
                self.ring_state_warning += (
                    " Recovered the interrupted ring-counter save."
                )
                return recovered
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                self.preserve_corrupt_ring_state(temporary_path)

        if primary_exists or backup_exists:
            self.ring_state_warning += " Ring total started at 0."
        return 0, set(), set()

    def write_ring_state_file(self, destination: Path, payload: dict) -> None:
        write_json_atomic(destination, payload)

    def persist_ring_state_payload(self, payload: dict) -> None:
        """Write a new primary while retaining the prior valid generation."""
        previous_payload = None
        if self.ring_counter_path.is_file():
            try:
                previous_state = self.read_ring_state_file(
                    self.ring_counter_path
                )
                previous_payload = normalized_ring_state_payload(
                    previous_state
                )
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                previous_payload = None

        # Keep the last known-good generation as the backup. On the very
        # first save, initialize both files to the same validated payload.
        self.write_ring_state_file(
            self.ring_counter_backup_path,
            previous_payload or payload,
        )
        self.write_ring_state_file(self.ring_counter_path, payload)

    def ring_persistence_worker(self) -> None:
        """Coalesce rapid deposits so disk latency never stalls Tk input."""
        while True:
            try:
                payload = self.ring_persistence_queue.get(timeout=0.25)
            except queue.Empty:
                if self.ring_persistence_stop_event.is_set():
                    return
                continue

            if payload is None:
                return

            # Only the newest snapshot matters. A burst of rings should
            # produce immediate UI updates and one durable final total, not a
            # queue of stale writes that blocks sensor handling for seconds.
            while True:
                try:
                    newer_payload = self.ring_persistence_queue.get_nowait()
                except queue.Empty:
                    break
                if newer_payload is None:
                    self.ring_persistence_stop_event.set()
                    break
                payload = newer_payload

            try:
                self.persist_ring_state_payload(payload)
                self.messages.put(("RING_STATE_SAVED", "", -1))
            except OSError as error:
                self.messages.put(
                    (
                        "RING_STATE_SAVE_FAILED",
                        str(error).replace("\n", " ")[:160],
                        -1,
                    )
                )

            if self.ring_persistence_stop_event.is_set():
                return

    def save_ring_state(self) -> None:
        payload = normalized_ring_state_payload(
            (
                self.ring_count,
                self.ring_milestones_shown,
                self.ring_milestones_pending,
            )
        )

        persistence_thread = getattr(
            self,
            "ring_persistence_thread",
            None,
        )
        persistence_queue = getattr(
            self,
            "ring_persistence_queue",
            None,
        )
        if (
            persistence_thread is not None
            and persistence_thread.is_alive()
            and persistence_queue is not None
            and not self.ring_persistence_stop_event.is_set()
        ):
            try:
                persistence_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                persistence_queue.put_nowait(payload)
                return
            except queue.Full:
                # The writer took the previous item between get/put. Falling
                # through to a synchronous save is rare and preserves data.
                pass

        try:
            self.ring_counter_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            self.persist_ring_state_payload(payload)
        except OSError:
            self.ring_state_warning = "Primary ring-counter save failed."
            self.write_status(
                "RING COUNTER SAVE FAILED | total="
                + str(self.ring_count),
                event=False,
            )
            return

        self.ring_state_warning = ""

    def ring_input_worker(self) -> None:
        """Watch every Windows joystick encoder for the configured coin input."""
        while self.running and not self.ring_input_stop_event.is_set():
            try:
                winmm = ctypes.windll.winmm
                joystick_slots = max(1, int(winmm.joyGetNumDevs()))
                button_mask = joystick_button_mask(RING_JOYSTICK_BUTTON)
                now = time.monotonic()
                live_keys = set()

                for joystick_id in range(joystick_slots):
                    joystick_state = Win32JoyInfoEx()
                    joystick_state.dwSize = ctypes.sizeof(Win32JoyInfoEx)
                    joystick_state.dwFlags = (
                        JOY_RETURNBUTTONS
                        | JOY_RETURNX
                        | JOY_RETURNY
                        | JOY_RETURNPOV
                    )
                    result = winmm.joyGetPosEx(
                        joystick_id,
                        ctypes.byref(joystick_state),
                    )
                    if result != JOYERR_NOERROR:
                        continue

                    live_keys.add(joystick_id)
                    buttons = int(joystick_state.dwButtons)
                    previous_buttons = self.joystick_button_states.get(
                        joystick_id
                    )
                    direction_active = joystick_direction_active(
                        joystick_state
                    )
                    previous_direction_active = (
                        self.joystick_direction_states.get(
                            joystick_id,
                            False,
                        )
                    )
                    new_buttons = (
                        buttons & ~previous_buttons
                        if previous_buttons is not None
                        else 0
                    )
                    ring_pressed = bool(buttons & button_mask)
                    was_ring_pressed = bool(
                        previous_buttons is not None
                        and previous_buttons & button_mask
                    )
                    last_press_at = self.ring_last_press_at.get(
                        joystick_id,
                        0.0,
                    )
                    new_direction = (
                        direction_active and not previous_direction_active
                    )
                    if new_buttons or new_direction:
                        self.joystick_press_sequence = (
                            getattr(self, "joystick_press_sequence", 0) + 1
                        )
                        self.messages.put(
                            (
                                "JOYSTICK_PRESS",
                                str(self.joystick_press_sequence),
                                -1,
                            )
                        )
                    if (
                        ring_pressed
                        and not was_ring_pressed
                        and ring_press_is_accepted(
                            now,
                            last_press_at,
                            self.ring_global_last_press_at,
                        )
                    ):
                        # Queue the generic press first. This guarantees the
                        # coin edge cannot race with and immediately dismiss
                        # the Ring Power banner that the same scan creates.
                        self.ring_last_press_at[joystick_id] = now
                        self.ring_global_last_press_at = now
                        self.messages.put(("RING", str(joystick_id), -1))
                    self.joystick_button_states[joystick_id] = buttons
                    self.joystick_direction_states[joystick_id] = (
                        direction_active
                    )

                for state_key in tuple(self.joystick_button_states):
                    if state_key not in live_keys:
                        del self.joystick_button_states[state_key]
                        self.joystick_direction_states.pop(state_key, None)
                        self.ring_last_press_at.pop(state_key, None)

                self.ring_joystick_signature = tuple(
                    sorted(live_keys)
                )
                self.ring_joystick_error = ""
                self.ring_input_restart_count = 0
                if self.service_warning.startswith("ring input"):
                    self.service_warning = ""
            except Exception as error:
                self.ring_joystick_error = str(error)[:120]
                self.joystick_button_states.clear()
                self.joystick_direction_states.clear()
                self.ring_global_last_press_at = 0.0
                if self.ring_input_stop_event.wait(0.5):
                    break
                continue

            if self.ring_input_stop_event.wait(0.01):
                break

    def current_ring_burst_origin(self) -> Optional[str]:
        if not self.guard_active:
            return None
        if self.overlay_kind == "robotnik":
            if self.guard_mode == "normal":
                return "normal_all_missing"
            return "story_robotnik"
        if self.guard_mode == "normal" and self.accepted_count == 0:
            if self.can_show_story_announcement():
                return "normal_all_missing"
        return None

    def ring_burst_is_eligible(self) -> bool:
        return self.current_ring_burst_origin() is not None

    def ring_power_seconds_remaining(self) -> int:
        if getattr(self, "ring_burst_selection_expired", False):
            return 0
        deadline = getattr(self, "ring_burst_selection_deadline", None)
        if deadline is None:
            return max(0, int(math.ceil(RING_POWER_SELECTION_SECONDS)))
        return max(
            0,
            int(math.ceil(deadline - time.monotonic())),
        )

    def cancel_ring_power_selection_timer(self) -> None:
        after_id = getattr(self, "ring_power_selection_after_id", None)
        if after_id is not None:
            try:
                self.root.after_cancel(after_id)
            except (AttributeError, tk.TclError):
                pass
        self.ring_power_selection_after_id = None

    def cancel_ring_power_countdown(self) -> None:
        after_id = getattr(self, "ring_power_countdown_after_id", None)
        if after_id is not None:
            try:
                self.root.after_cancel(after_id)
            except (AttributeError, tk.TclError):
                pass
        self.ring_power_countdown_after_id = None

    def start_ring_power_selection_window(self) -> None:
        self.cancel_ring_power_selection_timer()
        self.cancel_ring_power_countdown()
        self.ring_burst_selection_expired = False
        self.ring_power_selection_started_at = time.monotonic()
        self.ring_burst_selection_deadline = (
            self.ring_power_selection_started_at
            + RING_POWER_SELECTION_SECONDS
        )
        self.ring_power_last_countdown_seconds = None
        self.ring_power_meter_blink_on = True
        self.ring_power_meter_filling = True
        if not getattr(self, "root", None):
            return
        self.ring_power_selection_after_id = self.root.after(
            int(RING_POWER_SELECTION_SECONDS * 1000),
            self.handle_ring_power_selection_timeout,
        )
        self.ring_power_countdown_after_id = self.root.after(
            RING_POWER_COUNTDOWN_TICK_MS,
            self.ring_power_countdown_tick,
        )

    def cancel_ring_power_selection_for_game(self) -> None:
        self.cancel_ring_power_selection_timer()
        self.cancel_ring_power_countdown()
        self.hide_ring_power_meter_overlay()
        self.ring_burst_selection_deadline = None
        self.ring_burst_selection_expired = False
        self.ring_power_last_countdown_seconds = None
        self.ring_power_selection_started_at = 0.0

    def ring_power_countdown_tick(self) -> None:
        self.ring_power_countdown_after_id = None
        if not (
            getattr(self, "ring_burst_active", False)
            and getattr(self, "ring_burst_selection_deadline", None)
            is not None
            and not getattr(self, "ring_burst_selection_expired", False)
        ):
            return
        if getattr(self, "ring_burst_game_seen_since", 0.0) != 0.0:
            self.cancel_ring_power_selection_for_game()
            return
        remaining = self.ring_power_seconds_remaining()
        if remaining <= 0:
            self.handle_ring_power_selection_timeout()
            return

        try:
            self.ring_power_last_countdown_seconds = remaining
            self.update_ring_power_meter()
        except Exception as error:
            self.recover_ring_ui_error(
                "ring power meter countdown",
                error,
            )
            return
        self.ring_power_countdown_after_id = self.root.after(
            RING_POWER_COUNTDOWN_TICK_MS,
            self.ring_power_countdown_tick,
        )

    def handle_ring_power_selection_timeout(self) -> None:
        self.ring_power_selection_after_id = None
        if not (
            getattr(self, "ring_burst_active", False)
            and getattr(self, "guard_active", False)
        ):
            return
        if getattr(self, "ring_burst_game_seen_since", 0.0) != 0.0:
            self.cancel_ring_power_selection_for_game()
            return
        deadline = getattr(self, "ring_burst_selection_deadline", None)
        if deadline is None:
            return
        remaining = deadline - time.monotonic()
        if remaining > 0:
            self.ring_power_selection_after_id = self.root.after(
                max(
                    50,
                    int(remaining * 1000),
                ),
                self.handle_ring_power_selection_timeout,
            )
            return

        self.ring_burst_selection_expired = True
        self.ring_burst_selection_deadline = None
        self.ring_power_selection_started_at = 0.0
        self.ring_power_last_countdown_seconds = None
        self.cancel_ring_power_countdown()
        self.hide_ring_power_meter_overlay()
        if (
            getattr(self, "active_ring_announcement_kind", None) == "burst"
            or getattr(self, "ring_power_announcement_visible", False)
        ):
            if getattr(self, "pending_ring_announcement", None) == "burst":
                self.pending_ring_announcement = None
            self.active_ring_announcement_kind = None
            self.ring_power_announcement_visible = False
            try:
                self.hide_story_announcement()
            except Exception as error:
                self.recover_ring_ui_error(
                    "ring power timeout cleanup",
                    error,
                )
        self.write_status(
            "RING POWER SELECTION EXPIRED | waiting for safe Robotnik return"
        )
        try:
            self.handle_ring_burst_foreground()
        except Exception as error:
            self.recover_ring_burst_error(error)

    def expire_ring_burst_on_safe_menu(self) -> None:
        if not (
            getattr(self, "ring_burst_active", False)
            and getattr(self, "ring_burst_selection_expired", False)
        ):
            return
        burst_origin = getattr(self, "ring_burst_origin", None)
        self.reset_ring_burst_state()
        self.write_status(
            "RING POWER EXPIRED | Robotnik screen restored"
        )
        try:
            self.hide_story_announcement()
        except Exception as error:
            self.recover_ring_ui_error("timeout return cleanup", error)
        self.restore_ring_burst_origin_screen(burst_origin)

    def reset_ring_burst_state(
        self,
        clear_normal_lock: bool = False,
    ) -> None:
        self.cancel_ring_power_selection_timer()
        self.cancel_ring_power_countdown()
        self.cancel_announcement_energy_animation()
        self.hide_ring_power_meter_overlay()
        self.ring_burst_active = False
        self.ring_burst_game_seen = False
        self.ring_burst_game_seen_since = 0.0
        self.ring_burst_origin = None
        self.ring_burst_selection_deadline = None
        self.ring_power_selection_started_at = 0.0
        self.ring_burst_selection_expired = False
        self.ring_power_last_countdown_seconds = None
        self.ring_power_announcement_visible = False
        if clear_normal_lock:
            self.normal_ring_lock_active = False

    def handle_ring_entry(self) -> None:
        previous_total = self.ring_count
        self.ring_count += 1
        if getattr(self, "active_ring_announcement_kind", None) != "milestone":
            self.play_ring_sound()

        if (
            previous_total < RING_MILESTONE <= self.ring_count
            and RING_MILESTONE not in self.ring_milestones_shown
            and RING_MILESTONE not in self.ring_milestones_pending
        ):
            self.ring_milestones_pending.add(RING_MILESTONE)
            self.pending_ring_milestone = True

        self.save_ring_state()
        self.write_status(
            f"RING ENTERED | total={self.ring_count}",
        )

        burst_started = False
        burst_origin = None
        if not self.ring_burst_active:
            burst_origin = self.current_ring_burst_origin()
        if burst_origin is not None:
            burst_started = True
            self.ring_burst_origin = burst_origin
            if burst_origin == "normal_all_missing":
                self.normal_ring_lock_active = True
            self.ring_burst_active = True
            self.ring_burst_game_seen = False
            self.ring_burst_game_seen_since = 0.0
            self.write_status(
                "RING BURST ACTIVE | waiting for a game | "
                f"origin={burst_origin}"
            )

            if self.overlay_visible:
                self.hide_overlay()
            else:
                self.hide_story_announcement()
                self.cancel_normal_warning()

        if self.pending_ring_milestone:
            announcement_kind = "milestone"
        elif burst_started:
            announcement_kind = "burst"
        else:
            announcement_kind = "count"
        self.request_ring_announcement(announcement_kind)

    def reset_ring_count(self) -> None:
        previous_total = self.ring_count
        if getattr(self, "active_ring_announcement_kind", None) == "milestone":
            # A reset invalidates the in-flight milestone transaction. Cancel
            # its timeout without re-queuing or acknowledging the old total.
            self.active_ring_announcement_kind = None
            if self.announcement_after_id is not None:
                try:
                    self.root.after_cancel(self.announcement_after_id)
                except (AttributeError, tk.TclError):
                    pass
                self.announcement_after_id = None
            try:
                self.announcement_window.withdraw()
            except (AttributeError, tk.TclError):
                pass
        self.ring_count = 0
        self.ring_milestones_shown.clear()
        self.ring_milestones_pending.clear()
        self.pending_ring_milestone = False
        self.pending_ring_announcement = None
        self.save_ring_state()
        self.write_status(
            f"RING COUNTER RESET | previous_total={previous_total}"
        )
        self.update_control_panel()

    def confirm_reset_ring_count(self) -> None:
        if not messagebox.askyesno(
            "Reset Ring Count",
            (
                f"Reset the persistent ring total from {self.ring_count} "
                "to 0?\n\nThe 50-ring prize announcement will also be re-armed."
            ),
            parent=self.control_window,
        ):
            return
        self.reset_ring_count()
        messagebox.showinfo(
            "Ring Count Reset",
            "The persistent ring total is now 0.",
            parent=self.control_window,
        )

    def show_plain_announcement(
        self,
        title: str,
        detail: str,
        color: str,
        duration_seconds: Optional[float],
        allow_guard_overlay: bool = False,
        timeout_callback=None,
        energy_present_count: Optional[int] = None,
    ) -> bool:
        if getattr(self, "active_ring_announcement_kind", None) == "milestone":
            return False
        guard_overlay_is_safe = (
            allow_guard_overlay
            and self.overlay_visible
            and self.overlay_kind == "robotnik"
        )
        if guard_overlay_is_safe and self.emulator_owns_foreground():
            return False
        if not guard_overlay_is_safe and not self.can_show_story_announcement():
            return False

        self.hide_story_announcement()
        self.set_announcement_energy_meter(
            energy_present_count or 0,
            visible=energy_present_count is not None,
            emphasis=energy_present_count is not None,
        )
        banner_x, banner_y, banner_width, banner_height = (
            self.configure_announcement_content(
                title,
                detail,
                color,
                title_max_size=max(
                    16,
                    min(
                        32,
                        int(self.overlay_monitor_bounds[3] * 0.065),
                    ),
                ),
                title_min_size=10,
                detail_max_size=max(
                    12,
                    min(
                        24,
                        int(self.overlay_monitor_bounds[3] * 0.045),
                    ),
                ),
                detail_min_size=8,
            )
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

        if not self.announcement_window_matches_bounds(
            banner_x,
            banner_y,
            banner_width,
            banner_height,
        ):
            try:
                self.announcement_window.withdraw()
            except tk.TclError:
                pass
            self.write_status(
                "RING ANNOUNCEMENT DEFERRED | window did not render correctly"
            )
            return False

        self.announcement_after_id = None
        if duration_seconds is not None:
            self.announcement_after_id = self.root.after(
                int(duration_seconds * 1000),
                timeout_callback or self.hide_story_announcement,
            )
        return True

    def announcement_window_matches_bounds(
        self,
        expected_x: int,
        expected_y: int,
        expected_width: int,
        expected_height: int,
    ) -> bool:
        try:
            self.announcement_window.update_idletasks()
            window_handle = self.announcement_window.winfo_id()
            if not ctypes.windll.user32.IsWindowVisible(window_handle):
                return False
            window_rect = self.get_window_rect(window_handle)
        except (AttributeError, tk.TclError):
            return False
        if window_rect is None:
            return False
        actual_left, actual_top, actual_right, actual_bottom = window_rect
        actual_width = actual_right - actual_left
        actual_height = actual_bottom - actual_top
        tolerance = 4
        return (
            abs(actual_left - expected_x) <= tolerance
            and abs(actual_top - expected_y) <= tolerance
            and abs(actual_width - expected_width) <= tolerance
            and abs(actual_height - expected_height) <= tolerance
        )

    def ring_announcement_content(
        self,
        announcement_kind: str,
    ) -> tuple[str, str, str, Optional[float]]:
        if announcement_kind == "milestone":
            title = RING_MILESTONE_TITLE
            # This is a fixed milestone, not a live ring-count banner. Later
            # rings should not turn the prize announcement into "53 rings"
            # while the 50-ring event is being delivered.
            detail = RING_MILESTONE_MESSAGE
            color = "#ffdd55"
            duration = RING_MILESTONE_ANNOUNCEMENT_SECONDS
        elif announcement_kind == "burst":
            title = RING_BURST_TITLE
            detail = (
                f"{RING_BURST_MESSAGE}\n"
                f"TOTAL RINGS: {self.ring_count}"
            )
            color = "#66ff99"
            duration = None
        else:
            title = RING_COUNT_TITLE
            detail = f"TOTAL RINGS: {self.ring_count}"
            color = "#ffdd55"
            duration = RING_ANNOUNCEMENT_SECONDS

        # Normal Mode announcements are intentionally non-blocking, so the
        # current shrine energy is useful context. Story/Robotnik screens have
        # a dedicated graphical meter and do not duplicate this text.
        if (
            getattr(self, "guard_mode", None) == "normal"
            and announcement_kind != "burst"
        ):
            accepted_count = getattr(self, "accepted_count", None)
            if accepted_count is None:
                energy_text = "CHAOS ENERGY: --"
            else:
                energy_percent = round(
                    max(0, min(TOTAL_EMERALDS, accepted_count))
                    * 100
                    / TOTAL_EMERALDS
                )
                energy_text = f"CHAOS ENERGY: {energy_percent}%"
            detail = f"{detail}\n{energy_text}"

        return title, detail, color, duration

    def refresh_active_ring_announcement(self) -> None:
        """Refresh a visible ring banner with the latest persistent total."""
        announcement_kind = getattr(
            self,
            "active_ring_announcement_kind",
            None,
        )
        if announcement_kind is None or not self.announcement_window:
            return
        if announcement_kind == "milestone":
            # A later ring must not reset the milestone's full display timer.
            return

        title, detail, color, duration = self.ring_announcement_content(
            announcement_kind
        )
        banner_x, banner_y, banner_width, banner_height = (
            self.configure_announcement_content(
                title,
                detail,
                color,
                title_max_size=max(
                    16,
                    min(
                        32,
                        int(self.overlay_monitor_bounds[3] * 0.065),
                    ),
                ),
                title_min_size=10,
                detail_max_size=max(
                    12,
                    min(
                        24,
                        int(self.overlay_monitor_bounds[3] * 0.045),
                    ),
                ),
                detail_min_size=8,
            )
        )
        self.announcement_window.geometry(
            f"{banner_width}x{banner_height}{banner_x:+d}{banner_y:+d}"
        )
        self.apply_announcement_window_style()

        if self.announcement_after_id is not None:
            try:
                self.root.after_cancel(self.announcement_after_id)
            except (AttributeError, tk.TclError):
                pass
            self.announcement_after_id = None
        if duration is not None:
            callback = (
                self.complete_ring_milestone_announcement
                if announcement_kind == "milestone"
                else self.hide_story_announcement
            )
            self.announcement_after_id = self.root.after(
                int(duration * 1000),
                callback,
            )
        self.write_status(
            "RING ANNOUNCEMENT UPDATED | "
            f"kind={announcement_kind} | total={self.ring_count}"
        )

    def request_ring_announcement(self, announcement_kind: str) -> None:
        priorities = {"count": 1, "burst": 2, "milestone": 3}
        if getattr(self, "active_ring_announcement_kind", None) == "milestone":
            self.write_status(
                "RING ANNOUNCEMENT HELD | 50-RING PRIZE ACTIVE"
            )
            return

        active_kind = getattr(self, "active_ring_announcement_kind", None)
        if active_kind == "burst" and announcement_kind == "count":
            # Ring Power's segmented meter/countdown is a separate window.
            # Let a later ring briefly use the text banner without changing
            # the burst deadline, restarting its meter animation, or
            # consuming the one-game pass.
            self.pending_ring_announcement = "count"
            self.ring_count_ignore_press_sequence = getattr(
                self,
                "joystick_press_sequence",
                0,
            )
            self.write_status(
                "RING COUNT PRIORITIZED OVER RING POWER TEXT | "
                f"total={self.ring_count}"
            )
            self.hide_story_announcement()
            return

        current = getattr(self, "pending_ring_announcement", None)
        if priorities.get(announcement_kind, 0) >= priorities.get(current, 0):
            self.pending_ring_announcement = announcement_kind
        if active_kind is not None:
            active_priority = priorities.get(active_kind, 0)
            pending_priority = priorities.get(
                getattr(self, "pending_ring_announcement", None),
                0,
            )
            if pending_priority > active_priority:
                # A milestone must interrupt an ordinary count banner as soon
                # as the threshold ring is processed. hide_story_announcement
                # schedules the higher-priority pending announcement safely on
                # the Tk event queue.
                self.hide_story_announcement()
                return
            if pending_priority <= active_priority:
                self.pending_ring_announcement = None
            self.refresh_active_ring_announcement()
            return
        if getattr(self, "ring_power_announcement_visible", False):
            return
        self.maybe_show_pending_ring_announcement()

    def maybe_show_pending_ring_announcement(self) -> None:
        if (
            not self.running
            or getattr(self, "active_ring_announcement_kind", None)
            == "milestone"
        ):
            return

        announcement_kind = getattr(
            self,
            "pending_ring_announcement",
            None,
        )
        if (
            getattr(self, "ring_power_announcement_visible", False)
            and announcement_kind not in {"count", "milestone"}
        ):
            # The separate Ring Power meter remains visible, but a normal
            # ring-count notice is allowed to use the text banner briefly.
            return
        if getattr(self, "active_ring_announcement_kind", None) is not None:
            return
        if self.pending_ring_milestone:
            announcement_kind = "milestone"
        if announcement_kind is None:
            return

        title, detail, color, duration = self.ring_announcement_content(
            announcement_kind
        )
        burst_energy_count = None
        if announcement_kind == "burst":
            try:
                burst_energy_count = max(
                    0,
                    min(TOTAL_EMERALDS, int(self.accepted_count or 0)),
                )
            except (AttributeError, TypeError, ValueError):
                burst_energy_count = 0

        # Ring totals are permitted over Big Box or our own Robotnik screen.
        # Never create this window over an emulator: GroovyMAME and RetroArch
        # can use exclusive modes or change resolution while a game is active.
        if not self.show_plain_announcement(
            title,
            detail,
            color,
            duration,
            allow_guard_overlay=True,
            timeout_callback=(
                self.complete_ring_milestone_announcement
                if announcement_kind == "milestone"
                else None
            ),
        ):
            return

        self.pending_ring_announcement = None
        self.active_ring_announcement_kind = announcement_kind
        if announcement_kind == "milestone":
            self.play_act_clear_sound()
        if announcement_kind == "burst":
            self.ring_power_announcement_visible = True
            self.start_ring_power_selection_window()
            self.show_ring_power_meter_overlay(burst_energy_count or 0)
            self.animate_ring_power_meter_fill(
                burst_energy_count or 0,
                TOTAL_EMERALDS,
            )
            # The ring insertion that caused this announcement also produces
            # a joystick-edge event. Ignore that same scan so the new banner
            # cannot disappear immediately. The sequence check is important:
            # the message can sit in the queue while the GUI is busy restoring
            # Big Box, so a short time-only debounce is not reliable enough.
            self.ring_power_ignore_press_sequence = getattr(
                self,
                "joystick_press_sequence",
                0,
            )
            self.ring_power_ignore_until = time.monotonic() + 0.25
        if announcement_kind == "milestone":
            self.write_status(
                f"RING MILESTONE DISPLAY STARTED | total={self.ring_count}"
            )
        else:
            self.write_status(
                "RING COUNT DISPLAYED | "
                f"kind={announcement_kind} | total={self.ring_count}"
            )

    def complete_ring_milestone_announcement(self) -> None:
        if self.active_ring_announcement_kind != "milestone":
            return
        self.active_ring_announcement_kind = None
        self.pending_ring_milestone = False
        self.pending_ring_announcement = None
        self.ring_milestones_pending.discard(RING_MILESTONE)
        self.ring_milestones_shown.add(RING_MILESTONE)
        self.save_ring_state()
        self.write_status(
            f"RING MILESTONE DISPLAYED | total={self.ring_count}"
        )
        if getattr(self, "ring_burst_active", False):
            # If the 50th ring also bought a one-game pass, the prize message
            # gets its full uninterrupted display first, then the persistent
            # Ring Power instructions take over.
            self.pending_ring_announcement = "burst"
        self.hide_story_announcement()
        self.maybe_show_pending_ring_announcement()
        self.reconcile_deferred_count_change()

    def handle_joystick_press(self, press_sequence=None) -> None:
        """Dismiss temporary notices or Ring Power on joystick input."""
        active_kind = getattr(self, "active_ring_announcement_kind", None)
        if active_kind == "milestone":
            # The 50-ring prize owns the presentation and audio until its
            # guaranteed display interval completes.
            return

        if active_kind == "count":
            try:
                event_sequence = (
                    int(press_sequence)
                    if press_sequence is not None
                    else None
                )
            except (TypeError, ValueError):
                event_sequence = None
            ignored_sequence = getattr(
                self,
                "ring_count_ignore_press_sequence",
                0,
            )
            current_sequence = getattr(
                self,
                "joystick_press_sequence",
                0,
            )
            if (
                event_sequence is not None
                and event_sequence <= ignored_sequence
            ) or (
                event_sequence is None
                and current_sequence <= ignored_sequence
            ):
                return
            try:
                self.hide_story_announcement()
                self.write_status("RING COUNT ANNOUNCEMENT DISMISSED | joystick")
                self.maybe_show_pending_ring_announcement()
            except Exception as error:
                self.recover_ring_ui_error("count dismissal", error)
            return

        if (
            getattr(self, "announcement_after_id", None) is not None
            and getattr(self, "announcement_window", None) is not None
        ):
            try:
                self.hide_story_announcement()
                self.write_status(
                    "EMERALD ANNOUNCEMENT DISMISSED | joystick"
                )
            except Exception as error:
                self.recover_ring_ui_error(
                    "emerald announcement dismissal",
                    error,
                )
            return

        if not getattr(self, "ring_power_announcement_visible", False):
            return

        try:
            event_sequence = (
                int(press_sequence)
                if press_sequence is not None
                else None
            )
        except (TypeError, ValueError):
            event_sequence = None

        ignored_sequence = getattr(
            self,
            "ring_power_ignore_press_sequence",
            0,
        )
        current_sequence = getattr(
            self,
            "joystick_press_sequence",
            0,
        )
        if event_sequence is not None:
            if event_sequence <= ignored_sequence:
                return
        elif current_sequence <= ignored_sequence:
            return
        elif time.monotonic() < self.ring_power_ignore_until:
            return

        try:
            self.hide_story_announcement()
            self.write_status("RING POWER ANNOUNCEMENT DISMISSED | joystick")
            self.maybe_show_pending_ring_announcement()
        except Exception as error:
            # A stale Tk callback must not turn a harmless banner dismissal
            # into an application-wide guard shutdown. Ensure the banner is
            # hidden as far as possible and leave the game path fail-open.
            detail = str(error).replace("\n", " ")[:160]
            self.ring_power_announcement_visible = False
            self.write_status(
                "RING POWER DISMISS RECOVERED | " + detail
            )
            try:
                if self.announcement_window:
                    self.announcement_window.withdraw()
            except Exception:
                pass

    def recover_ring_ui_error(self, context: str, error: Exception) -> None:
        """Keep a small Ring UI failure from stopping the watchdog loop."""
        detail = str(error).replace("\n", " ")[:160]
        self.ring_power_announcement_visible = False
        self.write_status(
            f"RING UI RECOVERED | {context} | {detail}"
        )
        try:
            self.hide_story_announcement()
        except Exception:
            try:
                if self.announcement_window:
                    self.announcement_window.withdraw()
            except Exception:
                pass

    def recover_ring_burst_error(self, error: Exception) -> None:
        """Fail open after a Ring Power transition callback fails."""
        detail = str(error).replace("\n", " ")[:160]
        self.reset_ring_burst_state(clear_normal_lock=True)
        self.write_status(
            "RING BURST RECOVERED | fail-open | " + detail
        )
        try:
            self.hide_story_announcement()
        except Exception:
            try:
                if self.announcement_window:
                    self.announcement_window.withdraw()
            except Exception:
                pass

        # A Ring Power transition controls the release and later restoration
        # of Big Box. If that state machine itself fails, do not guess which
        # screen should be restored. Disable the guard and release the arcade.
        if self.guard_active or getattr(
            self,
            "suspended_process_handle",
            None,
        ):
            self.fault_disable_guard(
                "Ring Power transition failed: " + detail
            )

    def maybe_show_pending_ring_milestone(self) -> None:
        """Compatibility wrapper retained for older tests and diagnostics."""
        self.maybe_show_pending_ring_announcement()

    def restore_ring_burst_origin_screen(self, burst_origin) -> None:
        if not self.guard_active:
            return

        if burst_origin == "story_robotnik" and self.guard_mode == "story":
            if self.accepted_count == TOTAL_EMERALDS:
                self.show_missing_overlay(0)
                if self.guard_active and self.overlay_kind == "robotnik":
                    self.begin_final_emerald_transition()
                return
            self.pending_overlay_missing = (
                TOTAL_EMERALDS - (self.accepted_count or 0)
            )
            self.maybe_show_pending_overlay()
            return

        if burst_origin == "normal_all_missing" and self.guard_mode == "normal":
            accepted_count = self.accepted_count or 0
            if accepted_count > 0:
                self.normal_ring_lock_active = False
                self.pending_overlay_missing = None
                self.show_normal_restored_announcement(accepted_count)
                return
            self.normal_ring_lock_active = True
            self.pending_overlay_missing = (
                TOTAL_EMERALDS - (self.accepted_count or 0)
            )
            self.maybe_show_pending_overlay()

    def consume_ring_burst_on_return(self) -> None:
        if not self.ring_burst_active:
            return

        burst_origin = getattr(self, "ring_burst_origin", None)
        self.reset_ring_burst_state()
        self.write_status("RING BURST CONSUMED | Big Box returned")
        # The persistent Ring Power banner belongs to the one-game access
        # period. Never leave it above the restored Robotnik screen.
        try:
            self.hide_story_announcement()
        except Exception as error:
            self.recover_ring_ui_error("return cleanup", error)

        self.restore_ring_burst_origin_screen(burst_origin)

    def handle_ring_burst_foreground(self) -> None:
        if not self.ring_burst_active or not self.guard_active:
            return

        self.update_overlay_gate()
        now = time.monotonic()
        if (
            getattr(self, "ring_burst_selection_deadline", None) is not None
            and getattr(self, "ring_burst_game_seen_since", 0.0) == 0.0
            and now >= self.ring_burst_selection_deadline
        ):
            self.handle_ring_power_selection_timeout()
            return
        if self.foreground_process_name in EMULATOR_PROCESS_NAMES:
            # No small topmost window is ever allowed to survive into a game,
            # even if the joystick edge that normally dismisses Ring Power was
            # missed by Windows or by a fast coin-switch pulse.
            if (
                getattr(self, "ring_power_announcement_visible", False)
                or getattr(self, "announcement_after_id", None) is not None
            ):
                try:
                    self.hide_story_announcement()
                    self.write_status(
                        "RING ANNOUNCEMENT HIDDEN | emulator foreground"
                    )
                except Exception as error:
                    self.recover_ring_ui_error(
                        "emulator transition",
                        error,
                    )
            if getattr(self, "ring_burst_selection_expired", False):
                return
            if self.ring_burst_game_seen_since == 0.0:
                self.cancel_ring_power_selection_for_game()
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
        energy_restored_without_game = (
            self.accepted_count == TOTAL_EMERALDS
            or (
                getattr(self, "ring_burst_origin", None)
                == "normal_all_missing"
                and (self.accepted_count or 0) > 0
            )
        )
        if big_box_ready and energy_restored_without_game:
            self.consume_ring_burst_on_return()
            return

        if getattr(self, "ring_burst_selection_expired", False):
            if big_box_ready:
                self.expire_ring_burst_on_safe_menu()
            return

        if (
            big_box_ready
            and not self.ring_burst_game_seen
            and self.ring_burst_game_seen_since != 0.0
        ):
            # The emulator did become foreground, so the one-game access has
            # been used even if it closed before the normal three-second
            # stability threshold. Only a launch that never reaches an
            # emulator remains available for another attempt.
            self.write_status(
                "RING BURST GAME RETURNED BEFORE COMMIT | consuming burst"
            )
            self.consume_ring_burst_on_return()
            return

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

        # Fail open only when an exception can strand the arcade behind our
        # window or while Big Box is suspended. A panel or small-banner error
        # must not stop otherwise healthy sensor monitoring.
        if self.overlay_visible or self.suspended_process_handle:
            self.fault_disable_guard(
                "Application error: " + detail[-140:]
            )
        else:
            self.service_warning = (
                "Noncritical application error: " + detail[-120:]
            )
            self.write_status(
                "NONCRITICAL APP ERROR | monitoring continues"
            )

    def create_control_panel(self) -> None:
        self.control_window = tk.Toplevel(self.root)
        self.control_window.title("Chaos Heist Control")
        panel_width = max(520, min(680, self.screen_width - 20))
        panel_height = max(380, min(445, self.screen_height - 40))
        panel_x = min(40, max(0, (self.screen_width - panel_width) // 2))
        panel_y = min(20, max(0, (self.screen_height - panel_height) // 2))
        self.control_window.geometry(
            f"{panel_width}x{panel_height}{panel_x:+d}{panel_y:+d}"
        )
        self.control_window.minsize(
            min(620, panel_width),
            min(420, panel_height),
        )
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
            text="CHAOS HEIST",
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

        self.control_reset_rings_button = tk.Button(
            button_frame,
            text="RESET RING COUNT",
            width=52,
            command=self.confirm_reset_ring_count,
        )
        self.control_reset_rings_button.grid(
            row=2,
            column=0,
            columnspan=2,
            padx=6,
            pady=(8, 0),
        )

        tk.Label(
            self.control_window,
            text=(
                "Keyboard: Story Mode Ctrl+Alt+F10  |  "
                "Skip Cinematic Ctrl+Alt+F9\n"
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
        if self.pending_guard_activation:
            return (
                f"{mode_name} MODE — WAITING FOR SAFE CLEANUP",
                "#ffcc66",
            )
        if not self.guard_active:
            if self.last_fault:
                return "DISABLED — " + self.last_fault, "#ff9966"
            return f"{mode_name} MODE SELECTED — GUARD OFF", "#9e9e9e"

        if self.ring_burst_active:
            if getattr(self, "ring_burst_selection_expired", False):
                return "RING POWER EXPIRED — RETURNING TO LOCK", "#ffcc66"
            if self.ring_burst_game_seen_since:
                return "RING POWER — GAME ACCESS IN USE", "#66ff99"
            return "RING POWER — SELECT A GAME", "#66ff99"

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
        ring_data_text = (
            "WARNING - check chaos-heist-events.log"
            if self.ring_state_warning
            else "primary + backup OK"
        )
        service_text = self.service_warning or "none"
        details_text = (
            f"Reader: {reader_text}    "
            f"Foreground: {self.foreground_process_name}\n"
            f"Gate: {self.overlay_gate_state}    "
            f"Input: {input_text}\n"
            f"Rings entered: {self.ring_count}    "
            f"Ring data: {ring_data_text}\n"
            f"Service warning: {service_text}\n"
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
            state=(
                tk.NORMAL
                if self.guard_active or self.pending_guard_activation
                else tk.DISABLED
            )
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

    def audio_presentation_is_active(self) -> bool:
        """Return whether ChaosHeist currently owns an audio presentation."""
        if (
            getattr(self, "overlay_visible", False)
            or getattr(self, "cinematic_after_id", None) is not None
            or getattr(self, "active_ring_announcement_kind", None)
            is not None
            or getattr(self, "ring_power_meter_visible", False)
        ):
            return True

        # Missing/completion music is intentionally persistent while its
        # screen is active. A short event sound also counts as a presentation
        # so a media-player window cannot appear over a ring or emerald cue.
        if getattr(self, "music_mode", None) in {"missing", "completion"}:
            return True
        try:
            return bool(self.event_channel_busy())
        except Exception:
            return False

    def suppress_external_audio_player(self) -> bool:
        """Minimize Groove if it unexpectedly steals the guard's foreground.

        pygame's mixer is the only supported audio path in ChaosHeist. This
        containment is for Windows machines where an unrelated file
        association, media key, or shell integration launches Groove while a
        guard cue is playing. It does not close processes and does not touch
        unrelated foreground applications.
        """
        if not (
            getattr(self, "running", False)
            and getattr(self, "guard_active", False)
            and self.audio_presentation_is_active()
        ):
            return False

        try:
            user32 = ctypes.windll.user32
            foreground_window = user32.GetForegroundWindow()
        except (AttributeError, OSError):
            return False
        if not foreground_window:
            return False

        process_name = self.get_window_process_name(foreground_window)
        if not is_external_audio_player_process(process_name):
            self.last_external_audio_player_name = ""
            return False

        try:
            if user32.IsIconic(foreground_window):
                return False
            minimized = bool(
                user32.ShowWindow(foreground_window, SW_MINIMIZE)
            )
        except (AttributeError, OSError):
            return False

        if process_name != getattr(
            self,
            "last_external_audio_player_name",
            "",
        ):
            self.write_status(
                "UNEXPECTED AUDIO PLAYER HIDDEN | "
                f"process={process_name}"
            )
            self.last_external_audio_player_name = process_name
        return minimized

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
            f"pending_missing={self.pending_overlay_missing} | "
            f"ring_burst={self.ring_burst_active} | "
            f"ring_origin={self.ring_burst_origin or 'none'} | "
            f"normal_ring_lock={self.normal_ring_lock_active} | "
            f"pending_prize={self.pending_ring_milestone}",
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
            self.show_missing_overlay(
                missing_count,
                message=(
                    STORY_ROBOTNIK_MESSAGE
                    if self.guard_mode == "story" and missing_count > 0
                    else None
                ),
            )
            return

        self.maybe_show_pending_overlay()

    def maybe_show_pending_overlay(self) -> None:
        if (
            not self.running
            or getattr(self, "active_ring_announcement_kind", None)
            == "milestone"
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
        elif (
            self.guard_mode == "normal"
            and getattr(self, "normal_ring_lock_active", False)
            and self.accepted_count == 0
        ):
            self.show_normal_all_missing_overlay()
        else:
            self.show_missing_overlay(
                missing_count,
                message=(
                    STORY_ROBOTNIK_MESSAGE
                    if self.guard_mode == "story" and missing_count > 0
                    else None
                ),
            )

    def foreground_watchdog(self) -> None:
        try:
            if self.running and self.guard_active:
                try:
                    self.suppress_external_audio_player()
                except Exception as error:
                    # Audio-player containment is deliberately noncritical:
                    # a failure here must never disable a working guard.
                    self.write_status(
                        "AUDIO PLAYER CONTAINMENT RECOVERED | "
                        + str(error).replace("\n", " ")[:120]
                    )
            if self.running and self.guard_active:
                if (
                    self.ring_power_announcement_visible
                    and getattr(self, "joystick_press_sequence", 0)
                    > getattr(
                        self,
                        "ring_power_ignore_press_sequence",
                        0,
                    )
                ):
                    try:
                        self.handle_joystick_press()
                    except Exception as error:
                        self.recover_ring_ui_error(
                            "joystick watchdog",
                            error,
                        )
                try:
                    self.handle_ring_burst_foreground()
                except Exception as error:
                    self.recover_ring_burst_error(error)
                # Keep the Big Box readiness timer warm even before a sensor
                # event. This retains the settle delay after an emulator
                # closes while allowing safe announcements immediately.
                if (
                    not self.overlay_visible
                    and self.pending_overlay_missing is None
                ):
                    self.update_overlay_gate()
                self.maybe_show_pending_overlay()

            if self.running:
                announcement_is_visible = (
                    getattr(self, "active_ring_announcement_kind", None)
                    is not None
                    or getattr(self, "announcement_after_id", None)
                    is not None
                    or getattr(
                        self,
                        "ring_power_announcement_visible",
                        False,
                    )
                    or getattr(
                        self,
                        "ring_power_meter_visible",
                        False,
                    )
                )
                if (
                    announcement_is_visible
                    and self.emulator_owns_foreground()
                ):
                    self.defer_announcement_for_emulator()
                try:
                    self.maybe_show_pending_ring_announcement()
                except Exception as error:
                    self.recover_ring_ui_error(
                        "announcement watchdog",
                        error,
                    )
        except Exception as error:
            # The watchdog must always reschedule itself. A display or
            # foreground query failure should not strand Ring Power state or
            # turn the whole program into a generic Tk application error.
            detail = str(error).replace("\n", " ")[:160]
            self.write_status(
                "FOREGROUND WATCHDOG RECOVERED | " + detail
            )
            if self.ring_burst_active:
                self.recover_ring_burst_error(error)
            elif self.overlay_visible or self.suspended_process_handle:
                self.fault_disable_guard("Application error")
            else:
                self.service_warning = (
                    "Foreground monitor recovered: "
                    + detail[-120:]
                )
                self.reset_big_box_readiness()

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

            # Ring Power resumes Big Box so it can launch an emulator. Once an
            # emulator owns the foreground, a delayed focus retry must never
            # pull focus back to Big Box or interfere with the display-mode
            # transition. Keep this check here instead of process-name lookup;
            # putting it there recursively called get_window_process_name().
            if self.ring_burst_active:
                foreground_window = user32.GetForegroundWindow()
                if (
                    foreground_window
                    and foreground_window != window_handle
                    and self.get_window_process_name(foreground_window)
                    in EMULATOR_PROCESS_NAMES
                ):
                    self.return_window_handle = 0
                    self.write_status(
                        "FOCUS RESTORE ENDED | emulator owns foreground"
                    )
                    return

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
                # to be active on another monitor. Ring Power retries are safe
                # too because the emulator-foreground check above ends them
                # before they can pull focus away from a launched game.
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

    def hide_story_question_message(self) -> None:
        frame = getattr(self, "story_question_message_frame", None)
        if frame is None:
            return
        try:
            frame.place_forget()
        except tk.TclError:
            pass

    def show_text_takeover(
        self,
        title: str,
        message: str,
        overlay_kind: str,
    ) -> bool:
        if not self.running or not self.guard_active:
            return False
        if getattr(self, "active_ring_announcement_kind", None) == "milestone":
            self.milestone_deferred_count_change = True
            return False
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
        story_question = overlay_kind == "story_question"
        title_max_size = self.title_font_size
        message_max_size = self.count_font_size
        title_min_size = 12
        message_min_size = 12
        story_width_fraction = 0.82
        if story_takeover:
            # The story cards are meant to be read from across the arcade.
            # Their limits are based on the actual target monitor. The wider
            # card area and explicit line breaks let the cards use the largest
            # readable fonts that still respect CRT overscan.
            title_max_size = max(
                title_max_size,
                int(monitor_height * 0.20),
            )
            message_max_size = max(
                message_max_size,
                int(monitor_height * 0.20),
            )
            title_min_size = 14
            message_min_size = 14
            story_width_fraction = 0.88

        title_size = self.fit_font_size(
            tuple(title.splitlines()) or (title,),
            max_size=title_max_size,
            min_size=title_min_size,
            available_width=monitor_width,
            width_fraction=story_width_fraction,
        )
        message_size = self.fit_font_size(
            tuple(message.splitlines()) or (message,),
            max_size=message_max_size,
            min_size=message_min_size,
            available_width=monitor_width,
            width_fraction=story_width_fraction,
        )
        self.hide_story_question_message()
        self.title_label.configure(
            text=title,
            foreground="white",
            font=("Arial", title_size, "bold"),
            wraplength=max(1, int(monitor_width * story_width_fraction)),
            justify="center",
        )
        if story_question:
            self.count_label.configure(text="")
            self.count_label.place_forget()
            story_font = ("Arial", message_size, "bold")
            for label in (
                self.story_question_prefix_label,
                self.story_question_sonic_label,
                self.story_question_suffix_label,
            ):
                label.configure(font=story_font)
            self.story_question_message_frame.update_idletasks()
        else:
            self.count_label.configure(
                text=message,
                foreground="white",
                font=("Arial", message_size, "bold"),
                wraplength=max(1, int(monitor_width * story_width_fraction)),
                justify="center",
            )
        self.title_label.update_idletasks()
        if not story_question:
            self.count_label.update_idletasks()
        title_height = self.title_label.winfo_reqheight()
        message_height = (
            self.story_question_message_frame.winfo_reqheight()
            if story_question
            else self.count_label.winfo_reqheight()
        )
        vertical_gap = max(12, int(monitor_height * 0.035))
        total_height = title_height + message_height + vertical_gap
        top_y = max(10, (monitor_height - total_height) / 2)
        self.title_label.place(
            x=monitor_width / 2,
            y=top_y + title_height / 2,
            anchor="center",
        )
        if story_question:
            self.story_question_message_frame.place(
                x=monitor_width / 2,
                y=top_y + title_height + vertical_gap + message_height / 2,
                anchor="center",
            )
        else:
            self.count_label.place(
                x=monitor_width / 2,
                y=top_y + title_height + vertical_gap + message_height / 2,
                anchor="center",
            )

        if not self.reveal_overlay_window():
            self.fault_disable_guard("Overlay could not cover the display")
            return False
        return True

    def cancel_power_loss_effect(self) -> None:
        """Stop the temporary story power-failure presentation safely."""
        self.power_loss_generation += 1
        if self.power_loss_after_id is not None:
            try:
                self.root.after_cancel(self.power_loss_after_id)
            except tk.TclError:
                pass
            self.power_loss_after_id = None
        if self.power_loss_audio_after_id is not None:
            try:
                self.root.after_cancel(self.power_loss_audio_after_id)
            except tk.TclError:
                pass
            self.power_loss_audio_after_id = None

        self.power_loss_active = False
        self.power_loss_started_at = 0.0
        try:
            self.background_label.place_configure(x=0, y=0)
        except tk.TclError:
            pass

        if self.announcement_flash_window:
            try:
                self.announcement_flash_window.configure(
                    background="#fff6b0"
                )
                self.announcement_flash_window.attributes(
                    "-alpha",
                    0.78,
                )
            except tk.TclError:
                pass
        self.hide_announcement_flash()
        self.hide_power_loss_crt()
        self.restore_power_loss_cursor()
        if self.power_loss_crt_window:
            try:
                self.power_loss_crt_window.configure(background="#050505")
            except tk.TclError:
                pass
        if self.power_loss_crt_canvas:
            try:
                self.power_loss_crt_canvas.configure(background="#050505")
            except tk.TclError:
                pass

    def show_power_loss_filter(self, color: str, alpha: float) -> None:
        """Place a non-activating color filter above the guarded display."""
        if not self.announcement_flash_window:
            return

        x, y, width, height = self.overlay_monitor_bounds
        try:
            self.announcement_flash_window.geometry(
                f"{width}x{height}{x:+d}{y:+d}"
            )
            self.announcement_flash_window.configure(background=color)
            self.announcement_flash_window.attributes(
                "-alpha",
                max(0.0, min(1.0, float(alpha))),
            )
            self.apply_announcement_window_style(
                self.announcement_flash_window
            )
            self.announcement_flash_window.deiconify()
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
        except (AttributeError, tk.TclError):
            pass

    def show_power_loss_takeover(self) -> bool:
        """Pause Big Box while leaving its last menu frame visible underneath."""
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
        # Keep the final-emerald voice line alive while the visual power loss
        # begins. The normal shutdown card will stop it before narration.
        self.schedule_audio_watchdog()
        self.animation_generation += 1
        self.active_frames = []
        self.active_delays = []
        self.background_label.configure(image="", background="black")
        self.overlay_visible = True
        self.overlay_kind = "story_power_loss"
        self.reset_counter_style()
        self.hide_energy_meter()
        self.hide_power_loss_cursor()
        try:
            # The menu remains visible because Big Box is paused underneath;
            # only the temporary filter window should be visible above it.
            self.root.withdraw()
        except (AttributeError, tk.TclError):
            pass
        return True

    def start_story_power_loss_effect(self) -> bool:
        """Show the final-emerald power failure before story narration."""
        if not (
            self.guard_active
            and self.guard_mode == "story"
        ):
            return False

        self.story_sequence_after_id = None
        self.cancel_power_loss_effect()
        if not self.show_power_loss_takeover():
            return False

        # Keep the power-loss sequence image-free. The frozen Big Box menu is
        # visible underneath the flashes until the final full blackout.
        self.power_loss_active = True
        self.power_loss_started_at = time.monotonic()
        self.power_loss_generation += 1
        generation = self.power_loss_generation
        self.start_power_loss_audio_sequence(generation)
        self.power_loss_after_id = self.root.after(
            0,
            lambda: self.update_story_power_loss(generation),
        )
        return True

    def start_power_loss_audio_sequence(self, generation: int) -> None:
        """Play the staged electrical failure sounds without blocking Tk."""
        self.power_loss_audio_after_id = None

        def play_step(step_index: int) -> None:
            if (
                generation != self.power_loss_generation
                or not self.power_loss_active
                or not self.running
            ):
                return

            audio_key, duration_ms = POWER_LOSS_AUDIO_STEPS[step_index]
            self.play_event_sound(
                self.power_loss_sounds.get(audio_key),
                f"power_loss_{audio_key}",
                force=True,
                duck_music=False,
            )
            if step_index + 1 < len(POWER_LOSS_AUDIO_STEPS):
                self.power_loss_audio_after_id = self.root.after(
                    duration_ms,
                    lambda: play_step(step_index + 1),
                )

        play_step(0)

    def load_story_shutdown_sound(self) -> None:
        if getattr(self, "story_shutdown_sound", None) is not None:
            return
        if not getattr(self, "audio_ready", False) or not PYGAME_AVAILABLE:
            return

        audio_path = getattr(self, "story_shutdown_audio_path", None)
        if not audio_path or not audio_path.exists():
            return
        try:
            self.story_shutdown_sound = pygame.mixer.Sound(str(audio_path))
            self.story_shutdown_sound.set_volume(SOUND_EFFECT_VOLUME)
        except pygame.error:
            self.story_shutdown_sound = None

    def show_story_shutdown_narration(self) -> bool:
        if not self.show_text_takeover(
            STORY_SHUTDOWN_TITLE,
            STORY_SHUTDOWN_MESSAGE,
            "story_shutdown",
        ):
            return False
        self.load_story_shutdown_sound()
        played = self.play_event_sound(
            getattr(self, "story_shutdown_sound", None),
            "story_shutdown",
            force=True,
            duck_music=False,
        )
        if not played:
            self.write_status(
                "STORY SHUTDOWN SOUND NOT PLAYED | "
                f"asset={STORY_SHUTDOWN_AUDIO_NAME}",
                event=False,
            )
        self.story_sequence_after_id = self.root.after(
            int(STORY_SHUTDOWN_SECONDS * 1000),
            self.show_story_question,
        )
        return True

    def continue_story_power_loss_after_announcement(self) -> None:
        self.story_sequence_after_id = None
        if self.start_story_power_loss_effect():
            return
        if self.guard_active and self.guard_mode == "story":
            self.show_story_shutdown_narration()

    def update_story_power_loss(self, generation: int) -> None:
        if (
            generation != self.power_loss_generation
            or not self.power_loss_active
            or not self.running
        ):
            return

        self.power_loss_after_id = None
        elapsed_ms = max(
            0,
            int(
                (time.monotonic() - self.power_loss_started_at)
                * 1000
            ),
        )
        tick = elapsed_ms // STORY_POWER_LOSS_TICK_MS

        if elapsed_ms >= STORY_POWER_LOSS_TOTAL_MS:
            self.show_power_loss_filter("#000000", 1.0)
            self.hide_power_loss_crt()
            self.power_loss_after_id = self.root.after(
                STORY_POWER_LOSS_BLACKOUT_MS,
                lambda: self.finish_story_power_loss(generation),
            )
            return

        if elapsed_ms < POWER_LOSS_CRT_START_MS:
            # The menu remains visible underneath these sharp, irregular
            # pulses. The first audio clip gets bright fluorescent-like
            # flashes; the later clips make the blackouts longer and the red
            # warning color more unstable as the energy drains away.
            if elapsed_ms < POWER_LOSS_AUDIO_STEPS[0][1]:
                pattern = tick % 13
                if pattern in {0, 1}:
                    color, alpha = "#fff6b0", 0.80
                elif pattern in {2, 5, 9}:
                    color, alpha = "#ff3344", 0.62
                elif pattern in {3, 4}:
                    color, alpha = "#000000", 0.36
                else:
                    color, alpha = "#000000", 0.07
                glitch_alpha = 0.28 if pattern in {1, 4, 8, 11} else 0.0
            elif elapsed_ms < sum(
                duration_ms
                for _name, duration_ms in POWER_LOSS_AUDIO_STEPS[:2]
            ):
                phase = (
                    elapsed_ms - POWER_LOSS_AUDIO_STEPS[0][1]
                ) / POWER_LOSS_AUDIO_STEPS[1][1]
                pattern = tick % 17
                darkness = min(0.86, 0.20 + phase * 0.48)
                if pattern in {0, 6}:
                    color, alpha = "#ff1f3d", 0.48
                elif pattern in {1, 2, 3, 9, 10}:
                    color, alpha = "#000000", min(0.96, darkness + 0.25)
                else:
                    color, alpha = "#000000", darkness
                glitch_alpha = 0.34 if pattern in {2, 5, 8, 13} else 0.0
            else:
                final_phase_start = sum(
                    duration_ms
                    for _name, duration_ms in POWER_LOSS_AUDIO_STEPS[:2]
                )
                phase = (
                    elapsed_ms - final_phase_start
                ) / POWER_LOSS_AUDIO_STEPS[2][1]
                pattern = tick % 19
                darkness = min(0.98, 0.62 + phase * 0.30)
                if pattern in {0, 7}:
                    color, alpha = "#ff3344", 0.34
                elif pattern in {1, 2, 3, 4, 11, 12}:
                    color, alpha = "#000000", min(1.0, darkness + 0.18)
                else:
                    color, alpha = "#000000", darkness
                glitch_alpha = 0.42 if pattern in {3, 6, 10, 15} else 0.0

            self.show_power_loss_filter(color, alpha)
            if glitch_alpha:
                self.show_power_loss_glitches(tick, glitch_alpha)
            else:
                self.hide_power_loss_crt()
        else:
            # The TV-off clip begins with the display already black. Then the
            # remaining picture collapses into a bright horizontal CRT line,
            # contracts toward the center, becomes a dot, and disappears.
            collapse_elapsed = elapsed_ms - POWER_LOSS_CRT_START_MS
            self.show_power_loss_filter("#000000", 1.0)
            self.hide_power_loss_crt()
            if collapse_elapsed < 110:
                self.show_power_loss_filter("#ffffff", 0.94)
            else:
                collapse_progress = min(
                    1.0,
                    (collapse_elapsed - 110)
                    / max(1, POWER_LOSS_CRT_COLLAPSE_MS - 110),
                )
                monitor_width = max(1, self.overlay_monitor_bounds[2])
                if collapse_progress < 0.78:
                    width_progress = collapse_progress / 0.78
                    line_width = int(
                        monitor_width * (0.92 - 0.86 * width_progress)
                    )
                    line_height = 3
                elif collapse_progress < 0.93:
                    dot_progress = (collapse_progress - 0.78) / 0.15
                    line_width = int(
                        monitor_width * 0.06 * (1.0 - dot_progress)
                        + 8 * dot_progress
                    )
                    line_height = int(3 + 5 * dot_progress)
                else:
                    dot_progress = (collapse_progress - 0.93) / 0.07
                    dot_size = int(8 - 6 * min(1.0, dot_progress))
                    line_width = dot_size
                    line_height = dot_size
                line_alpha = 1.0
                if collapse_progress > 0.92:
                    line_alpha = max(
                        0.0,
                        1.0 - (collapse_progress - 0.92) / 0.08,
                    )
                self.show_power_loss_crt_line(
                    line_width,
                    line_height,
                    line_alpha,
                )

        self.power_loss_after_id = self.root.after(
            STORY_POWER_LOSS_TICK_MS,
            lambda: self.update_story_power_loss(generation),
        )

    def finish_story_power_loss(self, generation: int) -> None:
        if generation != self.power_loss_generation:
            return
        self.power_loss_after_id = None
        self.cancel_power_loss_effect()
        if not (
            self.guard_active
            and self.guard_mode == "story"
            and self.overlay_visible
            and self.overlay_kind == "story_power_loss"
        ):
            return

        self.show_story_shutdown_narration()

    def cancel_story_sequence(self) -> None:
        if self.story_sequence_after_id is not None:
            try:
                self.root.after_cancel(self.story_sequence_after_id)
            except tk.TclError:
                pass
            self.story_sequence_after_id = None
        self.cancel_cinematic()
        self.cancel_power_loss_effect()

    def skip_story_cinematic(self) -> None:
        """Skip the current/next story cinematic for operator testing."""
        if not self.running or self.guard_mode != "story":
            return

        if self.overlay_kind == "cinematic":
            self.skip_cinematic_requested = False
            self.write_status("CINEMATIC SKIPPED | ROBOTNIK SCREEN ACTIVE")
            self.finish_story_cinematic()
            return

        if self.story_cycle_started:
            self.skip_cinematic_requested = True
            self.write_status("CINEMATIC SKIP ARMED | STORY HEIST")
        else:
            self.write_status(
                "CINEMATIC SKIP IGNORED | NO STORY HEIST ACTIVE"
            )

    def start_story_shutdown_sequence(self) -> None:
        self.cancel_story_sequence()
        self.pending_overlay_missing = None
        self.skip_cinematic_requested = False
        self.story_cycle_started = True
        self.story_intro_completed = False

        # Give the final theft its own readable zero-energy announcement and
        # play the final-emerald line immediately, before the power-loss
        # transition begins.
        if self.show_story_announcement(
            0,
            "removed",
            duration_seconds=STORY_ANNOUNCEMENT_SECONDS,
        ):
            self.play_last_emerald_removal_sound()
            self.story_sequence_after_id = self.root.after(
                int(STORY_ANNOUNCEMENT_SECONDS * 1000),
                self.continue_story_power_loss_after_announcement,
            )
            return

        if self.start_story_power_loss_effect():
            self.play_last_emerald_removal_sound()
            return

        # A visual-only failure may still use the ordinary story screen, but
        # a safety failure disables the guard and must never be resurrected by
        # this stale transition callback.
        if (
            self.guard_active
            and self.guard_mode == "story"
            and self.show_story_shutdown_narration()
        ):
            self.play_last_emerald_removal_sound()

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
        title, detail = STORY_RETURNED_TEXT.get(
            present_count,
            (
                f"{present_count} CHAOS EMERALDS RESTORED!",
                "THE SHRINE IS RECLAIMING ITS POWER!",
            ),
        )
        return f"{title}  {detail}"

    def show_story_robotnik_screen(self) -> None:
        if (
            not self.guard_active
            or self.guard_mode != "story"
            or getattr(self, "active_ring_announcement_kind", None)
            == "milestone"
        ):
            return
        self.story_intro_completed = True
        present_count = self.accepted_count or 0
        missing_count = TOTAL_EMERALDS - present_count
        self.show_missing_overlay(
            max(1, missing_count),
            message=(
                STORY_ROBOTNIK_MESSAGE
                if missing_count > 0
                else None
            ),
        )
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

    def show_normal_all_missing_overlay(self) -> None:
        """Lock Normal Mode when the final emerald is removed."""
        self.cancel_normal_warning()
        self.normal_ring_lock_active = True
        self.pending_overlay_missing = None
        self.show_missing_overlay(
            TOTAL_EMERALDS,
            message=NORMAL_ALL_MISSING_MESSAGE,
        )
        if self.guard_active and self.overlay_visible:
            self.play_last_emerald_removal_sound()

    def show_normal_restored_announcement(self, present_count: int) -> bool:
        """Show a short, non-blocking Normal Mode restoration message."""
        if (
            not getattr(self, "guard_active", False)
            or getattr(self, "guard_mode", None) != "normal"
        ):
            return False
        return self.show_story_announcement(
            present_count,
            "restored_normal",
            duration_seconds=STORY_ANNOUNCEMENT_SECONDS,
        )

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

    def show_missing_overlay(
        self,
        missing_count: int,
        *,
        message: Optional[str] = None,
    ) -> None:
        if not self.running or not self.guard_active:
            return
        if getattr(self, "active_ring_announcement_kind", None) == "milestone":
            self.milestone_deferred_count_change = True
            return
        self.hide_story_announcement()
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
        self.hide_story_question_message()
        _, _, monitor_width, _ = self.overlay_monitor_bounds
        display_message = message or self.missing_text(missing_count)
        count_size = self.fit_font_size(
            (display_message,),
            max_size=self.count_font_size,
            min_size=12,
            available_width=monitor_width,
        )
        self.set_robotnik_title(LOCK_MESSAGE)
        self.reset_counter_style()
        self.count_label.configure(
            text=display_message,
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
        if (
            getattr(self, "active_ring_announcement_kind", None) == "milestone"
            and getattr(self, "guard_active", False)
        ):
            self.milestone_deferred_count_change = True
            return
        cleanup_warnings = []
        self.overlay_visible = False
        self.overlay_kind = None

        cleanup_steps = [
            ("announcement", self.hide_story_announcement),
            ("story question message", self.hide_story_question_message),
            ("audio watchdog", self.cancel_audio_watchdog),
            ("energy meter", self.hide_energy_meter),
            ("control panel", lambda: self.set_control_panel_visible(False)),
            ("counter style", self.reset_counter_style),
        ]
        if stop_music:
            cleanup_steps.extend(
                [
                    ("music", self.stop_music),
                    ("event sound", self.stop_event_sound),
                ]
            )

        for cleanup_name, cleanup_action in cleanup_steps:
            try:
                cleanup_action()
            except Exception as error:
                cleanup_warnings.append(
                    f"{cleanup_name}: "
                    + str(error).replace("\n", " ")[:100]
                )

        try:
            self.root.attributes("-topmost", False)
        except (AttributeError, tk.TclError):
            pass

        try:
            self.set_overlay_z_order(False)
        except Exception as error:
            cleanup_warnings.append(
                "z-order: " + str(error).replace("\n", " ")[:100]
            )
        try:
            self.root.withdraw()
        except (AttributeError, tk.TclError):
            pass

        # Input access is the highest-priority invariant. Always resume Big
        # Box before touching its audio session, regardless of why the overlay
        # is closing. Windows can reject session restoration while the target
        # process is suspended, and an audio problem must never keep controls
        # blocked.
        try:
            self.resume_and_restore_return_window()
        except Exception as error:
            cleanup_warnings.append(
                "input release: " + str(error).replace("\n", " ")[:100]
            )
            self.write_status(
                "FATAL CLEANUP ERROR | resume helper remains armed"
            )
            self.last_fault = "Could not release Big Box controls"
            if self.running:
                try:
                    self.root.after(0, self.exit_program)
                except (AttributeError, tk.TclError):
                    pass

        audio_restored = self.restore_other_audio_with_retries()
        if not audio_restored:
            self.schedule_audio_restore_retry()
            if not self.ring_burst_active:
                if not self.last_fault:
                    self.last_fault = "Could not restore background audio"
                if self.guard_active:
                    self.activation_generation += 1
                self.guard_active = False
                self.reader_connected = False
                self.overlay_gate_state = "DISABLED_ERROR"
                self.write_status(
                    "GUARD DISABLED | could not restore background audio"
                )
            else:
                self.write_status(
                    "RING BURST AUDIO RESTORE DEFERRED | retrying"
                )

        if cleanup_warnings:
            self.write_status(
                "OVERLAY CLEANUP RECOVERED | "
                + " | ".join(cleanup_warnings)[:500]
            )

    def schedule_audio_restore_retry(self) -> None:
        if not self.running or self.audio_restore_retry_after_id is not None:
            return
        try:
            self.audio_restore_retry_after_id = self.root.after(
                250,
                self.retry_pending_audio_restore,
            )
        except (AttributeError, tk.TclError):
            self.audio_restore_retry_after_id = None
            self.write_status(
                "DEFERRED AUDIO RESTORE COULD NOT BE SCHEDULED"
            )

    def retry_pending_audio_restore(self) -> None:
        self.audio_restore_retry_after_id = None
        if not self.running:
            return
        if not self.audio_muted or self.restore_other_audio_with_retries():
            self.audio_restore_retry_attempt = 0
            self.write_status("DEFERRED AUDIO RESTORE COMPLETE")
            self.complete_pending_activation()
            return

        self.audio_restore_retry_attempt += 1
        if self.audio_restore_retry_attempt in {1, 10, 30}:
            self.write_status(
                "DEFERRED AUDIO RESTORE RETRY | "
                f"attempt={self.audio_restore_retry_attempt}"
            )
        retry_delay = min(
            5000,
            250 * (1 + self.audio_restore_retry_attempt // 4),
        )
        try:
            self.audio_restore_retry_after_id = self.root.after(
                retry_delay,
                self.retry_pending_audio_restore,
            )
        except (AttributeError, tk.TclError):
            self.audio_restore_retry_after_id = None
            self.write_status(
                "DEFERRED AUDIO RESTORE STOPPED | callback unavailable"
            )

    def show_completion_message(self) -> None:
        if getattr(self, "active_ring_announcement_kind", None) == "milestone":
            self.milestone_deferred_count_change = True
            return
        if self.completion_in_progress:
            return

        self.set_control_panel_visible(False)
        self.completion_in_progress = True
        self.completion_animation_finished = False
        self.final_completion_sound_started = False
        self.final_completion_sound_playing = False
        self.final_completion_sound_started_at = 0.0
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
        self.hide_story_question_message()
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
        if (
            getattr(self, "active_ring_announcement_kind", None) == "milestone"
            and event_kind != "act_clear"
        ):
            # Keep the prize's act-clear audio uninterrupted by later rings or
            # sensor transitions.
            return False
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

    def play_ring_sound(self) -> bool:
        if not hasattr(self, "audio_ready"):
            return False
        return self.play_event_sound(
            self.ring_sound,
            "ring_entered",
            force=True,
            duck_music=True,
        )

    def play_act_clear_sound(self) -> bool:
        if not hasattr(self, "audio_ready"):
            return False
        return self.play_event_sound(
            self.act_clear_sound,
            "act_clear",
            force=True,
            duck_music=False,
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
        self.final_emerald_wait_started_at = 0.0

    def begin_final_emerald_transition(self) -> None:
        if getattr(self, "active_ring_announcement_kind", None) == "milestone":
            self.milestone_deferred_count_change = True
            return
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
        self.final_emerald_wait_started_at = time.monotonic()
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
            if (
                time.monotonic() - self.final_emerald_wait_started_at
                < EVENT_SOUND_MAX_SECONDS
            ):
                self.final_emerald_after_id = self.root.after(
                    50,
                    self.wait_for_final_emerald_transition,
                )
                return
            self.write_status(
                "FINAL EMERALD SOUND TIMED OUT | continuing victory"
            )
            self.stop_event_sound()

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
        if (
            elapsed >= COMPLETION_MAX_SECONDS
            and not self.completion_animation_finished
        ):
            self.write_status(
                "VICTORY ANIMATION TIMED OUT | continuing safely"
            )
            self.start_supersonic_animation()
        if victory_audio_done and self.completion_animation_finished:
            if not self.final_completion_sound_started:
                self.final_completion_sound_started = True
                self.final_completion_sound_started_at = time.monotonic()
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
                    if (
                        time.monotonic()
                        - self.final_completion_sound_started_at
                        < EVENT_SOUND_MAX_SECONDS
                    ):
                        self.completion_after_id = self.root.after(
                            100,
                            self.wait_for_completion_audio,
                        )
                        return
                    self.write_status(
                        "FINAL VICTORY SOUND TIMED OUT | closing safely"
                    )
                    self.stop_event_sound()
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
            self.final_completion_sound_started_at = 0.0
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
        self.final_completion_sound_started_at = 0.0

    def keep_window_on_top(self) -> None:
        if self.running and self.overlay_visible:
            if self.overlay_kind == "story_power_loss":
                # This effect deliberately leaves the frozen Big Box menu
                # visible beneath separate non-activating filter windows. The
                # normal topmost watchdog must not resurrect the hidden black
                # root window halfway through the shutdown animation.
                try:
                    self.root.withdraw()
                except tk.TclError:
                    pass
            else:
                try:
                    self.root.overrideredirect(True)
                    x, y, width, height = self.overlay_monitor_bounds
                    expected_rect = (x, y, x + width, y + height)
                    if (
                        self.get_window_rect(self.root.winfo_id())
                        != expected_rect
                    ):
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

                if parse_magnet_protocol_message(line) is not None:
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

                    if parse_magnet_protocol_message(line) is not None:
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

        parsed_message = parse_magnet_protocol_message(message)
        if parsed_message is None:
            return

        event_kind, count = parsed_message
        if event_kind == "ready":
            self.reader_connected = True
            self.controller_lost = False
            now = time.monotonic()
            self.activation_started_at = 0.0
            self.last_valid_message = now
            self.last_serial_message_at = now
            return

        self.reader_connected = True
        self.controller_lost = False
        now = time.monotonic()
        self.activation_started_at = 0.0
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

    def defer_count_change_for_milestone(
        self,
        previous_count: int,
        current_count: int,
    ) -> bool:
        """Hold sensor-driven presentation while the 50-ring prize plays."""
        if getattr(self, "active_ring_announcement_kind", None) != "milestone":
            return False
        if not getattr(self, "milestone_deferred_count_change", False):
            self.milestone_deferred_previous_count = previous_count
        self.milestone_deferred_count_change = True
        self.write_status(
            "SENSOR PRESENTATION DEFERRED | 50-RING PRIZE ACTIVE"
        )
        return True

    def reconcile_deferred_count_change(self) -> None:
        """Apply the final sensor state after the milestone releases priority."""
        if getattr(self, "active_ring_announcement_kind", None) == "milestone":
            return
        if not getattr(self, "milestone_deferred_count_change", False):
            return

        previous_count = self.milestone_deferred_previous_count
        self.milestone_deferred_count_change = False
        self.milestone_deferred_previous_count = None
        if not self.guard_active or self.ring_burst_active:
            return

        current_count = self.accepted_count
        if previous_count is None or current_count is None:
            self.maybe_show_pending_overlay()
            return
        if current_count == previous_count:
            self.maybe_show_pending_overlay()
            return

        if self.guard_mode == "story":
            self.handle_story_count_change(previous_count, current_count)
        else:
            self.handle_normal_count_change(previous_count, current_count)

    def handle_story_count_change(
        self,
        previous_count: int,
        current_count: int,
    ) -> None:
        if current_count == previous_count:
            return

        if self.defer_count_change_for_milestone(
            previous_count,
            current_count,
        ):
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

            self.show_missing_overlay(
                TOTAL_EMERALDS - current_count,
                message=(
                    STORY_ROBOTNIK_MESSAGE
                    if current_count < TOTAL_EMERALDS
                    else None
                ),
            )
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
            "story_power_loss",
            "cinematic",
        }:
            return

        # If the final-theft announcement is dismissed by returning an
        # emerald, cancel its queued power-loss transition before showing the
        # recovery announcement.
        if (
            current_count > 0
            and getattr(self, "story_sequence_after_id", None) is not None
            and not self.overlay_visible
        ):
            self.cancel_story_sequence()

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

        if self.defer_count_change_for_milestone(
            previous_count,
            current_count,
        ):
            return

        # A Normal Mode Ring Power pass creates a real all-missing lock that
        # must return after the one permitted game. Sensor updates still get
        # accepted during the pass, but presentation waits until Big Box is
        # safely back. Returning all seven cancels the lock entirely.
        if (
            getattr(self, "ring_burst_active", False)
            and getattr(self, "ring_burst_origin", None)
            == "normal_all_missing"
        ):
            return

        if getattr(self, "normal_ring_lock_active", False):
            if current_count == TOTAL_EMERALDS:
                self.normal_ring_lock_active = False
                self.pending_overlay_missing = None
                if self.overlay_kind == "robotnik":
                    self.hide_overlay()
                self.play_emerald_sound()
                self.show_normal_restored_announcement(current_count)
                return

            # Normal Mode only needs to remain locked while every emerald is
            # gone. Returning even one immediately restores play access.
            if current_count > 0:
                self.normal_ring_lock_active = False
                self.pending_overlay_missing = None
                if self.overlay_kind == "robotnik":
                    self.hide_overlay()
                self.play_emerald_sound()
                self.show_normal_restored_announcement(current_count)
                return

            self.request_missing_overlay(
                TOTAL_EMERALDS - current_count
            )
            if not self.guard_active:
                return
            self.animate_energy_meter(previous_count, current_count)
            if current_count > previous_count:
                self.play_emerald_sound()
                self.animate_counter("returned")
            else:
                self.play_removal_sound()
                self.animate_counter("removed")
            return

        if current_count > previous_count:
            if (
                self.overlay_kind == "normal_warning"
                or getattr(self, "normal_warning_trigger_count", None)
                is not None
            ):
                self.finish_normal_warning()
            if self.show_normal_restored_announcement(current_count):
                self.play_emerald_sound()
            return

        if (
            self.overlay_kind == "normal_warning"
            or getattr(self, "normal_warning_trigger_count", None)
            is not None
        ):
            # A second real removal is a new event, so keep the warning up
            # for a full interval from the newest removal.
            if current_count == 0:
                self.show_normal_all_missing_overlay()
            elif self.show_normal_warning(previous_count, current_count):
                self.play_removal_sound()
            return

        # Normal Mode reacts only to a downward edge observed while Big Box is
        # the usable full-screen foreground. A missing baseline or a steady
        # missing count never opens the warning.
        if current_count < previous_count:
            if current_count == 0:
                self.show_normal_all_missing_overlay()
            elif self.show_normal_warning(previous_count, current_count):
                self.play_removal_sound()

    def handle_disconnect(self, reason: str) -> None:
        if not self.guard_active:
            return

        self.reader_connected = False
        self.controller_lost = True
        self.pending_count = None
        self.last_serial_message_at = 0.0
        self.activation_started_at = 0.0
        self.fault_disable_guard(reason or "ESP32 disconnected")

    def process_messages(self) -> None:
        while True:
            try:
                message_type, value, generation = self.messages.get_nowait()
            except queue.Empty:
                break

            try:
                if message_type == "CONTROL":
                    if value == "EXIT":
                        self.exit_program()
                        return
                    elif value == "STORY_MODE":
                        self.select_story_mode()
                    elif value == "SKIP_CINEMATIC":
                        self.skip_story_cinematic()
                    elif value == "DEACTIVATE":
                        self.deactivate_guard()
                    elif value == "ACTIVATE":
                        self.activate_guard()
                    continue

                if message_type == "RING":
                    self.handle_ring_entry()
                    continue

                if message_type == "JOYSTICK_PRESS":
                    self.handle_joystick_press(value)
                    continue

                if message_type == "RING_STATE_SAVED":
                    self.ring_state_warning = ""
                    continue

                if message_type == "RING_STATE_SAVE_FAILED":
                    self.ring_state_warning = (
                        "Ring-counter save failed: " + value
                    )[:160]
                    self.write_status(
                        "RING COUNTER SAVE FAILED | " + value,
                        event=False,
                    )
                    continue

                if message_type == "SERVICE_FAULT":
                    self.service_warning = value[:160]
                    if value.startswith("ring input"):
                        self.ring_joystick_error = value[:120]
                        self.schedule_ring_input_restart()
                    self.write_status(
                        "NONCRITICAL SERVICE ERROR | " + value[:500]
                    )
                    continue

                if message_type == "CORE_SERVICE_FAULT":
                    self.serial_worker_failed = True
                    self.service_warning = value[:160]
                    self.fault_disable_guard(value)
                    continue

                if generation != self.activation_generation:
                    continue

                if message_type == "SERIAL":
                    self.handle_serial_message(value)

                elif message_type == "DISCONNECTED":
                    self.handle_disconnect(value)

                elif message_type == "FAULT":
                    self.fault_disable_guard(value)
            except Exception as error:
                detail = str(error).replace("\n", " ")[:160]
                self.write_status(
                    f"MESSAGE RECOVERED | type={message_type} | {detail}"
                )
                if message_type in {"RING", "JOYSTICK_PRESS"}:
                    if self.ring_burst_active:
                        self.recover_ring_burst_error(error)
                    else:
                        self.recover_ring_ui_error(
                            message_type.lower(),
                            error,
                        )
                elif self.guard_active or self.suspended_process_handle:
                    self.fault_disable_guard(
                        f"{message_type} processing failed: {detail}"
                    )

        try:
            self.accept_stable_count()
        except Exception as error:
            detail = str(error).replace("\n", " ")[:160]
            self.write_status(
                "SENSOR EVENT RECOVERED | " + detail
            )
            if self.ring_burst_active:
                self.recover_ring_burst_error(error)
            elif self.guard_active:
                self.fault_disable_guard("Application error")

        if self.running:
            self.root.after(50, self.process_messages)

    def connection_watchdog(self) -> None:
        if self.guard_active:
            now = time.monotonic()
            if self.reader_connected:
                elapsed = now - self.last_serial_message_at
                if elapsed > CONNECTION_TIMEOUT_SECONDS:
                    self.handle_disconnect("ESP32 heartbeat timed out")
            elif (
                self.activation_started_at
                and now - self.activation_started_at
                > INITIAL_CONNECTION_TIMEOUT_SECONDS
            ):
                self.handle_disconnect(
                    "ESP32 did not respond after guard activation"
                )

        if self.running:
            self.root.after(250, self.connection_watchdog)

    # --------------------------------------------------
    # Program control
    # --------------------------------------------------

    def guard_readiness_error(self) -> str:
        problems = list(CONFIG_VALIDATION_ERRORS)

        if getattr(self, "serial_worker_failed", False):
            problems.append(
                "ESP32 serial service stopped; restart the guard application"
            )

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
            elif (
                self.cinematic_prepare_state == "preparing"
                and self.cinematic_prepare_started_at
                and time.monotonic() - self.cinematic_prepare_started_at
                >= CINEMATIC_PREPARE_TIMEOUT_SECONDS
            ):
                problems.append("cinematic preparation timed out")

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
        if mode == self.guard_mode and self.guard_active:
            self.write_status(f"MODE ALREADY ACTIVE | {mode.upper()}")
            return
        if (
            self.guard_active
            or self.overlay_visible
            or self.suspended_process_handle
        ):
            self._deactivate_guard(None)
        self.guard_mode = mode
        self.last_fault = ""
        self.write_status(f"MODE SELECTED | {mode.upper()}")
        self.pending_guard_activation = True
        self.activate_guard()

    def cancel_pending_activation(self) -> None:
        self.pending_guard_activation = False
        if self.activation_retry_after_id is None:
            return
        try:
            self.root.after_cancel(self.activation_retry_after_id)
        except (AttributeError, tk.TclError):
            pass
        self.activation_retry_after_id = None

    def schedule_pending_activation(self) -> None:
        if (
            not self.running
            or not self.pending_guard_activation
            or self.activation_retry_after_id is not None
        ):
            return
        try:
            self.activation_retry_after_id = self.root.after(
                100,
                self.complete_pending_activation,
            )
        except (AttributeError, tk.TclError):
            self.activation_retry_after_id = None
            self.last_fault = "Could not schedule safe guard activation"
            self.pending_guard_activation = False
            self.overlay_gate_state = "DISABLED_ERROR"
            self.write_status("GUARD DISABLED | " + self.last_fault)

    def complete_pending_activation(self) -> None:
        self.activation_retry_after_id = None
        if not self.running or not self.pending_guard_activation:
            return
        if self.guard_active:
            self.pending_guard_activation = False
            return
        if self.suspended_process_handle or self.audio_muted:
            self.schedule_pending_activation()
            return
        self.pending_guard_activation = False
        self.activate_guard()

    def activate_guard(self) -> None:
        if self.guard_active or not self.running:
            return

        if self.suspended_process_handle or self.audio_muted:
            self.pending_guard_activation = True
            self.overlay_gate_state = "WAITING_FOR_CLEANUP"
            self.write_status(
                "ACTIVATION WAITING | releasing prior overlay side effects"
            )
            self.schedule_pending_activation()
            return

        self.cancel_pending_activation()

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
        self.activation_started_at = time.monotonic()
        self.accepted_count = None
        self.pending_overlay_missing = None
        self.pending_normal_warning = None
        self.story_armed = False
        self.story_cycle_started = False
        self.story_intro_completed = False
        self.skip_cinematic_requested = False
        self.milestone_deferred_count_change = False
        self.milestone_deferred_previous_count = None
        self.reset_ring_burst_state(clear_normal_lock=True)
        self.controller_lost = True
        self.reader_connected = False
        self.overlay_gate_state = "WAITING_FOR_SENSOR"
        self.reset_big_box_readiness()
        self.write_status(f"ACTIVATING GUARD | mode={self.guard_mode}")

    def deactivate_guard(self, event=None) -> str:
        if not self.running:
            return "break"

        self.cancel_pending_activation()
        self._deactivate_guard(None)
        return "break"

    def fault_disable_guard(self, reason: str) -> None:
        if not self.running:
            return

        clean_reason = reason.replace("\n", " ")[:160]
        self._deactivate_guard(clean_reason)

    def _deactivate_guard(self, fault_reason: Optional[str]) -> None:
        self.cancel_pending_activation()
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
        self.activation_started_at = 0.0
        self.pending_overlay_missing = None
        self.pending_normal_warning = None
        self.story_armed = False
        self.story_cycle_started = False
        self.story_intro_completed = False
        self.skip_cinematic_requested = False
        self.milestone_deferred_count_change = False
        self.milestone_deferred_previous_count = None
        self.reset_ring_burst_state(clear_normal_lock=True)
        self.completion_in_progress = False
        self.reset_big_box_readiness()
        self.overlay_gate_state = (
            "DISABLED_ERROR" if fault_reason else "DORMANT"
        )

        for cleanup_name, cleanup_action in (
            ("announcement", self.hide_story_announcement),
            ("story sequence", self.cancel_story_sequence),
            ("normal warning", self.cancel_normal_warning),
            ("completion", self.cancel_completion),
        ):
            try:
                cleanup_action()
            except Exception as error:
                self.write_status(
                    "DEACTIVATION CLEANUP RECOVERED | "
                    f"{cleanup_name} | "
                    + str(error).replace("\n", " ")[:120]
                )

        try:
            self.hide_overlay()
        except Exception as error:
            # No cleanup exception may strand Big Box. The independently
            # launched resume watchdog remains armed until resume succeeds.
            self.write_status(
                "DEACTIVATION EMERGENCY RELEASE | "
                + str(error).replace("\n", " ")[:160]
            )
            self.overlay_visible = False
            self.overlay_kind = None
            try:
                self.root.withdraw()
            except (AttributeError, tk.TclError):
                pass
            try:
                self.resume_and_restore_return_window()
            except Exception:
                pass
            try:
                if not self.restore_other_audio_with_retries():
                    self.schedule_audio_restore_retry()
            except Exception:
                pass

    def global_service_hotkey_worker(self) -> None:
        try:
            user32 = ctypes.windll.user32
        except AttributeError:
            return

        exit_combo = (0x11, 0x10, 0x7B)  # Ctrl, Shift, F12
        deactivate_combo = (0x11, 0x12, 0x7A)  # Ctrl, Alt, F11
        story_mode_combo = (0x11, 0x12, 0x79)  # Ctrl, Alt, F10
        skip_cinematic_combo = (0x11, 0x12, 0x78)  # Ctrl, Alt, F9
        keyboard_activate_combo = (0x11, 0x12, 0x7B)
        was_exit_pressed = False
        was_deactivate_pressed = False
        was_story_mode_pressed = False
        was_skip_cinematic_pressed = False
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
            ctrl_alt_f9_pressed = all(
                user32.GetAsyncKeyState(key) & 0x8000
                for key in skip_cinematic_combo
            )
            ctrl_alt_f12_pressed = all(
                user32.GetAsyncKeyState(key) & 0x8000
                for key in keyboard_activate_combo
            )
            exit_pressed = keyboard_combo_pressed
            deactivate_pressed = (
                ctrl_alt_f11_pressed
                and (
                    self.guard_active
                    or self.pending_guard_activation
                )
            )
            story_mode_pressed = ctrl_alt_f10_pressed
            activate_pressed = (
                ctrl_alt_f12_pressed
                and not self.guard_active
                and not self.pending_guard_activation
            )

            if exit_pressed and not was_exit_pressed:
                self.messages.put(("CONTROL", "EXIT", -1))

            if deactivate_pressed and not was_deactivate_pressed:
                self.messages.put(("CONTROL", "DEACTIVATE", -1))

            if story_mode_pressed and not was_story_mode_pressed:
                self.messages.put(("CONTROL", "STORY_MODE", -1))

            if ctrl_alt_f9_pressed and not was_skip_cinematic_pressed:
                self.messages.put(("CONTROL", "SKIP_CINEMATIC", -1))

            if activate_pressed and not was_activate_pressed:
                self.messages.put(("CONTROL", "ACTIVATE", -1))

            was_exit_pressed = exit_pressed
            was_deactivate_pressed = deactivate_pressed
            was_story_mode_pressed = story_mode_pressed
            was_skip_cinematic_pressed = ctrl_alt_f9_pressed
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

        self.ring_persistence_stop_event.set()
        persistence_queue = getattr(
            self,
            "ring_persistence_queue",
            None,
        )
        if persistence_queue is not None:
            try:
                persistence_queue.put_nowait(None)
            except queue.Full:
                # Let the queued latest snapshot finish; the worker observes
                # the stop event immediately afterward.
                pass
        persistence_thread = getattr(
            self,
            "ring_persistence_thread",
            None,
        )
        if (
            persistence_thread
            and persistence_thread.is_alive()
            and persistence_thread is not threading.current_thread()
        ):
            persistence_thread.join(timeout=2.0)
        if not persistence_thread or not persistence_thread.is_alive():
            # Guarantee the final in-memory count on an orderly shutdown,
            # even when several rings arrived during one coalesced write.
            self.save_ring_state()
        else:
            self.write_status(
                "CLEANUP WARNING | ring persistence did not stop in time"
            )
        self.guard_active = False
        self.activation_generation += 1
        self.overlay_visible = False
        self.pending_guard_activation = False
        self.reset_ring_burst_state(clear_normal_lock=True)

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

        # Resume input first. Audio APIs are less reliable against a suspended
        # target process, and playable controls take priority over sound.
        if not self.restore_other_audio_with_retries(attempts=10):
            self.write_status(
                "CLEANUP WARNING | background audio restore failed"
            )

        if self.audio_ready:
            try:
                pygame.mixer.quit()
            except pygame.error:
                pass

        release_mutex_handles(
            getattr(self, "instance_mutex_handles", ())
        )
        self.instance_mutex_handles = ()

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


def locate_self_test_asset(asset_name: str) -> Optional[Path]:
    candidates = [application_directory() / asset_name]
    bundled_directory = getattr(sys, "_MEIPASS", None)
    if bundled_directory:
        candidates.append(Path(bundled_directory) / asset_name)
    candidates.extend(
        (
            Path(__file__).resolve().parent / asset_name,
            SOURCE_ASSET_DIRECTORY / asset_name,
        )
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def run_self_test() -> int:
    """Exercise packaged imports, Tk, config, and required media safely."""
    problems = list(CONFIG_VALIDATION_ERRORS)
    if os.name != "nt":
        problems.append("ChaosHeist requires Windows")
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        problems.append("ChaosHeist requires 64-bit Python/Windows")
    for available, description in (
        (PIL_AVAILABLE, "Pillow image support"),
        (AV_AVAILABLE, "PyAV cinematic support"),
        (PYGAME_AVAILABLE, "pygame audio support"),
        (PYCAW_AVAILABLE, "Windows audio-session support"),
    ):
        if not available:
            problems.append(description + " unavailable")

    required_assets = tuple(
        dict.fromkeys(
            (
                BACKGROUND_IMAGE_NAME,
                COMPLETION_IMAGE_NAME,
                SUPERSONIC_IMAGE_NAME,
                COMPLETION_AUDIO_NAME,
                MISSING_AUDIO_NAME,
                EMERALD_AUDIO_NAME,
                RING_AUDIO_NAME,
                ACT_CLEAR_AUDIO_NAME,
                *REMOVAL_AUDIO_NAMES,
                LAST_EMERALD_REMOVAL_AUDIO_NAME,
                STORY_SHUTDOWN_AUDIO_NAME,
                *POWER_LOSS_AUDIO_NAMES.values(),
                FINAL_COMPLETION_AUDIO_NAME,
                EGGMAN_REVEAL_AUDIO_NAME,
                CINEMATIC_VIDEO_NAME,
            )
        )
    )
    missing_assets = [
        name for name in required_assets if locate_self_test_asset(name) is None
    ]
    if missing_assets:
        problems.append("missing media: " + ", ".join(missing_assets))

    try:
        test_root = tk.Tk()
        test_root.withdraw()
        test_root.update_idletasks()
        test_root.destroy()
    except tk.TclError as error:
        problems.append("Tk startup failed: " + str(error))

    if problems:
        print("CHAOSHEIST_SELF_TEST:FAIL")
        for problem in problems:
            print("- " + problem)
        return 1

    print(
        f"CHAOSHEIST_SELF_TEST:PASS version={APP_VERSION} "
        f"assets={len(required_assets)}"
    )
    return 0


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

    if len(sys.argv) == 2 and sys.argv[1] == "--self-test":
        return run_self_test()

    mutex_handles, is_first_instance = acquire_single_instance_mutex()
    if mutex_handles is None:
        show_message_box(
            "The guard could not create its safety lock and did not start.",
            APP_DISPLAY_NAME,
        )
        return 1
    if not is_first_instance:
        show_message_box(
            "Chaos Heist is already running.",
            APP_DISPLAY_NAME,
        )
        return 0

    app = None
    try:
        bootstrap_event("single-instance lock acquired")
        app = ChaosHeistApp(mutex_handles)
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
            release_mutex_handles(mutex_handles)
        show_message_box(
            "The guard stopped because of an error. The guard is disabled, "
            "and Big Box should remain usable.\n\n"
            + str(error),
            APP_DISPLAY_NAME,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
