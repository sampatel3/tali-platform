// CvMatchReview — the requirement-by-requirement CV match readout for the
// candidate standing report's Requirements tab. Surfaces met / partial / gap
// coverage of the role requirements against the CV. Each row is an expandable
// <details> with a 0–100 score bar (purple when strong, lavender when low) and
// a mono provenance chip (CV / Role match) that reveals the evidence on click —
// mirroring report-preview's `.reqrow`/`.reqg`/`.reqscore`/`.reqbar`/`.ev .src`.
// (The integrity / trust-band readout that used to sit here now renders via
// IntegrityFlags in the Overview hero — see #739.) Extracted from
// CandidateStandingReportPage.jsx to keep the page file under the frontend
// architecture line cap.
import React from 'react';
import { ChevronRight } from 'lucide-react';

import {
  asArray,
  extractRequirementEvidence,
  extractRequirementKey,
  reqGradeKey,
  requirementGrade,
} from './candidatesUiUtils';

// Status order for the unified requirement list: positives (met) first, then
// partial, then unclear, with gaps last.
const REQ_STATUS_RANK = { met: 0, partially_met: 1, unknown: 2, missing: 3 };

// Purple-forward, not traffic-light: "met" reads as brand purple so a strong
// candidate isn't a wall of green ticks. Only true gaps go amber.
const REQ_STATUS_META = {
  met: { label: 'Met', dot: 'var(--purple)' },
  partially_met: { label: 'Partial', dot: 'color-mix(in oklab, var(--purple) 45%, var(--bg-2))' },
  missing: { label: 'Gap', dot: 'var(--amber)' },
  unknown: { label: 'Unclear', dot: 'var(--mute)' },
};

// The provenance chip: a met / partial requirement is corroborated from the CV;
// a gap (or anything inferred only from the role spec) reads as "Role match".
const requirementSource = (gradeKey) => (
  (gradeKey === 'met' || gradeKey === 'partially_met') ? 'CV' : 'Role match'
);

const CvMatchReview = ({
  application,
  cvMatchDetails,
  matchedRequirements,
  missingRequirements,
  fitScore,
  onJumpToPrep,
}) => {
  // Build one list, scored requirements preferred. Fall back to raw skill
  // strings when the role hasn't been scored against criteria yet.
  const hasRequirements = Array.isArray(cvMatchDetails?.requirements_assessment)
    && cvMatchDetails.requirements_assessment.length > 0;
  const items = hasRequirements
    ? [...missingRequirements, ...matchedRequirements]
    : [
      ...asArray(cvMatchDetails?.missing_skills).map((skill) => ({
        requirement: skill, status: 'missing', evidence_quote: 'Probe this in the interview loop.',
      })),
      ...asArray(cvMatchDetails?.matching_skills).map((skill) => ({
        requirement: skill, status: 'met', evidence_quote: 'Skill matched in the candidate profile.',
      })),
    ];
  // Stable sort keeps the existing recruiter-first / priority order within a status.
  const ordered = [...items].sort(
    (a, b) => REQ_STATUS_RANK[reqGradeKey(a)] - REQ_STATUS_RANK[reqGradeKey(b)]
  );
  const counts = ordered.reduce((acc, item) => {
    const key = reqGradeKey(item);
    if (key === 'met') acc.met += 1;
    else if (key === 'partially_met') acc.partial += 1;
    else acc.missing += 1;
    return acc;
  }, { met: 0, partial: 0, missing: 0 });
  const total = ordered.length;

  const scoredAt = application?.cv_match_scored_at || application?.updated_at || null;
  const roleName = application?.role_name || application?.candidate_position || 'target role';
  const fit = Number.isFinite(Number(fitScore)) ? Math.round(Number(fitScore)) : null;

  return (
    <section className="cv-rail cv-match-summary cv-match-review" aria-label="Requirements and fit">
      {total ? (
        <div className="rail-card cvm-body">
          <div className="reqhead">
            <div className="mc-kicker">REQUIREMENTS &amp; FIT</div>
            {fit !== null ? (
              <span className="reqfit">Fit <b>{fit}</b>/100 · vs {roleName}</span>
            ) : (
              <span className="reqfit reqfit-sub">
                vs <b>{roleName}</b>{scoredAt ? ` · Scored ${new Date(scoredAt).toLocaleDateString()}` : ''}
              </span>
            )}
          </div>
          <p className="reqsub">Per-requirement match confidence (0–100). Click a row for evidence.</p>

          <div className="cvm-coverage">
            <div className="cvm-bar" aria-hidden="true">
              {counts.met ? <span style={{ flex: counts.met, background: 'var(--purple)' }} /> : null}
              {counts.partial ? <span style={{ flex: counts.partial, background: REQ_STATUS_META.partially_met.dot }} /> : null}
              {counts.missing ? <span style={{ flex: counts.missing, background: 'var(--amber)' }} /> : null}
            </div>
            <div className="cvm-legend">
              <span><i style={{ background: 'var(--purple)' }} /><b>{counts.met}</b> met</span>
              <span><i style={{ background: REQ_STATUS_META.partially_met.dot }} /><b>{counts.partial}</b> partial</span>
              <span><i style={{ background: 'var(--amber)' }} /><b>{counts.missing}</b> {counts.missing === 1 ? 'gap' : 'gaps'}</span>
            </div>
          </div>

          <div className="reqlist">
            {ordered.map((item, index) => {
              const key = reqGradeKey(item);
              const meta = REQ_STATUS_META[key] || REQ_STATUS_META.missing;
              const grade = requirementGrade(item);
              // The bar fills to the graded confidence (the real match_score).
              // When a row was never graded (raw-skill fallback) there is no
              // number to show — render the status pill alone, never a fabricated
              // score.
              const hasGrade = grade !== null && Number.isFinite(Number(grade));
              const pct = hasGrade ? Math.max(0, Math.min(100, Number(grade))) : null;
              const isLow = pct !== null && pct < 60;
              // Real evidence only — no invented "matched evidence" copy.
              const evidence = item?.impact
                || extractRequirementEvidence(item)
                || item?.evidence_quote
                || '';
              const isRecruiter = String(item?.requirement_id || '').startsWith('crit_');
              const src = requirementSource(key);
              return (
                <details key={extractRequirementKey(item, index)} className={`reqrow is-${key}`}>
                  <summary>
                    {pct !== null ? (
                      <span className="reqg" aria-hidden="true">
                        <span className={`reqscore ${isLow ? 'lo' : 'hi'}`}>{Math.round(pct)}</span>
                        <span className="reqbar">
                          <i className={isLow ? 'lo' : ''} style={{ width: `${pct}%` }} />
                        </span>
                      </span>
                    ) : null}
                    <span className="rqname">
                      {item.requirement || item.criterion_text || 'Requirement'}
                      {isRecruiter ? <span className="cvm-tag reqtag">Recruiter</span> : null}
                    </span>
                    <span className="reqstat" data-s={key}>{meta.label}</span>
                    <ChevronRight size={16} className="chev" aria-hidden="true" />
                  </summary>
                  <div className="ev">
                    {src ? <span className="src">{src}</span> : null}
                    {evidence || (key === 'met' || key === 'partially_met'
                      ? 'No verbatim evidence captured for this requirement.'
                      : 'Probe in the interview.')}
                  </div>
                </details>
              );
            })}
          </div>

          <button type="button" className="taali-text-btn rail-jump" onClick={onJumpToPrep}>
            View interview prep →
          </button>
        </div>
      ) : (
        <div className="rail-card">
          <div className="rail-empty">No requirements have been scored against this role yet.</div>
        </div>
      )}
    </section>
  );
};

export { CvMatchReview };
