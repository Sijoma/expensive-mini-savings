"""The approval lifecycle as a state machine (Hypothesis stateful testing): random sequences of new plans,
approvals, rejections, withdrawals, mode changes, battery levels and passing time. After every step the
rules of the executor must hold, whatever happened before."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from custom_components.ems import control as c
from custom_components.ems.adapter import PlanBlock

TZ = ZoneInfo("Europe/Berlin")
START = datetime(2026, 11, 13, 20, 0, tzinfo=TZ)
QUARTER = timedelta(minutes=15)

plan_blocks = st.lists(
    st.tuples(
        st.sampled_from(["h", "l"]),
        st.integers(-4, 110),  # start in quarter hours from now
        st.integers(1, 16),  # length in quarter hours
        st.floats(0, 12),  # kWh (grid charge)
        st.one_of(st.none(), st.floats(0, 2)),  # planned savings
    ),
    max_size=6,
)


def ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


class ApprovalLifecycle(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.now = START
        self.blocks: list[PlanBlock] = []
        self.approval = c.Approval()
        self.mode = c.WITH_APPROVAL
        self.soc = 50.0
        self.target = c.NORMAL
        self.just: str | None = None
        """The last step, for the rules that hold right after it."""

    # ---- steps ----

    @rule(raw=plan_blocks)
    def new_plan(self, raw):
        base = self.now.replace(minute=self.now.minute - self.now.minute % 15, second=0, microsecond=0)
        self.blocks = [
            PlanBlock(
                start_ms=ms(base + start * QUARTER),
                end_ms=ms(base + (start + length) * QUARTER),
                code=code,
                kwh=round(kwh, 1) if code == "l" else 0.0,
                saves_eur=saves,
            )
            for code, start, length, kwh, saves in raw
        ]
        self._step("plan")

    @rule()
    def approve(self):
        self.approval = c.approve(self.blocks, self.now, self.approval)
        self._step("approve")

    @rule()
    def reject(self):
        self.approval = c.reject(self.blocks, self.now, self.approval)
        self._step("reject")

    @rule()
    def withdraw(self):
        self.approval = c.withdraw(self.now, self.approval)
        self._step("withdraw")

    @rule(mode=st.sampled_from(c.MODES))
    def change_mode(self, mode):
        self.mode = mode
        self._step("mode")

    @rule(soc=st.floats(0, 100))
    def battery(self, soc):
        self.soc = soc
        self._step("soc")

    @rule(minutes=st.integers(1, 26 * 60))
    def time_passes(self, minutes):
        self.now += timedelta(minutes=minutes)
        self._step("time")

    def _step(self, name: str) -> None:
        self.just = name
        self.target = c.target(
            self.mode, self.blocks, ms(self.now), True, self.approval, self.target, self.soc
        )

    # ---- rules after every step ----

    @invariant()
    def pending_means_there_is_something_to_approve(self):
        if c.pending(self.mode, self.blocks, ms(self.now), self.approval):
            assert c.offered(self.blocks, ms(self.now)), "pending, but approving would change nothing"

    @invariant()
    def only_approved_actions_run_in_approval_mode(self):
        if self.mode == c.WITH_APPROVAL and self.target != c.NORMAL:
            assert ms(self.now) <= self.approval.until_ms
            assert c.CODE[self.target] in self.approval.codes or (
                self.target == c.HOLD and "l" in self.approval.codes  # full battery holds an approved charge
            )

    @invariant()
    def plan_only_never_runs_anything(self):
        if self.mode == c.PLAN_ONLY:
            assert self.target == c.NORMAL

    @invariant()
    def a_full_battery_is_not_charged(self):
        if self.soc >= c.CHARGE_STOP_SOC:
            assert self.target != c.CHARGE

    @invariant()
    def approve_and_reject_settle_the_offer(self):
        if self.just == "reject":
            assert not c.pending(c.WITH_APPROVAL, self.blocks, ms(self.now), self.approval)
        if self.just == "approve" and self.approval.kwh < c.APPROVED_KWH_MAX:
            assert not c.pending(c.WITH_APPROVAL, self.blocks, ms(self.now), self.approval)

    @invariant()
    def a_withdrawn_approval_stops_everything_in_approval_mode(self):
        if self.just == "withdraw" and self.mode == c.WITH_APPROVAL:
            assert self.target == c.NORMAL


# examples follow the active profile (100 by default, 2000 with --hypothesis-profile=thorough)
ApprovalLifecycle.TestCase.settings = settings(stateful_step_count=30, deadline=None)
TestApprovalLifecycle = ApprovalLifecycle.TestCase
