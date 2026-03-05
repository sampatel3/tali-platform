import React from 'react';

import { ScoreHeroCard, ScoreMetricCard, InsightCard } from '../../shared/ui/AssessmentSignalPrimitives';
import { ComparisonRadar } from '../../shared/ui/ComparisonRadar';
import { Badge, Card, Panel, cx } from '../../shared/ui/TaaliPrimitives';
import { RoleFitEvidenceSections } from './RoleFitEvidenceSections';

const variantConfig = {
  page: {
    root: 'space-y-4',
    heroGrid: 'xl:grid-cols-[minmax(0,1fr)_340px]',
    signalGrid: 'xl:grid-cols-[minmax(0,1fr)_320px]',
    scoreGrid: 'sm:grid-cols-2',
    scoreHeroValue: 'text-[3.8rem]',
    insightGrid: 'md:grid-cols-3',
    roleFitVariant: 'full',
    evidenceGrid: 'md:grid-cols-2',
  },
  sheet: {
    root: 'space-y-4',
    heroGrid: 'grid-cols-1',
    signalGrid: 'grid-cols-1',
    scoreGrid: 'sm:grid-cols-2',
    scoreHeroValue: 'text-[3.2rem]',
    insightGrid: 'grid-cols-1',
    roleFitVariant: 'compact',
    evidenceGrid: 'grid-cols-1',
  },
  preview: {
    root: 'space-y-3',
    heroGrid: 'xl:grid-cols-[minmax(0,1fr)_320px]',
    signalGrid: 'xl:grid-cols-[minmax(0,1fr)_280px]',
    scoreGrid: 'grid-cols-2',
    scoreHeroValue: 'text-[3.2rem]',
    insightGrid: 'md:grid-cols-3',
    roleFitVariant: 'compact',
    evidenceGrid: 'md:grid-cols-2',
  },
};

const scoreBarColor = (value) => {
  if (value >= 7) return 'var(--taali-success)';
  if (value >= 5) return 'var(--taali-warning)';
  return 'var(--taali-danger)';
};

const renderIdentityBadges = (identity = {}) => ([
  identity.position ? `Position: ${identity.position}` : null,
  identity.taskName ? `Task: ${identity.taskName}` : null,
  identity.roleName ? `Role: ${identity.roleName}` : null,
  identity.applicationStatus ? `Application: ${identity.applicationStatus}` : null,
  identity.durationLabel ? `Duration: ${identity.durationLabel}` : null,
  identity.completedLabel ? `Completed: ${identity.completedLabel}` : null,
].filter(Boolean));

const EvidenceSectionCard = ({ section }) => {
  if (!section) return null;

  return (
    <Card className="p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
          {section.title}
        </div>
        {section.badgeLabel ? (
          <Badge variant={section.badgeVariant || 'muted'} className="font-mono text-[11px]">
            {section.badgeLabel}
          </Badge>
        ) : null}
      </div>
      {section.description ? (
        <p className="mt-2 text-sm leading-6 text-[var(--taali-muted)]">{section.description}</p>
      ) : null}
      {section.items?.length ? (
        <ul className="mt-3 space-y-2">
          {section.items.map((item) => (
            <li key={item} className="flex gap-2 text-sm text-[var(--taali-text)]">
              <span className="mt-1 h-1.5 w-1.5 rounded-full bg-[var(--taali-purple)]" />
              <span>{item}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-sm text-[var(--taali-muted)]">{section.emptyMessage}</p>
      )}
    </Card>
  );
};

export function CandidateReportView({
  model,
  variant = 'page',
  className = '',
  showInsights = true,
  showRoleFitSection = true,
  showIntegritySection = true,
  showEvidenceSections = true,
  showRoleFitMetric = true,
  radarCategoryKeys = null,
}) {
  const config = variantConfig[variant] || variantConfig.page;
  const {
    identity = {},
    source,
    summaryModel,
    roleFitModel,
    recommendation,
    dimensionEntries,
    recruiterSummaryText,
    strongestSignalTitle,
    strongestSignalDescription,
    probeTitle,
    probeDescription,
    integritySummaryText,
    evidenceSections,
    hasCompletedAssessment,
    hasDimensionSignal,
  } = model || {};

  if (!model) {
    return null;
  }

  const radarSeries = hasDimensionSignal ? [{
    id: identity.assessmentId || 'report',
    name: identity.name || 'Candidate',
    _raw: {
      score_breakdown: {
        category_scores: summaryModel.categoryScores || {},
      },
    },
  }] : [];

  return (
    <div className={cx(config.root, className)}>
      <Panel className="overflow-hidden p-0">
        <div className={cx('grid gap-0', config.heroGrid)}>
          <div className="px-5 py-5 md:px-6" style={{ background: 'var(--taali-card-bg)' }}>
            <p className="mb-2 text-xs font-semibold uppercase tracking-[0.12em] text-[var(--taali-muted)]">
              {identity.sectionLabel || 'Candidate report'}
            </p>
            <h1 className="taali-display text-4xl font-semibold text-[var(--taali-text)]">{identity.name || 'Candidate'}</h1>
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

            <div className={cx('mt-6 grid gap-4', config.signalGrid)}>
              <div className="rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-4">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
                    Dimension profile
                  </div>
                  {hasDimensionSignal ? (
                    <Badge variant="muted" className="font-mono text-[11px]">{dimensionEntries.length} dimensions</Badge>
                  ) : (
                    <Badge variant={hasCompletedAssessment ? 'warning' : 'muted'} className="font-mono text-[11px]">
                      {hasCompletedAssessment ? 'Signal pending' : 'Pre-assessment'}
                    </Badge>
                  )}
                </div>
                {hasDimensionSignal ? (
                  <ComparisonRadar
                    assessments={radarSeries}
                    highlightAssessmentId={identity.assessmentId || 'report'}
                    categoryKeys={radarCategoryKeys}
                    showLegend={false}
                    height={variant === 'sheet' ? 260 : 300}
                  />
                ) : (
                  <div className="rounded-[var(--taali-radius-card)] border border-dashed border-[var(--taali-border-soft)] bg-[var(--taali-surface-subtle)] px-4 py-10 text-sm text-[var(--taali-muted)]">
                    {hasCompletedAssessment
                      ? 'Dimension scoring is still being finalized for this assessment.'
                      : 'This standing report will add dimension signal once the candidate completes an assessment.'}
                  </div>
                )}
              </div>

              <div className="rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-4">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
                    Dimension scores
                  </div>
                  {source ? (
                    <Badge variant={source.badgeVariant} className="font-mono text-[11px]">{source.label}</Badge>
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
                  <p className="text-sm text-[var(--taali-muted)]">
                    {hasCompletedAssessment
                      ? 'Assessment evidence is available, but dimension-level scoring has not been returned yet.'
                      : 'Role fit is currently the main ranking signal until the candidate completes an assessment.'}
                  </p>
                )}
              </div>
            </div>
          </div>

          <div className="border-t border-[var(--taali-border-soft)] p-4 text-[var(--taali-text)] xl:border-l xl:border-t-0" style={{ background: 'var(--taali-panel-bg)' }}>
            <div className="grid gap-3">
              <ScoreHeroCard
                label="TAALI score"
                value={summaryModel.taaliScore}
                scale="0-100"
                description={summaryModel.heuristicSummary}
                badgeLabel={recommendation?.label}
                badgeVariant={recommendation?.variant || 'muted'}
                valueClassName={config.scoreHeroValue}
              />

              <div className={cx('grid gap-3', showRoleFitMetric ? config.scoreGrid : 'grid-cols-1')}>
                {showRoleFitMetric ? (
                  <ScoreMetricCard label="Role fit" value={summaryModel.roleFitScore} />
                ) : null}
                <ScoreMetricCard label="Assessment" value={summaryModel.assessmentScore} />
              </div>
            </div>
          </div>
        </div>

        {showInsights ? (
          <div className={cx('grid gap-3 border-t border-[var(--taali-border-soft)] bg-[var(--taali-surface-subtle)] p-4', config.insightGrid)}>
            <InsightCard
              label="Strongest signal"
              title={strongestSignalTitle}
              description={strongestSignalDescription}
            />
            <InsightCard
              label="What to probe"
              title={probeTitle}
              description={probeDescription}
            />
            <InsightCard
              label="Recruiter summary"
              title={recommendation?.label || 'Pending review'}
              description={recruiterSummaryText}
            />
          </div>
        ) : null}
      </Panel>

      {showRoleFitSection ? (
        <RoleFitEvidenceSections
          model={roleFitModel}
          variant={config.roleFitVariant}
          showScoreCards={false}
          emptyMessage="Role-fit evidence will populate here as TAALI gathers more candidate data."
        />
      ) : null}

      {showIntegritySection ? (
        <Panel className="p-4">
          <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Integrity and history</div>
          <p className="mt-2 text-sm text-[var(--taali-text)]">{integritySummaryText}</p>
        </Panel>
      ) : null}

      {showEvidenceSections ? (
        <div className={cx('grid gap-4', config.evidenceGrid)}>
          <EvidenceSectionCard section={evidenceSections?.aiUsage} />
          <EvidenceSectionCard section={evidenceSections?.codeAndGit} />
          <EvidenceSectionCard section={evidenceSections?.timeline} />
          <EvidenceSectionCard section={evidenceSections?.documents} />
        </div>
      ) : null}
    </div>
  );
}

export default CandidateReportView;
