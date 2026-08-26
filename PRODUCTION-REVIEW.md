# ChaosHeist 1.0.0 Production Reliability Review

Reviewed: 2026-08-25

Scope: the Windows application, seven-sensor ESP32 firmware, configuration,
persistent ring counter, Big Box handoff, emulator gating, audio, overlays,
packaging, and deployment behavior.

## Production invariants

- Fail open: an ESP32 loss, malformed critical configuration, unsafe takeover,
  media failure, or Ring Power transition failure disables the guard and
  releases Big Box. It never deliberately leaves the arcade locked after a
  fault.
- Ring counting is independent: rings continue to count while the guard is
  off, while a game is active, and while emerald monitoring is disabled.
- Firmware lighting is independent: LED feedback continues without the guard
  and through a Windows disconnect.
- Emulator safety: no ChaosHeist window is intentionally created over MAME,
  GroovyMAME, RetroArch, or another configured emulator. Full takeovers wait
  for a stable, visible, full-monitor Big Box foreground window.
- Input recovery first: Big Box is resumed before audio restoration. A small
  independent watchdog process resumes Big Box if ChaosHeist dies while it is
  suspended.
- Milestone priority: the first 50-ring prize card and act-clear sound cannot
  be interrupted by later rings or emerald changes. The final sensor state is
  reconciled after the card finishes.
- One production instance: ChaosHeist holds both its new mutex and the former
  MagnetArcadeGuard mutex so an obsolete startup copy cannot run beside it.

## Adversarial behavior matrix

| Situation | Expected behavior |
|---|---|
| Guard starts with emeralds already missing | The reading becomes a baseline. Story Mode does not start until all seven are observed and a later theft occurs; Normal Mode does not invent a removal event. |
| ESP32 port stays open but stops sending before activation | Activation waits up to 15 seconds for the first valid controller message, then disables the guard instead of remaining indefinitely half-active. |
| Several emeralds move inside the stabilization interval | Intermediate chatter is ignored. The final stable count is accepted as one aggregate transition and its energy/count display is correct. |
| An emerald is removed and quickly returned | Only stable transitions are presented. A return during the final-return pause cancels safely if the count falls again. |
| Emeralds move while an ordinary game is active | Counts remain current, but temporary menu notices and their sounds wait rather than appearing or playing over the game. |
| Emeralds change during shutdown narration or the cinematic | Counts continue to update silently; the Robotnik recovery screen opens with the latest count. If all seven are already back, the victory transition begins. |
| An emerald is taken during the Sonic victory screen | Victory is cancelled and the Robotnik recovery screen returns with the current missing count. |
| More emeralds are taken while recovery is underway | The Robotnik title, missing message, meter, and event audio update without restarting the story cinematic. |
| A ring is inserted with the guard off | The persistent total and ring sound still update. No Ring Power pass is created. |
| A ring is inserted during a normal game | It is counted immediately; no window is drawn over the emulator. The latest count announcement waits for a safe Big Box menu. |
| The 50th ring is inserted during a game | The prize remains durably pending across game return or app restart and owns the next safe presentation for at least ten seconds. |
| An emulator starts while any small notice is already visible | The notice and its sound are removed immediately. An interrupted 50-ring prize remains durably pending and restarts for its full interval at the next safe menu. |
| More rings arrive while a ring banner is visible | The total updates immediately. Rapid disk saves are coalesced, and the visible ordinary counter refreshes to the latest total. |
| More rings arrive during the 50-ring prize | They count and persist, but do not replace the act-clear sound, reset the ten-second timer, or change the one-time prize text. |
| The same coin pulse is reported by both USB encoders | A global 90 ms debounce suppresses the duplicate in addition to per-device edge detection. |
| Ring button is held while ChaosHeist starts or an encoder reconnects | The held state becomes the baseline and is not counted. A release followed by a new press is required. |
| A ring buys access, but no game is launched | Ring Power remains ready at the Big Box menu. Additional rings count but do not extend or stack passes. |
| One emerald returns before a Normal Mode Ring Power game starts | The zero-energy lock and unused pass end as soon as stable Big Box is visible because playable Chaos Energy has already returned. |
| A game opens and closes very quickly | Seeing an emulator and then stable Big Box consumes the pass even when the game never meets the normal commit duration. |
| Rings are inserted during the paid game | They count but do not extend the active pass. Returning to Big Box restores Robotnik when the originating lock condition still exists. |
| Emeralds are restored during the paid game | Counts are retained. On return, Story Mode resumes or completes using the latest count; Normal Mode releases its zero-energy lock as soon as at least one emerald is present. |
| Mode is switched during a warning, takeover, victory, or Ring Power state | The old mode is deactivated, all input/audio/window side effects are cleaned up, and the selected mode activates only after cleanup is safe. |
| Guard is manually deactivated during a takeover | The presentation closes, Big Box is resumed, audio restoration is attempted, ring counting stays active, and firmware LED effects continue. |
| ESP32 disconnects or heartbeat stops | The guard disables and stays off until an operator activates it again. Ring counting remains independent. |
| Joystick service fails | Emerald monitoring remains active. The panel reports the noncritical error and the ring worker is supervised/restarted; a later ring also falls back to synchronous persistence if needed. |
| Ring-state primary is corrupt | It is preserved with a timestamp and recovery uses the prior valid backup. A valid interrupted `.tmp` save is another recovery source. |
| Legacy and new ring files coexist | New ChaosHeist state always wins. A durable one-time marker prevents a deleted backup or reset count from resurrecting old MagnetArcadeGuard data. |
| Config JSON is malformed | The guard remains disabled/fail-open and reports the exact file error. Ring counting and the control panel continue; an older config is not silently substituted. |
| Big Box is minimized, windowed, on the wrong foreground, or still settling after a game | Presentation remains pending. It begins only after Big Box is foreground, visible, covers at least 95% of its monitor, and stays geometrically stable for the configured delay. |
| ChaosHeist crashes while Big Box is suspended | The independent helper observes the parent exit and resumes Big Box. |
| Windows shuts down or the operator closes ChaosHeist | Workers are signaled, the latest ring total is flushed, overlay/audio/input side effects are released, and both instance locks are closed. |

## Release hardening completed

- Product-facing source, executable, spec, build script, config, firmware
  folder, log, app-data, and distribution names are now ChaosHeist.
- Old local builds are preserved in a dated archive rather than deleted.
- Ring state has explicit supported versions; unknown future versions are
  preserved as invalid rather than being guessed or overwritten.
- Config source, parse errors, legacy use, and unknown keys are visible.
- Production builds require 64-bit Python 3.14 and exact direct dependency
  pins, run `pip check`, and stop on missing Tk files or required media.
- UPX is disabled to remove an unpinned external compressor from the build.
- The EXE carries Windows version/product metadata.
- The finished one-file EXE runs a noninteractive `--self-test` before release
  packaging. Releases are versioned and include SHA-256 manifests.
- One hundred automated tests cover parsing, state persistence, migration, mutex
  compatibility, overlays, Ring Power, milestones, failure cleanup, cinematic
  deadlines, sensor baselines, and mode transitions.

## Known operational limits

- ChaosHeist is intentionally a Big Box menu experience, not a universal
  exclusive-fullscreen overlay. It will not cover active emulators.
- Every emulator executable must be listed in `emulator_process_names`.
  An unlisted emulator still prevents an overlay because it is not Big Box,
  but Ring Power cannot recognize that game launch and may remain unused.
- The Ring Power handoff depends on Big Box being the foreground full-screen
  menu after a game. Windows desktop, dialogs, launchers, or a minimized Big
  Box keep the presentation pending rather than guessing.
- The Windows multimedia joystick API exposes up to 32 buttons per device.
  The configured coin pulse must be long enough to appear in a 10 ms poll.
- The staged CRT effect is synchronized to the exact configured production
  clips. Replacing those clips with different durations requires timing edits.
- On an abrupt power cut, the newest sub-second batch of rings can be lost;
  the primary, previous-generation backup, and interrupted-save recovery keep
  the last completed state available. Normal close performs a final flush.
- Suspending Big Box uses Windows' native process-suspend interface. The crash
  helper and fail-open paths reduce the risk, but deployment should still be
  tested on the exact arcade PC, Big Box build, encoder order, and display mode.
- Arduino CLI is not installed on the development computer, so the firmware is
  source-reviewed and previously hardware-tested but is not compiled by the
  Windows release script. Upload it with Arduino IDE before the final cabinet
  acceptance test.

## Final cabinet acceptance test

1. Remove the old MagnetArcadeGuard startup shortcut and confirm only
   `ChaosHeist.exe` is running.
2. Start Big Box with the arcade monitor as the only display and verify the
   panel reports two joysticks, ESP32 connected, and seven emeralds detected.
3. Exercise Story Mode from 7 to 0 and back to 7, including one rapid
   remove/return during recovery.
4. Exercise Normal Mode, zero-energy lock, one Ring Power game, a very short
   game launch, and return to Big Box.
5. Insert several rings rapidly, insert rings during a game, and test a pending
   50-ring prize after resetting the count.
6. Disconnect the ESP32 during a takeover and confirm the guard disables and
   the arcade becomes usable; reconnect and reactivate manually.
7. Close ChaosHeist during a Robotnik screen and confirm Big Box controls and
   audio return.
8. Reboot once and confirm ring total persistence, Windows Startup behavior,
   correct monitor selection, and no old executable starts.
