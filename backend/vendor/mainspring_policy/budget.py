"""Budget governance.

Every model/vendor call an operation makes costs money. The governor caps
spend per operating period, meters each call, and *auto-pauses* the
operation when the cap is hit. Pausing is the safe default — an operation
that has spent its budget stops making decisions rather than running up an
unbounded bill or, worse, degrading silently.

This is the generic form of "a recruiting agent must not quietly cost
$10k/month." Any autonomous operation needs the same governor.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BudgetGovernor:
    """Per-operation spend cap with metering and auto-pause."""

    cap_micro_usd: int
    spent_micro_usd: int = 0
    paused: bool = False
    pause_reason: str | None = None
    ledger: list[tuple[str, int]] = field(default_factory=list)

    def meter(self, cost_micro_usd: int, *, label: str = "call") -> None:
        """Record spend. Trips the pause when the cap is reached."""
        if cost_micro_usd <= 0:
            return
        self.spent_micro_usd += int(cost_micro_usd)
        self.ledger.append((label, int(cost_micro_usd)))
        if self.spent_micro_usd >= self.cap_micro_usd and not self.paused:
            self.paused = True
            self.pause_reason = (
                f"monthly cap reached: spent {self.spent_micro_usd} "
                f"≥ cap {self.cap_micro_usd} micro-USD"
            )

    @property
    def remaining_micro_usd(self) -> int:
        return max(0, self.cap_micro_usd - self.spent_micro_usd)

    def ok(self) -> bool:
        """True if the operation may keep running this cycle."""
        return not self.paused and self.remaining_micro_usd > 0

    def resume(self) -> None:
        """Manual unblock (or call on billing-period rollover)."""
        self.paused = False
        self.pause_reason = None
