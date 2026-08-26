import json
import os
import queue
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

import chaos_heist as guard_module


class GuardLogicTests(unittest.TestCase):
    def make_guard(self):
        return guard_module.ChaosHeistApp.__new__(
            guard_module.ChaosHeistApp
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
        self.assertEqual(
            guard_module.config_number(float("nan"), 100, 25, 1000),
            100,
        )

    def test_new_config_takes_precedence_over_legacy_config(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_directory = Path(temporary_directory)
            (config_directory / guard_module.CONFIG_FILENAME).write_text(
                '{"default_mode": "normal"}\n',
                encoding="utf-8",
            )
            (config_directory / guard_module.LEGACY_CONFIG_FILENAME).write_text(
                '{"default_mode": "story", "auto_activate": true}\n',
                encoding="utf-8",
            )

            config, active_path, errors, warnings = (
                guard_module.load_runtime_config_details(config_directory)
            )

            self.assertEqual(config["default_mode"], "normal")
            self.assertFalse(config["auto_activate"])
            self.assertEqual(active_path.name, guard_module.CONFIG_FILENAME)
            self.assertEqual(errors, [])
            self.assertEqual(warnings, [])

    def test_malformed_new_config_does_not_fall_back_to_legacy(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_directory = Path(temporary_directory)
            (config_directory / guard_module.CONFIG_FILENAME).write_text(
                "{not json",
                encoding="utf-8",
            )
            (config_directory / guard_module.LEGACY_CONFIG_FILENAME).write_text(
                '{"auto_activate": true}\n',
                encoding="utf-8",
            )

            config, active_path, errors, _warnings = (
                guard_module.load_runtime_config_details(config_directory)
            )

            self.assertFalse(config["auto_activate"])
            self.assertEqual(active_path.name, guard_module.CONFIG_FILENAME)
            self.assertIn("could not be read", errors[0])

    def test_unknown_config_setting_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_directory = Path(temporary_directory)
            (config_directory / guard_module.CONFIG_FILENAME).write_text(
                '{"totla_emeralds": 7}\n',
                encoding="utf-8",
            )

            _config, _active_path, errors, warnings = (
                guard_module.load_runtime_config_details(config_directory)
            )

            self.assertEqual(errors, [])
            self.assertIn("totla_emeralds", warnings[0])

    def test_emulator_process_names_are_normalized_and_configurable(self):
        self.assertEqual(
            guard_module.config_process_names(
                [" MAME.EXE ", r"C:\\Arcade\\new-emulator", ""],
                ("fallback.exe",),
            ),
            frozenset({"mame.exe", "new-emulator.exe"}),
        )
        self.assertEqual(
            guard_module.config_process_names([], ("fallback.exe",)),
            frozenset({"fallback.exe"}),
        )

    def test_protocol_parser_rejects_malformed_heartbeat_traffic(self):
        self.assertEqual(
            guard_module.parse_magnet_protocol_message(
                "MAGNET_LOCK:READY"
            ),
            ("ready", None),
        )
        self.assertEqual(
            guard_module.parse_magnet_protocol_message(
                "MAGNET_LOCK:COUNT:7"
            ),
            ("count", 7),
        )
        for malformed in (
            "MAGNET_LOCK:",
            "MAGNET_LOCK:COUNT:nope",
            "MAGNET_LOCK:COUNT:+7",
            "MAGNET_LOCK:COUNT: 7",
            "MAGNET_LOCK:COUNT:8",
            "MAGNET_LOCK:UNKNOWN",
        ):
            self.assertIsNone(
                guard_module.parse_magnet_protocol_message(malformed)
            )

    def test_ring_button_number_maps_to_windows_button_mask(self):
        self.assertEqual(guard_module.joystick_button_mask(10), 1 << 9)
        self.assertEqual(guard_module.joystick_button_mask(1), 1)

    def test_ring_debounce_suppresses_cross_encoder_duplicate(self):
        now = 10.0
        self.assertFalse(
            guard_module.ring_press_is_accepted(
                now,
                0.0,
                now - guard_module.RING_DEBOUNCE_SECONDS / 2,
            )
        )
        self.assertTrue(
            guard_module.ring_press_is_accepted(
                now,
                0.0,
                now - guard_module.RING_DEBOUNCE_SECONDS * 2,
            )
        )

    def test_joystick_direction_counts_as_a_dismiss_input(self):
        centered = MagicMock(
            dwXpos=guard_module.JOYSTICK_AXIS_CENTER,
            dwYpos=guard_module.JOYSTICK_AXIS_CENTER,
            dwPOV=guard_module.JOY_POVCENTERED,
        )
        pushed = MagicMock(
            dwXpos=0,
            dwYpos=guard_module.JOYSTICK_AXIS_CENTER,
            dwPOV=guard_module.JOY_POVCENTERED,
        )
        pov_pushed = MagicMock(
            dwXpos=guard_module.JOYSTICK_AXIS_CENTER,
            dwYpos=guard_module.JOYSTICK_AXIS_CENTER,
            dwPOV=0,
        )

        self.assertFalse(guard_module.joystick_direction_active(centered))
        self.assertTrue(guard_module.joystick_direction_active(pushed))
        self.assertTrue(guard_module.joystick_direction_active(pov_pushed))

    def test_later_ring_does_not_refresh_active_milestone(self):
        guard = self.make_guard()
        guard.active_ring_announcement_kind = "milestone"
        guard.pending_ring_announcement = None
        guard.write_status = lambda *args, **kwargs: None
        refreshed = []
        guard.refresh_active_ring_announcement = (
            lambda: refreshed.append(True)
        )

        guard.request_ring_announcement("count")

        self.assertEqual(refreshed, [])
        self.assertIsNone(guard.pending_ring_announcement)

    def test_ring_entry_is_counted_while_guard_is_off(self):
        guard = self.make_guard()
        guard.ring_count = 4
        guard.ring_milestones_shown = set()
        guard.ring_milestones_pending = set()
        guard.ring_burst_active = False
        guard.pending_ring_milestone = False
        guard.guard_active = False
        guard.save_ring_state = lambda: None
        guard.write_status = lambda *args, **kwargs: None
        announcements = []
        guard.request_ring_announcement = announcements.append
        guard.ring_burst_is_eligible = lambda: False
        guard.guard_active = False

        guard.handle_ring_entry()

        self.assertEqual(guard.ring_count, 5)
        self.assertEqual(announcements, ["count"])

    def test_ring_entry_plays_ring_sound_even_when_guard_is_off(self):
        guard = self.make_guard()
        guard.ring_count = 0
        guard.ring_milestones_shown = set()
        guard.ring_milestones_pending = set()
        guard.ring_burst_active = False
        guard.pending_ring_milestone = False
        guard.guard_active = False
        guard.save_ring_state = lambda: None
        guard.write_status = lambda *args, **kwargs: None
        guard.request_ring_announcement = lambda kind: None
        guard.ring_burst_is_eligible = lambda: False

        with patch.object(guard, "play_ring_sound") as play_ring_sound:
            guard.handle_ring_entry()

        play_ring_sound.assert_called_once_with()

    def test_active_ring_count_banner_is_refreshed_immediately(self):
        guard = self.make_guard()
        guard.ring_count = 18
        guard.pending_ring_announcement = None
        guard.active_ring_announcement_kind = "count"
        guard.ring_power_announcement_visible = False
        refreshed = []
        guard.refresh_active_ring_announcement = (
            lambda: refreshed.append(guard.ring_count)
        )

        guard.request_ring_announcement("count")

        self.assertEqual(refreshed, [18])
        self.assertIsNone(guard.pending_ring_announcement)

    def test_milestone_interrupts_active_ring_count_banner(self):
        guard = self.make_guard()
        guard.pending_ring_announcement = "count"
        guard.active_ring_announcement_kind = "count"
        hidden = []
        guard.hide_story_announcement = lambda: hidden.append(True)

        guard.request_ring_announcement("milestone")

        self.assertEqual(hidden, [True])
        self.assertEqual(guard.pending_ring_announcement, "milestone")

    def test_milestone_message_does_not_follow_later_ring_total(self):
        guard = self.make_guard()
        guard.ring_count = 53

        title, detail, _, _ = guard.ring_announcement_content("milestone")

        self.assertEqual(title, guard_module.RING_MILESTONE_TITLE)
        self.assertEqual(detail, guard_module.RING_MILESTONE_MESSAGE)
        self.assertNotIn("53", detail)

    def test_ring_entry_during_active_burst_does_not_rearm_the_burst(self):
        guard = self.make_guard()
        guard.ring_count = 8
        guard.ring_milestones_shown = set()
        guard.ring_milestones_pending = set()
        guard.ring_burst_active = True
        guard.pending_ring_milestone = False
        guard.pending_ring_announcement = None
        guard.save_ring_state = lambda: None
        guard.write_status = lambda *args, **kwargs: None
        announcements = []
        guard.request_ring_announcement = announcements.append

        guard.handle_ring_entry()

        self.assertEqual(guard.ring_count, 9)
        self.assertTrue(guard.ring_burst_active)
        self.assertEqual(announcements, ["count"])

    def test_fiftieth_ring_queues_milestone_once(self):
        guard = self.make_guard()
        guard.ring_count = 49
        guard.ring_milestones_shown = set()
        guard.ring_milestones_pending = set()
        guard.ring_burst_active = False
        guard.pending_ring_milestone = False
        guard.save_ring_state = lambda: None
        guard.write_status = lambda *args, **kwargs: None
        announcements = []
        guard.request_ring_announcement = announcements.append
        guard.ring_burst_is_eligible = lambda: False
        guard.guard_active = False

        guard.handle_ring_entry()

        self.assertEqual(guard.ring_count, 50)
        self.assertTrue(guard.pending_ring_milestone)
        self.assertEqual(announcements, ["milestone"])
        self.assertNotIn(
            guard_module.RING_MILESTONE,
            guard.ring_milestones_shown,
        )
        self.assertIn(
            guard_module.RING_MILESTONE,
            guard.ring_milestones_pending,
        )

    def test_reset_ring_count_clears_total_and_rearms_milestone(self):
        guard = self.make_guard()
        guard.ring_count = 57
        guard.ring_milestones_shown = {guard_module.RING_MILESTONE}
        guard.ring_milestones_pending = {100}
        guard.pending_ring_milestone = True
        guard.pending_ring_announcement = "milestone"
        saved = []
        statuses = []
        guard.save_ring_state = lambda: saved.append(True)
        guard.write_status = statuses.append
        guard.update_control_panel = lambda: None

        guard.reset_ring_count()

        self.assertEqual(guard.ring_count, 0)
        self.assertEqual(guard.ring_milestones_shown, set())
        self.assertEqual(guard.ring_milestones_pending, set())
        self.assertFalse(guard.pending_ring_milestone)
        self.assertIsNone(guard.pending_ring_announcement)
        self.assertEqual(saved, [True])
        self.assertIn("previous_total=57", statuses[0])

    def test_ring_count_announcement_includes_latest_total(self):
        guard = self.make_guard()
        guard.running = True
        guard.ring_count = 12
        guard.pending_ring_milestone = False
        guard.pending_ring_announcement = "count"
        guard.write_status = lambda *args, **kwargs: None
        shown = []
        guard.show_plain_announcement = (
            lambda title, detail, color, duration, **kwargs: (
                shown.append((title, detail, color, duration, kwargs)) or True
            )
        )

        guard.maybe_show_pending_ring_announcement()

        self.assertEqual(shown[0][0], guard_module.RING_COUNT_TITLE)
        self.assertIn("TOTAL RINGS: 12", shown[0][1])
        self.assertIsNone(guard.pending_ring_announcement)

    def test_normal_ring_announcement_includes_current_chaos_energy(self):
        guard = self.make_guard()
        guard.running = True
        guard.guard_mode = "normal"
        guard.accepted_count = 3
        guard.ring_count = 12
        guard.pending_ring_milestone = False
        guard.pending_ring_announcement = "count"
        guard.write_status = lambda *args, **kwargs: None
        shown = []
        guard.show_plain_announcement = (
            lambda title, detail, color, duration, **kwargs: (
                shown.append(detail) or True
            )
        )

        guard.maybe_show_pending_ring_announcement()

        self.assertIn("CHAOS ENERGY: 43%", shown[0])

    def test_pending_milestone_can_replace_ring_power_at_safe_menu(self):
        guard = self.make_guard()
        guard.running = True
        guard.guard_mode = "normal"
        guard.ring_count = 50
        guard.accepted_count = 7
        guard.pending_ring_milestone = True
        guard.pending_ring_announcement = "milestone"
        guard.ring_power_announcement_visible = True
        guard.ring_milestones_pending = {guard_module.RING_MILESTONE}
        guard.ring_milestones_shown = set()
        guard.write_status = lambda *args, **kwargs: None
        guard.save_ring_state = lambda: None
        shown = []
        guard.show_plain_announcement = (
            lambda title, detail, color, duration, **kwargs: (
                shown.append((title, detail, duration)) or True
            )
        )

        guard.maybe_show_pending_ring_announcement()

        self.assertEqual(shown[0][0], guard_module.RING_MILESTONE_TITLE)
        self.assertIn("CHAOS ENERGY: 100%", shown[0][1])
        self.assertTrue(guard.pending_ring_milestone)
        self.assertEqual(guard.active_ring_announcement_kind, "milestone")

    def test_ring_power_announcement_waits_for_a_joystick_press(self):
        guard = self.make_guard()
        guard.running = True
        guard.ring_count = 9
        guard.pending_ring_milestone = False
        guard.pending_ring_announcement = "burst"
        guard.ring_power_announcement_visible = False
        guard.write_status = lambda *args, **kwargs: None
        shown = []
        guard.show_plain_announcement = (
            lambda title, detail, color, duration, **kwargs: (
                shown.append((title, detail, color, duration)) or True
            )
        )

        guard.maybe_show_pending_ring_announcement()

        self.assertEqual(shown[0][0], guard_module.RING_BURST_TITLE)
        self.assertIsNone(shown[0][3])
        self.assertTrue(guard.ring_power_announcement_visible)

    def test_joystick_press_dismisses_ring_power_and_reveals_pending_count(self):
        guard = self.make_guard()
        guard.ring_power_announcement_visible = True
        guard.ring_power_ignore_until = 0.0
        guard.joystick_press_sequence = 1
        guard.ring_power_ignore_press_sequence = 0
        hidden = []
        pending = []
        guard.hide_story_announcement = (
            lambda: hidden.append(True)
        )
        guard.maybe_show_pending_ring_announcement = (
            lambda: pending.append(True)
        )
        guard.write_status = lambda *args, **kwargs: None

        guard.handle_joystick_press()

        self.assertEqual(hidden, [True])
        self.assertEqual(pending, [True])

    def test_joystick_press_dismisses_emerald_announcement(self):
        guard = self.make_guard()
        guard.active_ring_announcement_kind = None
        guard.announcement_after_id = "timer"
        guard.announcement_window = MagicMock()
        guard.ring_power_announcement_visible = False
        hidden = []
        statuses = []
        guard.hide_story_announcement = lambda: hidden.append(True)
        guard.write_status = statuses.append

        guard.handle_joystick_press("9")

        self.assertEqual(hidden, [True])
        self.assertIn("EMERALD ANNOUNCEMENT DISMISSED", statuses[0])

    def test_ring_power_ignores_the_ring_trigger_edge_but_accepts_next_edge(self):
        guard = self.make_guard()
        guard.ring_power_announcement_visible = True
        guard.joystick_press_sequence = 7
        guard.ring_power_ignore_press_sequence = 7
        guard.ring_power_ignore_until = 0.0
        hidden = []
        guard.hide_story_announcement = lambda: hidden.append(True)
        guard.maybe_show_pending_ring_announcement = lambda: None
        guard.write_status = lambda *args, **kwargs: None

        guard.handle_joystick_press("7")
        self.assertEqual(hidden, [])

        guard.joystick_press_sequence = 8
        guard.handle_joystick_press("8")
        self.assertEqual(hidden, [True])

    def test_ring_power_banner_is_hidden_when_burst_is_consumed(self):
        guard = self.make_guard()
        guard.ring_burst_active = True
        guard.ring_burst_origin = "story_robotnik"
        guard.ring_power_announcement_visible = True
        guard.guard_active = False
        hidden = []
        guard.hide_story_announcement = lambda: hidden.append(True)
        guard.write_status = lambda *args, **kwargs: None

        guard.consume_ring_burst_on_return()

        self.assertEqual(hidden, [True])
        self.assertFalse(guard.ring_burst_active)

    def test_recovery_text_leaves_energy_to_the_meter(self):
        guard = self.make_guard()

        recovery_text = guard.story_recovery_message(3)

        self.assertNotIn("CHAOS ENERGY:", recovery_text)

    def test_milestone_is_marked_shown_only_after_successful_display(self):
        guard = self.make_guard()
        guard.running = True
        guard.ring_count = 50
        guard.ring_milestones_shown = set()
        guard.ring_milestones_pending = {guard_module.RING_MILESTONE}
        guard.pending_ring_milestone = True
        guard.pending_ring_announcement = "milestone"
        guard.write_status = lambda *args, **kwargs: None
        saved = []
        guard.save_ring_state = lambda: saved.append(True)
        guard.show_plain_announcement = lambda *args, **kwargs: True
        guard.hide_story_announcement = lambda: None

        guard.maybe_show_pending_ring_announcement()

        self.assertTrue(guard.pending_ring_milestone)
        self.assertEqual(
            guard.ring_milestones_pending,
            {guard_module.RING_MILESTONE},
        )
        self.assertEqual(guard.ring_milestones_shown, set())
        self.assertEqual(saved, [])

        guard.maybe_show_pending_ring_announcement = lambda: None
        guard.complete_ring_milestone_announcement()

        self.assertFalse(guard.pending_ring_milestone)
        self.assertEqual(guard.ring_milestones_pending, set())
        self.assertEqual(
            guard.ring_milestones_shown,
            {guard_module.RING_MILESTONE},
        )
        self.assertEqual(saved, [True])

    def test_milestone_announcement_stays_visible_for_at_least_ten_seconds(self):
        guard = self.make_guard()
        guard.running = True
        guard.ring_count = 50
        guard.pending_ring_milestone = True
        guard.pending_ring_announcement = "milestone"
        guard.ring_milestones_pending = {guard_module.RING_MILESTONE}
        guard.ring_milestones_shown = set()
        guard.write_status = lambda *args, **kwargs: None
        guard.save_ring_state = lambda: None
        shown = []
        guard.show_plain_announcement = (
            lambda title, detail, color, duration, **kwargs: (
                shown.append(duration) or True
            )
        )

        guard.maybe_show_pending_ring_announcement()

        self.assertGreaterEqual(
            shown[0],
            guard_module.RING_MILESTONE_ANNOUNCEMENT_SECONDS,
        )
        self.assertGreaterEqual(shown[0], 10.0)

    def test_fiftieth_ring_shows_ring_power_after_full_prize_message(self):
        guard = self.make_guard()
        guard.active_ring_announcement_kind = "milestone"
        guard.pending_ring_milestone = True
        guard.pending_ring_announcement = None
        guard.ring_milestones_pending = {guard_module.RING_MILESTONE}
        guard.ring_milestones_shown = set()
        guard.ring_burst_active = True
        guard.ring_count = 50
        guard.save_ring_state = lambda: None
        guard.write_status = lambda *args, **kwargs: None
        guard.hide_story_announcement = lambda: None
        queued = []
        guard.maybe_show_pending_ring_announcement = (
            lambda: queued.append(guard.pending_ring_announcement)
        )

        guard.complete_ring_milestone_announcement()

        self.assertEqual(queued, ["burst"])

    def test_pending_milestone_survives_ring_state_reload(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_directory = Path(temporary_directory)
            guard = self.make_guard()
            guard.ring_counter_path = state_directory / "ring-counter.json"
            guard.ring_counter_backup_path = (
                state_directory / "ring-counter.backup.json"
            )
            guard.ring_count = 50
            guard.ring_milestones_shown = set()
            guard.ring_milestones_pending = {
                guard_module.RING_MILESTONE
            }
            guard.ring_state_warning = ""
            guard.write_status = lambda *args, **kwargs: None

            guard.save_ring_state()

            reloaded = self.make_guard()
            reloaded.ring_counter_path = guard.ring_counter_path
            reloaded.ring_counter_backup_path = (
                guard.ring_counter_backup_path
            )
            total, shown, pending = reloaded.load_ring_state()
            self.assertEqual(total, 50)
            self.assertEqual(shown, set())
            self.assertEqual(
                pending,
                {guard_module.RING_MILESTONE},
            )

    def test_ring_state_rejects_fractional_totals(self):
        with self.assertRaises(ValueError):
            guard_module.parse_ring_state_payload(
                {"total_rings": 49.5, "milestones_shown": []}
            )

    def test_ring_state_rejects_unknown_future_version(self):
        with self.assertRaisesRegex(ValueError, "unsupported ring state"):
            guard_module.parse_ring_state_payload(
                {
                    "version": guard_module.RING_STATE_VERSION + 1,
                    "total_rings": 50,
                    "milestones_shown": [],
                }
            )

    def test_legacy_ring_state_migrates_once_as_a_consistent_pair(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            legacy_directory = base / "legacy"
            target_directory = base / "new"
            legacy_directory.mkdir()
            (legacy_directory / "ring-counter.json").write_text(
                json.dumps(
                    {
                        "version": guard_module.RING_STATE_VERSION,
                        "total_rings": 42,
                        "milestones_shown": [],
                        "milestones_pending": [],
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(
                guard_module,
                "local_app_data_directory",
                return_value=legacy_directory,
            ):
                message = guard_module.migrate_legacy_persistent_state(
                    target_directory
                )

                self.assertIn("Migrated", message)
                primary = json.loads(
                    (target_directory / "ring-counter.json").read_text(
                        encoding="utf-8"
                    )
                )
                backup = json.loads(
                    (target_directory / "ring-counter.backup.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(primary, backup)
                self.assertEqual(primary["total_rings"], 42)

                primary["total_rings"] = 0
                guard_module.write_json_atomic(
                    target_directory / "ring-counter.json",
                    primary,
                )
                (target_directory / "ring-counter.backup.json").unlink()
                second_message = (
                    guard_module.migrate_legacy_persistent_state(
                        target_directory
                    )
                )

            self.assertEqual(second_message, "")
            retained = json.loads(
                (target_directory / "ring-counter.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(retained["total_rings"], 0)
            self.assertFalse(
                (target_directory / "ring-counter.backup.json").exists()
            )

    def test_ring_backup_keeps_previous_valid_generation(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_directory = Path(temporary_directory)
            guard = self.make_guard()
            guard.ring_counter_path = state_directory / "ring-counter.json"
            guard.ring_counter_backup_path = (
                state_directory / "ring-counter.backup.json"
            )
            guard.ring_milestones_shown = set()
            guard.ring_milestones_pending = set()
            guard.ring_state_warning = ""
            guard.write_status = lambda *args, **kwargs: None

            guard.ring_count = 1
            guard.save_ring_state()
            guard.ring_count = 2
            guard.save_ring_state()

            primary = json.loads(
                guard.ring_counter_path.read_text(encoding="utf-8")
            )
            backup = json.loads(
                guard.ring_counter_backup_path.read_text(encoding="utf-8")
            )
            self.assertEqual(primary["total_rings"], 2)
            self.assertEqual(backup["total_rings"], 1)

    def test_corrupt_primary_ring_state_recovers_from_backup(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_directory = Path(temporary_directory)
            primary = state_directory / "ring-counter.json"
            backup = state_directory / "ring-counter.backup.json"
            primary.write_text("{not valid json", encoding="utf-8")
            backup.write_text(
                '{"total_rings": 37, "milestones_shown": []}\n',
                encoding="utf-8",
            )
            guard = self.make_guard()
            guard.ring_counter_path = primary
            guard.ring_counter_backup_path = backup

            total, shown, pending = guard.load_ring_state()

            self.assertEqual((total, shown, pending), (37, set(), set()))
            self.assertIn("Recovered from backup", guard.ring_state_warning)
            self.assertIn("Primary file repaired", guard.ring_state_warning)
            repaired = json.loads(primary.read_text(encoding="utf-8"))
            self.assertEqual(repaired["total_rings"], 37)
            self.assertEqual(
                len(list(state_directory.glob("ring-counter.corrupt-*.json"))),
                1,
            )

    def test_ring_count_announcement_waits_when_big_box_is_not_safe(self):
        guard = self.make_guard()
        guard.running = True
        guard.ring_count = 13
        guard.pending_ring_milestone = False
        guard.pending_ring_announcement = "count"
        guard.write_status = lambda *args, **kwargs: None
        attempts = []
        guard.show_plain_announcement = (
            lambda *args, **kwargs: attempts.append((args, kwargs)) or False
        )

        guard.maybe_show_pending_ring_announcement()

        self.assertEqual(len(attempts), 1)
        self.assertEqual(guard.pending_ring_announcement, "count")

    def test_ring_announcement_never_draws_over_foreground_emulator(self):
        guard = self.make_guard()
        guard.overlay_visible = True
        guard.overlay_kind = "robotnik"
        guard.emulator_owns_foreground = lambda: True
        guard.can_show_story_announcement = lambda: self.fail(
            "Big Box safety check should not run over an emulator"
        )

        shown = guard.show_plain_announcement(
            guard_module.RING_COUNT_TITLE,
            "TOTAL RINGS: 8",
            "#ffdd55",
            5.0,
            allow_guard_overlay=True,
        )

        self.assertFalse(shown)

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
        guard.ring_burst_origin = "story_robotnik"
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

    def test_ring_burst_consumes_when_emulator_returns_before_commit(self):
        guard = self.make_guard()
        guard.ring_burst_active = True
        guard.ring_burst_game_seen = False
        guard.ring_burst_game_seen_since = 1.0
        guard.guard_active = True
        guard.foreground_process_name = guard_module.BIG_BOX_PROCESS_NAME
        guard.overlay_gate_state = "BIGBOX_READY"
        guard.guard_mode = "normal"
        guard.accepted_count = 3
        consumed = []
        statuses = []
        guard.update_overlay_gate = lambda: True
        guard.consume_ring_burst_on_return = (
            lambda: consumed.append(True)
        )
        guard.write_status = statuses.append

        guard.handle_ring_burst_foreground()

        self.assertEqual(consumed, [True])
        self.assertIn("BEFORE COMMIT", statuses[0])

    def test_final_return_during_ring_burst_resumes_victory(self):
        guard = self.make_guard()
        guard.ring_burst_active = True
        guard.ring_burst_game_seen = True
        guard.ring_burst_game_seen_since = 1.0
        guard.guard_active = True
        guard.accepted_count = guard_module.TOTAL_EMERALDS
        guard.guard_mode = "story"
        guard.story_intro_completed = True
        guard.ring_burst_origin = "story_robotnik"
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

    def test_normal_ring_burst_releases_lock_after_any_emerald_returns(self):
        guard = self.make_guard()
        guard.ring_burst_active = True
        guard.ring_burst_game_seen = True
        guard.ring_burst_game_seen_since = 1.0
        guard.ring_burst_origin = "normal_all_missing"
        guard.ring_power_announcement_visible = True
        guard.guard_active = True
        guard.guard_mode = "normal"
        guard.accepted_count = 2
        guard.normal_ring_lock_active = True
        guard.write_status = lambda *args, **kwargs: None
        guard.hide_story_announcement = lambda: None
        pending = []
        guard.maybe_show_pending_overlay = lambda: pending.append(True)

        guard.consume_ring_burst_on_return()

        self.assertFalse(guard.ring_burst_active)
        self.assertFalse(guard.normal_ring_lock_active)
        self.assertIsNone(getattr(guard, "pending_overlay_missing", None))
        self.assertEqual(pending, [])

    def test_normal_ring_burst_does_not_restore_lock_if_all_are_back(self):
        guard = self.make_guard()
        guard.ring_burst_active = True
        guard.ring_burst_game_seen = True
        guard.ring_burst_game_seen_since = 1.0
        guard.ring_burst_origin = "normal_all_missing"
        guard.ring_power_announcement_visible = True
        guard.guard_active = True
        guard.guard_mode = "normal"
        guard.accepted_count = guard_module.TOTAL_EMERALDS
        guard.normal_ring_lock_active = True
        guard.pending_overlay_missing = 7
        guard.write_status = lambda *args, **kwargs: None
        guard.hide_story_announcement = lambda: None

        guard.consume_ring_burst_on_return()

        self.assertFalse(guard.normal_ring_lock_active)
        self.assertIsNone(guard.pending_overlay_missing)

    def test_normal_ring_burst_ends_when_any_energy_returns_at_big_box(self):
        guard = self.make_guard()
        guard.ring_burst_active = True
        guard.ring_burst_game_seen = False
        guard.ring_burst_game_seen_since = 0.0
        guard.ring_burst_origin = "normal_all_missing"
        guard.guard_active = True
        guard.guard_mode = "normal"
        guard.accepted_count = 1
        guard.foreground_process_name = guard_module.BIG_BOX_PROCESS_NAME
        guard.overlay_gate_state = "BIGBOX_READY"
        guard.update_overlay_gate = lambda: True
        consumed = []
        guard.consume_ring_burst_on_return = lambda: consumed.append(True)

        guard.handle_ring_burst_foreground()

        self.assertEqual(consumed, [True])

    def test_normal_ring_lock_releases_immediately_when_all_are_returned(self):
        guard = self.make_guard()
        guard.ring_burst_active = False
        guard.normal_ring_lock_active = True
        guard.overlay_kind = "robotnik"
        guard.pending_overlay_missing = 2
        events = []
        guard.hide_overlay = lambda: events.append("hide")
        guard.play_emerald_sound = lambda: events.append("sound")

        guard.handle_normal_count_change(6, 7)

        self.assertFalse(guard.normal_ring_lock_active)
        self.assertIsNone(guard.pending_overlay_missing)
        self.assertEqual(events, ["hide", "sound"])

    def test_power_loss_fails_open_if_big_box_cannot_be_paused(self):
        guard = self.make_guard()
        guard.overlay_visible = False
        guard.hide_story_announcement = lambda: None
        guard.capture_return_window = lambda: None
        guard.prepare_overlay_monitor = lambda: None
        guard.suspend_return_process = lambda: False
        faults = []
        guard.fault_disable_guard = faults.append

        self.assertFalse(guard.show_power_loss_takeover())
        self.assertEqual(faults, ["Could not safely pause Big Box"])

    def test_power_loss_fails_open_if_big_box_audio_cannot_be_muted(self):
        guard = self.make_guard()
        guard.overlay_visible = True
        guard.hide_story_announcement = lambda: None
        guard.mute_other_audio = lambda: False
        faults = []
        guard.fault_disable_guard = faults.append

        self.assertFalse(guard.show_power_loss_takeover())
        self.assertEqual(faults, ["Could not mute background audio"])

    def test_topmost_watchdog_keeps_black_root_hidden_during_power_loss(self):
        guard = self.make_guard()
        guard.running = True
        guard.overlay_visible = True
        guard.overlay_kind = "story_power_loss"
        guard.root = MagicMock()

        guard.keep_window_on_top()

        guard.root.withdraw.assert_called_once_with()
        guard.root.overrideredirect.assert_not_called()
        guard.root.after.assert_called_once_with(500, guard.keep_window_on_top)

    def test_stale_takeover_callbacks_do_nothing_after_guard_is_disabled(self):
        guard = self.make_guard()
        guard.running = True
        guard.guard_active = False

        self.assertFalse(
            guard.show_text_takeover("TITLE", "MESSAGE", "story_shutdown")
        )
        self.assertIsNone(guard.show_missing_overlay(7))

    def test_emulator_foreground_always_hides_ring_power_banner(self):
        guard = self.make_guard()
        guard.ring_burst_active = True
        guard.ring_burst_game_seen = False
        guard.ring_burst_game_seen_since = 0.0
        guard.ring_power_announcement_visible = True
        guard.announcement_after_id = None
        guard.guard_active = True
        guard.foreground_process_name = "retroarch.exe"
        guard.update_overlay_gate = lambda: False
        events = []
        guard.hide_story_announcement = lambda: events.append("hide")
        guard.write_status = lambda message: events.append(message)

        with patch.object(time, "monotonic", return_value=100.0):
            guard.handle_ring_burst_foreground()

        self.assertEqual(events[0], "hide")
        self.assertEqual(guard.ring_burst_game_seen_since, 100.0)

    def test_focus_retry_stops_when_ring_power_game_has_foreground(self):
        guard = self.make_guard()
        guard.return_window_handle = 100
        guard.ring_burst_active = True
        guard.write_status = lambda *args, **kwargs: None
        guard.get_window_process_name = lambda handle: "retroarch.exe"
        user32 = MagicMock()
        user32.GetForegroundWindow.return_value = 200
        fake_windll = MagicMock()
        fake_windll.user32 = user32
        fake_windll.kernel32 = MagicMock()

        with patch.object(guard_module.ctypes, "windll", fake_windll):
            guard.restore_return_window()

        self.assertEqual(guard.return_window_handle, 0)
        user32.BringWindowToTop.assert_not_called()

    def test_interrupted_milestone_is_requeued_instead_of_acknowledged(self):
        guard = self.make_guard()
        guard.active_ring_announcement_kind = "milestone"
        guard.pending_ring_milestone = True
        guard.pending_ring_announcement = None
        guard.ring_milestones_pending = {guard_module.RING_MILESTONE}
        guard.ring_milestones_shown = set()
        guard.ring_power_announcement_visible = False
        guard.announcement_after_id = "timer"
        guard.root = MagicMock()
        guard.announcement_window = MagicMock()
        guard.hide_announcement_flash = lambda: None
        guard.save_ring_state = lambda: None
        guard.write_status = lambda *args, **kwargs: None

        guard.hide_story_announcement()

        self.assertTrue(guard.pending_ring_milestone)
        self.assertEqual(guard.pending_ring_announcement, "milestone")
        self.assertNotIn(
            guard_module.RING_MILESTONE,
            guard.ring_milestones_shown,
        )

    def test_emulator_transition_requeues_and_hides_active_milestone(self):
        guard = self.make_guard()
        guard.active_ring_announcement_kind = "milestone"
        guard.pending_ring_milestone = False
        guard.pending_ring_announcement = None
        guard.ring_milestones_pending = set()
        guard.ring_milestones_shown = set()
        guard.ring_power_announcement_visible = False
        guard.announcement_after_id = "timer"
        guard.root = MagicMock()
        guard.announcement_window = MagicMock()
        guard.hide_announcement_flash = MagicMock()
        guard.set_announcement_energy_meter = MagicMock()
        guard.stop_event_sound = MagicMock()
        guard.save_ring_state = MagicMock()
        guard.write_status = MagicMock()

        guard.defer_announcement_for_emulator()

        self.assertIsNone(guard.active_ring_announcement_kind)
        self.assertTrue(guard.pending_ring_milestone)
        self.assertEqual(guard.pending_ring_announcement, "milestone")
        self.assertIn(
            guard_module.RING_MILESTONE,
            guard.ring_milestones_pending,
        )
        guard.root.after_cancel.assert_called_once_with("timer")
        guard.announcement_window.withdraw.assert_called_once_with()
        guard.stop_event_sound.assert_called_once_with()
        guard.save_ring_state.assert_called_once_with()

    def test_noncritical_ring_worker_failure_does_not_become_guard_fault(self):
        guard = self.make_guard()
        guard.messages = queue.Queue()
        guard.activation_generation = 4

        def broken_worker():
            raise RuntimeError("joystick unavailable")

        guard.worker_entry("ring input", broken_worker, False)

        message_type, message, generation = guard.messages.get_nowait()
        self.assertEqual(message_type, "SERVICE_FAULT")
        self.assertIn("joystick unavailable", message)
        self.assertEqual(generation, 4)

    def test_serial_worker_death_is_not_discarded_as_stale(self):
        guard = self.make_guard()
        guard.messages = queue.Queue()
        guard.messages.put(
            ("CORE_SERVICE_FAULT", "ESP32 serial worker stopped", 1)
        )
        guard.activation_generation = 9
        guard.serial_worker_failed = False
        guard.service_warning = ""
        guard.running = True
        guard.root = MagicMock()
        failures = []
        guard.fault_disable_guard = failures.append
        guard.accept_stable_count = lambda: None

        guard.process_messages()

        self.assertTrue(guard.serial_worker_failed)
        self.assertEqual(failures, ["ESP32 serial worker stopped"])

    def test_dead_ring_input_service_is_scheduled_for_restart(self):
        guard = self.make_guard()
        guard.running = True
        guard.ring_input_stop_event = MagicMock()
        guard.ring_input_stop_event.is_set.return_value = False
        guard.ring_input_restart_after_id = None
        guard.ring_input_restart_count = 0
        guard.root = MagicMock()
        guard.root.after.return_value = "ring-restart"

        guard.schedule_ring_input_restart()

        self.assertEqual(guard.ring_input_restart_after_id, "ring-restart")

    def test_ring_message_error_does_not_disable_uncovered_guard(self):
        guard = self.make_guard()
        guard.messages = queue.Queue()
        guard.messages.put(("RING", "0", -1))
        guard.activation_generation = 2
        guard.guard_active = True
        guard.ring_burst_active = False
        guard.suspended_process_handle = None
        guard.running = True
        guard.root = MagicMock()
        guard.handle_ring_entry = lambda: (_ for _ in ()).throw(
            RuntimeError("ring banner failed")
        )
        recovered = []
        guard.recover_ring_ui_error = (
            lambda context, error: recovered.append((context, str(error)))
        )
        guard.write_status = lambda *args, **kwargs: None
        guard.accept_stable_count = lambda: None

        guard.process_messages()

        self.assertTrue(guard.guard_active)
        self.assertEqual(recovered, [("ring", "ring banner failed")])

    def test_activation_waits_until_old_overlay_side_effects_are_released(self):
        guard = self.make_guard()
        guard.guard_active = False
        guard.running = True
        guard.suspended_process_handle = 123
        guard.audio_muted = False
        guard.pending_guard_activation = False
        guard.activation_retry_after_id = None
        guard.overlay_gate_state = "DORMANT"
        guard.root = MagicMock()
        guard.root.after.return_value = "activation-retry"
        guard.write_status = lambda *args, **kwargs: None

        guard.activate_guard()

        self.assertFalse(guard.guard_active)
        self.assertTrue(guard.pending_guard_activation)
        self.assertEqual(guard.overlay_gate_state, "WAITING_FOR_CLEANUP")
        self.assertEqual(
            guard.activation_retry_after_id,
            "activation-retry",
        )

    def test_selecting_already_active_mode_does_not_reset_sensor_state(self):
        guard = self.make_guard()
        guard.running = True
        guard.guard_active = True
        guard.guard_mode = "normal"
        guard.accepted_count = 4
        guard.write_status = lambda *args, **kwargs: None
        guard._deactivate_guard = lambda reason: self.fail(
            "active mode should not restart"
        )

        guard.select_guard_mode("normal")

        self.assertEqual(guard.accepted_count, 4)

    def test_nonblocking_tk_error_keeps_sensor_guard_active(self):
        guard = self.make_guard()
        guard.guard_active = True
        guard.overlay_visible = False
        guard.suspended_process_handle = None
        guard.service_warning = ""
        guard.write_status = lambda *args, **kwargs: None
        guard.fault_disable_guard = lambda reason: self.fail(reason)

        error = RuntimeError("panel refresh failed")
        guard.handle_tk_exception(RuntimeError, error, error.__traceback__)

        self.assertTrue(guard.guard_active)
        self.assertIn("panel refresh failed", guard.service_warning)

    def test_overlay_cleanup_resumes_input_before_restoring_audio(self):
        guard = self.make_guard()
        guard.overlay_visible = True
        guard.overlay_kind = "robotnik"
        guard.running = True
        guard.guard_active = False
        guard.ring_burst_active = False
        guard.last_fault = ""
        guard.reader_connected = False
        guard.audio_restore_retry_after_id = None
        guard.audio_restore_retry_attempt = 0
        guard.root = MagicMock()
        order = []
        guard.hide_story_announcement = lambda: None
        guard.cancel_audio_watchdog = lambda: None
        guard.hide_energy_meter = lambda: None
        guard.set_control_panel_visible = lambda visible: None
        guard.reset_counter_style = lambda: None
        guard.stop_music = lambda: None
        guard.stop_event_sound = lambda: None
        guard.set_overlay_z_order = lambda topmost: None
        guard.resume_and_restore_return_window = lambda: order.append("resume")
        guard.restore_other_audio_with_retries = (
            lambda: order.append("audio") or True
        )

        guard.hide_overlay()

        self.assertEqual(order, ["resume", "audio"])

    def test_cinematic_with_no_frames_fails_open_instead_of_waiting_forever(self):
        guard = self.make_guard()
        guard.running = True
        guard.guard_active = True
        guard.overlay_visible = True
        guard.overlay_kind = "cinematic"
        guard.cinematic_worker_error = ""
        guard.cinematic_pending_frame = None
        guard.cinematic_frame_queue = queue.Queue()
        guard.cinematic_started_at = 0.0
        guard.cinematic_wait_started_at = time.monotonic()
        guard.cinematic_worker_done = True
        guard.cinematic_after_id = None
        failures = []
        guard.fault_disable_guard = failures.append

        guard.poll_cinematic_playback()

        self.assertEqual(
            failures,
            ["Sonic cinematic produced no playable video frames"],
        )

    def test_story_cinematic_skip_is_armed_for_active_heist(self):
        guard = self.make_guard()
        guard.running = True
        guard.guard_mode = "story"
        guard.story_cycle_started = True
        guard.overlay_kind = "story_eggman"
        guard.skip_cinematic_requested = False
        statuses = []
        guard.write_status = statuses.append

        guard.skip_story_cinematic()

        self.assertTrue(guard.skip_cinematic_requested)
        self.assertEqual(
            statuses,
            ["CINEMATIC SKIP ARMED | STORY HEIST"],
        )

    def test_story_cinematic_skip_bypasses_preparation(self):
        guard = self.make_guard()
        guard.running = True
        guard.guard_active = True
        guard.guard_mode = "story"
        guard.story_cycle_started = True
        guard.skip_cinematic_requested = True
        guard.cinematic_prepare_state = "preparing"
        guard.story_sequence_after_id = object()
        guard.show_story_robotnik_screen = MagicMock()
        guard.write_status = MagicMock()

        guard.start_story_cinematic()

        self.assertFalse(guard.skip_cinematic_requested)
        guard.show_story_robotnik_screen.assert_called_once_with()
        guard.write_status.assert_called_once_with(
            "CINEMATIC SKIPPED | ROBOTNIK SCREEN ACTIVE"
        )

    def test_final_emerald_sound_timeout_does_not_hold_victory_forever(self):
        guard = self.make_guard()
        guard.final_emerald_after_id = None
        guard.guard_active = True
        guard.overlay_visible = True
        guard.accepted_count = guard_module.TOTAL_EMERALDS
        guard.controller_lost = False
        guard.final_emerald_sound_started = True
        guard.final_emerald_wait_started_at = 10.0
        guard.final_emerald_pause_started_at = None
        guard.event_channel_busy = lambda: True
        guard.root = MagicMock()
        events = []
        guard.stop_event_sound = lambda: events.append("stopped")
        guard.write_status = lambda *args, **kwargs: None

        with patch.object(time, "monotonic", return_value=100.0):
            guard.wait_for_final_emerald_transition()

        self.assertEqual(events, ["stopped"])

    def test_victory_animation_timeout_forces_completion_progress(self):
        guard = self.make_guard()
        guard.completion_after_id = None
        guard.completion_in_progress = True
        guard.completion_started_at = 0.0
        guard.completion_audio_playing = False
        guard.completion_animation_finished = False
        guard.final_completion_sound_started = False
        guard.final_completion_sound_playing = False
        guard.final_completion_sound = None
        guard.write_status = lambda *args, **kwargs: None
        guard.start_supersonic_animation = lambda: setattr(
            guard,
            "completion_animation_finished",
            True,
        )
        guard.play_event_sound = lambda *args, **kwargs: False
        completed = []
        guard.finish_completion = lambda: completed.append(True)

        with patch.object(
            time,
            "monotonic",
            return_value=guard_module.COMPLETION_MAX_SECONDS + 1,
        ):
            guard.wait_for_completion_audio()

        self.assertEqual(completed, [True])

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

        self.assertEqual(set(messages), set(range(1, 8)))
        self.assertEqual(
            len({title for title, detail in messages.values()}),
            7,
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

    def test_malformed_serial_message_does_not_refresh_heartbeat(self):
        guard = self.make_guard()
        guard.guard_active = True
        guard.reader_connected = False
        guard.controller_lost = True
        guard.last_valid_message = 10.0
        guard.last_serial_message_at = 10.0
        guard.pending_count = None

        guard.handle_serial_message("MAGNET_LOCK:COUNT:corrupt")

        self.assertFalse(guard.reader_connected)
        self.assertEqual(guard.last_valid_message, 10.0)
        self.assertEqual(guard.last_serial_message_at, 10.0)

    def test_silent_controller_times_out_after_activation(self):
        guard = self.make_guard()
        guard.running = False
        guard.guard_active = True
        guard.reader_connected = False
        guard.activation_started_at = 100.0
        reasons = []
        guard.handle_disconnect = reasons.append

        with patch.object(
            time,
            "monotonic",
            return_value=(
                100.0
                + guard_module.INITIAL_CONNECTION_TIMEOUT_SECONDS
                + 0.1
            ),
        ):
            guard.connection_watchdog()

        self.assertEqual(
            reasons,
            ["ESP32 did not respond after guard activation"],
        )

    def test_controller_startup_grace_period_does_not_disable_early(self):
        guard = self.make_guard()
        guard.running = False
        guard.guard_active = True
        guard.reader_connected = False
        guard.activation_started_at = 100.0
        guard.handle_disconnect = self.fail

        with patch.object(time, "monotonic", return_value=101.0):
            guard.connection_watchdog()

    def test_ready_message_clears_controller_startup_deadline(self):
        guard = self.make_guard()
        guard.guard_active = True
        guard.reader_connected = False
        guard.controller_lost = True
        guard.activation_started_at = 100.0

        with patch.object(time, "monotonic", return_value=101.0):
            guard.handle_serial_message("MAGNET_LOCK:READY")

        self.assertEqual(guard.activation_started_at, 0.0)

    def test_invalid_sensor_count_configuration_blocks_activation(self):
        guard = self.make_guard()
        guard.guard_mode = "normal"
        with (
            patch.object(
                guard_module,
                "CONFIG_VALIDATION_ERRORS",
                ["total_emeralds must be 7"],
            ),
            patch.object(guard_module, "PYCAW_AVAILABLE", True),
        ):
            self.assertIn(
                "total_emeralds must be 7",
                guard.guard_readiness_error(),
            )

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
        guard.show_normal_restored_announcement = lambda count: True
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
        guard.show_normal_restored_announcement = lambda count: True
        guard.play_emerald_sound = lambda: returns.append(True)

        guard.handle_normal_count_change(3, 4)

        self.assertEqual(finished, [True])
        self.assertEqual(returns, [True])

    def test_normal_warning_text_is_concise(self):
        self.assertEqual(
            guard_module.NORMAL_WARNING_MESSAGE,
            "Hey! Put that back!",
        )
        self.assertNotIn("We already did the thing", guard_module.NORMAL_WARNING_MESSAGE)

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

    def test_normal_mode_final_removal_uses_all_missing_lock(self):
        guard = self.make_guard()
        guard.ring_burst_active = False
        guard.normal_ring_lock_active = False
        guard.overlay_kind = None
        events = []
        guard.show_normal_all_missing_overlay = (
            lambda: events.append("all_missing")
        )

        guard.handle_normal_count_change(1, 0)

        self.assertEqual(events, ["all_missing"])

    def test_normal_all_missing_meter_uses_zero_present_emeralds(self):
        guard = self.make_guard()
        guard.normal_warning_after_id = None
        guard.normal_ring_lock_active = False
        guard.pending_overlay_missing = None
        guard.guard_active = False
        guard.cancel_normal_warning = lambda: None
        guard.show_missing_overlay = MagicMock()

        guard.show_normal_all_missing_overlay()

        guard.show_missing_overlay.assert_called_once_with(
            guard_module.TOTAL_EMERALDS,
            message=guard_module.NORMAL_ALL_MISSING_MESSAGE,
        )

    def test_story_robotnik_screen_uses_ring_message_below_meter(self):
        guard = self.make_guard()
        guard.guard_active = True
        guard.guard_mode = "story"
        guard.accepted_count = 3
        guard.show_missing_overlay = MagicMock()
        guard.set_robotnik_title = lambda text: None

        guard.show_story_robotnik_screen()

        guard.show_missing_overlay.assert_called_once_with(
            guard_module.TOTAL_EMERALDS - 3,
            message=guard_module.STORY_ROBOTNIK_MESSAGE,
        )

    def test_normal_mode_restoration_uses_dedicated_announcement(self):
        guard = self.make_guard()
        guard.guard_active = True
        guard.guard_mode = "normal"
        shown = []
        guard.show_story_announcement = (
            lambda count, kind, duration_seconds=None: shown.append(
                (count, kind, duration_seconds)
            ) or True
        )

        self.assertTrue(guard.show_normal_restored_announcement(6))
        self.assertEqual(
            shown,
            [
                (
                    6,
                    "restored_normal",
                    guard_module.STORY_ANNOUNCEMENT_SECONDS,
                )
            ],
        )

    def test_normal_return_sound_waits_for_a_safe_menu_announcement(self):
        guard = self.make_guard()
        guard.overlay_kind = None
        guard.normal_warning_trigger_count = None
        guard.normal_ring_lock_active = False
        guard.ring_burst_active = False
        guard.defer_count_change_for_milestone = lambda *args: False
        guard.show_normal_restored_announcement = lambda count: False
        guard.play_emerald_sound = MagicMock()

        guard.handle_normal_count_change(3, 4)

        guard.play_emerald_sound.assert_not_called()

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
        first_handles, first_allowed = (
            guard_module.acquire_single_instance_mutex()
        )
        second_handles, second_allowed = (
            guard_module.acquire_single_instance_mutex()
        )
        try:
            self.assertEqual(len(first_handles), 2)
            self.assertTrue(first_allowed)
            self.assertEqual(second_handles, ())
            self.assertFalse(second_allowed)
        finally:
            guard_module.release_mutex_handles(second_handles)
            guard_module.release_mutex_handles(first_handles)

    def test_legacy_guard_mutex_blocks_chaos_heist(self):
        guard_module.configure_windows_runtime()
        kernel32 = guard_module.ctypes.windll.kernel32
        legacy_handle = kernel32.CreateMutexW(
            None,
            False,
            guard_module.SINGLE_INSTANCE_MUTEX_NAMES[0],
        )
        try:
            handles, allowed = guard_module.acquire_single_instance_mutex()
            self.assertEqual(handles, ())
            self.assertFalse(allowed)
        finally:
            if legacy_handle:
                kernel32.CloseHandle(legacy_handle)


if __name__ == "__main__":
    unittest.main()
