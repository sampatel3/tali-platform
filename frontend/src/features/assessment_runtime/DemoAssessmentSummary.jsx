import React from 'react';

import { Button, Card, Panel } from '../../shared/ui/TaaliPrimitives';
import { AssessmentBrandGlyph } from './AssessmentBrandGlyph';

const formatDuration = (seconds) => {
  const safeSeconds = Math.max(0, Number(seconds) || 0);
  const minutes = Math.floor(safeSeconds / 60);
  const remainder = safeSeconds % 60;
  return `${minutes}m ${String(remainder).padStart(2, '0')}s`;
};

export const DemoAssessmentSummary = ({
  assessmentName,
  profile,
  summary,
  onRestart,
  onJoinTaali,
}) => {
  return (
    <div className="min-h-screen bg-[var(--taali-bg)] text-[var(--taali-text)]">
      <nav className="border-b-2 border-black bg-white">
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
        <Panel className="p-6">
          <div className="mb-2 inline-flex border-2 border-black bg-[var(--taali-purple)] px-3 py-1 font-mono text-xs font-bold text-white">
            TAALI PROFILE
          </div>
          <h1 className="text-3xl font-bold">
            {profile?.fullName ? `${profile.fullName}'s` : 'Your'} TAALI profile
          </h1>
          <p className="mt-2 font-mono text-sm text-[var(--taali-muted)]">
            Assessment: {assessmentName || 'Demo task'}
          </p>
          <p className="mt-3 max-w-3xl font-mono text-sm text-[var(--taali-muted)]">
            This is a short demo summary, not the full TAALI assessment report. The full report includes deeper evidence, reviewer context, and role-calibrated scoring.
          </p>

          <Card className="mt-6 p-4">
            <h3 className="text-lg font-bold">Compared with successful candidates</h3>
            <p className="mt-1 font-mono text-xs text-[var(--taali-muted)]">
              Placeholder benchmark view while live cohort comparison is being finalized.
            </p>
            <div className="mt-3 grid gap-2 font-mono text-sm">
              <div>
                <span className="text-[var(--taali-muted)]">Your TAALI profile:</span>{' '}
                <span className="font-bold">{summary?.comparison?.candidateScore ?? 0}/100</span>
              </div>
              <div>
                <span className="text-[var(--taali-muted)]">{summary?.comparison?.benchmarkLabel || 'Successful-candidate average'}:</span>{' '}
                <span className="font-bold">{summary?.comparison?.benchmarkScore ?? 0}/100</span>
              </div>
              <div>
                <span className="text-[var(--taali-muted)]">Difference:</span>{' '}
                <span className={`font-bold ${(summary?.comparison?.deltaScore || 0) >= 0 ? 'text-green-700' : 'text-red-700'}`}>
                  {(summary?.comparison?.deltaScore || 0) >= 0 ? '+' : ''}
                  {summary?.comparison?.deltaScore || 0}
                </span>
              </div>
            </div>
            <div className="mt-4 space-y-1.5">
              {(summary?.comparison?.categories || []).map((entry) => (
                <div key={entry.key} className="flex items-center justify-between gap-3 font-mono text-xs">
                  <span className="text-[var(--taali-muted)]">{entry.label}</span>
                  <span>
                    {entry.candidateScore}/100 vs {entry.benchmarkScore}/100
                  </span>
                </div>
              ))}
            </div>
          </Card>

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
        </Panel>
      </div>
    </div>
  );
};
