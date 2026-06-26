// CvMatchReview — the requirement-by-requirement CV match readout for the
// candidate standing report's Overview tab. Surfaces met / partial / gap
// coverage of the role requirements against the CV. (The integrity / trust-band
// readout that used to sit here now renders via IntegrityFlags in the Overview
// hero — see #739.) Extracted from CandidateStandingReportPage.jsx to keep the
// page file under the frontend architecture line cap.
import React from 'react';

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

const CvMatchReview = ({
  application,
  cvMatchDetails,
  matchedRequirements,
  missingRequirements,
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

  return (
    <section className="cv-rail cv-match-summary cv-match-review" aria-label="CV match summary">
      {total ? (
        <div className="rail-card cvm-body">
          <div className="cvm-head">
            <div className="mc-kicker">CV MATCH</div>
            <div className="meta" style={{ marginTop: 4 }}>
              vs <b>{roleName}</b>{scoredAt ? ` · Scored ${new Date(scoredAt).toLocaleDateString()}` : ''}
            </div>
          </div>
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

          <div className="cvm-list">
            {ordered.map((item, index) => {
              const key = reqGradeKey(item);
              const meta = REQ_STATUS_META[key] || REQ_STATUS_META.missing;
              const grade = requirementGrade(item);
              const evidence = item?.impact
                || extractRequirementEvidence(item)
                || item?.evidence_quote
                || (key === 'met' ? 'Matched evidence on file.' : 'Probe this live.');
              const isRecruiter = String(item?.requirement_id || '').startsWith('crit_');
              return (
                <div key={extractRequirementKey(item, index)} className={`cvm-row is-${key}`}>
                  <span className="cvm-status" data-s={key}>
                    <i style={{ background: meta.dot }} />
                    {meta.label}
                  </span>
                  <div className="cvm-req">
                    <div className="cvm-req-top">
                      <span className="cvm-req-name">{item.requirement || item.criterion_text || 'Requirement'}</span>
                      {isRecruiter ? <span className="cvm-tag">Recruiter</span> : null}
                      {grade !== null ? (
                        <span
                          className="cvm-grade"
                          title="Evidence-graded fit for this requirement (0-100) — this is what the score uses."
                          style={{
                            marginLeft: 'auto',
                            fontFamily: 'var(--font-mono)',
                            fontSize: '0.75rem',
                            fontWeight: 700,
                            color: 'var(--ink)',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {grade}
                          <span style={{ color: 'var(--mute)', fontWeight: 500 }}>/100</span>
                        </span>
                      ) : null}
                    </div>
                    <span className="cvm-ev">{evidence}</span>
                  </div>
                </div>
              );
            })}
          </div>

          <button type="button" className="rail-jump" onClick={onJumpToPrep}>
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
