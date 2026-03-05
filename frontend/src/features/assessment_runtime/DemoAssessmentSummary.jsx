import React from 'react';

import { CandidateAssessmentSummaryView } from '../candidates/CandidateAssessmentSummaryView';
import { Button, Card } from '../../shared/ui/TaaliPrimitives';
import { AssessmentBrandGlyph } from './AssessmentBrandGlyph';

const formatDuration = (seconds) => {
  const safeSeconds = Math.max(0, Number(seconds) || 0);
  const minutes = Math.floor(safeSeconds / 60);
  const remainder = safeSeconds % 60;
  return `${minutes}m ${String(remainder).padStart(2, '0')}s`;
};

export const DemoAssessmentSummary = ({
  summary,
  onRestart,
  onJoinTaali,
}) => {
  const reportModel = summary?.reportModel || null;

  return (
    <div className="min-h-screen bg-[var(--taali-bg)] text-[var(--taali-text)]">
      <nav className="border-b border-[var(--taali-border-soft)] bg-[var(--taali-surface)] backdrop-blur-sm">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-3 px-6 py-4">
          <div className="flex items-center gap-3">
            <AssessmentBrandGlyph />
            <span className="text-lg font-bold tracking-tight">TAALI Demo Results</span>
          </div>
          <Button type="button" variant="secondary" size="sm" onClick={onRestart}>
            Try another demo
          </Button>
        </div>
      </nav>

      <div className="mx-auto max-w-6xl px-6 py-10">
        {reportModel ? (
          <CandidateAssessmentSummaryView
            reportModel={reportModel}
            variant="page"
            showSupplementalPanels={false}
            showRoleFitMetric={false}
            radarCategoryKeys={reportModel.radarCategoryKeys}
          />
        ) : (
          <Card className="p-4 font-mono text-sm text-[var(--taali-muted)]">
            Demo summary is not available yet.
          </Card>
        )}

        <Card className="mt-5 grid gap-2 p-4 md:grid-cols-4">
          <div className="font-mono text-sm"><span className="text-[var(--taali-muted)]">AI prompts:</span> {summary?.meta?.promptCount ?? 0}</div>
          <div className="font-mono text-sm"><span className="text-[var(--taali-muted)]">Code runs:</span> {summary?.meta?.runCount ?? 0}</div>
          <div className="font-mono text-sm"><span className="text-[var(--taali-muted)]">Saves:</span> {summary?.meta?.saveCount ?? 0}</div>
          <div className="font-mono text-sm"><span className="text-[var(--taali-muted)]">Session time:</span> {formatDuration(summary?.meta?.timeSpentSeconds)}</div>
        </Card>

        <div className="mt-6 flex flex-wrap gap-3">
          <Button type="button" variant="primary" size="lg" onClick={onJoinTaali}>
            Join TAALI
          </Button>
          <Button type="button" variant="secondary" size="lg" onClick={onRestart}>
            Try another demo
          </Button>
        </div>
      </div>
    </div>
  );
};
