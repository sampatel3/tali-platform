import React, { useState } from 'react';
import { ChevronDown } from 'lucide-react';

import '../../styles/09-standing-report.css';

import {
  axisForRubricDimension,
  computeScorecard,
} from '../../shared/assessment/fluency4d';

// The ACTUAL graded rubric criteria — the EVIDENCE that hangs under the
// 5-axis scorecard. Each is the authoritative per-criterion grade the backend
// wrote to score_breakdown.rubric_grading.dimensions:
//   { id, score (0–10), rating ('excellent'|'good'|'poor'), reasoning,
//     evidence_citations }.
// Display-only; tolerant of a JSON-string score_breakdown.
export const readGradedRubricDimensions = (assessment) => {
  let sb = assessment?.score_breakdown;
  if (typeof sb === 'string') {
    try {
      sb = JSON.parse(sb);
    } catch {
      return [];
    }
  }
  const rubricGrading = sb?.rubric_grading || {};
  const dims = Array.isArray(rubricGrading?.dimensions)
    ? rubricGrading.dimensions
    : [];
  const parsed = dims
    .map((d) => ({
      id: String(d?.id || '').trim(),
      error: String(d?.error || '').trim(),
      score: !d?.error && d?.score != null && Number.isFinite(Number(d.score))
        ? Number(d.score)
        : null,
      rating: d?.error ? 'error' : String(d?.rating || '').trim().toLowerCase(),
      reasoning: String(d?.reasoning || '').trim(),
      citations: Array.isArray(d?.evidence_citations)
        ? d.evidence_citations.map((c) => String(c).trim()).filter(Boolean)
        : [],
    }))
    .filter((d) => d.id);
  const known = new Set(parsed.map((dimension) => dimension.id));
  const sharedError = String(rubricGrading?.error || 'Rubric grading did not complete.').trim();
  (Array.isArray(rubricGrading?.failed_dimension_ids)
    ? rubricGrading.failed_dimension_ids
    : []).forEach((id) => {
    const normalized = String(id || '').trim();
    if (!normalized || known.has(normalized)) return;
    parsed.push({
      id: normalized,
      error: sharedError,
      score: null,
      rating: 'error',
      reasoning: '',
      citations: [],
    });
  });
  return parsed;
};

// Turn a rubric dimension id ("design_decisions", "release_safety") into a
// readable label without a hardcoded vocabulary — these ids are task-defined.
const humanizeDimensionId = (id) => String(id || '')
  .replace(/_/g, ' ')
  .replace(/\b\w/g, (c) => c.toUpperCase());

// Per-criterion ratings are EVIDENCE, not a reject/advance verdict, so they stay
// on the purple scale — a stronger rating reads darker — rather than borrowing
// the green/red the design system reserves for terminal decisions. Mirrors the
// intensity treatment in InterviewFeedbackSection's recommendation chips.
const RATING_INTENSITY = {
  excellent: 100,
  good: 64,
  poor: 30,
};

const ratingBadgeStyle = (rating) => {
  const intensity = RATING_INTENSITY[rating] ?? 44;
  return {
    background: `color-mix(in oklab, var(--purple) ${intensity}%, transparent)`,
    color: intensity >= 60 ? 'var(--bg)' : 'var(--ink)',
  };
};

// Heuristic column ("prompt_quality_score") → readable label for the fallback
// expansion when an axis was scored from atomic signals, not the rubric.
const humanizeSourceColumn = (column) => humanizeDimensionId(
  String(column || '').replace(/_score$/, ''),
);

// THE 5 Ds as the spine of the Assessment tab. Each axis row expands into the
// graded rubric criteria that produced its score (grouped via the same
// lens→axis mapping the backend uses), so the scorecard and its evidence are
// one surface instead of two rival lists. An axis with no rubric criterion
// scores "—" and expands to its behavioural telemetry, explicitly marked as
// not a grade — it never borrows a heuristic number to look complete.
export const AssessmentScorecard = ({ assessment = null }) => {
  const [openAxes, setOpenAxes] = useState(() => new Set());
  const scorecard = computeScorecard(assessment);
  const graded = readGradedRubricDimensions(assessment);
  let scoreBreakdown = assessment?.score_breakdown;
  if (typeof scoreBreakdown === 'string') {
    try { scoreBreakdown = JSON.parse(scoreBreakdown); } catch { scoreBreakdown = {}; }
  }
  const grading = scoreBreakdown?.rubric_grading || {};
  const gradingPending = Boolean(
    assessment?.scoring_partial
    || assessment?.scoring_failed
    || grading?.fully_graded === false
    || grading?.status === 'partial'
    || grading?.status === 'failed',
  );
  const dimensionErrors = graded.filter((dimension) => dimension.error);
  const hasSuccessfulRubricEvidence = graded.some(
    (dimension) => !dimension.error && dimension.score != null,
  );

  if (!scorecard || (gradingPending && !hasSuccessfulRubricEvidence)) {
    return (
      <div className="sc5" data-testid="assessment-scorecard-empty">
        <div className="sc5-head">
          <span className="mc-kicker">SCORECARD · THE 5 Ds</span>
        </div>
        {gradingPending ? (
          <div className="sc5-empty" role="status" data-testid="assessment-grading-pending">
            <strong>Automated grading is still running.</strong>{' '}
            No TAALI score or agent decision will be produced until every rubric
            criterion is graded.
            {dimensionErrors.map((dimension) => (
              <p key={dimension.id}>
                {humanizeDimensionId(dimension.id)}: {dimension.error}
              </p>
            ))}
          </div>
        ) : (
          <p className="sc5-empty">
            The 5-dimension scorecard (Delegation, Description, Discernment, Diligence,
            Deliverable) appears once the assessment is scored.
          </p>
        )}
      </div>
    );
  }

  const rubric = (assessment?.evaluation_rubric && typeof assessment.evaluation_rubric === 'object')
    ? assessment.evaluation_rubric
    : {};
  const criteriaByAxis = {};
  graded.forEach((item) => {
    const axis = axisForRubricDimension(rubric[item.id]);
    if (!criteriaByAxis[axis]) criteriaByAxis[axis] = [];
    criteriaByAxis[axis].push(item);
  });

  const toggleAxis = (key) => {
    setOpenAxes((previous) => {
      const next = new Set(previous);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  return (
    <section className="sc5" aria-label="Assessment scorecard — the 5 Ds">
      <div className="sc5-head">
        <span className="mc-kicker">SCORECARD · THE 5 Ds</span>
        <span className="sc5-note">each score rolls up from the criteria inside</span>
      </div>
      {gradingPending ? (
        <div className="sc5-empty" role="status" data-testid="assessment-grading-pending">
          <strong>Grading incomplete — automatic retry in progress.</strong>{' '}
          Partial criterion results below are evidence only; no TAALI score or
          agent decision is available yet.
          {dimensionErrors.map((dimension) => (
            <p key={dimension.id}>
              {humanizeDimensionId(dimension.id)}: {dimension.error}
            </p>
          ))}
        </div>
      ) : null}
      {scorecard.map((axis) => {
        const criteria = criteriaByAxis[axis.key] || [];
        const isOpen = openAxes.has(axis.key);
        const pct = axis.hasSignal ? Math.max(0, Math.min(100, Math.round(axis.score))) : 0;
        const isLow = axis.hasSignal && pct < 45;
        // Behavioural telemetry for this axis — evidence only. Never a score:
        // see computeScorecard's header for why these can't stand in for a grade.
        const telemetry = axis.telemetry || [];
        return (
          <div key={axis.key} className={`sc5-row${isOpen ? ' open' : ''}`}>
            <button
              type="button"
              className="sc5-row-head"
              aria-expanded={isOpen}
              onClick={() => toggleAxis(axis.key)}
            >
              <span className="sc5-label">{axis.label}</span>
              <span className="sc5-mid">
                <span className="sc5-bar" aria-hidden="true">
                  <i className={isLow ? 'low' : ''} style={{ width: `${pct}%` }} />
                </span>
                <span className="sc5-blurb">{axis.blurb}</span>
              </span>
              <span className="sc5-score">
                {axis.hasSignal ? Math.round(axis.score) : '—'}
                {axis.hasSignal ? <em>/100</em> : null}
              </span>
              <ChevronDown size={15} className="sc5-chev" aria-hidden="true" />
            </button>
            {isOpen ? (
              <div className="sc5-body">
                {criteria.length > 0 ? criteria.map((item) => (
                  <div key={item.id} className="sc5-crit">
                    <div className="sc5-crit-row">
                      <span className="sc5-crit-name">{humanizeDimensionId(item.id)}</span>
                      {item.error ? (
                        <span className="taali-badge font-mono text-[0.625rem] uppercase">
                          grading error
                        </span>
                      ) : item.rating ? (
                        <span
                          className="taali-badge font-mono text-[0.625rem] uppercase"
                          style={ratingBadgeStyle(item.rating)}
                        >
                          {item.rating}
                        </span>
                      ) : null}
                      <span className="sc5-crit-score">
                        {item.error
                          ? 'pending'
                          : (item.score != null ? `${Math.round(item.score * 10)} / 100` : '—')}
                      </span>
                    </div>
                    <p className="sc5-crit-why">
                      {item.error
                        ? `This criterion is not graded yet: ${item.error}`
                        : (item.reasoning || 'Graded from the completed work sample and AI-collaboration trace.')}
                    </p>
                    {item.citations.length > 0 ? (
                      <div className="sc5-crit-cites">
                        {item.citations.map((citation) => (
                          <code key={citation}>{citation}</code>
                        ))}
                      </div>
                    ) : null}
                  </div>
                )) : telemetry.length > 0 ? (
                  <>
                    <p className="sc5-body-note">
                      Not graded — this task&apos;s rubric has no criterion for this dimension,
                      so there is no score. The behavioural signals below were captured during
                      the session as context; they are not a grade and don&apos;t roll into the
                      TAALI score.
                    </p>
                    {telemetry.map((signal) => (
                      <div key={signal.column} className="sc5-crit-row">
                        <span className="sc5-crit-name">{humanizeSourceColumn(signal.column)}</span>
                        <span className="sc5-crit-score">{Math.round(signal.value)}</span>
                      </div>
                    ))}
                  </>
                ) : (
                  <p className="sc5-body-note">No signal captured for this dimension yet.</p>
                )}
              </div>
            ) : null}
          </div>
        );
      })}
    </section>
  );
};
