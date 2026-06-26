// VerdictBand — the candidate report's at-a-glance verdict header.
//
// Replaces the old four-ring "recommendation hero": ONE canonical Taali ring +
// the agent's recommendation + the canonical IntegrityFlags trust readout (the
// same component the agent-decision card uses), with the remaining scores
// demoted to compact tiles so a recruiter reads "what do I do" before "here are
// five numbers". Pure presentational; renders safely with no integrity /
// decision (external client + demo views).
import React from 'react';

import { ScoreRing } from '../../shared/ui/ScoreRing';
import { ScoreProvenance } from './ScoreProvenance';
import { IntegrityFlags } from '../../shared/decisions/IntegrityFlags';

const fmtScore = (v) => (
  v == null || Number.isNaN(Number(v)) ? '—' : Math.round(Number(v))
);

export const VerdictBand = ({
  taaliScore,
  roleFitScore,
  assessmentScore,
  reqMet = 0,
  reqTotal = 0,
  recommendationLabel,
  confidence = null,
  summaryText = '',
  integrity = null,
  provenance = null,
}) => {
  const confPct = confidence != null && !Number.isNaN(Number(confidence))
    ? Math.round(Number(confidence) * 100)
    : null;

  return (
    <section className="mc-verdict" aria-label="Agent verdict">
      <div className="mc-verdict-band">
        <div className="mc-verdict-ring">
          <ScoreRing score={Number(taaliScore) || 0} label="TAALI" size={104} />
        </div>
        <div className="mc-verdict-main">
          <div className="mc-kicker">
            {confPct != null ? `Agent recommendation · ${confPct}% confident` : 'Agent recommendation'}
          </div>
          <div className="mc-verdict-rec">{recommendationLabel || 'Continue review'}</div>
          {summaryText ? <p className="mc-verdict-summary">{summaryText}</p> : null}
          <IntegrityFlags integrity={integrity} style={{ marginTop: 10 }} />
        </div>
        <div className="mc-verdict-tiles">
          <div className="mc-verdict-tile">
            <span className="mc-verdict-tile-k">Role fit</span>
            <span className="mc-verdict-tile-v">{fmtScore(roleFitScore)}</span>
          </div>
          <div className="mc-verdict-tile">
            <span className="mc-verdict-tile-k">Assessment</span>
            <span className="mc-verdict-tile-v">{fmtScore(assessmentScore)}</span>
          </div>
          {reqTotal ? (
            <div className="mc-verdict-tile">
              <span className="mc-verdict-tile-k">Requirements</span>
              <span className="mc-verdict-tile-v">
                {reqMet}<span className="mc-verdict-tile-sub"> of {reqTotal}</span>
              </span>
            </div>
          ) : null}
        </div>
      </div>
      {provenance ? (
        <ScoreProvenance provenance={provenance} className="mc-verdict-provenance" />
      ) : null}
    </section>
  );
};

export default VerdictBand;
