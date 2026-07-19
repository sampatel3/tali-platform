"""Strict Bullhorn pagination shared by typed service reads."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .errors import BullhornApiError

logger = logging.getLogger(__name__)

SEARCH_PAGE_CAP = 500
COMPLETE_SNAPSHOT_ROW_GUARD = 100_000


def paged(
    request: Callable[..., Any],
    kind: str,
    entity: str,
    *,
    fields: str,
    selector: str,
    count: int,
    limit: int | None = None,
    require_complete: bool = False,
) -> list[dict]:
    if not fields:
        raise ValueError(f"fields= is mandatory for {kind}/{entity}")
    page = min(int(count), SEARCH_PAGE_CAP)
    if page <= 0:
        raise ValueError("count must be positive")
    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive")
    if require_complete and limit is not None:
        raise ValueError("complete snapshots cannot be row-limited")
    page = min(page, limit) if limit is not None else page
    selector_key = "query" if kind == "search" else "where"
    out: list[dict] = []
    start = 0
    expected_total: int | None = None
    while True:
        params = {"fields": fields, "start": start, "count": page}
        if selector:
            params[selector_key] = selector
        payload = request("GET", f"{kind}/{entity}", params=params)
        if require_complete and not isinstance(payload, dict):
            raise BullhornApiError(
                f"Bullhorn complete snapshot returned malformed {kind}/{entity} payload"
            )
        data = payload.get("data") if isinstance(payload, dict) else None
        if require_complete and not isinstance(data, list):
            raise BullhornApiError(
                f"Bullhorn complete snapshot returned malformed {kind}/{entity} data"
            )
        if require_complete and any(not isinstance(row, dict) for row in data):
            raise BullhornApiError(
                f"Bullhorn complete snapshot returned malformed {kind}/{entity} row"
            )
        rows = [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []
        out.extend(rows)
        if limit is not None and len(out) >= limit:
            return out[:limit]
        total = payload.get("total") if isinstance(payload, dict) else None
        if require_complete:
            if type(total) is not int or total < 0:
                raise BullhornApiError(
                    f"Bullhorn complete snapshot omitted a valid total for {kind}/{entity}"
                )
            if expected_total is None:
                expected_total = total
                if expected_total > COMPLETE_SNAPSHOT_ROW_GUARD:
                    raise BullhornApiError(
                        f"Bullhorn complete snapshot exceeds the {kind}/{entity} safety guard"
                    )
            elif total != expected_total:
                raise BullhornApiError(
                    f"Bullhorn complete snapshot total changed during {kind}/{entity} pagination"
                )
            payload_start = payload.get("start")
            if payload_start is not None and (
                type(payload_start) is not int or payload_start != start
            ):
                raise BullhornApiError(
                    f"Bullhorn complete snapshot returned an unexpected {kind}/{entity} page"
                )
            if len(rows) > page:
                raise BullhornApiError(
                    f"Bullhorn complete snapshot exceeded the {kind}/{entity} page size"
                )
            next_start = start + len(rows)
            if next_start == expected_total:
                break
            if not rows or next_start > expected_total:
                raise BullhornApiError(
                    f"Bullhorn complete snapshot was partial for {kind}/{entity}"
                )
            start = next_start
            continue
        if len(rows) < page:
            break
        if isinstance(total, int) and start + len(rows) >= total:
            break
        start += page
        if start > COMPLETE_SNAPSHOT_ROW_GUARD:
            logger.warning(
                "Bullhorn %s/%s pagination guard hit at start=%d", kind, entity, start
            )
            break
    return out
