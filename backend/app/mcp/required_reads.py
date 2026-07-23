"""Deterministic planning for grounded candidate reads in chat runtimes.

Prompts may help a model choose a tool, but they are not an execution
contract.  This controller turns the request claims already classified by the
grounding ledger into one canonical tool call with server-owned arguments.
For exhaustive role-bound reads it also advances contiguous pages, subject to
strict page and row ceilings.  The grounding ledger remains the terminal trust
boundary: an invalid scope, failed page, or exhausted budget never certifies a
claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import re
from typing import Any

from .catalog import (
    CANDIDATE_ACTION_HISTORY,
    CANDIDATE_ACTION_HISTORY_EXHAUSTIVE,
    CANDIDATE_DECISION_HISTORY,
    CANDIDATE_DECISION_HISTORY_EXHAUSTIVE,
    CANDIDATE_POOL_EXHAUSTIVE,
    CANDIDATE_POOL_STATE,
    CANDIDATE_QUALITATIVE_EVIDENCE,
    CANDIDATE_QUALITATIVE_EXACT_EMPTY,
)
from .provenance import GroundingClaim, normalize_claim_value
from .shared_reads import GroundingLedger


ROLE_SCOPE_REQUIRED_MESSAGE = (
    "This exact candidate read needs one role because every role has its own "
    "independent candidate pool and history. Select or name the role you want "
    "me to check."
)

_ACTION_CAPABILITIES = frozenset(
    {CANDIDATE_ACTION_HISTORY, CANDIDATE_ACTION_HISTORY_EXHAUSTIVE}
)
_DECISION_CAPABILITIES = frozenset(
    {CANDIDATE_DECISION_HISTORY, CANDIDATE_DECISION_HISTORY_EXHAUSTIVE}
)
_QUALITATIVE_CAPABILITIES = frozenset(
    {CANDIDATE_QUALITATIVE_EVIDENCE, CANDIDATE_QUALITATIVE_EXACT_EMPTY}
)
_POOL_CAPABILITIES = frozenset({CANDIDATE_POOL_STATE, CANDIDATE_POOL_EXHAUSTIVE})

_FAMILIES: tuple[tuple[str, frozenset[str]], ...] = (
    ("actions", _ACTION_CAPABILITIES),
    ("decisions", _DECISION_CAPABILITIES),
    ("qualitative", _QUALITATIVE_CAPABILITIES),
    ("pool", _POOL_CAPABILITIES),
)

_PAGED_TOOLS = frozenset(
    {
        "list_candidate_actions",
        "list_recent_agent_decisions",
        "search_role_candidates",
    }
)

_RESULT_FILTER_KEYS: dict[str, tuple[str, ...]] = {
    "list_candidate_actions": (
        "application_id",
        "candidate_id",
        "action",
        "target_stage",
        "status",
        "actor_type",
        "actor_id",
        "occurred_after",
        "occurred_before",
        "result_view",
    ),
    "list_recent_agent_decisions": (
        "role_id",
        "status",
        "application_id",
        "candidate_id",
        "decision_type",
        "created_after",
        "created_before",
        "resolved_after",
        "resolved_before",
    ),
    "search_role_candidates": (
        "q",
        "pipeline_stage",
        "application_outcome",
        "ats_stage",
        "workable_stage",
        "has_pending_decision",
        "min_score",
        "score_type",
        "sort_by",
        "sort_order",
    ),
}


def _scope_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        moment = value
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat()
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "T" in raw or len(raw) == 10:
        try:
            moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
        else:
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            return moment.astimezone(timezone.utc).isoformat()
    return normalize_claim_value(raw)


def _bool_filter(value: str) -> bool:
    return normalize_claim_value(value) == "true"


def _subject_matches_name(subject: str, candidate_name: str) -> bool:
    normalized_subject = normalize_claim_value(subject)
    normalized_name = normalize_claim_value(candidate_name)
    if not normalized_subject or not normalized_name:
        return False
    return f" {normalized_subject} " in f" {normalized_name} "


@dataclass
class _SubjectResolutionAccumulator:
    """Validated pages from one exact role-local identity lookup."""

    total: int
    pages: dict[int, tuple[tuple[int, int, str], ...]] = field(default_factory=dict)
    terminal_offsets: set[int] = field(default_factory=set)
    invalid: bool = False

    def add(self, result: dict[str, Any]) -> bool:
        try:
            offset = int(result.get("offset") or 0)
            total = int(result.get("total") or 0)
        except (TypeError, ValueError):
            self.invalid = True
            return False
        items = result.get("items")
        if (
            total != self.total
            or offset < 0
            or not isinstance(items, list)
            or offset + len(items) > total
        ):
            self.invalid = True
            return False

        rows: list[tuple[int, int, str]] = []
        for item in items:
            if not isinstance(item, dict):
                self.invalid = True
                return False
            try:
                application_id = int(item["application_id"])
                candidate_id = int(item["candidate_id"])
            except (KeyError, TypeError, ValueError):
                self.invalid = True
                return False
            candidate_name = str(item.get("candidate_name") or "").strip()
            if application_id < 1 or candidate_id < 1 or not candidate_name:
                self.invalid = True
                return False
            rows.append((application_id, candidate_id, candidate_name))

        page_rows = tuple(rows)
        existing = self.pages.get(offset)
        if existing is not None and existing != page_rows:
            self.invalid = True
            return False
        self.pages[offset] = page_rows
        expected_has_more = offset + len(page_rows) < total
        if result.get("has_more") is not expected_has_more:
            self.invalid = True
            return False
        if not expected_has_more:
            self.terminal_offsets.add(offset)
        return self.complete

    @property
    def complete(self) -> bool:
        if self.invalid or 0 not in self.pages:
            return False
        expected_offset = 0
        seen_application_ids: set[int] = set()
        for offset, rows in sorted(self.pages.items()):
            if offset != expected_offset:
                return False
            application_ids = {row[0] for row in rows}
            if len(application_ids) != len(rows) or seen_application_ids.intersection(
                application_ids
            ):
                return False
            seen_application_ids.update(application_ids)
            expected_offset += len(rows)
        return bool(
            expected_offset == self.total
            and any(
                offset + len(self.pages[offset]) == self.total
                for offset in self.terminal_offsets
            )
        )

    def matching_candidates(self, subject: str) -> tuple[tuple[int, str], ...]:
        matches: dict[int, str] = {}
        for rows in self.pages.values():
            for _application_id, candidate_id, candidate_name in rows:
                if _subject_matches_name(subject, candidate_name):
                    matches[candidate_id] = candidate_name
        return tuple(sorted(matches.items()))


@dataclass(frozen=True)
class RequiredReadPlan:
    """One server-bound canonical read required before a terminal answer."""

    family: str
    tool_name: str
    capability: str
    arguments: dict[str, Any]
    page_index: int = 0
    resolution_subject: str | None = None

    @property
    def tool_choice(self) -> dict[str, Any]:
        # Anthropic's forced tool choice selects the name; the controller still
        # overwrites the input below because tool_choice cannot constrain args.
        return {
            "type": "tool",
            "name": self.tool_name,
            "disable_parallel_tool_use": True,
        }


@dataclass
class RequiredReadController:
    """Plan bounded, claim-derived candidate reads for one user turn."""

    ledger: GroundingLedger
    role_bound: bool = True
    current_user_id: int | None = None
    enabled: bool = True
    page_size: int = 100
    max_pages: int = 5
    max_rows: int = 500
    _attempted_families: set[str] = field(default_factory=set)
    _pending: list[RequiredReadPlan] = field(default_factory=list)
    _rows_by_family: dict[str, int] = field(default_factory=dict)
    _subject_resolutions: dict[tuple[str, str], _SubjectResolutionAccumulator] = field(
        default_factory=dict
    )
    _scope_blocked: bool = False

    def __post_init__(self) -> None:
        if self.current_user_id is not None:
            self.ledger.bind_current_actor(int(self.current_user_id))
        self.page_size = max(1, min(int(self.page_size), 100))
        self.max_pages = max(1, int(self.max_pages))
        self.max_rows = max(1, int(self.max_rows))

    @property
    def requires_role_scope(self) -> bool:
        """Whether global Chat lacks an exact organization-wide contract."""

        if self._scope_blocked:
            return True
        if not self.enabled or self.role_bound:
            return False
        missing = self.ledger.missing_for_answer("")
        if missing.intersection(_ACTION_CAPABILITIES):
            return True
        if any(
            claim.subjects
            and claim.capability in missing.intersection(_DECISION_CAPABILITIES)
            for claim in self.ledger.required_claims
        ):
            return True
        for claim in self.ledger.required_claims:
            if claim.capability not in missing.intersection(_POOL_CAPABILITIES):
                continue
            # Global ``search_applications`` is a bounded bare-list contract.
            # It can ground a non-empty current-state lookup, but not an exact
            # all/count/zero answer. It also cannot filter role-specific ATS or
            # pending-decision state. Ask for a role instead of silently
            # truncating at 100 or dropping one of those filters.
            if claim.capability == CANDIDATE_POOL_EXHAUSTIVE or set(
                claim.filter_map
            ).intersection({"ats_stage", "workable_stage", "has_pending_decision"}):
                return True
        return False

    def next_plan(self) -> RequiredReadPlan | None:
        """Consume the next initial read or validated page continuation."""

        if not self.enabled:
            return None
        if self.requires_role_scope:
            self._scope_blocked = True
            return None
        if self._pending:
            return self._pending.pop(0)

        missing = self.ledger.missing_for_answer("")
        for family, capabilities in _FAMILIES:
            if family in self._attempted_families:
                continue
            claim = next(
                (
                    item
                    for item in self.ledger.required_claims
                    if item.capability in capabilities and item.capability in missing
                ),
                None,
            )
            if claim is None:
                continue
            self._attempted_families.add(family)
            plan = self._plan_for_claim(family, claim)
            if plan is None and family == "actions" and not self.role_bound:
                self._scope_blocked = True
            if plan is not None:
                return plan
        return None

    def bind_assistant_blocks(
        self,
        plan: RequiredReadPlan | None,
        blocks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Bind one provider tool block to the server-owned name and args.

        A forced tool response should contain one tool-use block.  Treat that
        as an untrusted transport envelope: discard any additional tool calls
        and replace both its name and arguments before dispatch or persistence.
        If the provider returns no tool block, leave the response untouched so
        the terminal grounding gate can fail closed.
        """

        if plan is None:
            return blocks
        first_tool = next(
            (block for block in blocks if block.get("type") == "tool_use"),
            None,
        )
        if first_tool is None:
            return blocks
        bound = {
            **first_tool,
            "name": plan.tool_name,
            "input": dict(plan.arguments),
        }
        result: list[dict[str, Any]] = []
        inserted = False
        for block in blocks:
            if block.get("type") != "tool_use":
                result.append(block)
            elif not inserted:
                result.append(bound)
                inserted = True
        return result

    def observe(
        self,
        plan: RequiredReadPlan | None,
        *,
        tool_name: str,
        result: Any,
        arguments: dict[str, Any] | None = None,
    ) -> None:
        """Queue only a contiguous, correctly scoped exhaustive next page."""

        if plan is not None and plan.resolution_subject is not None:
            self._observe_subject_resolution(
                plan,
                tool_name=tool_name,
                result=result,
                arguments=arguments,
            )
            return
        if (
            plan is None
            or tool_name != plan.tool_name
            or plan.tool_name not in _PAGED_TOOLS
            or plan.capability
            not in {
                CANDIDATE_ACTION_HISTORY_EXHAUSTIVE,
                CANDIDATE_DECISION_HISTORY_EXHAUSTIVE,
                CANDIDATE_POOL_EXHAUSTIVE,
            }
            or not isinstance(result, dict)
            or result.get("total_is_exact") is not True
        ):
            return
        safe_arguments = dict(arguments or {})
        if safe_arguments != plan.arguments:
            return
        try:
            offset = int(result.get("offset") or 0)
            total = int(result.get("total") or 0)
        except (TypeError, ValueError):
            return
        items = result.get("items")
        if (
            offset != int(plan.arguments.get("offset") or 0)
            or total < 0
            or not isinstance(items, list)
            or offset + len(items) > total
            or not self._result_scope_matches(plan, result)
        ):
            return
        expected_has_more = offset + len(items) < total
        if result.get("has_more") is not expected_has_more:
            return

        rows = self._rows_by_family.get(plan.family, 0) + len(items)
        self._rows_by_family[plan.family] = rows
        if not expected_has_more:
            return
        next_page_index = plan.page_index + 1
        if not items or next_page_index >= self.max_pages or rows >= self.max_rows:
            return
        remaining_budget = self.max_rows - rows
        next_limit = min(self.page_size, remaining_budget)
        if next_limit < 1:
            return
        self._pending.append(
            replace(
                plan,
                arguments={
                    **plan.arguments,
                    "limit": next_limit,
                    "offset": offset + len(items),
                },
                page_index=next_page_index,
            )
        )

    def _observe_subject_resolution(
        self,
        plan: RequiredReadPlan,
        *,
        tool_name: str,
        result: Any,
        arguments: dict[str, Any] | None,
    ) -> None:
        """Resolve one request name or leave its history claim ungrounded."""

        safe_arguments = dict(arguments or {})
        if (
            tool_name != "search_role_candidates"
            or plan.tool_name != "search_role_candidates"
            or safe_arguments != plan.arguments
            or not isinstance(result, dict)
            or result.get("total_is_exact") is not True
            or not self._result_scope_matches(plan, result)
        ):
            return
        try:
            offset = int(result.get("offset") or 0)
            total = int(result.get("total") or 0)
        except (TypeError, ValueError):
            return
        if offset != int(plan.arguments.get("offset") or 0) or total < 0:
            return

        subject = str(plan.resolution_subject)
        key = (plan.family, normalize_claim_value(subject))
        accumulator = self._subject_resolutions.get(key)
        if accumulator is None:
            if offset != 0:
                return
            accumulator = _SubjectResolutionAccumulator(total=total)
            self._subject_resolutions[key] = accumulator
        complete = accumulator.add(result)
        if accumulator.invalid:
            return
        items = result.get("items")
        assert isinstance(items, list)
        rows = sum(len(page) for page in accumulator.pages.values())

        if complete:
            matches = accumulator.matching_candidates(subject)
            if len(matches) != 1:
                return
            candidate_id, candidate_name = matches[0]
            if not self.ledger.bind_history_subject(
                capability=plan.capability,
                subject=subject,
                candidate_id=candidate_id,
                candidate_name=candidate_name,
            ):
                return
            bound_claim = next(
                (
                    claim
                    for claim in self.ledger.required_claims
                    if claim.capability == plan.capability
                    and claim.filter_map.get("candidate_id") == str(candidate_id)
                ),
                None,
            )
            if bound_claim is not None:
                history_plan = self._history_plan_for_claim(plan.family, bound_claim)
                if history_plan is not None:
                    self._pending.append(history_plan)
            return

        expected_has_more = offset + len(items) < total
        next_page_index = plan.page_index + 1
        if (
            not expected_has_more
            or not items
            or next_page_index >= self.max_pages
            or rows >= self.max_rows
        ):
            return
        next_limit = min(self.page_size, self.max_rows - rows)
        if next_limit < 1:
            return
        self._pending.append(
            replace(
                plan,
                arguments={
                    **plan.arguments,
                    "limit": next_limit,
                    "offset": offset + len(items),
                },
                page_index=next_page_index,
            )
        )

    def _plan_for_claim(
        self,
        family: str,
        claim: GroundingClaim,
    ) -> RequiredReadPlan | None:
        if family in {"actions", "decisions"}:
            if claim.subjects:
                if not self.role_bound or len(claim.subjects) != 1:
                    return None
                subject = claim.subjects[0]
                return RequiredReadPlan(
                    family,
                    "search_role_candidates",
                    claim.capability,
                    {
                        "q": subject,
                        "application_outcome": None,
                        "limit": self.page_size,
                        "offset": 0,
                    },
                    resolution_subject=subject,
                )
            return self._history_plan_for_claim(family, claim)

        filters = claim.filter_map
        if family == "qualitative":
            return RequiredReadPlan(
                family,
                "find_top_candidates",
                claim.capability,
                {"query": self.ledger.request_text, "limit": 10},
            )

        tool_name = (
            "search_role_candidates" if self.role_bound else "search_applications"
        )
        args = {"limit": self.page_size, "offset": 0}
        supported = {
            "q",
            "pipeline_stage",
            "application_outcome",
            "min_score",
            "score_type",
            "sort_by",
            "sort_order",
        }
        if self.role_bound:
            supported.update({"ats_stage", "workable_stage", "has_pending_decision"})
        for key in supported:
            value = filters.get(key)
            if value is None:
                continue
            if key == "has_pending_decision":
                args[key] = _bool_filter(value)
            elif key == "pipeline_stage":
                args[key] = value.replace(" ", "_")
            else:
                args[key] = value
        return RequiredReadPlan(family, tool_name, claim.capability, args)

    def _history_plan_for_claim(
        self,
        family: str,
        claim: GroundingClaim,
    ) -> RequiredReadPlan | None:
        filters = claim.filter_map
        if family == "actions":
            if not self.role_bound:
                return None
            args: dict[str, Any] = {
                "status": "confirmed",
                "result_view": self._action_result_view(),
                "limit": self.page_size,
                "offset": 0,
            }
            for key in (
                "application_id",
                "candidate_id",
                "action",
                "target_stage",
                "actor_type",
                "actor_id",
            ):
                if value := filters.get(key):
                    args[key] = (
                        int(value)
                        if key in {"application_id", "candidate_id", "actor_id"}
                        else value
                    )
            if value := filters.get("time_after"):
                args["occurred_after"] = value
            if value := filters.get("time_before"):
                args["occurred_before"] = value
            return RequiredReadPlan(
                family, "list_candidate_actions", claim.capability, args
            )

        if family == "decisions":
            args = {"limit": self.page_size, "offset": 0}
            for key in (
                "status",
                "application_id",
                "candidate_id",
                "decision_type",
            ):
                if value := filters.get(key):
                    args[key] = (
                        int(value)
                        if key in {"application_id", "candidate_id"}
                        else value
                    )
            time_prefix = (
                "resolved" if filters.get("time_axis") == "resolved" else "created"
            )
            if value := filters.get("time_after"):
                args[f"{time_prefix}_after"] = value
            if value := filters.get("time_before"):
                args[f"{time_prefix}_before"] = value
            return RequiredReadPlan(
                family,
                "list_recent_agent_decisions",
                claim.capability,
                args,
            )
        return None

    def _action_result_view(self) -> str:
        text = self.ledger.request_text
        asks_for_candidates = bool(
            re.search(
                r"\b(?:candidates?|applicants?|people|persons?|who|whom|names?|everyone)\b",
                text,
                re.IGNORECASE,
            )
        )
        asks_for_events = bool(
            re.search(
                r"\b(?:when|event|events|history|historical|chronology|each\s+time)\b",
                text,
                re.IGNORECASE,
            )
        )
        return "candidates" if asks_for_candidates and not asks_for_events else "events"

    @staticmethod
    def _result_scope_matches(
        plan: RequiredReadPlan,
        result: dict[str, Any],
    ) -> bool:
        filters = result.get("filters")
        if not isinstance(filters, dict):
            return False
        for key in _RESULT_FILTER_KEYS[plan.tool_name]:
            if key not in plan.arguments:
                continue
            if _scope_value(filters.get(key)) != _scope_value(plan.arguments[key]):
                return False
        return True


__all__ = [
    "ROLE_SCOPE_REQUIRED_MESSAGE",
    "RequiredReadController",
    "RequiredReadPlan",
]
