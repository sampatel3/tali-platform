// VerdictBand — the candidate report's at-a-glance verdict header.
//
// Replaces the old four-ring "recommendation hero": ONE canonical Taali ring,
// the agent's recommendation, and — critically — the integrity/trust signal on
// the same line (lifted out of the CV-match block, where it used to sit BELOW
// the scores it qualifies). The remaining scores demote to compact tiles so a
// recruiter reads "what do I do" before "here are five numbers".
//
// Pure presentational: the page owns the decision + handlers; the action
// surface stays on <CandidateDecisionStrip>. Renders safely when decision /
// integrity are absent (external client + demo views).
import React from 'react';
import { AlertTriangle, ShieldCheck } from 'lucide-react';

import { ScoreRing } from '../../shared/ui/ScoreRing';
import { ScoreProvenance } from './ScoreProvenance';

const TRUST_META = {
  high: { label: 'High trust', tone: 'ok' },
  medium: { label: 'Verify', tone: 'warn' },
  low: { label: 'Verify before advancing', tone: 'warn' },
};

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
  const band = String(integrity?.trust_band || '').toLowerCase();
  const trust = integrity ? (TRUST_META[band] || { label: band || 'Reviewed', tone: 'mute' }) : null;
  const toVerify = Number(integrity?.to_verify) || 0;
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
          {trust ? (
            <span className={`mc-verdict-trust is-${trust.tone}`}>
              {trust.tone === 'ok'
                ? <ShieldCheck size={14} strokeWidth={2.2} aria-hidden="true" />
                : <AlertTriangle size={14} strokeWidth={2.2} aria-hidden="true" />}
              {trust.label}{toVerify ? ` · ${toVerify} to verify` : ''}
            </span>
          ) : null}
          {summaryText ? <p className="mc-verdict-summary">{summaryText}</p> : null}
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
