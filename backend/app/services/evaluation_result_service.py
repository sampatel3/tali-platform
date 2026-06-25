from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .evaluation_service import calculate_weighted_rubric_score


ALLOWED_MANUAL_SCORES = {"excellent", "good", "poor"}
ALLOWED_EVALUATION_DECISIONS = {"advance", "hold", "reject"}
ALLOWED_EVALUATION_CONFIDENCE = {"low", "medium", "high"}
# A recorded decision is either a working ``draft`` or a ``submitted`` (final,
# recorded) state. Both are editable — submitting again just records a new
# version — but the distinction lets the UI show "Draft" vs "Recorded" and the
# audit trail capture when the decision actually became the source of truth.
ALLOWED_DECISION_STATUS = {"draft", "submitted"}
# Keep the in-row change log bounded so the JSON column can't grow without limit
# under repeated manual edits. The newest entries are the ones recruiters care
# about; older ones roll off.
MAX_DECISION_HISTORY = 20


def _to_evidence_list(value: Any) -> List[str]:
    if isinstance(value, list):
        items = [str(v).strip() for v in value]
    elif isinstance(value, str):
        items = [line.strip() for line in value.splitlines()]
    elif value is None:
        items = []
    else:
        items = [str(value).strip()]
    return [item for item in items if item]


def _to_notes_list(value: Any) -> List[str]:
    if isinstance(value, list):
        items = [str(v).strip() for v in value]
    elif isinstance(value, str):
        items = [line.strip() for line in value.splitlines()]
    else:
        items = []
    return [item for item in items if item]


def _safe_weight(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _normalized_choice(value: Any, allowed: set[str], label: str) -> Optional[str]:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    if normalized not in allowed:
        allowed_values = ", ".join(sorted(allowed))
        raise ValueError(f"{label} must be one of {allowed_values}")
    return normalized


def _normalized_rationale(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def author_from_user(user: Any) -> Optional[Dict[str, Any]]:
    """Capture a compact {id, name} stamp for the recruiter making the edit.

    Stored alongside the decision so manual updates are attributable. Kept
    deliberately small (no email/role) — it's an audit label, not a user copy.
    """
    if user is None:
        return None
    name = (
        getattr(user, "full_name", None)
        or getattr(user, "email", None)
        or "Recruiter"
    )
    return {"id": getattr(user, "id", None), "name": str(name)}


def _normalized_status(value: Any, default: str = "submitted") -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in ALLOWED_DECISION_STATUS else default


def _decision_action(new_status: str, prior_status: str) -> str:
    """Classify what this save did, for the history log.

    draft save → ``saved_draft``; first time a decision is recorded →
    ``submitted``; re-recording an already-submitted decision → ``updated``.
    """
    if new_status == "draft":
        return "saved_draft"
    if prior_status == "submitted":
        return "updated"
    return "submitted"


def _rationale_excerpt(value: Any, limit: int = 160) -> str:
    text = str(value or "").strip().replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _normalized_author(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    name = value.get("name")
    return {"id": value.get("id"), "name": str(name) if name is not None else None}


def _normalized_history(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    entries: List[Dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        entries.append(
            {
                "version": int(raw.get("version", 0) or 0),
                "action": str(raw.get("action") or ""),
                "status": _normalized_status(raw.get("status")),
                "decision": raw.get("decision"),
                "confidence": raw.get("confidence"),
                "rationale_excerpt": str(raw.get("rationale_excerpt") or ""),
                "at": raw.get("at"),
                "by": _normalized_author(raw.get("by")),
            }
        )
    return entries[-MAX_DECISION_HISTORY:]


def apply_decision_metadata(
    result: Dict[str, Any],
    *,
    prior: Optional[Dict[str, Any]],
    status: Any,
    author: Optional[Dict[str, Any]],
    now_iso: str,
) -> Dict[str, Any]:
    """Stamp draft/submitted lifecycle metadata onto a freshly built decision.

    ``prior`` is the decision currently stored (so we can bump the version,
    preserve ``submitted_at`` across draft edits, and append to the change
    log). Shared by both the assessment evaluation and the application-level
    decision so the two surfaces behave identically.
    """
    prior = prior if isinstance(prior, dict) else {}
    prior_version = int(prior.get("version", 0) or 0)
    prior_status = str(prior.get("status") or "").strip().lower()
    new_status = _normalized_status(status)
    new_version = prior_version + 1
    action = _decision_action(new_status, prior_status)

    # ``submitted_at`` marks when the decision last became a recorded source of
    # truth. A draft save preserves any prior submission timestamp rather than
    # clearing it (the recorded decision still stands until re-submitted).
    submitted_at = now_iso if new_status == "submitted" else prior.get("submitted_at")

    history = _normalized_history(prior.get("history"))
    history.append(
        {
            "version": new_version,
            "action": action,
            "status": new_status,
            "decision": result.get("decision"),
            "confidence": result.get("confidence"),
            "rationale_excerpt": _rationale_excerpt(result.get("rationale")),
            "at": now_iso,
            "by": author,
        }
    )

    result["status"] = new_status
    result["version"] = new_version
    result["updated_at"] = now_iso
    result["updated_by"] = author
    result["submitted_at"] = submitted_at
    result["history"] = history[-MAX_DECISION_HISTORY:]
    return result


def _decision_metadata_fields(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Read-side projection of the lifecycle metadata for API responses."""
    return {
        "status": _normalized_status(raw.get("status")),
        "version": int(raw.get("version", 0) or 0),
        "updated_at": raw.get("updated_at"),
        "updated_by": _normalized_author(raw.get("updated_by")),
        "submitted_at": raw.get("submitted_at"),
        "history": _normalized_history(raw.get("history")),
    }


def _normalized_category_scores(
    category_scores: Dict[str, Any],
    evaluation_rubric: Dict[str, Any],
    require_evidence_for_scored: bool = False,
) -> Dict[str, Dict[str, Any]]:
    normalized: Dict[str, Dict[str, Any]] = {}
    for category, raw_value in (category_scores or {}).items():
        data = raw_value if isinstance(raw_value, dict) else {"score": raw_value}
        score = str((data.get("score") or "")).strip().lower()
        if score and score not in ALLOWED_MANUAL_SCORES:
            raise ValueError(f"Score for {category} must be one of excellent, good, poor")
        evidence = _to_evidence_list(data.get("evidence"))
        if score and require_evidence_for_scored and not evidence:
            raise ValueError(f"Evidence is required for scored category '{category}'")
        weight = _safe_weight((evaluation_rubric.get(category) or {}).get("weight"))
        normalized[category] = {
            "score": score or None,
            "weight": weight,
            "evidence": evidence,
        }
    return normalized


def _overall_score(
    category_scores: Dict[str, Dict[str, Any]],
    evaluation_rubric: Dict[str, Any],
) -> Optional[float]:
    flat_scores = {
        category: details.get("score")
        for category, details in category_scores.items()
        if isinstance(details, dict) and details.get("score")
    }
    if not flat_scores:
        return None
    # Converts weighted grade scale (1..3) into the candidate-facing 0..100
    # range (the UI renders this value as "/100").
    return round(calculate_weighted_rubric_score(flat_scores, evaluation_rubric) * (100.0 / 3.0), 2)


def build_evaluation_result(
    *,
    assessment_id: int,
    completed_due_to_timeout: bool,
    evaluation_rubric: Dict[str, Any],
    body: Dict[str, Any],
    author: Optional[Dict[str, Any]] = None,
    prior: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    category_scores = _normalized_category_scores(
        body.get("category_scores") or {},
        evaluation_rubric,
        require_evidence_for_scored=True,
    )
    result = {
        "assessment_id": assessment_id,
        "completed_due_to_timeout": bool(completed_due_to_timeout),
        "category_scores": category_scores,
        "overall_score": _overall_score(category_scores, evaluation_rubric),
        "decision": _normalized_choice(
            body.get("decision"),
            ALLOWED_EVALUATION_DECISIONS,
            "decision",
        ),
        "rationale": _normalized_rationale(body.get("rationale")),
        "confidence": _normalized_choice(
            body.get("confidence"),
            ALLOWED_EVALUATION_CONFIDENCE,
            "confidence",
        ),
        "next_steps": _to_notes_list(body.get("next_steps")),
        "strengths": _to_notes_list(body.get("strengths")),
        "improvements": _to_notes_list(body.get("improvements")),
    }
    return apply_decision_metadata(
        result,
        prior=prior,
        status=body.get("status"),
        author=author,
        now_iso=datetime.now(timezone.utc).isoformat(),
    )


def normalize_stored_evaluation_result(
    raw: Dict[str, Any] | None,
    *,
    assessment_id: int,
    completed_due_to_timeout: bool,
    evaluation_rubric: Dict[str, Any],
) -> Dict[str, Any] | None:
    if not raw or not isinstance(raw, dict):
        return None
    category_scores = _normalized_category_scores(
        raw.get("category_scores") or {},
        evaluation_rubric,
        require_evidence_for_scored=False,
    )
    return {
        "assessment_id": raw.get("assessment_id") or assessment_id,
        "completed_due_to_timeout": bool(
            raw.get("completed_due_to_timeout", completed_due_to_timeout)
        ),
        "category_scores": category_scores,
        "overall_score": raw.get("overall_score")
        if raw.get("overall_score") is not None
        else _overall_score(category_scores, evaluation_rubric),
        "decision": _normalized_choice(
            raw.get("decision"),
            ALLOWED_EVALUATION_DECISIONS,
            "decision",
        ),
        "rationale": _normalized_rationale(raw.get("rationale")),
        "confidence": _normalized_choice(
            raw.get("confidence"),
            ALLOWED_EVALUATION_CONFIDENCE,
            "confidence",
        ),
        "next_steps": _to_notes_list(raw.get("next_steps")),
        "strengths": _to_notes_list(raw.get("strengths")),
        "improvements": _to_notes_list(raw.get("improvements")),
        **_decision_metadata_fields(raw),
    }


def build_application_decision(
    *,
    body: Dict[str, Any],
    author: Optional[Dict[str, Any]] = None,
    prior: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build an application-level recruiter decision (no assessment rubric).

    For candidates with no assessment linked (e.g. rejected at CV stage), the
    recruiter can still record/update a decision against the application. Shares
    the exact decision + draft/submitted lifecycle of the assessment evaluation,
    minus the rubric-scoring fields.
    """
    result = {
        "decision": _normalized_choice(
            body.get("decision"),
            ALLOWED_EVALUATION_DECISIONS,
            "decision",
        ),
        "rationale": _normalized_rationale(body.get("rationale")),
        "confidence": _normalized_choice(
            body.get("confidence"),
            ALLOWED_EVALUATION_CONFIDENCE,
            "confidence",
        ),
        "next_steps": _to_notes_list(body.get("next_steps")),
    }
    return apply_decision_metadata(
        result,
        prior=prior,
        status=body.get("status"),
        author=author,
        now_iso=datetime.now(timezone.utc).isoformat(),
    )


def normalize_stored_application_decision(
    raw: Dict[str, Any] | None,
) -> Dict[str, Any] | None:
    if not raw or not isinstance(raw, dict):
        return None
    return {
        "decision": _normalized_choice(
            raw.get("decision"),
            ALLOWED_EVALUATION_DECISIONS,
            "decision",
        ),
        "rationale": _normalized_rationale(raw.get("rationale")),
        "confidence": _normalized_choice(
            raw.get("confidence"),
            ALLOWED_EVALUATION_CONFIDENCE,
            "confidence",
        ),
        "next_steps": _to_notes_list(raw.get("next_steps")),
        **_decision_metadata_fields(raw),
    }
