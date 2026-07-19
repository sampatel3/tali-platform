"""Exact, provider-free terminal accounting for durable scoring cohorts."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class ExactScoringTerminalCounts:
    target_total: int
    scored: int
    errors: int
    pre_screened_out: int
    not_enqueued: int

    @property
    def accounted(self) -> int:
        return self.scored + self.errors + self.pre_screened_out + self.not_enqueued


def _exact_positive_ids(value: object) -> tuple[int, ...] | None:
    if not isinstance(value, list):
        return None
    normalized = tuple(
        sorted({item for item in value if type(item) is int and item > 0})
    )
    if not normalized or list(normalized) != value:
        return None
    return normalized


def exact_scoring_terminal_counts(
    counters: Mapping[str, object],
    *,
    scored: int,
    errors: int,
    pre_screened_out: int,
) -> ExactScoringTerminalCounts | None:
    """Validate immutable targets and reconcile stored fan-out deficit evidence."""

    targets = _exact_positive_ids(counters.get("target_application_ids"))
    if targets is None:
        return None
    target_total = len(targets)
    for key in ("total", "selected_total"):
        if key in counters and (
            type(counters.get(key)) is not int or counters.get(key) != target_total
        ):
            return None
    if any(
        type(value) is not int or value < 0
        for value in (scored, errors, pre_screened_out)
    ):
        return None
    observed = scored + errors + pre_screened_out
    if observed > target_total:
        return None

    dispatched = counters.get("dispatched_application_ids")
    if not isinstance(dispatched, list):
        return None
    dispatched_ids = tuple(
        sorted({item for item in dispatched if type(item) is int and item > 0})
    )
    if list(dispatched_ids) != dispatched or not set(dispatched_ids) <= set(targets):
        return None
    expected_not_enqueued = target_total - len(dispatched_ids)
    raw_not_enqueued = counters.get("not_enqueued", 0)
    if type(raw_not_enqueued) is not int or raw_not_enqueued < 0:
        return None
    if "not_enqueued" in counters and raw_not_enqueued != expected_not_enqueued:
        return None
    if raw_not_enqueued > target_total - observed:
        return None
    return ExactScoringTerminalCounts(
        target_total=target_total,
        scored=scored,
        errors=errors,
        pre_screened_out=pre_screened_out,
        not_enqueued=raw_not_enqueued,
    )


def exact_scoring_terminal_identity_error(
    counters: Mapping[str, object],
    *,
    terminal_application_ids: Collection[int],
    active_application_ids: Collection[int] = (),
    drained: bool,
) -> str | None:
    """Validate that job identities match the immutable dispatch receipt."""

    targets = _exact_positive_ids(counters.get("target_application_ids"))
    dispatched = counters.get("dispatched_application_ids")
    if targets is None or not isinstance(dispatched, list):
        return "scoring_batch_invalid_terminal_receipts"
    dispatched_ids = tuple(
        sorted({item for item in dispatched if type(item) is int and item > 0})
    )
    if list(dispatched_ids) != dispatched or not set(dispatched_ids) <= set(targets):
        return "scoring_batch_invalid_terminal_receipts"
    terminal_ids = set(terminal_application_ids)
    active_ids = set(active_application_ids)
    if any(type(item) is not int or item <= 0 for item in terminal_ids | active_ids):
        return "scoring_batch_invalid_terminal_receipts"
    dispatched_set = set(dispatched_ids)
    if (
        terminal_ids & active_ids
        or not terminal_ids <= dispatched_set
        or not active_ids <= dispatched_set
    ):
        return "scoring_batch_invalid_terminal_receipts"
    if drained and terminal_ids != dispatched_set:
        return "scoring_batch_incomplete_terminal_receipts"
    return None


def resolve_exact_scoring_terminal_state(
    counters: Mapping[str, object],
    *,
    stored_status: str,
    scored: int,
    errors: int,
    pre_screened_out: int,
) -> tuple[str, str | None]:
    """Resolve a drained exact cohort without trusting stored terminal totals."""

    exact = exact_scoring_terminal_counts(
        counters,
        scored=scored,
        errors=errors,
        pre_screened_out=pre_screened_out,
    )
    if exact is None:
        return "failed", "scoring_batch_invalid_terminal_receipts"
    if exact.accounted != exact.target_total:
        return "failed", "scoring_batch_incomplete_terminal_receipts"
    if stored_status == "cancelling":
        return "cancelled", None
    if stored_status in {"completed", "failed"}:
        return stored_status, None
    return ("failed" if counters.get("fanout_failed") is True else "completed"), None


__all__ = [
    "ExactScoringTerminalCounts",
    "exact_scoring_terminal_counts",
    "exact_scoring_terminal_identity_error",
    "resolve_exact_scoring_terminal_state",
]
