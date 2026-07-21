"""Required/preferred criterion normalization and verified-match policy."""

from __future__ import annotations

from typing import Any

from ..models.candidate_application import CandidateApplication
from .constraint_policy import (
    _is_constraint,
    _is_junk_criterion,
    _merge_constraint_fragments,
    _recompute_self_score_verdict,
    _tokens,
)
from .grounded_evidence import CriterionVerdict, Evidence

DEFAULT_MAX_CRITERIA = 8
DEFAULT_ROLE_EVIDENCE_LIMIT = 3


def _graph_predicate_text(predicate) -> str:
    """Render a legacy graph predicate as a source-verifiable statement."""

    value = str(getattr(predicate, "value", "") or "").strip()
    if not value:
        return ""
    predicate_type = str(getattr(predicate, "type", "") or "").strip()
    if predicate_type == "worked_at":
        return f"worked at {value}"
    if predicate_type == "studied_at":
        return f"studied at {value}"
    if predicate_type == "colleague_of":
        return f"was a colleague of {value}"
    if predicate_type == "n_hop_from":
        hops = getattr(predicate, "n_hops", None)
        distance = f" within {int(hops)} hops" if hops else ""
        return f"connected{distance} to candidate {value}"
    return value


def _graph_criterion_inputs(parsed) -> list[str]:
    rendered = [
        text
        for predicate in (getattr(parsed, "graph_predicates", []) or [])
        if (text := _graph_predicate_text(predicate))
    ]
    if len(rendered) > 1 and getattr(parsed, "graph_predicate_operator", "all") == "any":
        # One verdict preserves the user's OR. Evaluating each branch as a
        # separate required criterion would silently turn OR into AND.
        return [" OR ".join(rendered)]
    return rendered


def _criterion_inputs(parsed) -> list[tuple[str, str]]:
    required = [
        *list(parsed.soft_criteria),
        *list(parsed.keywords),
        *_graph_criterion_inputs(parsed),
    ]
    preferred = list(getattr(parsed, "preferred_criteria", []) or [])
    return [
        *((str(text or ""), "required") for text in required),
        *((str(text or ""), "preferred") for text in preferred),
    ]


def _criteria_related(left: str, right: str) -> bool:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return left.strip().lower() == right.strip().lower()
    return left_tokens <= right_tokens or right_tokens <= left_tokens


def _collect_criteria(
    parsed, *, limit: int | None = DEFAULT_MAX_CRITERIA
) -> list[str]:
    """Normalize qualitative criteria without collapsing their modality."""

    raw: list[tuple[str, str, int]] = []
    seen: dict[str, int] = {}
    for source_index, (text, priority) in enumerate(_criterion_inputs(parsed)):
        criterion = text.strip()
        key = criterion.lower()
        if not criterion or _is_junk_criterion(criterion):
            continue
        existing = seen.get(key)
        if existing is not None:
            old_text, old_priority, old_index = raw[existing]
            if priority == "required" and old_priority != "required":
                raw[existing] = (old_text, "required", old_index)
            continue
        seen[key] = len(raw)
        raw.append((criterion, priority, source_index))

    collapsed: list[tuple[str, str, int]] = []
    for index, row in enumerate(raw):
        text, priority, _source_index = row
        row_tokens = _tokens(text)
        dominated = False
        for other_index, other in enumerate(raw):
            if index == other_index:
                continue
            other_text, other_priority, _ = other
            other_tokens = _tokens(other_text)
            if not row_tokens or not other_tokens:
                continue
            same_priority_redundant = (
                priority == other_priority
                and (
                    row_tokens < other_tokens
                    or (row_tokens == other_tokens and other_index < index)
                )
            )
            optional_redundant_with_stricter_required = (
                priority == "preferred"
                and other_priority == "required"
                and row_tokens <= other_tokens
            )
            if same_priority_redundant or optional_redundant_with_stricter_required:
                dominated = True
                break
        if not dominated:
            collapsed.append(row)

    collapsed.sort(key=lambda row: (row[1] != "required", row[2]))
    kept = [row[0] for row in collapsed]
    kept = _merge_constraint_fragments(kept, getattr(parsed, "free_text", None))
    return kept if limit is None else kept[: max(0, int(limit))]


def _required_criteria(parsed, criteria: list[str]) -> list[str]:
    required_inputs = [
        text.strip()
        for text, priority in _criterion_inputs(parsed)
        if priority == "required" and text.strip() and not _is_junk_criterion(text)
    ]
    required_exact = {criterion.lower() for criterion in required_inputs}
    preferred_exact = {
        str(criterion or "").strip().lower()
        for criterion in getattr(parsed, "preferred_criteria", []) or []
        if str(criterion or "").strip()
    }
    out: list[str] = []
    for criterion in criteria:
        key = criterion.lower()
        if key in required_exact:
            out.append(criterion)
            continue
        if key in preferred_exact:
            continue
        if any(_criteria_related(criterion, raw) for raw in required_inputs):
            out.append(criterion)
    return out


def _preferred_criteria(parsed, criteria: list[str]) -> list[str]:
    required = {criterion.lower() for criterion in _required_criteria(parsed, criteria)}
    return [criterion for criterion in criteria if criterion.lower() not in required]


_ROLE_PRIORITY_ORDER = {
    "constraint": 0,
    "must_have": 1,
    "strong_preference": 2,
    "nice_to_have": 3,
}


def _stored_role_requirement_verdicts(
    app: CandidateApplication,
    *,
    limit: int = DEFAULT_ROLE_EVIDENCE_LIMIT,
) -> list[CriterionVerdict]:
    """Reuse citation-bearing stored role-scorecard rows for a bare top-N."""

    details = getattr(app, "cv_match_details", None)
    rows = details.get("requirements_assessment") if isinstance(details, dict) else None
    if not isinstance(rows, list):
        return []
    indexed = [
        (index, row) for index, row in enumerate(rows) if isinstance(row, dict)
    ]
    indexed.sort(
        key=lambda item: (
            _ROLE_PRIORITY_ORDER.get(
                str(item[1].get("priority") or "").strip().lower(), 4
            ),
            item[0],
        )
    )

    verdicts: list[CriterionVerdict] = []
    for _, row in indexed:
        criterion = str(
            row.get("requirement")
            or row.get("criterion_text")
            or row.get("label")
            or ""
        ).strip()
        if not criterion:
            continue
        raw_quotes = row.get("evidence_quotes")
        if not isinstance(raw_quotes, list):
            raw = row.get("evidence") or row.get("cv_quote")
            raw_quotes = raw if isinstance(raw, list) else ([raw] if raw else [])
        quotes = [
            quote.strip()
            for quote in raw_quotes
            if isinstance(quote, str) and quote.strip()
        ][:3]
        raw_status = (
            str(row.get("status") or "missing").strip().lower().replace(" ", "_")
        )
        status = {
            "partial": "partially_met",
            "partially": "partially_met",
            "unknown": "missing",
        }.get(raw_status, raw_status)
        if status not in {"met", "partially_met", "not_met", "missing", "error"}:
            status = "missing"
        verdicts.append(
            CriterionVerdict(
                criterion=criterion,
                status=status,
                grounded=bool(quotes),
                source="role_requirement" if quotes else "none",
                evidence=[
                    Evidence(quote=quote, source="role_requirement") for quote in quotes
                ],
                note=str(row.get("reasoning") or row.get("impact") or "").strip(),
            )
        )
        if len(verdicts) >= max(1, int(limit)):
            break
    return verdicts


def _fully_met_count(
    rows: list[tuple[CandidateApplication, list[CriterionVerdict]]],
    criteria: list[str] | None = None,
) -> int:
    selected = {criterion.lower() for criterion in (criteria or [])}
    return sum(
        1
        for _app, verdicts in rows
        if verdicts
        and all(
            verdict.status == "met" and verdict.grounded
            for verdict in verdicts
            if not selected or verdict.criterion.lower() in selected
        )
        and (
            not selected
            or selected <= {verdict.criterion.lower() for verdict in verdicts}
        )
    )


def _evidence_succeeded_count(
    rows: list[tuple[CandidateApplication, list[CriterionVerdict]]],
) -> int:
    return sum(
        1
        for _app, verdicts in rows
        if verdicts and all(verdict.status != "error" for verdict in verdicts)
    )


def _partition_required_matches(
    rows: list[tuple[CandidateApplication, list[CriterionVerdict]]],
    required_criteria: list[str],
) -> tuple[
    list[tuple[CandidateApplication, list[CriterionVerdict]]],
    dict[str, Any],
]:
    """Separate verified required matches from missing or failed evidence."""

    survivors: list[tuple[CandidateApplication, list[CriterionVerdict]]] = []
    candidate_buckets = {
        "not_met": 0,
        "missing": 0,
        "partial": 0,
        "unverified": 0,
    }
    by_criterion: dict[str, dict[str, Any]] = {}
    for app, verdicts in rows:
        for verdict in verdicts:
            _recompute_self_score_verdict(verdict, app)
        verdict_by_key = {verdict.criterion.lower(): verdict for verdict in verdicts}
        blockers: list[CriterionVerdict] = []
        for criterion in required_criteria:
            verdict = verdict_by_key.get(criterion.lower())
            if verdict is None:
                verdict = CriterionVerdict(
                    criterion=criterion,
                    status="error",
                    note="Evidence check returned no verdict.",
                )
            if _is_constraint(criterion):
                if verdict.status == "not_met" and verdict.grounded:
                    blockers.append(verdict)
                continue
            if not (verdict.status == "met" and verdict.grounded):
                blockers.append(verdict)
        if not blockers:
            survivors.append((app, verdicts))
            continue

        seen_buckets: set[str] = set()
        for verdict in blockers:
            if verdict.status == "not_met" and verdict.grounded:
                bucket = "not_met"
            elif verdict.status == "partially_met" and verdict.grounded:
                bucket = "partial"
            elif verdict.status == "missing":
                bucket = "missing"
            else:
                bucket = "unverified"
            seen_buckets.add(bucket)
            summary = by_criterion.setdefault(
                verdict.criterion,
                {"criterion": verdict.criterion, "count": 0, "statuses": {}},
            )
            summary["count"] += 1
            summary["statuses"][bucket] = summary["statuses"].get(bucket, 0) + 1
        for bucket in seen_buckets:
            candidate_buckets[bucket] += 1
    return survivors, {
        "required_total": len(rows) - len(survivors),
        "not_met_total": candidate_buckets["not_met"],
        "missing_total": candidate_buckets["missing"],
        "partial_total": candidate_buckets["partial"],
        "unverified_total": candidate_buckets["unverified"],
        "by_criterion": list(by_criterion.values()),
    }


__all__ = [
    "_collect_criteria",
    "_evidence_succeeded_count",
    "_fully_met_count",
    "_partition_required_matches",
    "_preferred_criteria",
    "_required_criteria",
    "_stored_role_requirement_verdicts",
]
