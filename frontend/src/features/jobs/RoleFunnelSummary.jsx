import React from 'react';

import { PIPELINE_FUNNEL_STAGES, funnelStageTone, formatCount } from '../../shared/metrics';

// Compact six-stage funnel summary (Applied → Invited → Assessing → Review →
// Advanced → Rejected) for the role-detail header — the same canonical funnel
// the home/jobs-list role card renders, so a role's standing reads identically
// wherever it's surfaced. Counts come from the role GET's stage_counts
// aggregate (whole pipeline, not the row-capped applications fetch). Review
// goes purple when it needs you; Rejected is the muted, divided-off terminal.
export const RoleFunnelSummary = ({ stageCounts }) => (
  <div className="job-summary-card">
    <div className="job-stats">
      {PIPELINE_FUNNEL_STAGES.map((stage) => {
        const value = Number(stageCounts?.[stage.key] || 0);
        const tone = funnelStageTone(stage.key, value);
        return (
          <div key={stage.key} className={`js-cell${tone === 'term' ? ' is-term' : ''}`}>
            <div className="k">{stage.label}</div>
            <div
              className="v"
              style={tone === 'attn' ? { color: 'var(--purple)' } : tone === 'term' ? { color: 'var(--mute)' } : undefined}
            >
              {formatCount(value)}
            </div>
          </div>
        );
      })}
    </div>
  </div>
);
