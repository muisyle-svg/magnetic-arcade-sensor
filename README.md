# Magnetic Arcade Guard — Story/Normal Edition

This is an experimental version kept completely separate from the known-good guard. It has its own source folder, build folder, EXE name, and distribution folder.

The frozen release remains at:

`C:\Users\Projector\Documents\RFIDlock\versions\stable-pre-story-mode`

Its portable ZIP is:

`C:\Users\Projector\Documents\RFIDlock\versions\MagnetArcadeGuard-stable-pre-story-mode.zip`

## Modes

### Story Mode

Story Mode arms only after the guard has observed all seven emeralds in place. Starting the guard with an emerald already absent does nothing until all seven are restored and a later removal occurs.

- Removing emeralds 1–6 shows a centered, briefly flashing, non-blocking theft banner over a full-screen Big Box menu. Big Box remains usable and the banner does not take keyboard or joystick focus.
- The shutdown story cards use larger monitor-aware text, while still shrinking or wrapping to stay inside the arcade display.
- Each partial theft chooses a different sound from the configured reaction clips; the same clip is never chosen twice in a row.
- Removing the seventh emerald starts the full takeover only after Big Box is the stable full-screen foreground. It pauses Big Box, mutes its audio, announces the shutdown, asks where a hero can be found, and plays the bundled Sonic CD opening.
- The final theft uses the dedicated `no-he-s-got-the-last-emerald.mp3` clip. Before the cinematic, the Eggman reveal screen only says `SO EGGMAN'S BEHIND THIS, HUH?` and plays its matching voice clip.
- After the cinematic, the Robotnik recovery screen shows the number recovered while the separate Chaos Energy meter updates as each emerald returns.
- The final return immediately updates the text, lets the return sound finish, pauses briefly, and plays the Sonic/Super Sonic victory sequence. After the victory music finishes while Super Sonic is displayed, the bundled `i-ll-show-you-what-the-chaos-emeralds-can-really-do.mp3` clip plays.
- Completing the full story automatically changes the running guard to Normal Mode.

Changes made while MAME, GroovyMAME, or RetroArch is active never try to draw over the emulator. A complete seven-emerald theft waits until Big Box has safely returned before starting the story takeover.

### Normal Mode

Normal Mode reacts only to a new downward sensor transition while Big Box is the usable full-screen foreground.

- It displays a non-blocking banner: `A Chaos Emerald Was Stolen!` followed by `Hey! Put that back! We already did the thing!` and plays one of the randomized removal voice clips.
- It returns to the normal Big Box view after 10 seconds even if the emerald is still missing.
- It returns immediately if any emerald is put back first.
- A missing count present when the mode starts does not trigger it, and a steady missing count does not retrigger it.

Select either mode from the regular Windows operator panel. Selecting a mode activates the guard in that mode.

## Ring input and one-game energy burst

This build watches all connected USB joystick encoders independently of guard
activation. Human-numbered joystick button 10 is treated as the coin/ring
input. The guard reads it through the Windows joystick API, so it does not
need focus and does not compete with cinematic playback. A short 90 ms
anti-bounce interval prevents one coin-switch pulse from counting twice while
still allowing rings to be entered quickly.

- Every ring is counted, including while the guard is dormant or deactivated.
- The persistent total is stored in `%LOCALAPPDATA%\MagnetArcadeGuard\ring-counter.json`, with an automatically maintained `ring-counter.backup.json`. If the primary file is corrupt, the guard preserves a timestamped copy, recovers from the backup when possible, and shows a warning in the operator panel instead of silently losing the total.
- Every deposit queues a non-activating `RING COLLECTED!` announcement showing
  the new persistent total. Ring Power and 50-ring announcements also include
  that total. In Normal Mode, these announcements also show the current Chaos
  Energy percentage; the Robotnik screen keeps energy in its graphical meter.
- Ring announcements are shown only while full-screen Big Box is safely in the
  foreground or over the guard's own Robotnik recovery screen. A ring deposited
  during MAME, GroovyMAME, RetroArch, or another game is still counted
  immediately, but its latest total waits until Big Box or the guard screen is
  safely visible. The guard never creates a ring-count window over a running
  emulator.
- When the Robotnik screen is active, one ring hides the takeover and gives a
  one-game Ring Power burst. The Ring Power banner remains visible until the
  next joystick button press; rings inserted during that game are counted but
  never extend the burst.
- In Normal Mode, the same burst is available when all seven emeralds are
  missing.
- The burst remains available while the menu is idle. It becomes consumed after
  an emulator has actually become foreground and Big Box has safely returned;
  even a very brief game launch is treated as using the burst. A launch that
  never reaches an emulator remains available for another attempt.
  The Robotnik
  screen is then restored in Story Mode if emeralds are still missing.
- At the first total of 50 rings, the non-blocking message `50 RINGS!` and
  `Find Alex for your prize!` is shown for at least 10 seconds when Big Box is
  safely visible. If a game is active at that moment, the message remains
  pending and is shown the next time Big Box returns. That pending prize message
  is persisted, so closing or restarting the guard cannot lose it.

## LED energy meter

The ESP32 independently controls the existing red and green LED legs; the blue leg remains disconnected.

- The resting color moves from red through orange, amber, and yellow-green to bright green as the detected count rises from 0 to 7.
- The pulse gradually becomes faster as Chaos Energy rises.
- The Robotnik screen shows a seven-segment graphical `MASTER EMERALD POWER` meter with a short stepped increase/decrease animation whenever the count changes.
- Removing an emerald produces two red alarm flashes.
- Returning an emerald produces a green absorption flash followed by a temporary faster energy pulse.
- Returning the final emerald produces the three-stage green charge effect followed by the fast green pulse.

The LED does not depend on the Windows guard, Big Box, or the USB connection. The Windows guard keeps its serial connection open while dormant so deactivation does not unnecessarily interrupt the controller. The animation remains integer-only to avoid the ESP32-C3 floating-point crash encountered during earlier testing.

## ESP32 wiring

No hardware changes are required for this version.

| Part | XIAO ESP32-C3 |
|---|---|
| KY-003 #1 `S` / `OUT` | `D1` |
| KY-003 #2 `S` / `OUT` | `D2` |
| KY-003 #3 `S` / `OUT` | `D3` |
| KY-003 #4 `S` / `OUT` | `D4` |
| KY-003 #5 `S` / `OUT` | `D5` |
| KY-003 #6 `S` / `OUT` | `D6` |
| KY-003 #7 `S` / `OUT` | `D7` |
| RGB LED red leg | `D8` through its own 220–330 ohm resistor |
| RGB LED green leg | `D10` through its own 220–330 ohm resistor |
| RGB LED common/long anode | `3V3` |
| RGB LED blue leg | Not connected |

Power every KY-003 from `3V3`, share ground, and leave `D0` and `D9` unconnected. The sketch is configured for the existing common-anode LED. Use a separate resistor on each connected color leg; 330 ohms is the safer default and 220 ohms is brighter.

Upload `magnet_test\magnet_test.ino` with Arduino IDE set to `XIAO_ESP32C3` and **Tools > USB CDC On Boot > Enabled**.

## Fail-open behavior

The guard favors access to the arcade over lockout. An ESP32 disconnect, heartbeat timeout, media/decoder failure, overlay failure, audio failure, or internal callback failure disables the guard, restores Big Box input/audio, and leaves the guard off until an operator activates it again. A helper process also resumes Big Box if the main process crashes while Big Box is paused.

Only one guard instance can run at a time.

## Operator controls

- Story Mode and Normal Mode buttons select and activate that mode.
- Deactivate Guard stops monitoring and closes any presentation.
- Reset Ring Count asks for confirmation, resets the persistent total to zero,
  and re-arms the one-time 50-ring prize announcement. It does not change the
  guard mode or interrupt an active Ring Power pass.
- Close Program exits the guard.
- `Ctrl+Alt+F10` selects and activates Story Mode from anywhere.
- `Ctrl+Alt+F12` activates the currently selected mode from anywhere.
- `Ctrl+Alt+F11` deactivates the guard from anywhere.
- `Ctrl+Shift+F12` closes the program from anywhere.

The panel is an ordinary resizable/minimizable Windows window. Big Box and the takeover naturally cover it on the arcade display; the guard does not force the panel open or restore it from the taskbar.

Status files are written to:

- `%LOCALAPPDATA%\MagnetArcadeGuard\guard-status.txt`
- `%LOCALAPPDATA%\MagnetArcadeGuard\guard-events.log`
- `%LOCALAPPDATA%\MagnetArcadeGuard\ring-counter.json`

## Configuration

Keep `guard-config.json` beside `MagnetArcadeGuardRings.exe`.

- `default_mode`: `story` or `normal`.
- `total_emeralds`: must remain `7`; a different value disables the guard because the installed firmware and sensor assembly are fixed at seven inputs.
- `auto_activate`: normally `false` so the operator chooses a mode after launching.
- `serial_port`: empty for automatic detection, or a fixed value such as `COM4`.
- `emulator_process_names`: executable names that count as active games. Add another emulator's Windows `.exe` name here if the arcade configuration expands; names are case-insensitive.
- `normal_warning_seconds`: Normal Mode timeout; default `10.0`.
- `story_announcement_seconds`: duration of each non-blocking theft banner.
- `story_shutdown_seconds`: duration of the arcade-shutdown announcement.
- `story_question_seconds`: duration of the hero prompt before the cinematic.
- `story_eggman_seconds`: duration of the Eggman reveal before the cinematic.
- `cinematic_fade_seconds`: black-to-video fade duration.
- `cinematic_video_file`: exact Sonic CD video filename used when building.
- `big_box_ready_delay_seconds`: how long Big Box must cover its monitor and remain stable before a full takeover.
- `sensor_stable_ms`: Windows-side sensor stabilization after the firmware debounce.
- `final_emerald_pause_ms`: pause after the final return sound and before Sonic appears.
- `music_volume` and `sound_effect_volume`: values from `0.0` to `1.0`.
- `removal_sound_files`: list of partial-theft sounds; the guard randomly chooses without repeating the previous clip.
- `last_emerald_removal_sound_file`: dedicated sound for the seventh and final theft.
- `final_completion_sound_file`: sound played after the victory music and Super Sonic animation.
- `ring_joystick_button`: human-numbered joystick button used for rings; default `10`.
- `ring_debounce_ms`: short anti-bounce interval; default `90` ms.
- `ring_game_commit_seconds`: how long an emulator must remain active before one Ring Power use is committed; default `3.0` seconds. Returning to Big Box restores the Robotnik screen if it granted the burst.
- `ring_announcement_seconds`: how long the 50-ring message remains visible; default `5.0`.
- `cinematic_max_fps`: maximum cinematic display rate; default `15` to keep audio and video synchronized on the arcade PC.

Other GIF, MP3, and MP4 files beside the EXE also override their bundled copies when their configured filenames match.

## Build this separate version

From PowerShell on the development computer:

```powershell
Set-Location "C:\Users\Projector\Documents\RFIDlock\RingEnabledVersion"
.\build_story_guard.cmd
```

To install or refresh the pinned build dependencies first:

```powershell
.\build_story_guard.cmd -InstallDependencies
```

The portable files are created only at:

- `dist-ring-enabled\MagnetArcadeGuardRings.exe`
- `dist-ring-enabled\guard-config.json`
- `dist-ring-enabled\MagnetArcadeGuardRings-test.zip` (EXE, config, firmware, version note, and this guide)

The arcade PC does not need Python installed.

## Revert to the stable version

1. Close `MagnetArcadeGuardRings.exe`.
2. Run `MagnetArcadeGuard.exe` from `versions\stable-pre-story-mode` instead.
3. If the stable firmware is also desired, upload `versions\stable-pre-story-mode\magnet_test.ino`.

The separate names allow both versions to remain on the arcade PC without replacing each other.
