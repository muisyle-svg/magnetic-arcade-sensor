# Magnetic Arcade Guard

The guard watches seven ESP32 magnetic sensors while Big Box is at its menu. If an emerald is removed, it pauses Big Box, mutes Big Box audio, and displays the Robotnik screen. It does not attempt to cover MAME, GroovyMAME, or RetroArch games.

The ESP32 independently drives a two-color portion of a four-leg RGB LED:

- Emerald removed: two bright red alarm flashes.
- Emerald returned while others are missing: one bright green absorption flash.
- Final emerald returned: three increasingly bright green charge pulses.
- Fewer than seven emeralds at rest: slowly pulsing red.
- All seven emeralds at rest: slowly pulsing bright green.

The LED is controlled entirely by the ESP32 and does not depend on the Windows guard, Big Box, or the USB connection. Each Hall sensor is independently debounced before its state is reported. LED animation uses integer-only timing for compatibility with the ESP32-C3.

## ESP32 wiring

Power every KY-003 from `3V3`, not `5V`, so its digital output remains safe for the ESP32. Join every Hall module to the same ground connection. The common-anode LED's long/common leg connects to `3V3`; it does not connect directly to ground.

| Part | XIAO ESP32-C3 |
|---|---|
| KY-003 #1 `S` / `OUT` | `D1` |
| KY-003 #2 `S` / `OUT` | `D2` |
| KY-003 #3 `S` / `OUT` | `D3` |
| KY-003 #4 `S` / `OUT` | `D4` |
| KY-003 #5 `S` / `OUT` | `D5` |
| KY-003 #6 `S` / `OUT` | `D6` |
| KY-003 #7 `S` / `OUT` | `D7` |
| RGB LED red leg | `D8` through its own 220-330 ohm resistor |
| RGB LED green leg | `D10` through its own 220-330 ohm resistor |
| RGB LED common/long anode | `3V3` |
| RGB LED blue leg | Not connected |

Use a **separate 220-330 ohm, 1/4-watt resistor on each connected color leg**. A 330-ohm resistor is the safer default; use 220 ohms if that channel is too dim. Do not put one shared resistor on the common leg, and never connect an LED color leg directly to a GPIO.

The sketch is configured for your common-anode RGB LED. Connect its common/long leg to `3V3`; the sketch automatically inverts the channel outputs. RGB LED leg order varies, so identify the red and green legs for the specific LED rather than relying only on physical order. If you use a common-cathode LED later, connect its common leg to `GND` and change `RGB_COMMON_ANODE` to `false`.

Leave `D0` and `D9` unconnected. They map to ESP32-C3 boot-strapping pins that are poor choices for magnet sensors. `D8` is also a strapping pin, but normal SPI boot permits either level there; it is used only as a current-limited LED output, never as a Hall input.

Upload `magnet_test\magnet_test.ino` after wiring. Most KY-003 modules detect only one magnet pole; reverse a magnet if its sensor does not activate.

When uploading in Arduino IDE, select `XIAO_ESP32C3` and set **Tools > USB CDC On Boot > Enabled**. This is important because the sketch reports counts over USB serial while `D6` is also used as sensor input. With USB CDC disabled, `D6` is the XIAO's UART transmit pin and the serial heartbeat can make the sensor indicator flicker. Use only Serial Monitor or the guard at one time, and press RESET once after uploading if the USB serial port does not reappear.

## Safety behavior

The guard is intentionally **fail-open**. An ESP32 disconnect, heartbeat timeout, missing media dependency, overlay sizing failure, audio failure, or internal callback failure disables the guard and restores Big Box input/audio. It stays disabled until an operator activates it again or restarts the program. A separate helper process resumes Big Box if the main guard process crashes while Big Box is paused.

Only one guard instance can run at a time.

## Experience behavior

- Return and removal events use one dedicated sound-effects channel, so repeated sounds cannot overlap. Multiple rapid returns are combined into one clean chime.
- The Robotnik music is lowered briefly under ordinary event sounds.
- When the final emerald is returned, the counter immediately changes to `ALL CHAOS EMERALDS RESTORED!` and Robotnik's music fades. The guard waits for the final emerald sound to actually finish, pauses briefly, and then starts the Sonic victory screen.
- Counter text briefly grows and flashes red for a removal or green for a return.
- Emerald changes made while MAME, GroovyMAME, or RetroArch is active are tracked silently. Sounds and presentation resume only after Big Box has safely returned.

The optional removal sound defaults to `emerald-removed.mp3`. Put that file beside `MagnetArcadeGuard.exe` and restart the guard; no rebuild is required. You can use a different filename by changing `removal_sound_file` in `guard-config.json`. If the file is absent, the guard remains usable and provides the visual and LED feedback without a removal sound.

## Operator controls

- `Ctrl+Alt+F12`: activate the guard from anywhere.
- `Ctrl+Alt+F11`: deactivate the guard from anywhere.
- `Ctrl+Shift+F12`: close the program from anywhere.
- The operator panel is an ordinary resizable Windows window with standard minimize, maximize, and close controls. The guard does not automatically hide, restore, or unminimize it. Big Box and the full-screen takeover naturally cover it on the arcade display.

Status files are written to:

- `%LOCALAPPDATA%\MagnetArcadeGuard\guard-status.txt`
- `%LOCALAPPDATA%\MagnetArcadeGuard\guard-events.log`

## Configuration

Keep `guard-config.json` beside `MagnetArcadeGuard.exe`.

- `total_emeralds`: must match the ESP32 firmware sensor count.
- `auto_activate`: `false` starts dormant; `true` starts checking automatically.
- `serial_port`: leave empty for automatic detection, or set a value such as `COM4`.
- `big_box_ready_delay_seconds`: how long a full-monitor Big Box menu must remain stable before the overlay can appear.
- `sensor_stable_ms`: additional Windows-side stabilization after the ESP32's per-sensor debounce. The default is `100`.
- `final_emerald_pause_ms`: pause after the final emerald sound finishes and before Sonic appears. The default is `150`.
- `sound_effect_cooldown_ms`: suppresses repeated copies of the same sound. The default is `350`.
- `counter_flash_ms`: duration of the red/green counter animation. The default is `300`.
- `robotnik_fade_ms`: final-emeral fade time for Robotnik's music. The default is `250`.
- `music_volume` and `sound_effect_volume`: values from `0.0` to `1.0`.
- `removal_sound_file`: optional removal-effect filename beside the EXE.

If all emeralds are present at activation, the guard establishes that as its baseline and shows nothing until one is removed.

## Build

From PowerShell on the development computer:

```powershell
Set-Location "C:\Users\Projector\Documents\RFIDlock"
.\build_guard.cmd
```

To install or refresh the pinned build dependencies first:

```powershell
.\build_guard.cmd -InstallDependencies
```

The finished portable files are `dist\MagnetArcadeGuard.exe` and `dist\guard-config.json`. The arcade PC does not need Python installed.

## Arcade PC checklist

1. Replace the old EXE and copy `guard-config.json` beside it.
2. Connect the ESP32 and verify its COM port appears in Device Manager.
3. Start the guard. Confirm the operator panel says `DORMANT — GUARD OFF`.
4. Start Big Box, then press `Ctrl+Alt+F12` or activate from the panel before entering Big Box.
5. Test removal/return only at a Big Box menu first.
6. Confirm each removal causes red LED alarm flashes and each return causes a green flash without overlapping return sounds.
7. Confirm the final return updates the counter immediately, finishes the emerald sound, and then starts the Sonic victory sequence.
8. Unplug the ESP32 while the Robotnik screen is active. The overlay should close, Big Box should resume, and the panel should report `DISABLED` with the disconnect reason.

The supplied firmware and `guard-config.json` are both configured for seven emeralds.
