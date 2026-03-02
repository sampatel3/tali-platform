import React from 'react';

import { Badge, Panel } from '../../shared/ui/TaaliPrimitives';
import { formatCvScore100, formatDateTime } from './candidatesUiUtils';
import { CandidateScoreRing } from './CandidateScoreRing';

export function CandidateSidebarScoreHero({
  application,
  score,
  scoreDetails = { score_scale: '0-100' },
  mode,
  subtitle,
}) {
  const resolvedSubtitle = subtitle
    || application?.role_name
    || application?.candidate_position
    || application?.candidate_headline
    || 'Candidate summary';

  return (
    <Panel className="overflow-hidden border-2 border-[var(--taali-border)] bg-[linear-gradient(135deg,rgba(190,171,255,0.16),rgba(255,255,255,0.98))] p-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <CandidateScoreRing
            score={score}
            details={scoreDetails}
            size={88}
            strokeWidth={8}
            label={`TAALI Score for ${application?.candidate_name || application?.candidate_email || 'candidate'}`}
            valueClassName="text-[1.25rem]"
          />
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">TAALI Score</p>
            <p className="mt-1.5 font-mono text-2xl font-bold text-[var(--taali-text)]">
              {formatCvScore100(score, scoreDetails)}
            </p>
            {resolvedSubtitle ? (
              <p className="mt-1.5 text-[13px] text-[var(--taali-muted)]">{resolvedSubtitle}</p>
            ) : null}
          </div>
        </div>
        <div className="space-y-2 sm:text-right">
          {mode ? <Badge variant={mode.variant}>{mode.label}</Badge> : null}
          <p className="text-xs text-[var(--taali-muted)]">
            Updated {formatDateTime(application?.updated_at || application?.created_at)}
          </p>
        </div>
      </div>
    </Panel>
  );
}
