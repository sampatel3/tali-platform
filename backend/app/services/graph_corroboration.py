"""Knowledge-graph collective corroboration (Prong 2, Wave 2 — the moat).

Cross-check a candidate's claimed (employer, tech-stack) against the COLLECTIVE
picture the Graphiti graph holds: across every candidate we've ever seen who
worked at that company, what stack co-occurs? A genuine candidate's claimed
stack sits inside that distribution; a spec-tailored / inflated one is an
outlier nobody else from that employer shows (the classic "bleeding-edge ML
stack at a bank where everyone else ran SAS/SQL" tell).

FP-safe by construction:
  * **corroboration-first** — a positive match boosts confidence; a negative
    only FLAGS (never gates, never auto-rejects);
  * **cold-start fail-open** — silent until the graph holds >= N independent
    candidate observations for a company (the graph is populated only from
    in-assessment / advanced candidates, so per-company coverage is thin early);
  * **conservative anomaly** — fires only when the company has a CONCENTRATED
    signature stack AND the candidate shares NONE of it, so role diversity at
    big heterogeneous employers does not false-positive;
  * **gated** on ``GRAPH_CORROBORATION_ENABLED`` + ``NEO4J``/Voyage config;
    degrades to no-signal cleanly when unset.

Async enrichment — persisted into
``cv_match_details.integrity_signals.graph_corroboration``.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("taali.graph_corroboration")

_SKILL_RE = re.compile(r"[a-z0-9+#.]+")

# A company "signature" skill = present in >= this fraction of its candidates.
# Below this the company stack is too diffuse to call an outlier fairly.
_DEFAULT_CONCENTRATION = 0.5


def _norm_skill(s: Any) -> str:
    return " ".join(_SKILL_RE.findall(str(s or "").lower())).strip()


@dataclass
class GraphCorroborationResult:
    status: str  # corroborated | anomaly | no_signal
    company: str
    total_candidates: int
    claimed_skill_count: int
    matched_skills: list[str] = field(default_factory=list)
    company_signature: list[str] = field(default_factory=list)
    overlap: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "company": self.company,
            "total_candidates": self.total_candidates,
            "claimed_skill_count": self.claimed_skill_count,
            "matched_skills": self.matched_skills[:12],
            "company_signature": self.company_signature[:12],
            "overlap": round(self.overlap, 3),
        }


def corroborate_claimed_stack(
    claimed_skills: Any,
    distribution: dict[str, Any] | None,
    *,
    min_observations: int,
    concentration: float = _DEFAULT_CONCENTRATION,
) -> GraphCorroborationResult:
    """Pure analyser. ``distribution`` is ``company_tech_stack()`` output:
    ``{company, total_candidates, skills: {name_lower: candidate_count}}``.

    - **no_signal** — cold-start (``total < min_observations``), no claimed
      skills, or the company has no concentrated signature stack (too diffuse).
    - **corroborated** — the candidate shares the company's signature stack.
    - **anomaly** — the company HAS a signature stack and the candidate shares
      NONE of the company's seen skills at all (the inflation-to-spec tell).
    """
    company = str((distribution or {}).get("company") or "")
    total = int((distribution or {}).get("total_candidates") or 0)
    raw = (distribution or {}).get("skills") or {}
    claimed = {s for s in (_norm_skill(x) for x in (claimed_skills or [])) if s}

    if total < min_observations or not raw or not claimed:
        return GraphCorroborationResult("no_signal", company, total, len(claimed))

    seen = {str(k).strip().lower(): int(v) for k, v in raw.items()}
    signature = {k for k, c in seen.items() if total and (c / total) >= concentration}
    if not signature:
        return GraphCorroborationResult("no_signal", company, total, len(claimed))

    # Substring-tolerant skill match ("kubernetes" vs "kubernetes (k8s)").
    def _hit(skill: str, pool: set[str]) -> bool:
        return any(skill == p or skill in p or p in skill for p in pool)

    matched_sig = sorted({s for s in claimed if _hit(s, signature)})
    matched_any = {s for s in claimed if _hit(s, set(seen))}

    if matched_sig:
        status = "corroborated"
    elif matched_any:
        status = "no_signal"  # shares non-signature skills — weak, don't flag
    else:
        status = "anomaly"  # shares nothing the company shows

    overlap = (len(matched_any) / len(claimed)) if claimed else 0.0
    return GraphCorroborationResult(
        status=status,
        company=company,
        total_candidates=total,
        claimed_skill_count=len(claimed),
        matched_skills=matched_sig or sorted(matched_any),
        company_signature=sorted(signature),
        overlap=overlap,
    )


def corroborate_candidate_stack(
    *,
    organization_id: int | None,
    cv_sections: dict[str, Any] | None,
    min_observations: int,
    now: dt.datetime | None = None,
    max_companies: int = 8,
) -> dict[str, Any] | None:
    """Run graph corroboration for one candidate's claimed employers. Returns the
    ``integrity_signals.graph_corroboration`` payload, or ``None`` when disabled
    / no usable input / no graph. Fail-open everywhere — any error → ``None``.
    """
    from ..platform.config import settings

    if not settings.GRAPH_CORROBORATION_ENABLED:
        return None
    try:
        from ..candidate_graph import client as graph_client
        from ..candidate_graph import graphrag_queries as gq

        if organization_id is None or not graph_client.is_configured():
            return None
        sections = cv_sections if isinstance(cv_sections, dict) else {}
        experience = sections.get("experience") or []
        claimed_skills = [s for s in (sections.get("skills") or []) if s]
        if not claimed_skills or not experience:
            return None

        group_id = graph_client.group_id_for_org(int(organization_id))
        t = now or dt.datetime.now(dt.timezone.utc)

        companies: list[str] = []
        seen_names: set[str] = set()
        for e in experience:
            name = str((e.get("company") if isinstance(e, dict) else "") or "").strip()
            key = name.lower()
            if name and key not in seen_names:
                seen_names.add(key)
                companies.append(name)

        results: list[GraphCorroborationResult] = []
        for name in companies[:max_companies]:
            dist = gq.company_tech_stack(group_id=group_id, company=name, t=t)
            res = corroborate_claimed_stack(
                claimed_skills, dist, min_observations=min_observations
            )
            if res.status != "no_signal":
                results.append(res)

        if not results:
            return None
        anomalies = [r for r in results if r.status == "anomaly"]
        return {
            "status": "anomaly" if anomalies else "corroborated",
            "min_observations": min_observations,
            "anomaly_count": len(anomalies),
            "companies": [r.to_dict() for r in results],
        }
    except Exception:  # pragma: no cover — never break scoring on a flag
        logger.debug("graph corroboration failed", exc_info=True)
        return None
