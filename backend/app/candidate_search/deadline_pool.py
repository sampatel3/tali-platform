"""Small bounded executor that shares one absolute monotonic deadline."""

from __future__ import annotations

import concurrent.futures as futures
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar


_Item = TypeVar("_Item")
_Result = TypeVar("_Result")


@dataclass(frozen=True, slots=True)
class DeadlinePoolResult(Generic[_Result]):
    results: dict[int, _Result]
    incomplete: frozenset[int]
    deadline: float


def run_deadline_pool(
    items: list[_Item],
    worker: Callable[[_Item, float], _Result],
    *,
    max_workers: int,
    timeout_s: float,
    clock: Callable[[], float] = time.monotonic,
) -> DeadlinePoolResult[_Result]:
    """Run only admitted work slots and stop admission at one deadline."""

    deadline = clock() + max(0.0, float(timeout_s))
    if not items:
        return DeadlinePoolResult({}, frozenset(), deadline)
    results: dict[int, _Result] = {}
    pending: dict[futures.Future[_Result], int] = {}
    next_index = 0
    executor = futures.ThreadPoolExecutor(
        max_workers=max(1, min(int(max_workers), len(items)))
    )

    def submit_available() -> None:
        nonlocal next_index
        while (
            next_index < len(items)
            and len(pending) < max(1, int(max_workers))
            and clock() < deadline
        ):
            index = next_index
            next_index += 1
            pending[executor.submit(worker, items[index], deadline)] = index

    try:
        submit_available()
        while pending:
            done = {future for future in pending if future.done()}
            if not done:
                remaining = deadline - clock()
                if remaining <= 0:
                    break
                done, _ = futures.wait(
                    tuple(pending),
                    timeout=remaining,
                    return_when=futures.FIRST_COMPLETED,
                )
                if not done:
                    break
            for future in done:
                index = pending.pop(future)
                try:
                    results[index] = future.result()
                except Exception:
                    pass
            submit_available()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    return DeadlinePoolResult(
        results=results,
        incomplete=frozenset(set(range(len(items))) - results.keys()),
        deadline=deadline,
    )


__all__ = ["DeadlinePoolResult", "run_deadline_pool"]
