from __future__ import annotations

from app.candidate_search.deadline_pool import run_deadline_pool


def test_deadline_pool_never_starts_queued_work_after_shared_deadline() -> None:
    clock = [0.0]
    started: list[int] = []

    def worker(item: int, deadline: float) -> int:
        started.append(item)
        assert deadline == 10.0
        clock[0] = deadline
        return item * 2

    result = run_deadline_pool(
        list(range(5)),
        worker,
        max_workers=1,
        timeout_s=10.0,
        clock=lambda: clock[0],
    )

    assert started == [0]
    assert result.results == {0: 0}
    assert result.incomplete == frozenset({1, 2, 3, 4})
    assert result.deadline == 10.0


def test_deadline_pool_returns_every_completed_result_by_input_index() -> None:
    result = run_deadline_pool(
        ["a", "b", "c"],
        lambda item, _deadline: item.upper(),
        max_workers=2,
        timeout_s=1.0,
    )

    assert result.results == {0: "A", 1: "B", 2: "C"}
    assert result.incomplete == frozenset()
