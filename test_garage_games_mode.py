import unittest

import garage_games_mode as gg

TOKEN = "0123456789ABCDEF"
OTHER = "FEDCBA9876543210"


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def sends(commands):
    return [command for command in commands if command[0] == "send"]


class GarageStationProtocolTests(unittest.TestCase):
    def test_station_lines_parse_strictly(self):
        parse = gg.parse_garage_station_message
        self.assertEqual(
            parse(f"MAGNET_LOCK:GG:RUN:{TOKEN}:ARMED"),
            ("run", TOKEN, "ARMED"),
        )
        self.assertEqual(parse("MAGNET_LOCK:GG:RUN:-:NONE"), ("run", None, "NONE"))
        self.assertEqual(parse("MAGNET_LOCK:GG:RUN:-:STALE"), ("run", None, "STALE"))
        self.assertEqual(
            parse(f"MAGNET_LOCK:GG:EVENT:{TOKEN}:ACTIVE"),
            ("event", TOKEN, "ACTIVE"),
        )
        self.assertEqual(
            parse(f"MAGNET_LOCK:GG:RESULT:{TOKEN}:S:42:ACTIVE"),
            ("result", TOKEN, "S", 42, "ACTIVE"),
        )
        self.assertEqual(
            parse(f"MAGNET_LOCK:GG:NOACK:{TOKEN}:F:43:TIMEOUT"),
            ("noack", TOKEN, "F", 43, "TIMEOUT"),
        )
        self.assertEqual(
            parse("MAGNET_LOCK:GG:STATION:A0B1C2D3E4F5:READY"),
            ("station", "A0B1C2D3E4F5", "READY"),
        )
        self.assertEqual(parse("MAGNET_LOCK:GG:IDENT:SENT"), ("ident", "SENT"))

        for bad in (
            "MAGNET_LOCK:COUNT:3",
            f"MAGNET_LOCK:GG:RUN:{TOKEN}:NONE",
            "MAGNET_LOCK:GG:RUN:-:ACTIVE",
            "MAGNET_LOCK:GG:RUN:0123456789abcdef:ACTIVE",
            f"MAGNET_LOCK:GG:RUN:{TOKEN}:RUNNING",
            f"MAGNET_LOCK:GG:RESULT:{TOKEN}:X:42:ACTIVE",
            f"MAGNET_LOCK:GG:RESULT:{TOKEN}:S:0:ACTIVE",
            f"MAGNET_LOCK:GG:RESULT:{TOKEN}:S:42:MAYBE",
            f"MAGNET_LOCK:GG:RESULT:{TOKEN}:S::ACTIVE",
            "MAGNET_LOCK:GG:STATION:a0b1c2d3e4f5:READY",
            f"MAGNET_LOCK:GG:EVENT:{TOKEN}:ACTIVE:extra",
            None,
        ):
            self.assertIsNone(parse(bad), bad)

    def test_send_command_is_bounded(self):
        self.assertEqual(
            gg.format_send_command(TOKEN, "S", 7, 120),
            f"GG:SEND:{TOKEN}:S:7:120\n",
        )
        self.assertEqual(
            gg.format_send_command(TOKEN, "F", 8, 999_999),
            f"GG:SEND:{TOKEN}:F:8:{gg.MAX_SIGNAL_AGE_MS}\n",
        )
        with self.assertRaises(ValueError):
            gg.format_send_command("-", "S", 1, 0)


class GarageRunControllerTests(unittest.TestCase):
    def make(self, **kwargs):
        self.clock = FakeClock()
        controller = gg.GarageRunController(
            sequence_base=500,
            clock=self.clock,
            zero_confirm_seconds=1.0,
            **kwargs,
        )
        return controller

    def ready_to_place(self, controller, token=TOKEN, state="ACTIVE"):
        controller.handle_count(0)
        controller.handle_run_status(token, state)
        self.clock.advance(1.0)
        controller.tick()
        self.assertEqual(controller.stage, gg.STAGE_PLACING)
        controller.take_commands()

    def place_all(self, controller):
        for count in range(1, 8):
            controller.handle_count(count)

    def test_new_run_shows_robotnik_and_requires_confirmed_zero(self):
        controller = self.make()
        controller.handle_count(7)
        self.assertEqual(controller.view().kind, gg.VIEW_NONE)

        controller.handle_run_status(TOKEN, "ARMED")
        self.assertIn(("new_run", TOKEN), controller.take_commands())
        view = controller.view()
        self.assertEqual(view.kind, gg.VIEW_ROBOTNIK)
        self.assertTrue(view.warning)
        self.assertIn("REMOVE ALL CHAOS EMERALDS", view.headline)

        # Leftover emeralds from the last run never start anything.
        controller.handle_run_status(TOKEN, "ACTIVE")
        self.clock.advance(5)
        controller.tick()
        self.assertEqual(sends(controller.take_commands()), [])
        self.assertEqual(controller.stage, gg.STAGE_RESET)

        # Zero must hold for the confirmation time before placements count.
        controller.handle_count(0)
        self.assertEqual(controller.view().headline, "CHECKING THE SHRINE...")
        self.clock.advance(0.5)
        controller.handle_count(1)
        controller.handle_count(0)
        self.clock.advance(0.9)
        controller.tick()
        self.assertEqual(controller.stage, gg.STAGE_RESET)
        self.clock.advance(0.2)
        controller.tick()
        self.assertEqual(controller.stage, gg.STAGE_PLACING)
        view = controller.view()
        self.assertEqual(view.kind, gg.VIEW_ROBOTNIK)
        self.assertEqual(view.emeralds, 0)
        self.assertFalse(view.warning)

    def test_unknown_sensor_count_waits(self):
        controller = self.make()
        controller.handle_run_status(TOKEN, "ARMED")
        self.assertEqual(
            controller.view().headline,
            "WAITING FOR THE EMERALD SENSORS",
        )
        self.clock.advance(10)
        controller.tick()
        self.assertEqual(controller.stage, gg.STAGE_RESET)

    def test_seventh_emerald_sends_start_once_and_switches_to_rings(self):
        controller = self.make()
        self.ready_to_place(controller)
        for count in range(1, 7):
            controller.handle_count(count)
            self.assertEqual(controller.view().emeralds, count)
        self.assertEqual(sends(controller.take_commands()), [])

        controller.handle_count(7)
        self.assertEqual(
            sends(controller.take_commands()),
            [("send", TOKEN, "S", 500, 0)],
        )
        view = controller.view()
        self.assertEqual(view.kind, gg.VIEW_RINGS)
        self.assertEqual(view.rings, 0)
        self.assertEqual(view.ring_goal, 20)

        # The started notifications for the same run never reset it.
        controller.handle_run_status(TOKEN, "ACTIVE")
        controller.handle_result(TOKEN, "S", 500, "ACTIVE")
        controller.handle_event_state(TOKEN, "ACTIVE")
        controller.handle_run_status(TOKEN, "ACTIVE")
        commands = controller.take_commands()
        self.assertNotIn(("new_run", TOKEN), commands)
        self.assertEqual(sends(commands), [])
        self.assertEqual(controller.stage, gg.STAGE_RINGS)

        # Emerald changes no longer matter once rings have begun.
        controller.handle_count(3)
        controller.handle_count(7)
        self.assertEqual(sends(controller.take_commands()), [])
        self.assertEqual(controller.stage, gg.STAGE_RINGS)

    def test_twentieth_ring_finishes_once_then_victory(self):
        controller = self.make()
        self.ready_to_place(controller)
        self.place_all(controller)
        controller.handle_result(TOKEN, "S", 500, "ACTIVE")
        controller.take_commands()

        for _ in range(19):
            controller.handle_ring()
        self.assertEqual(controller.view().rings, 19)
        self.assertEqual(controller.take_commands(), [])

        self.clock.advance(2)
        controller.handle_ring()
        self.assertEqual(
            controller.take_commands(),
            [("send", TOKEN, "F", 501, 0), ("victory",)],
        )
        self.assertEqual(controller.view().kind, gg.VIEW_VICTORY)

        for _ in range(5):
            controller.handle_ring()
        controller.tick()
        self.assertEqual(controller.take_commands(), [])
        controller.handle_result(TOKEN, "F", 501, "COMPLETED")
        controller.finish_presentation()
        self.assertEqual(controller.view().kind, gg.VIEW_NONE)

    def test_rings_before_the_transition_do_not_count(self):
        controller = self.make()
        controller.handle_ring()
        self.ready_to_place(controller)
        controller.handle_ring()
        controller.handle_count(7)
        self.assertEqual(controller.view().rings, 0)
        controller.handle_ring()
        self.assertEqual(controller.view().rings, 1)

    def test_custom_ring_goal(self):
        controller = self.make(ring_goal=3)
        self.ready_to_place(controller)
        controller.handle_count(7)
        controller.handle_result(TOKEN, "S", 500, "ACTIVE")
        controller.take_commands()
        for _ in range(3):
            controller.handle_ring()
        self.assertIn(("victory",), controller.take_commands())

    def test_seven_before_the_run_starts_requires_a_replace(self):
        controller = self.make()
        self.ready_to_place(controller, state="ARMED")
        self.place_all(controller)
        self.assertEqual(sends(controller.take_commands()), [])
        view = controller.view()
        self.assertTrue(view.warning)
        self.assertIn("Wait for the run to start", view.detail)

        controller.handle_run_status(TOKEN, "COUNTDOWN")
        controller.handle_run_status(TOKEN, "ACTIVE")
        self.assertEqual(sends(controller.take_commands()), [])
        self.assertIn("Remove and replace one emerald", controller.view().detail)

        controller.handle_count(6)
        controller.handle_count(7)
        self.assertEqual(
            sends(controller.take_commands()),
            [("send", TOKEN, "S", 500, 0)],
        )

    def test_paused_run_holds_the_start(self):
        controller = self.make()
        self.ready_to_place(controller)
        controller.handle_run_status(TOKEN, "PAUSED")
        self.place_all(controller)
        self.assertEqual(sends(controller.take_commands()), [])
        self.assertTrue(controller.need_replace)

    def test_rejected_start_returns_to_placing_and_discards_rings(self):
        controller = self.make()
        self.ready_to_place(controller)
        self.place_all(controller)
        controller.handle_ring()
        controller.handle_result(TOKEN, "S", 500, "REJECTED")
        self.assertEqual(controller.stage, gg.STAGE_PLACING)
        self.assertEqual(controller.rings, 0)
        self.assertTrue(controller.view().warning)
        controller.take_commands()
        controller.handle_count(6)
        controller.handle_count(7)
        self.assertEqual(
            sends(controller.take_commands()),
            [("send", TOKEN, "S", 501, 0)],
        )

    def test_lost_acknowledgement_resends_the_same_sequence(self):
        controller = self.make()
        self.ready_to_place(controller)
        self.place_all(controller)
        controller.take_commands()
        self.clock.advance(6)
        controller.handle_noack(TOKEN, "S", 500, "TIMEOUT")
        self.assertEqual(
            sends(controller.take_commands()),
            [("send", TOKEN, "S", 500, 6000)],
        )
        controller.handle_noack(TOKEN, "S", 500, "TIMEOUT")
        controller.take_commands()
        controller.handle_noack(TOKEN, "S", 500, "TIMEOUT")
        self.assertEqual(sends(controller.take_commands()), [])
        self.assertEqual(controller.stage, gg.STAGE_PLACING)
        self.assertIn("No answer from Garage Games", controller.view().detail)

    def test_silent_station_times_out_into_a_resend(self):
        controller = self.make()
        self.ready_to_place(controller)
        self.place_all(controller)
        controller.take_commands()
        self.clock.advance(gg.SIGNAL_REPLY_TIMEOUT_SECONDS - 0.1)
        controller.tick()
        self.assertEqual(sends(controller.take_commands()), [])
        self.clock.advance(0.2)
        controller.tick()
        resent = sends(controller.take_commands())
        self.assertEqual([c[:4] for c in resent], [("send", TOKEN, "S", 500)])

    def test_garage_event_state_confirms_a_start_whose_reply_was_lost(self):
        controller = self.make()
        self.ready_to_place(controller)
        self.place_all(controller)
        for _ in range(20):
            controller.handle_ring()
        commands = controller.take_commands()
        self.assertEqual(sends(commands), [("send", TOKEN, "S", 500, 0)])
        self.assertIn(("victory",), commands)
        # The finish waits for Garage Games to confirm the start.
        self.clock.advance(1.5)
        controller.handle_event_state(TOKEN, "ACTIVE")
        self.assertEqual(
            sends(controller.take_commands()),
            [("send", TOKEN, "F", 501, 1500)],
        )
        controller.handle_event_state(TOKEN, "COMPLETED")
        self.assertEqual(controller.finish.state, "confirmed")

    def test_finish_rejected_while_paused_is_resent_on_resume(self):
        controller = self.make()
        self.ready_to_place(controller)
        self.place_all(controller)
        controller.handle_result(TOKEN, "S", 500, "ACTIVE")
        for _ in range(19):
            controller.handle_ring()
        controller.handle_run_status(TOKEN, "PAUSED")
        controller.take_commands()
        controller.handle_ring()
        self.assertEqual(
            sends(controller.take_commands()),
            [("send", TOKEN, "F", 501, 0)],
        )
        controller.handle_result(TOKEN, "F", 501, "REJECTED")
        self.assertIn("paused", controller.notice.lower())
        self.clock.advance(30)
        controller.handle_run_status(TOKEN, "PAUSED")
        self.assertEqual(sends(controller.take_commands()), [])
        controller.handle_run_status(TOKEN, "ACTIVE")
        self.assertEqual(
            sends(controller.take_commands()),
            [("send", TOKEN, "F", 502, 0)],
        )
        controller.handle_result(TOKEN, "F", 502, "COMPLETED")
        controller.handle_run_status(TOKEN, "ACTIVE")
        self.assertEqual(sends(controller.take_commands()), [])

    def test_link_loss_never_resets_or_sends(self):
        controller = self.make()
        self.ready_to_place(controller)
        self.place_all(controller)
        controller.handle_result(TOKEN, "S", 500, "ACTIVE")
        controller.handle_ring()
        controller.take_commands()
        controller.handle_run_status(None, "STALE")
        controller.handle_run_status(None, "NONE")
        self.assertEqual(controller.stage, gg.STAGE_RINGS)
        self.assertEqual(controller.rings, 1)
        for _ in range(19):
            controller.handle_ring()
        commands = controller.take_commands()
        self.assertEqual(sends(commands), [], "no finish while the run is unknown")
        self.assertIn(("victory",), commands)
        controller.handle_run_status(TOKEN, "ACTIVE")
        self.assertEqual(len(sends(controller.take_commands())), 1)

    def test_new_run_cancels_victory_and_resets(self):
        controller = self.make()
        self.ready_to_place(controller)
        self.place_all(controller)
        controller.handle_result(TOKEN, "S", 500, "ACTIVE")
        for _ in range(20):
            controller.handle_ring()
        controller.take_commands()
        self.assertEqual(controller.view().kind, gg.VIEW_VICTORY)

        controller.handle_run_status(OTHER, "ARMED")
        self.assertIn(("new_run", OTHER), controller.take_commands())
        view = controller.view()
        self.assertEqual(view.kind, gg.VIEW_ROBOTNIK)
        self.assertEqual(controller.rings, 0)
        # The emeralds from the last competitor are still on the shrine.
        self.assertTrue(view.warning)
        controller.handle_count(7)
        self.assertEqual(sends(controller.take_commands()), [])

    def test_finished_run_statuses_do_not_start_a_new_run(self):
        controller = self.make()
        controller.handle_run_status(TOKEN, "FINISHED")
        controller.handle_run_status(TOKEN, "TIMED_OUT")
        self.assertEqual(controller.stage, gg.STAGE_IDLE)
        self.assertEqual(controller.take_commands(), [])

    def test_event_started_elsewhere_moves_to_rings(self):
        controller = self.make()
        self.ready_to_place(controller)
        controller.handle_count(3)
        controller.handle_event_state(TOKEN, "ACTIVE")
        self.assertEqual(controller.view().kind, gg.VIEW_RINGS)
        controller.handle_event_state(OTHER, "COMPLETED")
        self.assertEqual(controller.view().kind, gg.VIEW_RINGS)

    def test_snapshot_restore_resumes_rings_and_resends_in_flight(self):
        controller = self.make()
        self.ready_to_place(controller)
        self.place_all(controller)
        controller.handle_result(TOKEN, "S", 500, "ACTIVE")
        for _ in range(20):
            controller.handle_ring()
        controller.take_commands()
        snapshot = controller.snapshot()

        restored = gg.GarageRunController(clock=self.clock, sequence_base=1)
        self.assertTrue(restored.restore(snapshot))
        self.assertEqual(restored.view().kind, gg.VIEW_NONE)
        self.assertGreaterEqual(restored.next_sequence, 502)
        restored.handle_run_status(TOKEN, "ACTIVE")
        commands = restored.take_commands()
        self.assertNotIn(("new_run", TOKEN), commands)
        self.assertEqual([c[:4] for c in sends(commands)], [("send", TOKEN, "F", 501)])

        mid_run = self.make()
        self.ready_to_place(mid_run)
        self.place_all(mid_run)
        mid_run.handle_result(TOKEN, "S", 500, "ACTIVE")
        for _ in range(7):
            mid_run.handle_ring()
        again = gg.GarageRunController(clock=self.clock)
        self.assertTrue(again.restore(mid_run.snapshot()))
        again.handle_run_status(TOKEN, "ACTIVE")
        self.assertEqual(again.view().kind, gg.VIEW_RINGS)
        self.assertEqual(again.view().rings, 7)

        self.assertFalse(gg.GarageRunController().restore({"version": 2}))
        self.assertFalse(gg.GarageRunController().restore("junk"))


if __name__ == "__main__":
    unittest.main()
