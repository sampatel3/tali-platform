import React from 'react';

import {
  PIPELINE_FUNNEL_STAGES,
  funnelStageTone,
  formatCount,
  funnelDecisionRow,
  awaitingFromStageCounts,
} from '../metrics';
import './FunnelBoard.css';

const OUTCOME_KEYS = new Set(['advanced', 'rejected']);

// Shared B2 funnel board — stage counts on top
// (Applied · Scored · Invited · Completed · Advanced │ Rejected), with an
// "awaiting your decision" row beneath that surfaces the candidates needing
// YOUR call at each decision stage (Scored → send/reject, Completed →
// advance/decide). Derived purely from the stage counts, so it works on both
// the role page and the home hub, agent on or off. Advanced and Rejected are
// terminal outcomes, divided off with no decision row.
export const FunnelBoard = ({ stageCounts, awaitingTotal = null, scopeLabel = 'this role' }) => {
  const decisionRow = funnelDecisionRow(stageCounts);
  const awaiting = awaitingTotal != null ? Number(awaitingTotal) : awaitingFromStageCounts(stageCounts);
  return (
    <div className="funnel-board">
      <div className="fb-cap">
        <span>Pipeline · {scopeLabel}</span>
        {awaiting > 0 ? <span className="fb-cap-aw">{formatCount(awaiting)} awaiting you</span> : null}
      </div>

      <div className="fb-grid fb-stages">
        {PIPELINE_FUNNEL_STAGES.map((stage) => {
          const value = Number(stageCounts?.[stage.key] || 0);
          const tone = funnelStageTone(stage.key, value);
          return (
            <div
              key={stage.key}
              className={`fb-st${stage.key === 'advanced' ? ' is-out-start' : ''}${OUTCOME_KEYS.has(stage.key) ? ' is-out' : ''}`}
            >
              <div className={`fb-v${tone === 'attn' ? ' attn' : ''}${tone === 'term' ? ' term' : ''}`}>{formatCount(value)}</div>
              <div className="fb-l">{stage.label}</div>
            </div>
          );
        })}
      </div>

      <div className="fb-drow-hdr">Awaiting your decision</div>
      <div className="fb-grid fb-drow">
        {PIPELINE_FUNNEL_STAGES.map((stage) => {
          const gate = decisionRow[stage.key];
          return (
            <div key={stage.key} className="fb-dcell">
              {OUTCOME_KEYS.has(stage.key) ? (
                <span className="fb-dnone">outcome</span>
              ) : gate && gate.count > 0 ? (
                <span className="fb-dchip">{formatCount(gate.count)} {gate.action}</span>
              ) : (
                <span className="fb-dnone">—</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};
