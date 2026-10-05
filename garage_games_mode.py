"""Garage Games mode for Chaos Heist.

This module holds the event logic only; chaos_heist.py owns the screen, audio,
serial port, and ring input. Nothing here touches Tk or Windows, so every
transition can be unit tested.

How the two apps talk
---------------------
Garage Games runs on another computer. Its USB master broadcasts the current
run over ESP-NOW, and the Chaos Heist ESP32 (the emerald shrine) listens as a
"magnetic arcade" station and relays that to this app over USB:

    MAGNET_LOCK:GG:RUN:<token|->:<state>      run state (or STALE)
    MAGNET_LOCK:GG:EVENT:<token>:<state>      this station's event state
    MAGNET_LOCK:GG:RESULT:<token>:<S|F>:<seq>:<state>
    MAGNET_LOCK:GG:NOACK:<token>:<S|F>:<seq>:<reason>
    MAGNET_LOCK:GG:STATION:<mac>:<READY|NORADIO>
    MAGNET_LOCK:GG:IDENT:<SENT|BUSY>

This app asks the ESP32 to send the event's two signals back through the
master (S = all seven emeralds placed, F = ring goal reached):

    GG:SEND:<token>:<S|F>:<seq>:<ageMs>
    GG:IDENT

Garage Games owns all timing. The age lets it time each signal from when the
emerald or ring actually happened rather than when the radio delivered it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable, Optional

GG_LINE_PREFIX = "MAGNET_LOCK:GG:"
RUN_STATES = frozenset(
    {
        "NONE",
        "ARMED",
        "COUNTDOWN",
        "ACTIVE",
        "PAUSED",
        "FINISHED",
        "TIMED_OUT",
        "STALE",
    }
)
# A run in one of these states belongs to a competitor who may still play.
LIVE_RUN_STATES = frozenset({"ARMED", "COUNTDOWN", "ACTIVE", "PAUSED"})
EVENT_STATES = frozenset({"PENDING", "ACTIVE", "COMPLETED"})
RESULT_STATES = frozenset({"PENDING", "ACTIVE", "COMPLETED", "REJECTED", "NEXT"})
SIGNAL_START = "S"
SIGNAL_FINISH = "F"
DEFAULT_RING_GOAL = 20
DEFAULT_ZERO_CONFIRM_SECONDS = 1.0
# One app-level resend after the ESP32's own six seconds of radio retries.
# Resends reuse the signal's sequence number, so Garage Games treats a copy
# that already arrived as a duplicate rather than a second start or finish.
MAX_SIGNAL_ATTEMPTS = 3
# The ESP32 reports NOACK after about six seconds of radio retries. If even
# that never arrives (a USB write was lost or the ESP32 rebooted), treat the
# signal as unanswered after this long.
SIGNAL_REPLY_TIMEOUT_SECONDS = 8.0
# Garage Games caps how far back a signal can be dated.
MAX_SIGNAL_AGE_MS = 10_000

STAGE_IDLE = "idle"
STAGE_RESET = "reset"
STAGE_PLACING = "placing"
STAGE_RINGS = "rings"
STAGE_FINISHED = "finished"
STAGES = frozenset(
    {STAGE_IDLE, STAGE_RESET, STAGE_PLACING, STAGE_RINGS, STAGE_FINISHED}
)

VIEW_NONE = "none"
VIEW_ROBOTNIK = "robotnik"
VIEW_RINGS = "rings"
VIEW_VICTORY = "victory"


def _is_token(text: str) -> bool:
    return len(text) == 16 and all(
        character in "0123456789ABCDEF" for character in text
    )


def _is_mac(text: str) -> bool:
    return len(text) == 12 and all(
        character in "0123456789ABCDEF" for character in text
    )


def parse_garage_station_message(line: str) -> Optional[tuple]:
    """Validate one ESP32 station line; return None for anything else."""
    if not isinstance(line, str) or not line.startswith(GG_LINE_PREFIX):
        return None
    fields = line[len(GG_LINE_PREFIX):].split(":")
    if not fields or any(not field for field in fields):
        return None
    kind = fields[0]
    if kind == "RUN" and len(fields) == 3:
        token, state = fields[1], fields[2]
        if state not in RUN_STATES:
            return None
        if token == "-":
            if state not in {"NONE", "STALE"}:
                return None
            return ("run", None, state)
        if not _is_token(token) or state in {"NONE", "STALE"}:
            return None
        return ("run", token, state)
    if kind == "EVENT" and len(fields) == 3:
        if _is_token(fields[1]) and fields[2] in EVENT_STATES:
            return ("event", fields[1], fields[2])
        return None
    if kind in {"RESULT", "NOACK"} and len(fields) == 5:
        token, signal, sequence_text, detail = fields[1:]
        if (
            not _is_token(token)
            or signal not in {SIGNAL_START, SIGNAL_FINISH}
            or not sequence_text.isdecimal()
            or int(sequence_text) <= 0
        ):
            return None
        if kind == "RESULT" and detail not in RESULT_STATES:
            return None
        if kind == "NOACK" and not detail.isalpha():
            return None
        return (kind.lower(), token, signal, int(sequence_text), detail)
    if kind == "STATION" and len(fields) == 3:
        if _is_mac(fields[1]) and fields[2] in {"READY", "NORADIO"}:
            return ("station", fields[1], fields[2])
        return None
    if kind == "IDENT" and len(fields) == 2:
        if fields[1] in {"SENT", "BUSY"}:
            return ("ident", fields[1])
        return None
    return None


def format_send_command(token: str, signal: str, sequence: int, age_ms: int) -> str:
    if not _is_token(token) or signal not in {SIGNAL_START, SIGNAL_FINISH}:
        raise ValueError("invalid Garage Games signal")
    age = max(0, min(MAX_SIGNAL_AGE_MS, int(age_ms)))
    return f"GG:SEND:{token}:{signal}:{int(sequence)}:{age}\n"


IDENT_COMMAND = "GG:IDENT\n"


@dataclass(frozen=True)
class GarageView:
    """What the arcade screen should show right now."""

    kind: str
    emeralds: Optional[int] = None
    rings: int = 0
    ring_goal: int = DEFAULT_RING_GOAL
    headline: str = ""
    detail: str = ""
    warning: bool = False


@dataclass
class _Signal:
    signal: str
    sequence: int
    happened_at: float
    attempts: int = 0
    state: str = "waiting"  # waiting, sent, confirmed, failed
    sent_at: float = 0.0

    def to_dict(self, now: float) -> dict:
        return {
            "signal": self.signal,
            "sequence": self.sequence,
            "age_ms": int(max(0.0, now - self.happened_at) * 1000),
            "attempts": self.attempts,
            "state": self.state,
        }

    @classmethod
    def from_dict(cls, payload, now: float) -> Optional["_Signal"]:
        if not isinstance(payload, dict):
            return None
        signal = payload.get("signal")
        sequence = payload.get("sequence")
        state = payload.get("state")
        age_ms = payload.get("age_ms", 0)
        attempts = payload.get("attempts", 0)
        if (
            signal not in {SIGNAL_START, SIGNAL_FINISH}
            or not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or sequence <= 0
            or state not in {"waiting", "sent", "confirmed", "failed"}
            or not isinstance(age_ms, int)
            or not isinstance(attempts, int)
        ):
            return None
        return cls(
            signal=signal,
            sequence=sequence,
            happened_at=now - max(0, age_ms) / 1000.0,
            attempts=max(0, attempts),
            state=state,
        )


class GarageRunController:
    """One competitor's emerald-and-ring event, driven by Garage Games runs.

    The caller feeds it run updates, accepted emerald counts, ring entries,
    and station replies, then reads ``view()`` and drains ``take_commands()``.
    Commands are ``("send", token, signal, sequence, age_ms)``,
    ``("new_run", token)``, and ``("victory",)``.
    """

    def __init__(
        self,
        *,
        ring_goal: int = DEFAULT_RING_GOAL,
        zero_confirm_seconds: float = DEFAULT_ZERO_CONFIRM_SECONDS,
        sequence_base: Optional[int] = None,
        clock: Callable[[], float] = None,
    ) -> None:
        import time as _time

        self.clock = clock or _time.monotonic
        self.ring_goal = max(1, int(ring_goal))
        self.zero_confirm_seconds = max(0.0, float(zero_confirm_seconds))
        # Garage Games ignores a repeated (token, station, sequence), so a
        # restart must never reuse a sequence number for a different signal.
        self.next_sequence = (
            int(sequence_base)
            if sequence_base is not None
            else random.randint(1, 0x3FFFFFFF)
        )
        self.link_token: Optional[str] = None
        self.link_state = "STALE"
        self.run_token: Optional[str] = None
        self.stage = STAGE_IDLE
        self.emeralds: Optional[int] = None
        self.zero_since: Optional[float] = None
        self.need_replace = False
        self.rings = 0
        self.start: Optional[_Signal] = None
        self.finish: Optional[_Signal] = None
        self.finish_after_resume = False
        self.event_state: Optional[str] = None
        # True once the victory sequence for a finished run has played (or
        # was already over before a restart); the screen then stays clear.
        self.presentation_done = False
        self.notice = ""
        self.commands: list[tuple] = []
        self.changed = True

    # ------------------------------------------------------------------
    # Inputs
    # ------------------------------------------------------------------

    def handle_run_status(self, token: Optional[str], state: str) -> None:
        now = self.clock()
        if state not in RUN_STATES:
            return
        self.link_token = token
        self.link_state = state
        if token is None:
            # Garage Games has no current run, or its master went quiet (for
            # example while the scorekeeper restarts). Neither is a new
            # competitor, so the event on screen is kept as it is.
            self._mark_changed()
            return

        if token != self.run_token:
            if state in LIVE_RUN_STATES:
                self._begin_run(token, now)
            else:
                self._mark_changed()
            return

        # The same run moving between armed, countdown, active, and paused
        # never resets it; this includes the update caused by our own start.
        if self.stage == STAGE_PLACING and state == "ACTIVE":
            self._maybe_start(now)
        if self.stage in {STAGE_RINGS, STAGE_FINISHED}:
            self._maybe_send_start(now)
            if self.finish_after_resume and state == "ACTIVE":
                self.finish_after_resume = False
                self._reissue_finish(now)
            self._maybe_send_finish(now)
        self._mark_changed()

    def handle_count(self, count: Optional[int]) -> None:
        now = self.clock()
        self.emeralds = count
        if self.stage == STAGE_RESET:
            if count == 0:
                if self.zero_since is None:
                    self.zero_since = now
            else:
                self.zero_since = None
            self._advance_reset(now)
        elif self.stage == STAGE_PLACING:
            if count is None or count < 7:
                # Taking an emerald back off clears a held start.
                if count is not None and self.need_replace:
                    self.need_replace = False
                    self.notice = ""
            else:
                self._maybe_start(now)
        # Once the ring stage begins, emeralds no longer affect the event.
        self._mark_changed()

    def handle_ring(self) -> None:
        now = self.clock()
        if self.stage != STAGE_RINGS:
            return
        self.rings += 1
        if self.rings >= self.ring_goal:
            self.rings = self.ring_goal
            self.stage = STAGE_FINISHED
            self.finish = _Signal(
                SIGNAL_FINISH,
                self._take_sequence(),
                happened_at=now,
            )
            self._maybe_send_finish(now)
            self.commands.append(("victory",))
        self._mark_changed()

    def handle_event_state(self, token: str, state: str) -> None:
        now = self.clock()
        if token != self.run_token or state not in EVENT_STATES:
            return
        previous = self.event_state
        self.event_state = state
        if state == "ACTIVE":
            if self.stage in {STAGE_RESET, STAGE_PLACING}:
                # Garage Games already has this event running (an operator
                # start, or a start this app sent before it restarted). Follow
                # it instead of asking the competitor to start again.
                self.stage = STAGE_RINGS
                self.need_replace = False
                self.notice = "Event already running in Garage Games."
                self.start = _Signal(
                    SIGNAL_START,
                    self._take_sequence(),
                    happened_at=now,
                    state="confirmed",
                )
            elif self.start is not None and self.start.state != "confirmed":
                self.start.state = "confirmed"
                self.notice = ""
                self._maybe_send_finish(now)
        elif state == "COMPLETED":
            if self.start is not None:
                self.start.state = "confirmed"
            if self.finish is not None:
                self.finish.state = "confirmed"
                self.finish_after_resume = False
            elif self.stage in {STAGE_RESET, STAGE_PLACING, STAGE_RINGS}:
                self.notice = "Event already finished in Garage Games."
        if previous != state:
            self._mark_changed()

    def handle_result(self, token: str, signal: str, sequence: int, state: str) -> None:
        now = self.clock()
        pending = self._signal(signal)
        if (
            token != self.run_token
            or pending is None
            or pending.sequence != sequence
        ):
            return
        if signal == SIGNAL_START:
            if state in {"ACTIVE", "NEXT"}:
                pending.state = "confirmed"
                self.notice = ""
                self._maybe_send_finish(now)
            elif state == "COMPLETED":
                pending.state = "confirmed"
                if self.finish is None:
                    self.notice = "Event already finished in Garage Games."
            elif self.event_state == "ACTIVE":
                pending.state = "confirmed"
                self._maybe_send_finish(now)
            elif self.stage == STAGE_RINGS:
                self._start_rejected(
                    "Garage Games did not accept the start. "
                    "Remove and replace one emerald to try again."
                )
            else:
                pending.state = "failed"
                self.notice = (
                    "Garage Games did not accept the start; "
                    "start this event on the scorekeeper."
                )
        else:
            if state == "COMPLETED":
                pending.state = "confirmed"
                self.finish_after_resume = False
                self.notice = ""
            elif self.link_state == "PAUSED" and self.event_state != "COMPLETED":
                # The 20th ring arrived while the run clock was frozen. Send
                # it again when the run resumes; no run time passes meanwhile.
                pending.state = "failed"
                self.finish_after_resume = True
                self.notice = "Run paused: the finish will be sent when it resumes."
            elif self.link_state == "TIMED_OUT":
                pending.state = "failed"
                self.notice = "Time ran out before the ring goal was recorded."
            else:
                pending.state = "failed"
                self.notice = (
                    "Garage Games did not accept the finish; "
                    "finish this event on the scorekeeper."
                )
        self._mark_changed()

    def handle_noack(self, token: str, signal: str, sequence: int, reason: str) -> None:
        now = self.clock()
        pending = self._signal(signal)
        if (
            token != self.run_token
            or pending is None
            or pending.sequence != sequence
            or pending.state != "sent"
        ):
            return
        if pending.attempts < MAX_SIGNAL_ATTEMPTS and reason in {
            "TIMEOUT",
            "STALE",
        }:
            # Unknown whether it arrived. Resend the same sequence when the
            # link allows; a copy that already counted is just a duplicate.
            pending.state = "waiting"
            if signal == SIGNAL_START:
                self._maybe_send_start(now)
            else:
                self._maybe_send_finish(now)
        else:
            pending.state = "failed"
            if signal == SIGNAL_START:
                if self.stage == STAGE_RINGS and self.event_state != "ACTIVE":
                    self._start_rejected(
                        "No answer from Garage Games. Check its master, then "
                        "remove and replace one emerald."
                    )
                elif self.event_state != "ACTIVE":
                    self.notice = (
                        "Start not confirmed by Garage Games; "
                        "check the scorekeeper."
                    )
            else:
                self.notice = (
                    "Finish not confirmed by Garage Games; "
                    "check the scorekeeper."
                )
        self._mark_changed()

    def tick(self) -> None:
        now = self.clock()
        if self.stage == STAGE_RESET:
            self._advance_reset(now)
        for pending in (self.start, self.finish):
            if (
                pending is not None
                and pending.state == "sent"
                and now - pending.sent_at >= SIGNAL_REPLY_TIMEOUT_SECONDS
                and self.run_token is not None
            ):
                self.handle_noack(
                    self.run_token,
                    pending.signal,
                    pending.sequence,
                    "TIMEOUT",
                )
        if self.stage in {STAGE_RINGS, STAGE_FINISHED}:
            self._maybe_send_start(now)
            self._maybe_send_finish(now)

    # ------------------------------------------------------------------
    # Outputs
    # ------------------------------------------------------------------

    def take_commands(self) -> list:
        commands, self.commands = self.commands, []
        return commands

    def take_changed(self) -> bool:
        changed, self.changed = self.changed, False
        return changed

    def view(self) -> GarageView:
        if self.stage == STAGE_IDLE:
            return GarageView(kind=VIEW_NONE, detail=self.notice)
        if self.stage == STAGE_FINISHED and self.presentation_done:
            return GarageView(kind=VIEW_NONE, detail=self.notice)
        if self.stage == STAGE_FINISHED:
            return GarageView(
                kind=VIEW_VICTORY,
                rings=self.rings,
                ring_goal=self.ring_goal,
                detail=self.notice,
            )
        if self.stage == STAGE_RINGS:
            return GarageView(
                kind=VIEW_RINGS,
                rings=self.rings,
                ring_goal=self.ring_goal,
                headline=f"COLLECT {self.ring_goal} RINGS!",
                detail=self.notice,
            )

        count = self.emeralds
        if self.stage == STAGE_RESET:
            if count is None:
                return GarageView(
                    kind=VIEW_ROBOTNIK,
                    emeralds=None,
                    headline="WAITING FOR THE EMERALD SENSORS",
                    detail="Check the shrine's USB cable.",
                    warning=True,
                )
            if count > 0:
                emerald_word = "EMERALD" if count == 1 else "EMERALDS"
                return GarageView(
                    kind=VIEW_ROBOTNIK,
                    emeralds=count,
                    headline=f"REMOVE ALL CHAOS EMERALDS ({count} {emerald_word} STILL IN PLACE)",
                    detail="Operator: clear the shrine before the next competitor.",
                    warning=True,
                )
            return GarageView(
                kind=VIEW_ROBOTNIK,
                emeralds=0,
                headline="CHECKING THE SHRINE...",
                detail="Keep all seven emeralds off the shrine.",
            )

        placed = count or 0
        if self.need_replace:
            if self.notice:
                detail = self.notice
            elif self.link_state != "ACTIVE" or self.link_token != self.run_token:
                detail = "Wait for the run to start, then remove and replace one emerald."
            else:
                detail = "Remove and replace one emerald to start!"
            warning = True
        else:
            emerald_word = "Emerald" if 7 - placed == 1 else "Emeralds"
            detail = (
                self.notice
                or f"Return {7 - placed} Chaos {emerald_word} to stop Robotnik!"
            )
            warning = bool(self.notice)
        return GarageView(
            kind=VIEW_ROBOTNIK,
            emeralds=placed,
            headline="ROBOTNIK STOLE THE CHAOS EMERALDS!",
            detail=detail,
            warning=warning,
        )

    def status_text(self) -> str:
        token = self.run_token or "-"
        start_state = self.start.state if self.start else "-"
        finish_state = self.finish.state if self.finish else "-"
        return (
            f"run={token} link={self.link_state} stage={self.stage} "
            f"rings={self.rings}/{self.ring_goal} start={start_state} "
            f"finish={finish_state}"
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        now = self.clock()
        return {
            "version": 1,
            "run_token": self.run_token,
            "stage": self.stage,
            "rings": self.rings,
            "ring_goal": self.ring_goal,
            "need_replace": self.need_replace,
            "presentation_done": self.presentation_done,
            "next_sequence": self.next_sequence,
            "finish_after_resume": self.finish_after_resume,
            "start": self.start.to_dict(now) if self.start else None,
            "finish": self.finish.to_dict(now) if self.finish else None,
        }

    def restore(self, payload) -> bool:
        """Resume a run saved before a restart; the next RUN line confirms it."""
        now = self.clock()
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return False
        token = payload.get("run_token")
        stage = payload.get("stage")
        rings = payload.get("rings")
        next_sequence = payload.get("next_sequence")
        if (
            token is not None
            and (not isinstance(token, str) or not _is_token(token))
        ) or stage not in STAGES or not isinstance(rings, int) or isinstance(
            rings, bool
        ):
            return False
        if isinstance(next_sequence, int) and not isinstance(next_sequence, bool):
            self.next_sequence = max(self.next_sequence, next_sequence)
        if token is None or stage == STAGE_IDLE:
            return False
        self.run_token = token
        self.stage = stage
        # A finished event's victory never replays after a restart, but an
        # unconfirmed finish is still delivered.
        self.presentation_done = stage == STAGE_FINISHED
        self.rings = max(0, min(self.ring_goal, rings))
        self.need_replace = bool(payload.get("need_replace"))
        if stage != STAGE_FINISHED:
            self.presentation_done = False
        self.finish_after_resume = bool(payload.get("finish_after_resume"))
        self.start = _Signal.from_dict(payload.get("start"), now)
        self.finish = _Signal.from_dict(payload.get("finish"), now)
        # Anything that was in flight is re-sent with its original sequence.
        for pending in (self.start, self.finish):
            if pending is not None and pending.state == "sent":
                pending.state = "waiting"
        self.zero_since = None
        self._mark_changed()
        return True

    def finish_presentation(self) -> None:
        """The victory sequence ended; keep the result, clear the screen."""
        if self.stage == STAGE_FINISHED and not self.presentation_done:
            self.presentation_done = True
            self._mark_changed()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _mark_changed(self) -> None:
        self.changed = True

    def _take_sequence(self) -> int:
        sequence = self.next_sequence
        self.next_sequence += 1
        if self.next_sequence > 0xFFFFFFFF:
            self.next_sequence = 1
        return sequence

    def _signal(self, signal: str) -> Optional[_Signal]:
        return self.start if signal == SIGNAL_START else self.finish

    def _begin_run(self, token: str, now: float) -> None:
        self.run_token = token
        self.stage = STAGE_RESET
        self.rings = 0
        self.start = None
        self.finish = None
        self.finish_after_resume = False
        self.event_state = None
        self.need_replace = False
        self.presentation_done = False
        self.notice = ""
        self.zero_since = now if self.emeralds == 0 else None
        self.commands.append(("new_run", token))
        self._advance_reset(now)
        self._mark_changed()

    def _advance_reset(self, now: float) -> None:
        if (
            self.stage == STAGE_RESET
            and self.emeralds == 0
            and self.zero_since is not None
            and now - self.zero_since >= self.zero_confirm_seconds
        ):
            self.stage = STAGE_PLACING
            self.zero_since = None
            self.need_replace = False
            self._mark_changed()

    def _link_live_for_run(self) -> bool:
        return self.link_token == self.run_token and self.link_state in {
            "ACTIVE",
            "PAUSED",
            "TIMED_OUT",
        }

    def _maybe_start(self, now: float) -> None:
        if self.stage != STAGE_PLACING or self.emeralds != 7:
            return
        if self.need_replace:
            return
        if self.link_token != self.run_token or self.link_state != "ACTIVE":
            # Seven emeralds before the clock runs: hold, and require one
            # emerald to be lifted and replaced once the run is going.
            self.need_replace = True
            self._mark_changed()
            return
        self.stage = STAGE_RINGS
        self.rings = 0
        self.notice = ""
        self.start = _Signal(SIGNAL_START, self._take_sequence(), happened_at=now)
        self._maybe_send_start(now)
        self._mark_changed()

    def _maybe_send_start(self, now: float) -> None:
        pending = self.start
        if (
            pending is None
            or pending.state != "waiting"
            or self.link_token != self.run_token
            or self.link_state != "ACTIVE"
        ):
            return
        self._send(pending, now)

    def _maybe_send_finish(self, now: float) -> None:
        pending = self.finish
        if (
            pending is None
            or pending.state != "waiting"
            or self.start is None
            or self.start.state != "confirmed"
            or not self._link_live_for_run()
        ):
            return
        self._send(pending, now)

    def _send(self, pending: _Signal, now: float) -> None:
        pending.attempts += 1
        pending.state = "sent"
        pending.sent_at = now
        age_ms = int(max(0.0, now - pending.happened_at) * 1000)
        self.commands.append(
            (
                "send",
                self.run_token,
                pending.signal,
                pending.sequence,
                min(MAX_SIGNAL_AGE_MS, age_ms),
            )
        )
        self._mark_changed()

    def _reissue_finish(self, now: float) -> None:
        # A new sequence, timed now: the run clock did not move while paused.
        self.finish = _Signal(SIGNAL_FINISH, self._take_sequence(), happened_at=now)
        self.notice = ""

    def _start_rejected(self, notice: str) -> None:
        if self.start is not None:
            self.start.state = "failed"
        self.start = None
        self.stage = STAGE_PLACING
        self.rings = 0
        self.need_replace = True
        self.notice = notice
        self._mark_changed()
