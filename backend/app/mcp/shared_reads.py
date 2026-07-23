"""Shared candidate-read definitions and in-process dispatch.

The four model-facing surfaces use different authentication and mutation
plumbing.  Their authoritative candidate reads do not: contracts live in the
MCP catalogue and resolve to the same pure handlers here.  A role-bound
surface hides ``role_id`` from the model and this adapter injects it before
strict validation, so a guessed id can never escape the active role.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from . import handlers, operations
from .catalog import (
    CANDIDATE_ACTION_HISTORY,
    CANDIDATE_ACTION_HISTORY_EXHAUSTIVE,
    CANDIDATE_DECISION_HISTORY,
    CANDIDATE_DECISION_HISTORY_EXHAUSTIVE,
    CANDIDATE_POOL_EXHAUSTIVE,
    CANDIDATE_POOL_STATE,
    CANDIDATE_QUALITATIVE_EVIDENCE,
    CANDIDATE_QUALITATIVE_EXACT_EMPTY,
    ToolSpec,
    get_tool_spec,
    tools_for,
)
from .provenance import (
    GroundingClaim,
    grounding_claims_for_message,
    meaningful_qualitative_terms,
    normalize_claim_value,
)


_QUALITATIVE_SEARCH_TOOLS = frozenset(
    {
        "find_top_candidates",
        "screen_pool_against_requirement",
    }
)
_HISTORY_CAPABILITIES = frozenset(
    {
        CANDIDATE_ACTION_HISTORY,
        CANDIDATE_ACTION_HISTORY_EXHAUSTIVE,
        CANDIDATE_DECISION_HISTORY,
        CANDIDATE_DECISION_HISTORY_EXHAUSTIVE,
    }
)


def _qualitative_query_matches_request(
    *, arguments: dict[str, Any], request_text: str | None
) -> bool:
    query = arguments.get("query") or arguments.get("requirement_text")
    query_terms = set(meaningful_qualitative_terms(query))
    if not query_terms:
        return False
    if not request_text:
        return True
    request_terms = set(meaningful_qualitative_terms(request_text))
    return bool(request_terms and request_terms.issubset(query_terms))


def _criterion_is_cited_met(raw: object) -> bool:
    if not isinstance(raw, dict):
        return False
    evidence = raw.get("evidence")
    return bool(
        raw.get("status") == "met"
        and raw.get("grounded") is True
        and isinstance(evidence, list)
        and any(
            isinstance(item, dict) and bool(str(item.get("quote") or "").strip())
            for item in evidence
        )
    )


def _required_criteria(result: dict[str, Any]) -> list[str]:
    explicit = result.get("required_criteria")
    if isinstance(explicit, list):
        return [str(item).strip() for item in explicit if str(item).strip()]
    spec = result.get("spec")
    rows = spec.get("criteria") if isinstance(spec, dict) else None
    if not isinstance(rows, list):
        return []
    return [
        str(row.get("text") or "").strip()
        for row in rows
        if isinstance(row, dict)
        and row.get("priority") != "preferred"
        and str(row.get("text") or "").strip()
    ]


def _candidate_has_cited_required_evidence(
    candidate: object, required_criteria: list[str]
) -> bool:
    if not isinstance(candidate, dict) or not required_criteria:
        return False
    verdicts = candidate.get("criteria")
    if not isinstance(verdicts, list):
        return False
    by_criterion = {
        str(row.get("criterion") or "").strip().lower(): row
        for row in verdicts
        if isinstance(row, dict)
    }
    return all(
        _criterion_is_cited_met(by_criterion.get(criterion.lower()))
        for criterion in required_criteria
    )


def _qualitative_capabilities(
    name: str,
    result: Any,
    *,
    arguments: dict[str, Any] | None,
    request_text: str | None,
) -> frozenset[str]:
    """Validate cited positives and truly exhaustive qualitative negatives."""

    if name not in _QUALITATIVE_SEARCH_TOOLS or not isinstance(result, dict):
        return frozenset()
    safe_arguments = dict(arguments or {})
    if not _qualitative_query_matches_request(
        arguments=safe_arguments,
        request_text=request_text,
    ):
        return frozenset()
    required = _required_criteria(result)
    requested = result.get("criteria_requested")
    unchecked = result.get("criteria_unchecked")
    if (
        not required
        or not isinstance(requested, list)
        or not requested
        or (isinstance(unchecked, list) and bool(unchecked))
    ):
        return frozenset()
    requested_terms = set(meaningful_qualitative_terms(request_text))
    criterion_terms = {
        term
        for criterion in required
        for term in meaningful_qualitative_terms(criterion)
    }
    if requested_terms and not requested_terms.issubset(criterion_terms):
        return frozenset()

    candidates = result.get("candidates")
    if isinstance(candidates, list) and any(
        _candidate_has_cited_required_evidence(candidate, required)
        for candidate in candidates
    ):
        return frozenset({CANDIDATE_QUALITATIVE_EVIDENCE})

    try:
        checked = int(result.get("deep_checked") or 0)
        succeeded = int(result.get("evidence_succeeded") or 0)
        if result.get("role_roster_size") is None or result.get("pool_size") is None:
            return frozenset()
        roster_size = int(result["role_roster_size"])
        pool_size = int(result["pool_size"])
    except (TypeError, ValueError):
        return frozenset()
    exact_negative = bool(
        not candidates
        and result.get("search_status") == "no_verified_matches"
        and result.get("qualified_total") == 0
        and result.get("capped") is False
        and result.get("exhaustive") is True
        and roster_size >= 0
        and pool_size == roster_size
        and checked == roster_size
        and succeeded == checked
    )
    if exact_negative:
        return frozenset(
            {
                CANDIDATE_QUALITATIVE_EVIDENCE,
                CANDIDATE_QUALITATIVE_EXACT_EMPTY,
            }
        )
    return frozenset()


def _is_complete_exact_page(result: Any) -> bool:
    if not isinstance(result, dict) or result.get("total_is_exact") is not True:
        return False
    items = result.get("items")
    if not isinstance(items, list):
        return False
    try:
        return bool(
            int(result.get("offset") or 0) == 0
            and result.get("has_more") is False
            and len(items) == int(result.get("total") or 0)
        )
    except (TypeError, ValueError):
        return False


_EXHAUSTIVE_CAPABILITY = {
    CANDIDATE_POOL_STATE: CANDIDATE_POOL_EXHAUSTIVE,
    CANDIDATE_ACTION_HISTORY: CANDIDATE_ACTION_HISTORY_EXHAUSTIVE,
    CANDIDATE_DECISION_HISTORY: CANDIDATE_DECISION_HISTORY_EXHAUSTIVE,
}
_PAGED_READS: dict[str, tuple[str, str]] = {
    "search_role_candidates": (CANDIDATE_POOL_STATE, "application_id"),
    "list_candidate_actions": (CANDIDATE_ACTION_HISTORY, "event_id"),
    "list_recent_agent_decisions": (CANDIDATE_DECISION_HISTORY, "id"),
}


def _normalized_scope_value(value: object) -> str:
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
    if "T" in raw or re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
        else:
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            return moment.astimezone(timezone.utc).isoformat()
    return normalize_claim_value(raw)


def _candidate_names(result: dict[str, Any]) -> tuple[str, ...]:
    names: set[str] = set()
    candidates = result.get("candidates")
    if not isinstance(candidates, list):
        return ()
    required = _required_criteria(result)
    for candidate in candidates:
        if not _candidate_has_cited_required_evidence(candidate, required):
            continue
        if not isinstance(candidate, dict):
            continue
        raw_name = (
            candidate.get("candidate_name")
            or candidate.get("full_name")
            or candidate.get("name")
        )
        normalized = normalize_claim_value(raw_name)
        if normalized:
            names.add(normalized)
    return tuple(sorted(names))


def _item_candidate_name(item: dict[str, Any]) -> str:
    raw_name = item.get("candidate_name") or item.get("full_name") or item.get("name")
    candidate = item.get("candidate")
    if not raw_name and isinstance(candidate, dict):
        raw_name = (
            candidate.get("candidate_name")
            or candidate.get("full_name")
            or candidate.get("name")
        )
    return normalize_claim_value(raw_name)


def _result_candidate_names(result: dict[str, Any]) -> tuple[str, ...]:
    rows: list[dict[str, Any]] = [result]
    for key in ("items", "candidates", "applications"):
        value = result.get(key)
        if isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, dict))
    return tuple(
        sorted(
            {normalized for item in rows if (normalized := _item_candidate_name(item))}
        )
    )


@dataclass(frozen=True)
class GroundingCertificate:
    """A successful canonical read bound to its exact factual scope."""

    capability: str
    filters: tuple[tuple[str, str], ...] = ()
    terms: tuple[str, ...] = ()
    candidate_names: tuple[str, ...] = ()
    total: int | None = None

    @property
    def filter_map(self) -> dict[str, str]:
        return dict(self.filters)


@dataclass
class _PageAccumulator:
    total: int
    id_field: str
    pages: dict[int, tuple[object, ...]] = field(default_factory=dict)
    names: dict[int, tuple[str, ...]] = field(default_factory=dict)
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
        identifiers: list[object] = []
        candidate_names: list[str] = []
        for item in items:
            if not isinstance(item, dict) or item.get(self.id_field) is None:
                self.invalid = True
                return False
            identifiers.append(item[self.id_field])
            if name := _item_candidate_name(item):
                candidate_names.append(name)
        page_ids = tuple(identifiers)
        existing = self.pages.get(offset)
        if existing is not None and existing != page_ids:
            self.invalid = True
            return False
        self.pages[offset] = page_ids
        self.names[offset] = tuple(sorted(set(candidate_names)))
        expected_has_more = offset + len(page_ids) < total
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
        seen_ids: set[object] = set()
        for offset, identifiers in sorted(self.pages.items()):
            if offset != expected_offset:
                return False
            if seen_ids.intersection(identifiers):
                return False
            seen_ids.update(identifiers)
            expected_offset += len(identifiers)
        return bool(
            expected_offset == self.total
            and any(
                offset + len(self.pages[offset]) == self.total
                for offset in self.terminal_offsets
            )
        )

    @property
    def candidate_names(self) -> tuple[str, ...]:
        return tuple(sorted({name for names in self.names.values() for name in names}))


def _paged_scope(
    name: str,
    result: dict[str, Any],
    arguments: dict[str, Any],
) -> tuple[tuple[str, str], ...]:
    filters = result.get("filters")
    raw_filters = dict(filters) if isinstance(filters, dict) else {}
    if name == "search_role_candidates":
        keys = (
            "q",
            "pipeline_stage",
            "application_outcome",
            "ats_stage",
            "has_pending_decision",
            "min_score",
            "score_type",
            "sort_by",
            "sort_order",
        )
    elif name == "list_candidate_actions":
        keys = (
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
        )
    else:
        keys = (
            "role_id",
            "status",
            "application_id",
            "candidate_id",
            "decision_type",
            "created_after",
            "created_before",
            "resolved_after",
            "resolved_before",
        )
    scope: dict[str, str] = {}
    for key in keys:
        value = raw_filters.get(key, arguments.get(key))
        if value is None or str(value).strip() == "":
            continue
        normalized = _normalized_scope_value(value)
        if normalized:
            scope[key] = normalized
    role = result.get("role")
    role_id = role.get("id") if isinstance(role, dict) else None
    role_id = role_id if role_id is not None else raw_filters.get("role_id")
    role_id = role_id if role_id is not None else arguments.get("role_id")
    if role_id is not None:
        scope["role_id"] = str(int(role_id))
    return tuple(sorted(scope.items()))


def _as_utc_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _time_scope_matches(
    claim_filters: dict[str, str],
    certificate_filters: dict[str, str],
    *,
    capability: str,
) -> bool:
    claim_after = _as_utc_datetime(claim_filters.get("time_after"))
    claim_before = _as_utc_datetime(claim_filters.get("time_before"))
    if claim_after is None and claim_before is None:
        return True
    if capability in {
        CANDIDATE_ACTION_HISTORY,
        CANDIDATE_ACTION_HISTORY_EXHAUSTIVE,
    }:
        after_key, before_key = "occurred_after", "occurred_before"
    else:
        time_axis = claim_filters.get("time_axis", "created")
        after_key = f"{time_axis}_after"
        before_key = f"{time_axis}_before"
    actual_after = _as_utc_datetime(certificate_filters.get(after_key))
    actual_before = _as_utc_datetime(certificate_filters.get(before_key))
    if actual_after is None or actual_before is None:
        return False
    # Natural-language "last week" is commonly translated as rolling seven
    # days or date-boundary seven days. Accept that one-day boundary variance,
    # while still rejecting a materially different date window.
    tolerance_seconds = 24 * 60 * 60
    return bool(
        claim_after is not None
        and claim_before is not None
        and abs((actual_after - claim_after).total_seconds()) <= tolerance_seconds
        and abs((actual_before - claim_before).total_seconds()) <= tolerance_seconds
    )


def _certificate_satisfies(
    certificate: GroundingCertificate,
    claim: GroundingClaim,
) -> bool:
    accepted_capabilities = {claim.capability}
    exhaustive = _EXHAUSTIVE_CAPABILITY.get(claim.capability)
    if exhaustive is not None:
        accepted_capabilities.add(exhaustive)
    if claim.capability == CANDIDATE_QUALITATIVE_EVIDENCE:
        accepted_capabilities.add(CANDIDATE_QUALITATIVE_EXACT_EMPTY)
    if certificate.capability not in accepted_capabilities:
        return False

    claim_filters = claim.filter_map
    certificate_filters = certificate.filter_map
    for key, expected in claim_filters.items():
        if key in {"time_after", "time_before", "time_axis"}:
            continue
        if certificate_filters.get(key) != expected:
            return False
    if not _time_scope_matches(
        claim_filters,
        certificate_filters,
        capability=claim.capability,
    ):
        return False

    claim_terms = set(claim.terms)
    certificate_terms = set(certificate.terms)
    if claim_terms:
        if claim.capability == CANDIDATE_QUALITATIVE_EXACT_EMPTY:
            if claim_terms != certificate_terms:
                return False
        elif not claim_terms.issubset(certificate_terms):
            return False
    if claim.subject_resolution_required:
        return False
    if claim.subjects and not set(claim.subjects).issubset(certificate.candidate_names):
        return False
    if claim.expected_total is not None and certificate.total != claim.expected_total:
        return False
    return True


class GroundingLedger:
    """Same-turn, claim-specific grounding certificates for chat runtimes."""

    def __init__(
        self,
        request_text: str | None,
        *,
        now: datetime | None = None,
    ) -> None:
        self.request_text = str(request_text or "")
        self.now = now or datetime.now(timezone.utc)
        required_claims = grounding_claims_for_message(
            self.request_text,
            now=self.now,
            include_values=False,
            include_subjects=True,
        )
        required_claims = tuple(
            replace(
                claim,
                subject_resolution_required=bool(claim.subjects),
            )
            if claim.capability in _HISTORY_CAPABILITIES
            else replace(claim, subjects=())
            for claim in required_claims
        )
        # A recruiter asking whether a pool is empty needs a grounded search,
        # not a preordained empty outcome. The terminal answer determines
        # whether the stronger exact-empty certificate is required.
        self.required_claims = tuple(
            replace(claim, capability=CANDIDATE_QUALITATIVE_EVIDENCE)
            if claim.capability == CANDIDATE_QUALITATIVE_EXACT_EMPTY
            else claim
            for claim in required_claims
        )
        self._certificates: set[GroundingCertificate] = set()
        self._pages: dict[
            tuple[str, tuple[tuple[str, str], ...]], _PageAccumulator
        ] = {}
        self._subject_bindings: dict[str, tuple[int, str]] = {}

    def bind_current_actor(self, actor_id: int) -> None:
        """Resolve first-person action claims to the authenticated user id."""

        resolved: list[GroundingClaim] = []
        for claim in self.required_claims:
            filters = tuple(
                (
                    key,
                    str(int(actor_id))
                    if key == "actor_id" and value == "current_user"
                    else value,
                )
                for key, value in claim.filters
            )
            resolved.append(replace(claim, filters=filters))
        self.required_claims = tuple(resolved)

    def bind_history_subject(
        self,
        *,
        capability: str,
        subject: str,
        candidate_id: int,
        candidate_name: str,
    ) -> bool:
        """Bind one request name to one canonical role-local candidate.

        The controller calls this only after an exhaustive identity lookup has
        returned exactly one candidate.  History certificates can then prove a
        negative result (an exact empty page) through ``candidate_id`` without
        requiring the candidate's name to appear in a non-existent event row.
        """

        normalized_subject = normalize_claim_value(subject)
        normalized_name = normalize_claim_value(candidate_name)
        if (
            capability not in _HISTORY_CAPABILITIES
            or not normalized_subject
            or not normalized_name
            or int(candidate_id) < 1
        ):
            return False
        existing = self._subject_bindings.get(normalized_subject)
        if existing is not None and existing[0] != int(candidate_id):
            return False

        changed = False
        resolved: list[GroundingClaim] = []
        for claim in self.required_claims:
            if claim.capability != capability or claim.subjects != (
                normalized_subject,
            ):
                resolved.append(claim)
                continue
            filters = claim.filter_map
            existing_candidate_id = filters.get("candidate_id")
            if existing_candidate_id not in {None, str(int(candidate_id))}:
                resolved.append(claim)
                continue
            filters["candidate_id"] = str(int(candidate_id))
            resolved.append(
                replace(
                    claim,
                    filters=tuple(sorted(filters.items())),
                    subjects=(),
                    subject_resolution_required=False,
                )
            )
            changed = True
        if not changed:
            return False
        binding = (int(candidate_id), normalized_name)
        self._subject_bindings[normalized_subject] = binding
        self._subject_bindings[normalized_name] = binding
        self.required_claims = tuple(resolved)
        return True

    def _bind_known_answer_subjects(self, claim: GroundingClaim) -> GroundingClaim:
        if claim.capability not in _HISTORY_CAPABILITIES or not claim.subjects:
            return claim
        bindings = [self._subject_bindings.get(subject) for subject in claim.subjects]
        if any(binding is None for binding in bindings):
            return claim
        candidate_ids = {binding[0] for binding in bindings if binding is not None}
        if len(candidate_ids) != 1:
            return claim
        candidate_id = candidate_ids.pop()
        filters = claim.filter_map
        existing_candidate_id = filters.get("candidate_id")
        if existing_candidate_id not in {None, str(candidate_id)}:
            return claim
        filters["candidate_id"] = str(candidate_id)
        return replace(claim, filters=tuple(sorted(filters.items())), subjects=())

    @property
    def capabilities(self) -> frozenset[str]:
        return frozenset(item.capability for item in self._certificates)

    def observe(
        self,
        name: str,
        result: Any,
        *,
        arguments: dict[str, Any] | None = None,
    ) -> frozenset[str]:
        """Record only successful reads, retaining their exact query scope."""

        safe_arguments = dict(arguments or {})
        capabilities = capabilities_for_successful_read(
            name,
            result,
            arguments=safe_arguments,
            request_text=self.request_text,
        )
        if not capabilities:
            return capabilities
        if isinstance(result, list):
            names = tuple(
                sorted(
                    {
                        normalized
                        for item in result
                        if isinstance(item, dict)
                        and (normalized := _item_candidate_name(item))
                    }
                )
            )
            scope = tuple(
                sorted(
                    (key, _normalized_scope_value(safe_arguments[key]))
                    for key in (
                        "q",
                        "pipeline_stage",
                        "application_outcome",
                        "min_score",
                        "score_type",
                    )
                    if safe_arguments.get(key) is not None
                )
            )
            for capability in capabilities:
                self._certificates.add(
                    GroundingCertificate(
                        capability,
                        filters=scope,
                        candidate_names=names,
                    )
                )
            return capabilities
        if not isinstance(result, dict):
            return capabilities

        paged = _PAGED_READS.get(name)
        if paged is not None and result.get("total_is_exact") is True:
            base_capability, id_field = paged
            scope = _paged_scope(name, result, safe_arguments)
            page_names = tuple(
                sorted(
                    {
                        normalized
                        for item in result.get("items") or []
                        if isinstance(item, dict)
                        and (normalized := _item_candidate_name(item))
                    }
                )
            )
            try:
                total = int(result.get("total") or 0)
            except (TypeError, ValueError):
                return capabilities
            self._certificates.add(
                GroundingCertificate(
                    base_capability,
                    filters=scope,
                    candidate_names=page_names,
                    total=total,
                )
            )
            page_key = (name, scope)
            accumulator = self._pages.get(page_key)
            if accumulator is None:
                accumulator = _PageAccumulator(total=total, id_field=id_field)
                self._pages[page_key] = accumulator
            page_set_complete = accumulator.add(result)
            if not accumulator.invalid:
                # Multiple fetched pages can jointly ground named rows even
                # when the caller did not request the entire population.
                # Exhaustiveness remains a separate, stricter certificate.
                self._certificates.add(
                    GroundingCertificate(
                        base_capability,
                        filters=scope,
                        candidate_names=accumulator.candidate_names,
                        total=total,
                    )
                )
            if page_set_complete:
                self._certificates.add(
                    GroundingCertificate(
                        _EXHAUSTIVE_CAPABILITY[base_capability],
                        filters=scope,
                        candidate_names=accumulator.candidate_names,
                        total=total,
                    )
                )

        if name in _QUALITATIVE_SEARCH_TOOLS:
            query = safe_arguments.get("query") or safe_arguments.get(
                "requirement_text"
            )
            terms = meaningful_qualitative_terms(query)
            candidate_names = _candidate_names(result)
            if CANDIDATE_QUALITATIVE_EVIDENCE in capabilities:
                self._certificates.add(
                    GroundingCertificate(
                        CANDIDATE_QUALITATIVE_EVIDENCE,
                        terms=terms,
                        candidate_names=candidate_names,
                        total=(
                            int(result["qualified_total"])
                            if result.get("qualified_total") is not None
                            else None
                        ),
                    )
                )
            if CANDIDATE_QUALITATIVE_EXACT_EMPTY in capabilities:
                self._certificates.add(
                    GroundingCertificate(
                        CANDIDATE_QUALITATIVE_EXACT_EMPTY,
                        terms=terms,
                        total=0,
                    )
                )

        structured_capabilities = set(_EXHAUSTIVE_CAPABILITY)
        structured_capabilities.update(_EXHAUSTIVE_CAPABILITY.values())
        structured_capabilities.update(
            {CANDIDATE_QUALITATIVE_EVIDENCE, CANDIDATE_QUALITATIVE_EXACT_EMPTY}
        )
        for capability in capabilities.difference(structured_capabilities):
            total: int | None = None
            if result.get("total_is_exact") is True:
                try:
                    total = int(result.get("total") or 0)
                except (TypeError, ValueError):
                    total = None
            self._certificates.add(
                GroundingCertificate(
                    capability,
                    candidate_names=_result_candidate_names(result),
                    total=total,
                )
            )
        return capabilities

    def missing_for_answer(self, answer_text: str | None) -> frozenset[str]:
        """Return unsupported request or terminal-answer claim capabilities."""

        answer_claims = tuple(
            self._bind_known_answer_subjects(claim)
            for claim in grounding_claims_for_message(answer_text, now=self.now)
        )
        claims = tuple(dict.fromkeys((*self.required_claims, *answer_claims)))
        return frozenset(
            claim.capability
            for claim in claims
            if not any(
                _certificate_satisfies(certificate, claim)
                for certificate in self._certificates
            )
        )


def shared_read_specs_for(exposure: str) -> tuple[ToolSpec, ...]:
    """Candidate-grounding read specs exposed on one transport."""

    return tuple(
        spec
        for spec in tools_for(exposure)
        if spec.effect == "read" and bool(spec.capabilities)
    )


def shared_read_definitions(
    exposure: str,
    *,
    bound_role: bool,
) -> list[dict[str, Any]]:
    """Anthropic definitions generated from the canonical typed contracts."""

    return [
        spec.anthropic_definition(bound_role=bound_role)
        for spec in shared_read_specs_for(exposure)
    ]


def _resolve_handler(spec: ToolSpec) -> Callable[..., Any]:
    matches = [
        candidate
        for module in (handlers, operations)
        if callable(candidate := getattr(module, spec.handler_name, None))
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"shared read {spec.name!r} must resolve exactly one handler "
            f"named {spec.handler_name!r}; found {len(matches)}"
        )
    return matches[0]


def dispatch_shared_read(
    name: str,
    arguments: dict[str, Any] | None,
    *,
    exposure: str,
    db: Session,
    principal: Any,
    bound_role_id: int | None = None,
    handler_kwargs: dict[str, Any] | None = None,
) -> Any:
    """Validate and dispatch one catalogue-backed candidate read."""

    spec = get_tool_spec(name)
    if spec not in shared_read_specs_for(exposure):
        raise KeyError(f"unknown shared read for {exposure}: {name}")

    raw = dict(arguments or {})
    if bound_role_id is not None and spec.role_scoped:
        supplied = raw.get("role_id")
        if supplied is not None and supplied != int(bound_role_id):
            raise ValueError(
                f"invalid arguments for {name}: role_id is bound to the active role"
            )
        raw["role_id"] = int(bound_role_id)
    safe_args = spec.validate(raw)
    extra = dict(handler_kwargs or {})
    overlap = set(safe_args).intersection(extra)
    if overlap:
        raise ValueError(
            f"duplicate server-owned handler arguments for {name}: "
            f"{', '.join(sorted(overlap))}"
        )
    return _resolve_handler(spec)(db, principal, **safe_args, **extra)


def capabilities_for_successful_read(
    name: str,
    result: Any,
    *,
    arguments: dict[str, Any] | None = None,
    request_text: str | None = None,
) -> frozenset[str]:
    """Capabilities grounded by a successful result and its actual query."""

    if isinstance(result, dict) and (
        result.get("error") or result.get("available") is False
    ):
        return frozenset()
    qualitative = _qualitative_capabilities(
        name,
        result,
        arguments=arguments,
        request_text=request_text,
    )
    # Candidate search/comparison predates the capability catalogue on a few
    # surfaces. Count those results only when they contain positive canonical
    # rows, or an explicitly exhaustive exact empty result. A capped or
    # evidence-unavailable zero must never unlock a hard-zero answer.
    if name in {
        "find_top_candidates",
        "search_candidates",
        "nl_search_candidates",
    } and isinstance(result, dict):
        rows = result.get("candidates") or result.get("applications") or []
        if isinstance(rows, list) and rows:
            return frozenset({CANDIDATE_POOL_STATE}).union(qualitative)
        if result.get("is_exact_empty") is True and result.get("exhaustive") is True:
            return frozenset({CANDIDATE_POOL_STATE}).union(qualitative)
        return qualitative
    if name == "compare_role_applications":
        rows = result.get("applications") if isinstance(result, dict) else None
        return (
            frozenset({CANDIDATE_POOL_STATE})
            if isinstance(rows, list) and bool(rows)
            else frozenset()
        )
    if name == "get_candidate" and isinstance(result, dict):
        applications = result.get("applications")
        return (
            frozenset({CANDIDATE_POOL_STATE})
            if isinstance(applications, list) and bool(applications)
            else frozenset()
        )
    if name == "search_applications":
        return (
            frozenset({CANDIDATE_POOL_STATE})
            if isinstance(result, list) and bool(result)
            else frozenset()
        )
    try:
        spec = get_tool_spec(name)
    except KeyError:
        return frozenset()
    # A partial action audit may safely surface verified rows, but it cannot
    # ground an exhaustive historical answer or a hard zero.
    if (
        CANDIDATE_ACTION_HISTORY in spec.capabilities
        and isinstance(result, dict)
        and result.get("total_is_exact") is not True
    ):
        return frozenset()
    if (
        CANDIDATE_DECISION_HISTORY in spec.capabilities
        and isinstance(result, dict)
        and result.get("total_is_exact") is not True
    ):
        return frozenset()
    if (
        CANDIDATE_POOL_STATE in spec.capabilities
        and name == "search_role_candidates"
        and isinstance(result, dict)
        and result.get("total_is_exact") is not True
    ):
        return frozenset()
    capabilities = set(spec.capabilities.union(qualitative))
    if _is_complete_exact_page(result):
        if name == "search_role_candidates":
            capabilities.add(CANDIDATE_POOL_EXHAUSTIVE)
        elif name == "list_candidate_actions":
            capabilities.add(CANDIDATE_ACTION_HISTORY_EXHAUSTIVE)
        elif name == "list_recent_agent_decisions":
            capabilities.add(CANDIDATE_DECISION_HISTORY_EXHAUSTIVE)
    return frozenset(capabilities)


__all__ = [
    "GroundingCertificate",
    "GroundingLedger",
    "capabilities_for_successful_read",
    "dispatch_shared_read",
    "shared_read_definitions",
    "shared_read_specs_for",
]
