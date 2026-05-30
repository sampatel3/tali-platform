import React from 'react';

import { PIPELINE_FUNNEL_STAGES, funnelStageTone, formatCount, decisionGatesByStage } from '../metrics';
import './FunnelBoard.css';

const OUTCOME_KEYS = new Set(['advanced', 'rejected']);

// Shared B2 funnel board — stage counts on top
// (Applied · Scored · Invited · Completed · Advanced │ Rejected), and — when
// pending decisions are supplied — the agent's pending recommendation for each
// stage aligned in the row directly beneath it (Pre-screen under Applied, Send
// assessment / Reject under Scored, Advance under Completed). Advanced and
// Rejected are terminal outcomes, divided off with no decision row.
//
// Used on the role-detail page (scopeLabel "this role", with the role's pending
// decisions) and the home hub (scopeLabel "all roles", stages-only — the org
// decision breakdown lives in the hero). `decisions` is a list (or
// {decision_type: count} map) of pending agent decisions.
export const FunnelBoard = ({ stageCounts, decisions = null, awaitingTotal = null, scopeLabel = 'this role' }) => {
  const byStage = decisions ? decisionGatesByStage(decisions) : null;
  return (
    <div className="funnel-board">
      <div className="fb-cap">
        <span>Pipeline · {scopeLabel}</span>
        {byStage && awaitingTotal != null && Number(awaitingTotal) > 0 ? (
          <span className="fb-cap-aw">{formatCount(awaitingTotal)} awaiting you</span>
        ) : null}
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

      {byStage ? (
        <>
          <div className="fb-drow-hdr">Awaiting your decision</div>
          <div className="fb-grid fb-drow">
            {PIPELINE_FUNNEL_STAGES.map((stage) => {
              const gates = (byStage[stage.key] || []).filter((gate) => gate.count > 0);
              return (
                <div key={stage.key} className="fb-dcell">
                  {OUTCOME_KEYS.has(stage.key) ? (
                    <span className="fb-dnone">outcome</span>
                  ) : gates.length ? (
                    gates.map((gate) => (
                      <span key={gate.key} className={`fb-dchip${gate.tone === 'reject' ? ' is-reject' : ''}`}>
                        {formatCount(gate.count)} {gate.short}
                      </span>
                    ))
                  ) : (
                    <span className="fb-dnone">—</span>
                  )}
                </div>
              );
            })}
          </div>
        </>
      ) : null}
    </div>
  );
};
