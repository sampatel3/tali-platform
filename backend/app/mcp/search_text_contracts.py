"""Shared runtime bounds for recruiter-authored search text."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from inspect import signature
from typing import Any, TypeVar, cast

from ..candidate_search.input_contracts import CANDIDATE_SEARCH_QUERY_MAX_LENGTH


PUBLIC_SEARCH_TEXT_MAX_LENGTH = 500
RICH_CANDIDATE_TEXT_MAX_LENGTH = CANDIDATE_SEARCH_QUERY_MAX_LENGTH

_Handler = TypeVar("_Handler", bound=Callable[..., Any])


def bounded_search_argument(
    field_name: str,
    *,
    max_length: int,
    optional: bool = False,
) -> Callable[[_Handler], _Handler]:
    """Reject empty/oversize keyword text before handler side effects."""

    def decorate(handler: _Handler) -> _Handler:
        handler_signature = signature(handler)

        @wraps(handler)
        def checked(*args: Any, **kwargs: Any) -> Any:
            bound = handler_signature.bind(*args, **kwargs)
            bound.apply_defaults()
            value = bound.arguments.get(field_name)
            if value is None and optional:
                return handler(*bound.args, **bound.kwargs)
            if not isinstance(value, str):
                raise ValueError(f"{field_name} must be a string")
            if len(value) > int(max_length):
                raise ValueError(
                    f"{field_name} must be at most {int(max_length)} characters"
                )
            text = value.strip()
            if not text:
                raise ValueError(f"{field_name} must be non-empty")
            bound.arguments[field_name] = text
            return handler(*bound.args, **bound.kwargs)

        return cast(_Handler, checked)

    return decorate


def public_search_text(
    field_name: str,
    *,
    optional: bool = False,
) -> Callable[[_Handler], _Handler]:
    return bounded_search_argument(
        field_name,
        max_length=PUBLIC_SEARCH_TEXT_MAX_LENGTH,
        optional=optional,
    )


def rich_candidate_text(field_name: str) -> Callable[[_Handler], _Handler]:
    return bounded_search_argument(
        field_name,
        max_length=RICH_CANDIDATE_TEXT_MAX_LENGTH,
    )


__all__ = [
    "PUBLIC_SEARCH_TEXT_MAX_LENGTH",
    "RICH_CANDIDATE_TEXT_MAX_LENGTH",
    "bounded_search_argument",
    "public_search_text",
    "rich_candidate_text",
]
