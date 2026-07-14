import React from 'react';

import {
  PIPELINE_FUNNEL_STAGES,
  funnelStageTone,
  formatCount,
  funnelDecisionRow,
  awaitingHitlFromDecisions,
  invitedStageValue,
} from '../metrics';
import { MotionNumber } from '../motion';
import './FunnelBoard.css';

const OUTCOME_KEYS = new Set(['advanced', 'rejected']);
const formatFunnelValue = (value) => formatCount(Math.round(Number(value) || 0));

// Shared B2 funnel board — stage counts on top
// (Sourced · Applied · Scored · Invited · Advanced │ Rejected), with an
// "awaiting your decision" row beneath. Under each stage that row stacks the
// agent's pending decisions by type ("25 send assessment", "8 advance",
// "3 pre-screen reject"…) plus a "N decision pending" chip for candidates the
// agent hasn't ruled on yet. `decisionsByType` is the role's pending decisions
// (a list of {decision_type} or a {type: count} map); when omitted every
// scored/completed candidate shows as "decision pending". Advanced and Rejected
// are terminal outcomes, divided off with no decision row.
// The "N awaiting you" pill = the agent's pending recommendations (HITL — what
// needs *your* call), NOT every scored candidate; pass `awaitingTotal` to
// override (e.g. an org-wide count from a different source).
//
// `variant`:
//   'full' (default, role-detail / pipeline) — stage counts on top, then a
//     separate "Awaiting your decision" grid beneath, with the "Pipeline ·
//     scope" cap line above.
//   'flat' (home hub, matching home-preview) — a single strip where each stage
//     cell stacks its value + label + the agent's pending-decision chips
//     inline. No cap line, no separate decision grid. Terminal outcomes show
//     "outcome".
export const FunnelBoard = ({
  stageCounts,
  decisionsByType = null,
  awaitingTotal = null,
  scopeLabel = 'this role',
  variant = 'full',
}) => {
  const decisionRow = funnelDecisionRow(stageCounts, decisionsByType);
  const awaiting = awaitingTotal != null ? Number(awaitingTotal) : awaitingHitlFromDecisions(decisionsByType);

  if (variant === 'flat') {
    return (
      <div className="funnel-board fb-flat">
        <div className="fb-grid fb-stages">
          {PIPELINE_FUNNEL_STAGES.map((stage) => {
            // Invited is the whole Assess step: its value sums invited +
            // completed (assessment out + done), so `completed` counts here
            // instead of in its own tile.
            const value = stage.key === 'invited'
              ? invitedStageValue(stageCounts)
              : Number(stageCounts?.[stage.key] || 0);
            const tone = funnelStageTone(stage.key, value);
            // `completed` lost its own tile, so its pending-decision chips fold
            // in under Invited alongside Invited's own.
            const chips = stage.key === 'invited'
              ? [...(decisionRow.invited || []), ...(decisionRow.completed || [])]
              : (decisionRow[stage.key] || []);
            // Assessment lifecycle sub-counts beneath the Invited stage — the
            // nested funnel of the value above: delivered ≥ opened ≥ started ≥
            // completed. Each renders only when it has real data (delivery/open
            // come from the Resend webhook); a stage with no data just doesn't
            // show, so the chips never claim progress that didn't happen.
            const invitedChips = [];
            if (stage.key === 'invited') {
              const delivered = Number(stageCounts?.invited_delivered || 0);
              const opened = Number(stageCounts?.invited_opened || 0);
              const started = Number(stageCounts?.in_assessment || 0);
              const completed = Number(stageCounts?.completed || 0);
              if (delivered > 0) invitedChips.push({ key: 'delivered', count: delivered, label: 'delivered', tone: 'pending', tip: 'Invite emails delivered to the candidate — a sub-count of Invited' });
              if (opened > 0) invitedChips.push({ key: 'opened', count: opened, label: 'opened', tone: 'pending', tip: 'Invite emails opened — a sub-count of Invited' });
              if (started > 0) invitedChips.push({ key: 'started', count: started, label: 'started', tone: 'send', tip: 'Assessments started, not yet completed — a sub-count of Invited' });
              if (completed > 0) invitedChips.push({ key: 'completed', count: completed, label: 'completed', tone: 'advance', tip: 'Assessments completed, awaiting your decision — a sub-count of Invited' });
            }
            return (
              <div
                key={stage.key}
                className={`fb-st${stage.key === 'advanced' ? ' is-out-start' : ''}${OUTCOME_KEYS.has(stage.key) ? ' is-out' : ''}`}
              >
                <div className="fb-l">{stage.label}</div>
                <div className={`fb-v${tone === 'attn' ? ' attn' : ''}${tone === 'term' ? ' term' : ''}`}>
                  <MotionNumber value={value} format={formatFunnelValue} />
                </div>
                <div className="fb-stchips">
                  {OUTCOME_KEYS.has(stage.key) ? (
                    <span className="fb-dnone">outcome</span>
                  ) : (
                    <>
                      {invitedChips.map((chip) => (
                        <span
                          key={chip.key}
                          className={`fb-dchip is-${chip.tone}`}
                          title={chip.tip}
                        >
                          {formatCount(chip.count)} {chip.label}
                        </span>
                      ))}
                      {chips.map((chip) => (
                        <span
                          key={chip.key}
                          className={`fb-dchip is-${chip.tone}`}
                          title={chip.tip || undefined}
                        >
                          {formatCount(chip.count)} {chip.label}
                        </span>
                      ))}
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  return (
    <div className="funnel-board">
      <div className="fb-cap">
        <span>Pipeline · {scopeLabel}</span>
        {awaiting > 0 ? <span className="fb-cap-aw">{formatCount(awaiting)} awaiting you</span> : null}
      </div>

      <div className="fb-grid fb-stages">
        {PIPELINE_FUNNEL_STAGES.map((stage) => {
          const value = stage.key === 'invited'
            ? invitedStageValue(stageCounts)
            : Number(stageCounts?.[stage.key] || 0);
          const tone = funnelStageTone(stage.key, value);
          return (
            <div
              key={stage.key}
              className={`fb-st${stage.key === 'advanced' ? ' is-out-start' : ''}${OUTCOME_KEYS.has(stage.key) ? ' is-out' : ''}`}
            >
              <div className={`fb-v${tone === 'attn' ? ' attn' : ''}${tone === 'term' ? ' term' : ''}`}>
                <MotionNumber value={value} format={formatFunnelValue} />
              </div>
              <div className="fb-l">{stage.label}</div>
            </div>
          );
        })}
      </div>

      <div className="fb-drow-hdr">Awaiting your decision</div>
      <div className="fb-grid fb-drow">
        {PIPELINE_FUNNEL_STAGES.map((stage) => {
          const chips = stage.key === 'invited'
            ? [...(decisionRow.invited || []), ...(decisionRow.completed || [])]
            : (decisionRow[stage.key] || []);
          return (
            <div key={stage.key} className="fb-dcell">
              {OUTCOME_KEYS.has(stage.key) ? (
                <span className="fb-dnone">outcome</span>
              ) : chips.length ? (
                chips.map((chip) => (
                  <span
                    key={chip.key}
                    className={`fb-dchip is-${chip.tone}`}
                    title={chip.tip || undefined}
                  >
                    {formatCount(chip.count)} {chip.label}
                  </span>
                ))
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
