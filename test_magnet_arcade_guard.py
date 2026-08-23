import os
import queue
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

import magnet_arcade_guard as guard_module


class GuardLogicTests(unittest.TestCase):
    def make_guard(self):
        return guard_module.MagnetArcadeGuard.__new__(
            guard_module.MagnetArcadeGuard
        )

    def test_config_boolean_understands_string_false(self):
        self.assertFalse(guard_module.config_boolean("false", True))
        self.assertTrue(guard_module.config_boolean("yes", False))

    def test_config_number_clamps_and_falls_back(self):
        self.assertEqual(
            guard_module.config_number("150", 100, 25, 1000),
            150,
        )
        self.assertEqual(
            guard_module.config_number(5000, 100, 25, 1000),
            1000,
        )
        self.assertEqual(
            guard_module.config_number("invalid", 100, 25, 1000),
            100,
        )

    def test_ring_button_number_maps_to_windows_button_mask(self):
        self.assertEqual(guard_module.joystick_button_mask(10), 1 << 9)
        self.assertEqual(guard_module.joystick_button_mask(1), 1)

    def test_ring_entry_is_counted_while_guard_is_off(self):
        guard = self.make_guard()
        guard.ring_count = 4
        guard.ring_milestones_shown = set()
        guard.ring_burst_active = False
        guard.pending_ring_milestone = False
        guard.save_ring_state = lambda: None
        guard.write_status = lambda *args, **kwargs: None
        guard.maybe_show_pending_ring_milestone = lambda: None
        guard.ring_burst_is_eligible = lambda: False
        guard.guard_active = False

        guard.handle_ring_entry()

        self.assertEqual(guard.ring_count, 5)

    def test_fiftieth_ring_queues_milestone_once(self):
        guard = self.make_guard()
        guard.ring_count = 49
        guard.ring_milestones_shown = set()
        guard.ring_burst_active = False
        guard.pending_ring_milestone = False
        guard.save_ring_state = lambda: None
        guard.write_status = lambda *args, **kwargs: None
        milestone_checks = []
        guard.maybe_show_pending_ring_milestone = (
            lambda: milestone_checks.append(True)
        )
        guard.ring_burst_is_eligible = lambda: False
        guard.guard_active = False

        guard.handle_ring_entry()

        self.assertEqual(guard.ring_count, 50)
        self.assertTrue(guard.pending_ring_milestone)
        self.assertEqual(milestone_checks, [True])
        self.assertIn(guard_module.RING_MILESTONE, guard.ring_milestones_shown)

    def test_ring_burst_requires_robotnik_or_normal_all_missing(self):
        guard = self.make_guard()
        guard.guard_active = True
        guard.guard_mode = "story"
        guard.overlay_kind = "robotnik"
        self.assertTrue(guard.ring_burst_is_eligible())

        guard.overlay_kind = None
        guard.guard_mode = "normal"
        guard.accepted_count = 0
        guard.can_show_story_announcement = lambda: True
        self.assertTrue(guard.ring_burst_is_eligible())

        guard.accepted_count = 1
        self.assertFalse(guard.ring_burst_is_eligible())

    def test_ring_burst_is_consumed_only_after_game_returns(self):
        guard = self.make_guard()
        guard.ring_burst_active = True
        guard.ring_burst_game_seen = True
        guard.ring_burst_game_seen_since = 1.0
        guard.guard_active = True
        guard.accepted_count = 2
        guard.guard_mode = "story"
        guard.story_intro_completed = True
        pending = []
        guard.write_status = lambda *args, **kwargs: None
        guard.maybe_show_pending_overlay = lambda: pending.append(True)

        guard.consume_ring_burst_on_return()

        self.assertFalse(guard.ring_burst_active)
        self.assertEqual(
            guard.pending_overlay_missing,
            guard_module.TOTAL_EMERALDS - 2,
        )
        self.assertEqual(pending, [True])

    def test_ring_burst_waits_for_a_stable_game_launch(self):
        guard = self.make_guard()
        guard.ring_burst_active = True
        guard.ring_burst_game_seen = False
        guard.ring_burst_game_seen_since = 0.0
        guard.guard_active = True
        guard.foreground_process_name = "retroarch.exe"
        guard.overlay_gate_state = "WAITING_FOR_BIGBOX"
        guard.update_overlay_gate = lambda: False
        guard.write_status = lambda *args, **kwargs: None

        with patch.object(time, "monotonic", side_effect=[100.0, 102.0, 104.0]):
            guard.handle_ring_burst_foreground()
            self.assertFalse(guard.ring_burst_game_seen)
            guard.handle_ring_burst_foreground()
            self.assertFalse(guard.ring_burst_game_seen)
            guard.handle_ring_burst_foreground()

        self.assertTrue(guard.ring_burst_game_seen)

    def test_final_return_during_ring_burst_resumes_victory(self):
        guard = self.make_guard()
        guard.ring_burst_active = True
        guard.ring_burst_game_seen = True
        guard.ring_burst_game_seen_since = 1.0
        guard.guard_active = True
        guard.accepted_count = guard_module.TOTAL_EMERALDS
        guard.guard_mode = "story"
        guard.story_intro_completed = True
        guard.overlay_kind = None
        events = []
        guard.write_status = lambda *args, **kwargs: None

        def show_missing(missing):
            events.append(("overlay", missing))
            guard.overlay_kind = "robotnik"

        guard.show_missing_overlay = show_missing
        guard.begin_final_emerald_transition = (
            lambda: events.append(("victory", None))
        )

        guard.consume_ring_burst_on_return()

        self.assertEqual(events, [("overlay", 0), ("victory", None)])

    def test_missing_text_uses_singular_emerald(self):
        guard = self.make_guard()
        self.assertEqual(
            guard.missing_text(1),
            "1 Chaos Emerald Missing!",
        )
        self.assertEqual(
            guard.missing_text(2),
            "2 Chaos Emeralds Missing!",
        )

    def test_story_theft_messages_are_unique_for_each_partial_removal(self):
        messages = guard_module.STORY_STOLEN_TEXT

        self.assertEqual(set(messages), set(range(1, 7)))
        self.assertEqual(
            len({title for title, detail in messages.values()}),
            6,
        )
        self.assertTrue(
            all("CHAOS" in title and "STOLEN" in title
                for title, detail in messages.values())
        )

    def test_story_intro_text_does_not_assume_sonic_has_been_recruited(self):
        self.assertNotIn("SONIC!", guard_module.STORY_QUESTION_TITLE)
        self.assertNotIn("SONIC", guard_module.STORY_QUESTION_MESSAGE)
        self.assertEqual(guard_module.STORY_EGGMAN_MESSAGE, "")

    def test_removal_sound_selection_excludes_previous_sound(self):
        guard = self.make_guard()
        sounds = [object(), object(), object()]
        guard.removal_sounds = sounds
        guard.removal_sound = sounds[1]
        guard.last_removal_sound_index = 1
        selected = []
        guard.play_event_sound = (
            lambda sound, event_kind, **kwargs: (
                selected.append(sound) or True
            )
        )

        with patch.object(
            guard_module.random,
            "choice",
            side_effect=lambda options: (
                self.assertNotIn(1, options) or options[0]
            ),
        ):
            self.assertTrue(guard.play_removal_sound())

        self.assertEqual(selected, [sounds[0]])

    def test_window_must_cover_at_least_95_percent_of_monitor(self):
        guard = self.make_guard()
        monitor = (0, 0, 640, 480)
        self.assertTrue(
            guard.window_covers_monitor((0, 0, 640, 480), monitor)
        )
        self.assertFalse(
            guard.window_covers_monitor((0, 0, 500, 480), monitor)
        )

    def test_disconnect_disables_active_guard(self):
        guard = self.make_guard()
        guard.guard_active = True
        guard.reader_connected = True
        guard.controller_lost = False
        guard.pending_count = 1
        reasons = []
        guard.fault_disable_guard = reasons.append

        guard.handle_disconnect("ESP32 unplugged")

        self.assertFalse(guard.reader_connected)
        self.assertTrue(guard.controller_lost)
        self.assertIsNone(guard.pending_count)
        self.assertEqual(reasons, ["ESP32 unplugged"])

    def test_same_recent_event_sound_does_not_overlap(self):
        class FakeChannel:
            def __init__(self):
                self.play_count = 0
                self.stop_count = 0

            def get_busy(self):
                return True

            def stop(self):
                self.stop_count += 1

            def set_volume(self, volume):
                return None

            def play(self, sound):
                self.play_count += 1

        guard = self.make_guard()
        channel = FakeChannel()
        guard.audio_ready = True
        guard.event_channel = channel
        guard.last_event_sound_kind = "returned"
        guard.last_event_sound_at = time.monotonic()
        guard.music_mode = "missing"
        guard.schedule_event_audio_watchdog = lambda: None

        played = guard.play_event_sound(object(), "returned")

        self.assertFalse(played)
        self.assertEqual(channel.stop_count, 0)
        self.assertEqual(channel.play_count, 0)

    def test_different_event_replaces_busy_sound(self):
        class FakeChannel:
            def __init__(self):
                self.play_count = 0
                self.stop_count = 0

            def get_busy(self):
                return True

            def stop(self):
                self.stop_count += 1

            def set_volume(self, volume):
                return None

            def play(self, sound):
                self.play_count += 1

        guard = self.make_guard()
        channel = FakeChannel()
        guard.audio_ready = True
        guard.event_channel = channel
        guard.last_event_sound_kind = "returned"
        guard.last_event_sound_at = time.monotonic()
        guard.music_mode = None
        guard.schedule_event_audio_watchdog = lambda: None

        played = guard.play_event_sound(object(), "removed")

        self.assertTrue(played)
        self.assertEqual(channel.stop_count, 1)
        self.assertEqual(channel.play_count, 1)

    def test_stale_serial_messages_are_ignored_after_reactivation(self):
        guard = self.make_guard()
        guard.messages = queue.Queue()
        guard.activation_generation = 3
        guard.running = False
        handled = []
        guard.handle_serial_message = handled.append
        guard.handle_disconnect = self.fail
        guard.fault_disable_guard = self.fail
        guard.accept_stable_count = lambda: None
        guard.messages.put(("SERIAL", "old", 2))
        guard.messages.put(("SERIAL", "current", 3))

        guard.process_messages()

        self.assertEqual(handled, ["current"])

    def test_story_mode_missing_start_is_only_a_baseline(self):
        guard = self.make_guard()
        guard.pending_count = 5
        guard.pending_count_since = time.monotonic() - 10
        guard.accepted_count = None
        guard.guard_mode = "story"
        guard.story_armed = False
        handled = []
        guard.handle_story_count_change = (
            lambda previous, current: handled.append((previous, current))
        )

        guard.accept_stable_count()

        self.assertEqual(guard.accepted_count, 5)
        self.assertFalse(guard.story_armed)
        self.assertEqual(handled, [])

    def test_story_mode_arms_on_complete_baseline(self):
        guard = self.make_guard()
        guard.pending_count = guard_module.TOTAL_EMERALDS
        guard.pending_count_since = time.monotonic() - 10
        guard.accepted_count = None
        guard.guard_mode = "story"
        guard.story_armed = False
        guard.handle_story_count_change = self.fail

        guard.accept_stable_count()

        self.assertTrue(guard.story_armed)

    def test_story_mode_announces_partial_theft_then_stages_shutdown(self):
        guard = self.make_guard()
        guard.story_intro_completed = False
        guard.story_armed = True
        guard.overlay_kind = None
        guard.pending_overlay_missing = None
        announcements = []
        sounds = []
        pending_checks = []
        guard.show_story_announcement = lambda count, kind: (
            announcements.append((count, kind)) or True
        )
        guard.play_removal_sound = lambda: sounds.append("removed")
        guard.play_emerald_sound = lambda: sounds.append("returned")
        guard.hide_story_announcement = lambda: announcements.append("hide")
        guard.maybe_show_pending_overlay = lambda: pending_checks.append(True)

        guard.handle_story_count_change(7, 6)
        guard.handle_story_count_change(1, 0)

        self.assertEqual(announcements[0], (6, "removed"))
        self.assertEqual(sounds, ["removed"])
        self.assertEqual(
            guard.pending_overlay_missing,
            guard_module.TOTAL_EMERALDS,
        )
        self.assertEqual(pending_checks, [True])

    def test_normal_mode_only_warns_on_a_downward_edge(self):
        guard = self.make_guard()
        guard.overlay_kind = None
        warnings = []
        returns = []
        guard.show_normal_warning = (
            lambda previous, current: warnings.append((previous, current))
        )
        guard.play_emerald_sound = lambda: returns.append(True)

        guard.handle_normal_count_change(4, 5)
        guard.handle_normal_count_change(5, 4)

        self.assertEqual(warnings, [(5, 4)])
        self.assertEqual(returns, [True])

    def test_normal_warning_ends_immediately_on_any_return(self):
        guard = self.make_guard()
        guard.overlay_kind = "normal_warning"
        guard.normal_warning_trigger_count = 3
        finished = []
        returns = []
        guard.finish_normal_warning = lambda: finished.append(True)
        guard.play_emerald_sound = lambda: returns.append(True)

        guard.handle_normal_count_change(3, 4)

        self.assertEqual(finished, [True])
        self.assertEqual(returns, [True])

    def test_normal_mode_removal_plays_random_voice_after_banner(self):
        guard = self.make_guard()
        guard.overlay_kind = None
        warnings = []
        sounds = []
        guard.show_normal_warning = (
            lambda previous, current: (
                warnings.append((previous, current)) or True
            )
        )
        guard.play_removal_sound = lambda: sounds.append("removed") or True

        guard.handle_normal_count_change(7, 6)

        self.assertEqual(warnings, [(7, 6)])
        self.assertEqual(sounds, ["removed"])

    def test_energy_meter_text_shows_master_energy_and_progress(self):
        guard = self.make_guard()

        self.assertEqual(
            guard.energy_meter_text(0),
            "MASTER EMERALD POWER  0%",
        )
        self.assertEqual(
            guard.energy_meter_text(7),
            "MASTER EMERALD POWER  100%",
        )

    @unittest.skipUnless(
        guard_module.AV_AVAILABLE
        and guard_module.ORIGINAL_CINEMATIC_VIDEO_PATH.is_file(),
        "Sonic cinematic asset or decoder is not available",
    )
    def test_cinematic_audio_can_be_prepared(self):
        guard = self.make_guard()
        guard.audio_ready = False
        guard.cinematic_video_path = (
            guard_module.ORIGINAL_CINEMATIC_VIDEO_PATH
        )
        guard.cinematic_prepare_state = "preparing"
        guard.cinematic_prepare_error = ""
        guard.cinematic_duration = 0.0
        guard.cinematic_audio_pcm = b""
        guard.cinematic_audio_rate = 0

        guard.prepare_cinematic_audio()

        self.assertEqual(guard.cinematic_prepare_state, "ready")
        self.assertGreater(guard.cinematic_duration, 90.0)
        self.assertGreater(len(guard.cinematic_audio_pcm), 1_000_000)

    @unittest.skipUnless(
        guard_module.AV_AVAILABLE
        and guard_module.ORIGINAL_CINEMATIC_VIDEO_PATH.is_file(),
        "Sonic cinematic asset or decoder is not available",
    )
    def test_cinematic_frame_can_be_scaled_directly_to_rgb(self):
        with guard_module.av.open(
            str(guard_module.ORIGINAL_CINEMATIC_VIDEO_PATH)
        ) as container:
            video_stream = next(
                stream for stream in container.streams if stream.type == "video"
            )
            frame = next(container.decode(video_stream))
            image = frame.reformat(
                width=640,
                height=480,
                format="rgb24",
            ).to_image()

        self.assertEqual(image.mode, "RGB")
        self.assertEqual(image.size, (640, 480))

    def test_resume_watchdog_honors_cancel_before_resuming(self):
        guard_module.configure_windows_runtime()
        with tempfile.TemporaryDirectory() as temporary_directory:
            cancel_path = Path(temporary_directory) / "cancel.flag"
            cancel_path.write_text("cancel\n", encoding="utf-8")

            result = guard_module.run_resume_watchdog(
                os.getpid(),
                os.getpid(),
                str(cancel_path),
            )

            self.assertEqual(result, 0)
            self.assertFalse(cancel_path.exists())

    def test_single_instance_mutex_rejects_second_copy(self):
        guard_module.configure_windows_runtime()
        first_handle, first_allowed = (
            guard_module.acquire_single_instance_mutex()
        )
        second_handle, second_allowed = (
            guard_module.acquire_single_instance_mutex()
        )
        try:
            self.assertTrue(first_handle)
            self.assertTrue(first_allowed)
            self.assertTrue(second_handle)
            self.assertFalse(second_allowed)
        finally:
            kernel32 = guard_module.ctypes.windll.kernel32
            if second_handle:
                kernel32.CloseHandle(second_handle)
            if first_handle:
                kernel32.CloseHandle(first_handle)


if __name__ == "__main__":
    unittest.main()
