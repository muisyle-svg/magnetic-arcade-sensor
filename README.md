# ChaosHeist — Production Story/Normal Edition

ChaosHeist is the production version of the seven-emerald arcade experience. It includes Story Mode, Normal Mode, persistent ring counting, one-game Ring Power access, the 50-ring prize announcement, and the ESP32-controlled Master Emerald lighting effects.

On the development computer, superseded local builds were preserved on August 25, 2026 at:

`C:\Users\Projector\Documents\RFIDlock\Archive\2026-08-25-pre-ChaosHeist`

## Modes

### Story Mode

Story Mode arms only after the guard has observed all seven emeralds in place. Starting the guard with an emerald already absent does nothing until all seven are restored and a later removal occurs.

- Removing emeralds 1–6 shows a centered, briefly flashing, non-blocking theft banner over a full-screen Big Box menu. Big Box remains usable and the banner does not take keyboard or joystick focus.
- The shutdown story cards use the largest monitor-aware text that fits, while still shrinking or wrapping to stay inside the arcade display. The cards read `ROBOTNIK'S CHAOS HEIST!` / `Robotnik has stolen the Chaos Emeralds and taken them back to his fortress!`, `THE ARCADE HAS LOST ITS CHAOS ENERGY!` / `Only Sonic can save us!` (with `Sonic` in blue), and `SO EGGMAN'S BEHIND THIS, HUH?`.
- Each partial theft chooses a different sound from the configured reaction clips; the same clip is never chosen twice in a row.
- Removing the seventh emerald starts the full takeover only after Big Box is the stable full-screen foreground. It pauses Big Box, mutes its audio, and runs a roughly ten-second staged power failure over the frozen menu: fluorescent flicker, worsening glitches and blackouts, then a classic CRT line-to-dot collapse. The four synchronized clips `flourescent-lights-buzzing.mp3`, `lantern-buzzes-fades.mp3`, `lantern-whines-buzzing-dies.mp3`, and `tv-off.mp3` play in that order before the shutdown narration continues.
- The final theft uses the dedicated `no-he-s-got-the-last-emerald.mp3` clip. Before the cinematic, the Eggman reveal screen only says `SO EGGMAN'S BEHIND THIS, HUH?` and plays its matching voice clip.
- The first shutdown narration card also plays `i-m-afraid-our-little-game-ends-now.mp3`.
- After the cinematic, the Robotnik recovery screen shows the number recovered while the separate Chaos Energy meter updates as each emerald returns.
- The final return immediately updates the text, lets the return sound finish, pauses briefly, and plays the Sonic/Super Sonic victory sequence. After the victory music finishes while Super Sonic is displayed, the bundled `i-ll-show-you-what-the-chaos-emeralds-can-really-do.mp3` clip plays.
- Completing the full story automatically changes the running guard to Normal Mode.

Changes made while MAME, GroovyMAME, or RetroArch is active never try to draw over the emulator. A complete seven-emerald theft waits until Big Box has safely returned before starting the story takeover.

### Normal Mode

Normal Mode reacts only to a new downward sensor transition while Big Box is the usable full-screen foreground.

- It displays a non-blocking banner: `A Chaos Emerald Was Stolen!` followed by `Hey! Put that back!` and plays one of the randomized removal voice clips.
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
still allowing rings to be entered quickly. The same debounce also spans both
USB encoders, so one electrical pulse reported by two identical devices cannot
be counted twice.

- Every ring is counted, including while the guard is dormant or deactivated.
- The persistent total is stored in `%LOCALAPPDATA%\ChaosHeist\ring-counter.json`, with the previous valid generation in `ring-counter.backup.json`. On first production launch, ChaosHeist validates and imports one existing ring total from `%LOCALAPPDATA%\MagnetArcadeGuard`, writes a durable migration marker, and never imports the legacy files again. If the primary file is corrupt, the guard preserves a timestamped copy, recovers from the backup or an interrupted atomic save when possible, and shows a warning in the operator panel instead of silently losing the total.
- Rapid deposits update the on-screen total immediately. Durable ring-state writes are serialized and coalesced on a background worker so a slow disk or antivirus scan cannot stall sensor and joystick handling; an orderly shutdown performs a final synchronous save.
- Every deposit queues a non-activating `RING COLLECTED!` announcement showing
  the new persistent total. Ring Power and 50-ring announcements also include
  that total. In Normal Mode, these announcements also show the current Chaos
  Energy percentage; the Robotnik screen keeps energy in its graphical meter.
- Ring Power shows the current segmented Chaos Energy meter, rapidly fills it
  to full, and gives 35 seconds to launch a game. The meter itself is the
  countdown: each of its seven segments blinks for five seconds and then goes
  dark, while the percentage readout decreases continuously and reaches the
  exact segment percentage at each boundary. Selecting a game cancels the
  countdown immediately; returning to Big Box then restores the Robotnik lock
  as usual. If no game is selected before
  the countdown expires, the Robotnik lock returns without covering an active
  game. The instructional text can be dismissed independently, but the meter
  remains until a game starts or the deadline expires.
- While Ring Power is waiting, a later ring briefly replaces only the
  instructional text with the current ring total. The independent energy
  countdown keeps its original deadline and continues draining; a milestone
  prize still has priority and is never interrupted.
- Ring announcements are shown only while full-screen Big Box is safely in the
  foreground or over the guard's own Robotnik recovery screen. A ring deposited
  during MAME, GroovyMAME, RetroArch, or another game is still counted
  immediately, but its latest total waits until Big Box or the guard screen is
  safely visible. If a game starts while a small notice is already visible,
  the notice and its sound are removed immediately. The guard never leaves a
  ring-count window over a running emulator.
- When the Robotnik screen is active, one ring hides the takeover and gives a
  one-game Ring Power burst. The Ring Power banner remains visible until the
  next joystick button press, or is removed automatically as soon as an
  emulator takes the foreground; rings inserted during that game are counted
  but never extend the burst.
- In Normal Mode, the same burst is available when all seven emeralds are
  missing. After the permitted game returns to Big Box, Story Mode restores
  Robotnik while any emeralds are still absent; Normal Mode restores the lock
  only if all seven remain absent. Returning even one emerald ends Normal
  Mode's zero-energy lock and any unused Ring Power pass.
- The burst remains available while the menu is idle. It becomes consumed after
  an emulator has actually become foreground and Big Box has safely returned;
  even a very brief game launch is treated as using the burst. A launch that
  never reaches an emulator remains available for another attempt.
  The Robotnik screen is then restored if emeralds are still missing.
- At the first total of 50 rings, the non-blocking message `50 Rings!` and
  `Sonic holds the KEY to your prize... if it hasn't already been taken!` is shown for at least 10 seconds when Big Box is
  safely visible. If a game is active at that moment, the message remains
  pending and is shown the next time Big Box returns. Sensor and mode changes
  wait behind its ten-second priority interval. If a game interrupts the
  display, the card and act-clear sound are removed, and the full announcement
  is re-queued instead of being marked delivered. Its pending state is
  persisted, so closing or restarting the guard cannot lose it.

## LED energy meter

The ESP32 independently controls the existing red and green LED legs; the blue leg remains disconnected.

- The resting color moves from red through orange, amber, and yellow-green to bright green as the detected count rises from 0 to 7.
- The pulse gradually becomes faster as Chaos Energy rises.
- The Robotnik screen shows a seven-segment graphical `MASTER EMERALD POWER` meter with a short stepped increase/decrease animation whenever the count changes.
- Removing an emerald produces two red alarm flashes. Removing the final emerald starts a long red power-failure effect synchronized to the Windows shutdown sequence: unstable flicker, fading glow, dying buzz, and a final blackout before the normal red warning pulse resumes.
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

Upload `ChaosHeistController\ChaosHeistController.ino` with Arduino IDE set to `XIAO_ESP32C3` and **Tools > USB CDC On Boot > Enabled**.

## Fail-open behavior

The guard favors access to the arcade over lockout. An ESP32 disconnect,
heartbeat timeout, takeover/media failure, incorrectly rendered full-screen
window, or failure inside a Ring Power handoff disables the guard, restores Big
Box input before attempting audio cleanup, and leaves the guard off until an
operator activates it again. Cinematic startup, cinematic playback, final
emerald audio, and victory callbacks have explicit deadlines so no damaged
media or stuck audio channel can hold the arcade forever. A helper process also
resumes Big Box if the main process crashes while Big Box is paused.

Noncritical failures are isolated. A ring-count banner or operator-panel error
does not disable healthy emerald monitoring, and a failed ring-input worker is
automatically restarted with bounded backoff. If the core serial worker itself
stops, reactivation is blocked with a visible restart-app message instead of
waiting forever for sensor data that cannot arrive. Mode selection waits for
any previous input/audio cleanup to finish before reactivating. If a serial
port remains open but no valid controller message arrives after activation,
the guard disables itself after a 15-second startup grace period.

Only one guard instance can run at a time. ChaosHeist also holds the former
MagnetArcadeGuard lock, so an old executable accidentally left in Windows
Startup cannot run beside the production app.

## Operator controls

- Story Mode and Normal Mode buttons select and activate that mode.
- Deactivate Guard stops monitoring and closes any presentation.
- Reset Ring Count asks for confirmation, resets the persistent total to zero,
  and re-arms the one-time 50-ring prize announcement. It does not change the
  guard mode or interrupt an active Ring Power pass.
- Close Program exits the guard.
- `Ctrl+Alt+F10` selects and activates Story Mode from anywhere.
- `Ctrl+Alt+F9` skips the Sonic CD cinematic during the active Story Mode
  heist. It is a one-shot testing shortcut; the narration and Robotnik screen
  still run. Press it during the narration to arm the skip, or during the
  cinematic to skip immediately.
- `Ctrl+Alt+F12` activates the currently selected mode from anywhere.
- `Ctrl+Alt+F11` deactivates the guard from anywhere.
- `Ctrl+Shift+F12` closes the program from anywhere.

The panel is an ordinary resizable/minimizable Windows window. Big Box and the takeover naturally cover it on the arcade display; the guard does not force the panel open or restore it from the taskbar.

Status files are written to:

- `%LOCALAPPDATA%\ChaosHeist\chaos-heist-status.txt`
- `%LOCALAPPDATA%\ChaosHeist\chaos-heist-events.log`
- `%LOCALAPPDATA%\ChaosHeist\ring-counter.json`

## Configuration

Keep `chaos-heist-config.json` beside `ChaosHeist.exe`. A legacy `guard-config.json` is still accepted when the new filename is absent, so an older arcade-PC configuration can be migrated safely. The active filename is recorded in the event log. A malformed new config never falls through to an older legacy config: the guard stays disabled/fail-open, ring counting continues, and the error is shown in the panel. Unknown setting names are reported as warnings so spelling mistakes are visible.

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
- `power_loss_lights_sound_file`, `power_loss_buzz_fades_sound_file`, `power_loss_buzz_dies_sound_file`, and `power_loss_tv_off_sound_file`: required staged sounds for the final Story Mode power failure. Their built-in timing is 2.347, 3.968, 2.005, and 2.051 seconds respectively.
- `final_completion_sound_file`: sound played after the victory music and Super Sonic animation.
- `act_clear_sound_file`: exact 50-ring prize sound filename; the production media set uses `16-act-clear.mp3`.
- `ring_joystick_button`: human-numbered joystick button used for rings; default `10`.
- `ring_debounce_ms`: short anti-bounce interval; default `90` ms.
- `ring_game_commit_seconds`: how long an emulator must remain active before one Ring Power use is committed; default `3.0` seconds. Returning to Big Box restores the Robotnik screen if it granted the burst.
- `ring_power_selection_seconds`: how long Ring Power waits for a game to be selected; default `35.0` seconds. The decorative seven-segment Chaos Energy meter still changes phase every five seconds, while its numeric percentage drains continuously and linearly from 100% to 0% across the full 35 seconds. The meter is independent from the dismissible Ring Power text and disappears as soon as an emulator is detected.
- `ring_announcement_seconds`: how long ordinary ring-count messages remain visible; default `3.0`.
- `ring_milestone_announcement_seconds`: how long the 50-ring prize message must remain visible before it is acknowledged; minimum and default `10.0`.
- `cinematic_max_fps`: maximum cinematic display rate; default `15` to keep audio and video synchronized on the arcade PC.

ChaosHeist plays all configured audio through pygame's mixer; it never opens
MP3 files with the Windows default player. If Groove Music nevertheless steals
the foreground during an active guard presentation, the watchdog minimizes it
without closing it and records the event in the status log. Cinematic frames
are copied row-by-row into tightly packed RGB images before resizing for Tk,
removing decoder padding that can appear as vertical artifacts on arcade
displays.

Other GIF, MP3, and MP4 files beside the EXE also override their bundled copies when their configured filenames match. The configured cinematic, shutdown sequence, ring, prize, emerald, victory, Robotnik, and removal assets are mandatory for a production build; the build stops with the exact missing filenames instead of silently producing an incomplete EXE.

## Build ChaosHeist

From PowerShell on the development computer:

```powershell
Set-Location "C:\Users\Projector\Documents\RFIDlock\ChaosHeist"
.\build_chaos_heist.cmd
```

To install or refresh the pinned build dependencies first:

```powershell
.\build_chaos_heist.cmd -InstallDependencies
```

The production build uses 64-bit Python 3.14 and the exact versions in
`requirements.txt`. The script verifies every pin, runs `pip check`, compiles
the source, runs the complete unit suite, builds the EXE, and then runs the
packaged `--self-test` to verify bundled imports, Tk, configuration, and media.

The portable files are created only at:

- `dist-chaos-heist\ChaosHeist.exe`
- `dist-chaos-heist\chaos-heist-config.json`
- `dist-chaos-heist\ChaosHeist-1.0.0-windows-x64.zip`
- `dist-chaos-heist\ChaosHeist-1.0.0-windows-x64.zip.sha256`
- `dist-chaos-heist\ChaosHeist-production.zip` (a convenient copy of the tested versioned archive)

The arcade PC does not need Python installed.

Before replacing the arcade copy, close any old MagnetArcadeGuard process and
remove its old Windows Startup shortcut. Extract the production ZIP into a new
`ChaosHeist` folder, keep the config beside the EXE, and start only
`ChaosHeist.exe`. The included `SHA256SUMS.txt` verifies the files inside the
archive; the adjacent `.sha256` verifies the complete versioned ZIP.

## Archived versions

The legacy root build, separate Story Mode build, stable pre-Story snapshot,
and previous Ring Enabled build artifacts are retained under
`C:\Users\Projector\Documents\RFIDlock\Archive\2026-08-25-pre-ChaosHeist`.
The Git repository also keeps the historical branches and commits.
