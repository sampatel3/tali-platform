import React, { useState } from 'react';
import { ChevronDown } from 'lucide-react';

import {
  FLUENCY_4D_AXES,
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
  const dims = sb?.rubric_grading?.dimensions;
  if (!Array.isArray(dims)) return [];
  return dims
    .map((d) => ({
      id: String(d?.id || '').trim(),
      score: Number.isFinite(Number(d?.score)) ? Number(d.score) : null,
      rating: String(d?.rating || '').trim().toLowerCase(),
      reasoning: String(d?.reasoning || '').trim(),
      citations: Array.isArray(d?.evidence_citations)
        ? d.evidence_citations.map((c) => String(c).trim()).filter(Boolean)
        : [],
    }))
    .filter((d) => d.id);
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
// one surface instead of two rival lists. Axes with no rubric criteria fall
// back to listing the heuristic signals they were averaged from.
export const AssessmentScorecard = ({ assessment = null }) => {
  const [openAxes, setOpenAxes] = useState(() => new Set());
  const scorecard = computeScorecard(assessment);

  if (!scorecard) {
    return (
      <div className="sc5" data-testid="assessment-scorecard-empty">
        <div className="sc5-head">
          <span className="mc-kicker">SCORECARD · THE 5 Ds</span>
        </div>
        <p className="sc5-empty">
          The 5-dimension scorecard (Delegation, Description, Discernment, Diligence,
          Deliverable) appears once the assessment is scored.
        </p>
      </div>
    );
  }

  const graded = readGradedRubricDimensions(assessment);
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
      {scorecard.map((axis) => {
        const criteria = criteriaByAxis[axis.key] || [];
        const isOpen = openAxes.has(axis.key);
        const pct = axis.hasSignal ? Math.max(0, Math.min(100, Math.round(axis.score))) : 0;
        const isLow = axis.hasSignal && pct < 45;
        const sources = (FLUENCY_4D_AXES.find((a) => a.key === axis.key)?.sources || [])
          .map((column) => ({
            column,
            value: Number.isFinite(Number(assessment?.[column])) && assessment?.[column] != null
              ? Math.round(Number(assessment[column]) * 10)
              : null,
          }))
          .filter((s) => s.value != null);
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
                      {item.rating ? (
                        <span
                          className="taali-badge font-mono text-[0.625rem] uppercase"
                          style={ratingBadgeStyle(item.rating)}
                        >
                          {item.rating}
                        </span>
                      ) : null}
                      <span className="sc5-crit-score">
                        {item.score != null ? `${Math.round(item.score * 10)} / 100` : '—'}
                      </span>
                    </div>
                    <p className="sc5-crit-why">
                      {item.reasoning || 'Graded from the completed work sample and AI-collaboration trace.'}
                    </p>
                    {item.citations.length > 0 ? (
                      <div className="sc5-crit-cites">
                        {item.citations.map((citation) => (
                          <code key={citation}>{citation}</code>
                        ))}
                      </div>
                    ) : null}
                  </div>
                )) : sources.length > 0 ? (
                  <>
                    <p className="sc5-body-note">
                      No rubric criteria map to this dimension — its score is the average of
                      these assessment signals:
                    </p>
                    {sources.map((source) => (
                      <div key={source.column} className="sc5-crit-row">
                        <span className="sc5-crit-name">{humanizeSourceColumn(source.column)}</span>
                        <span className="sc5-crit-score">{source.value} / 100</span>
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
