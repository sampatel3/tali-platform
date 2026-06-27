"""LLM-as-recruiter judge for synthetic calibration labels.

Replaces the cold-start dependency on accumulated recruiter overrides.
Sonnet plays a senior recruiter and emits a probability that the
candidate would be advanced. Used by the calibrator extractor to
synthesise advance/reject labels day-1, before any real recruiter
override data exists.

When real recruiter overrides do accumulate, the calibrator extractor
weighted-blends judge labels with real overrides
(``λ·sonnet + (1−λ)·recruiter`` with λ → 0 as recruiter data grows).

Public surface:

    judge_advance_probability(jd_text, cv_text, requirements) -> float | None

Returns None on judge failure (no key, model error). Caller skips.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger("taali.cv_match.judge")

_JUDGE_MODEL = "claude-sonnet-4-6"
_JUDGE_TEMPERATURE = 0.0
_JUDGE_MAX_TOKENS = 400


_JUDGE_PROMPT = """You are a senior recruiter making an advance/reject decision.

Read the job spec and the candidate's CV. Produce a single number: the
probability you would advance this candidate to the next stage.

Rules:
- Output ONLY valid JSON, no commentary, no markdown fences.
- The number must be in [0.0, 1.0].
- 0.0 = certainly reject. 1.0 = certainly advance. 0.5 = genuinely on the fence.
- Do not refuse. Do not hedge. Make a decision.

=== JOB SPECIFICATION ===
{jd_text}

{requirements_block}
=== CANDIDATE CV ===
{cv_text}

=== OUTPUT ===

{{
    "p_advance": <float in [0.0, 1.0]>,
    "reasoning": "<one or two sentences>"
}}
"""


def _render_requirements(requirements) -> str:
    if not requirements:
        return ""
    lines = ["=== RECRUITER REQUIREMENTS ==="]
    for r in requirements:
        prio = getattr(r.priority, "value", str(r.priority))
        lines.append(f"- ({prio}) {r.requirement}")
    lines.append("")
    return "\n".join(lines)


def judge_advance_probability(
    *,
    jd_text: str,
    cv_text: str,
    requirements=None,
    client=None,
    organization_id: int | None = None,
) -> float | None:
    """Run Sonnet as a senior recruiter, return P(advance). None on failure.

    ``organization_id`` binds the spend to an org so a ``usage_event`` is
    actually written. Without it the shared client has no org context and
    the metering wrapper skips the event (logged warning). Pass it through
    from the calling calibration flow.
    """
    if client is None:
        try:
            from ...services.claude_client_resolver import get_shared_client

            client = get_shared_client(organization_id=organization_id)
        except Exception as exc:
            logger.warning("Cannot judge — no Anthropic client: %s", exc)
            return None

    prompt = _JUDGE_PROMPT.format(
        jd_text=jd_text or "",
        cv_text=cv_text or "",
        requirements_block=_render_requirements(requirements),
    )
    metering: dict = {"feature": "pairwise_judge"}
    if organization_id is not None:
        # Belt-and-suspenders: also tag the per-call dict so attribution is
        # correct even when a caller injects a non-org-bound ``client``.
        metering["organization_id"] = int(organization_id)
    try:
        response = client.messages.create(
            model=_JUDGE_MODEL,
            max_tokens=_JUDGE_MAX_TOKENS,
            temperature=_JUDGE_TEMPERATURE,
            system="You are a senior recruiter. Output only JSON.",
            messages=[{"role": "user", "content": prompt}],
            metering=metering,
        )
    except Exception as exc:
        logger.warning("Judge call failed: %s", exc)
        return None

    try:
        raw = response.content[0].text  # type: ignore[attr-defined]
    except (AttributeError, IndexError):
        return None

    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        raw = raw.rsplit("```", 1)[0]
    try:
        blob = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Judge returned invalid JSON: %s", exc)
        return None

    p = blob.get("p_advance")
    if not isinstance(p, (int, float)):
        return None
    return float(max(0.0, min(1.0, p)))


__all__ = ["judge_advance_probability"]
