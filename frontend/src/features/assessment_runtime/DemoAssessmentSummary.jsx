import React from 'react';

import { ComparisonRadar } from '../../shared/ui/ComparisonRadar';
import { Badge, Button, Card, Panel } from '../../shared/ui/TaaliPrimitives';
import { AssessmentBrandGlyph } from './AssessmentBrandGlyph';

const formatDuration = (seconds) => {
  const safeSeconds = Math.max(0, Number(seconds) || 0);
  const minutes = Math.floor(safeSeconds / 60);
  const remainder = safeSeconds % 60;
  return `${minutes}m ${String(remainder).padStart(2, '0')}s`;
};

const scoreBarColor = (value) => {
  if (value >= 7) return 'var(--taali-success)';
  if (value >= 5) return 'var(--taali-warning)';
  return 'var(--taali-danger)';
};

const renderIdentityBadges = (identity = {}) => ([
  identity.taskName ? `Task: ${identity.taskName}` : null,
  identity.durationLabel ? `Duration: ${identity.durationLabel}` : null,
  identity.completedLabel ? `Completed: ${identity.completedLabel}` : null,
].filter(Boolean));

export const DemoAssessmentSummary = ({
  summary,
  onRestart,
  onJoinTaali,
}) => {
  const reportModel = summary?.reportModel || null;
  const identity = reportModel?.identity || {};
  const summaryModel = reportModel?.summaryModel || {};
  const source = reportModel?.source || null;
  const feedback = reportModel?.feedback || {};
  const hasDimensionSignal = Boolean(reportModel?.hasDimensionSignal);
  const dimensionEntries = Array.isArray(reportModel?.dimensionEntries) ? reportModel.dimensionEntries : [];
  const radarSeries = hasDimensionSignal ? [{
    id: identity.assessmentId || 1,
    name: identity.name || 'Candidate',
    _raw: {
      score_breakdown: {
        category_scores: summaryModel.categoryScores || {},
      },
    },
  }] : [];

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
          <Panel className="overflow-hidden p-0">
            <div className="grid gap-0 xl:grid-cols-[minmax(0,1fr)_340px]">
              <div className="px-5 py-5 md:px-6" style={{ background: 'var(--taali-card-bg)' }}>
                <p className="mb-2 text-xs font-semibold uppercase tracking-[0.12em] text-[var(--taali-muted)]">
                  {identity.sectionLabel || 'TAALI profile'}
                </p>
                <h1 className="taali-display text-4xl font-semibold text-[var(--taali-text)]">{identity.name || 'Your TAALI profile'}</h1>
                {identity.email ? (
                  <p className="mt-2 text-sm text-[var(--taali-muted)]">{identity.email}</p>
                ) : null}
                {renderIdentityBadges(identity).length ? (
                  <div className="mt-4 flex flex-wrap gap-2">
                    {renderIdentityBadges(identity).map((label) => (
                      <Badge key={label} variant="muted" className="font-mono text-[11px]">{label}</Badge>
                    ))}
                  </div>
                ) : null}

                <div className="mt-6 grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
                  <div className="rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-4">
                    <div className="mb-3 flex items-center justify-between gap-3">
                      <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
                        Dimension profile
                      </div>
                      {hasDimensionSignal ? (
                        <Badge variant="muted" className="font-mono text-[11px]">{dimensionEntries.length} dimensions</Badge>
                      ) : null}
                    </div>
                    {hasDimensionSignal ? (
                      <ComparisonRadar
                        assessments={radarSeries}
                        highlightAssessmentId={identity.assessmentId || 1}
                        categoryKeys={reportModel.radarCategoryKeys}
                        showLegend={false}
                        height={300}
                      />
                    ) : (
                      <p className="text-sm text-[var(--taali-muted)]">Assessment evidence is available, but dimension-level scoring has not been returned yet.</p>
                    )}
                  </div>

                  <div className="rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-4">
                    <div className="mb-3 flex items-center justify-between gap-3">
                      <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
                        Dimension scores
                      </div>
                      {source ? (
                        <Badge variant={source.badgeVariant || 'muted'} className="font-mono text-[11px]">{source.label}</Badge>
                      ) : null}
                    </div>
                    {hasDimensionSignal ? (
                      <div className="space-y-2 font-mono text-xs">
                        {dimensionEntries.map((item) => (
                          <div key={item.key} className="grid grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)_auto] items-center gap-3">
                            <span className="min-w-0 leading-snug text-[var(--taali-muted)]">{item.label}</span>
                            <div className="h-2 flex-1 overflow-hidden rounded-full bg-[var(--taali-border-subtle)]">
                              <div
                                className="h-full rounded-full"
                                style={{
                                  width: `${(item.value / 10) * 100}%`,
                                  backgroundColor: scoreBarColor(item.value),
                                }}
                              />
                            </div>
                            <span className="w-10 text-right text-[var(--taali-text)]">{item.value.toFixed(1)}</span>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="text-sm text-[var(--taali-muted)]">Dimension scoring is still being finalized.</p>
                    )}
                  </div>
                </div>
              </div>

              <div className="border-t border-[var(--taali-border-soft)] p-4 text-[var(--taali-text)] xl:border-l xl:border-t-0" style={{ background: 'var(--taali-panel-bg)' }}>
                <div className="grid gap-3">
                  <Card className="bg-[var(--taali-surface)] p-4">
                    <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--taali-muted)]">
                      {feedback?.title || 'Assessment feedback'}
                    </div>
                    <p className="mt-2 text-sm leading-6 text-[var(--taali-text)]">
                      {feedback?.summary || 'Assessment evidence is available for this demo run.'}
                    </p>
                    {Array.isArray(feedback?.bullets) && feedback.bullets.length ? (
                      <ul className="mt-3 space-y-2">
                        {feedback.bullets.map((item) => (
                          <li key={item} className="flex gap-2 text-sm text-[var(--taali-text)]">
                            <span className="mt-1 h-1.5 w-1.5 rounded-full bg-[var(--taali-purple)]" />
                            <span>{item}</span>
                          </li>
                        ))}
                      </ul>
                    ) : null}
                    {feedback?.note ? (
                      <p className="mt-4 text-xs leading-5 text-[var(--taali-muted)]">{feedback.note}</p>
                    ) : null}
                  </Card>
                </div>
              </div>
            </div>
          </Panel>
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
