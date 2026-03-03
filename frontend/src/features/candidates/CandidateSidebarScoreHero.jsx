import React from 'react';

import { formatScale100Score } from '../../lib/scoreDisplay';
import { Badge, Panel } from '../../shared/ui/TaaliPrimitives';
import { formatDateTime } from './candidatesUiUtils';
import { CandidateScoreRing } from './CandidateScoreRing';

export function CandidateSidebarScoreHero({
  application,
  score,
  scoreDetails = { score_scale: '0-100' },
  mode,
  subtitle,
  sourceMeta = null,
  caption = '',
}) {
  const resolvedSubtitle = subtitle
    || application?.role_name
    || application?.candidate_position
    || application?.candidate_headline
    || 'Candidate summary';

  return (
    <Panel className="overflow-hidden border border-[var(--taali-border-soft)] bg-[linear-gradient(145deg,rgba(255,255,255,0.98),rgba(239,233,255,0.88))] p-5 shadow-[var(--taali-shadow-soft)]">
      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-4">
            <CandidateScoreRing
              score={score}
              details={scoreDetails}
              size={96}
              strokeWidth={9}
              label={`TAALI Score for ${application?.candidate_name || application?.candidate_email || 'candidate'}`}
              valueClassName="text-[1.45rem]"
            />
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">TAALI Score</p>
                {sourceMeta ? <Badge variant={sourceMeta.badgeVariant}>{sourceMeta.label}</Badge> : null}
              </div>
              <p className="mt-2 taali-display text-4xl font-semibold text-[var(--taali-text)]">
                {formatScale100Score(score, scoreDetails?.score_scale || '0-100')}
              </p>
              {resolvedSubtitle ? (
                <p className="mt-2 text-sm text-[var(--taali-muted)]">{resolvedSubtitle}</p>
              ) : null}
            </div>
          </div>

          <div className="space-y-3 sm:text-right">
            {mode ? <Badge variant={mode.variant}>{mode.label}</Badge> : null}
            <p className="text-xs text-[var(--taali-muted)]">
              Updated {formatDateTime(sourceMeta?.updatedAt || application?.updated_at || application?.created_at)}
            </p>
          </div>
        </div>

        {caption ? (
          <p className="rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[rgba(255,255,255,0.72)] px-3 py-2 text-sm text-[var(--taali-muted)]">
            {caption}
          </p>
        ) : null}
      </div>
    </Panel>
  );
}
