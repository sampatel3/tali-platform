import React from 'react';

import { formatScale100Score, scoreTone100 } from '../../lib/scoreDisplay';
import { Badge, Card, Panel, cx } from '../../shared/ui/TaaliPrimitives';

const variantConfig = {
  compact: {
    reasonLimit: 3,
    requirementsLimit: 3,
    chipLimit: 5,
    titleSize: 'text-sm font-semibold',
  },
  full: {
    reasonLimit: 6,
    requirementsLimit: Infinity,
    chipLimit: Infinity,
    titleSize: 'text-base font-semibold',
  },
};

const statusBadgeVariant = (status) => {
  if (status === 'met') return 'success';
  if (status === 'partially_met') return 'warning';
  if (status === 'missing') return 'danger';
  return 'muted';
};

const priorityBadgeVariant = (priority) => {
  if (priority === 'must_have' || priority === 'constraint') return 'warning';
  return 'muted';
};

const ScoreCard = ({ label, value }) => (
  <Card className="bg-[var(--taali-surface-subtle)] p-4">
    <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">{label}</div>
    <div className="mt-2 taali-display text-3xl font-semibold" style={{ color: scoreTone100(value) }}>
      {formatScale100Score(value, '0-100')}
    </div>
  </Card>
);

export function RoleFitEvidenceSections({
  model,
  variant = 'full',
  className = '',
  showScoreCards = true,
  emptyMessage = 'No role-fit evidence is available for this candidate.',
}) {
  const config = variantConfig[variant] || variantConfig.full;
  const rationaleBullets = (model?.rationaleBullets || []).slice(0, config.reasonLimit);
  const requirements = (model?.requirementsAssessment || []).slice(0, config.requirementsLimit);
  const matchingSkills = (model?.matchingSkills || []).slice(0, config.chipLimit);
  const missingSkills = (model?.missingSkills || []).slice(0, config.chipLimit);
  const experienceHighlights = (model?.experienceHighlights || []).slice(0, config.reasonLimit);
  const concerns = (model?.concerns || []).slice(0, config.reasonLimit);
  const requirementsCoverage = model?.requirementsCoverage || {};

  if (!model?.hasAnyEvidence) {
    return (
      <Panel className={cx('p-4 text-sm text-[var(--taali-muted)]', className)}>
        {emptyMessage}
      </Panel>
    );
  }

  return (
    <div className={cx('space-y-4', className)}>
      {showScoreCards ? (
        <div className={cx('grid gap-3', variant === 'compact' ? 'md:grid-cols-2' : 'md:grid-cols-3')}>
          {model?.overallScore != null ? <ScoreCard label="CV fit" value={model.overallScore} /> : null}
          {model?.requirementsFitScore != null ? <ScoreCard label="Requirements fit" value={model.requirementsFitScore} /> : null}
          {variant === 'full' && model?.experienceScore != null ? <ScoreCard label="Experience" value={model.experienceScore} /> : null}
        </div>
      ) : null}

      {rationaleBullets.length > 0 ? (
        <Panel className="p-4">
          <div className={cx(config.titleSize, 'text-[var(--taali-text)]')}>Why this score</div>
          <ul className="mt-3 space-y-2">
            {rationaleBullets.map((item, index) => (
              <li key={`${variant}-reason-${index}`} className="flex gap-2 text-sm text-[var(--taali-text)]">
                <span className="mt-1 h-1.5 w-1.5 rounded-full bg-[var(--taali-purple)]" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}

      {(requirements.length > 0 || requirementsCoverage.total) ? (
        <Panel className="p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className={cx(config.titleSize, 'text-[var(--taali-text)]')}>Recruiter requirements fit</div>
            {model?.requirementsFitScore != null ? (
              <Badge variant="purple" className="text-[11px]">
                {formatScale100Score(model.requirementsFitScore, '0-100')}
              </Badge>
            ) : null}
          </div>

          {requirementsCoverage.total ? (
            <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-[var(--taali-muted)] md:grid-cols-4">
              <div>Total: {requirementsCoverage.total}</div>
              <div>Met: {requirementsCoverage.met ?? 0}</div>
              <div>Partial: {requirementsCoverage.partially_met ?? 0}</div>
              <div>Missing: {requirementsCoverage.missing ?? 0}</div>
            </div>
          ) : null}

          {requirements.length > 0 ? (
            <div className="mt-3 space-y-3">
              {requirements.map((item, index) => (
                <Card key={`${item.requirement}-${index}`} className="bg-[var(--taali-surface-subtle)] p-3.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant={priorityBadgeVariant(item.priority)} className="text-[11px]">
                      {item.priority.replace(/_/g, ' ')}
                    </Badge>
                    <Badge variant={statusBadgeVariant(item.status)} className="text-[11px]">
                      {item.status.replace(/_/g, ' ')}
                    </Badge>
                  </div>
                  <div className="mt-3 text-sm font-semibold text-[var(--taali-text)]">{item.requirement}</div>
                  {item.evidence ? (
                    <div className="mt-2 text-sm text-[var(--taali-muted)]">
                      <span className="font-medium text-[var(--taali-text)]">Evidence:</span> {item.evidence}
                    </div>
                  ) : null}
                  {item.impact ? (
                    <div className="mt-1 text-sm text-[var(--taali-muted)]">
                      <span className="font-medium text-[var(--taali-text)]">Impact:</span> {item.impact}
                    </div>
                  ) : null}
                </Card>
              ))}
            </div>
          ) : null}
        </Panel>
      ) : null}

      {matchingSkills.length > 0 ? (
        <Panel className="p-4">
          <div className={cx(config.titleSize, 'text-[var(--taali-text)]')}>Matching skills</div>
          <div className="mt-3 flex flex-wrap gap-2">
            {matchingSkills.map((skill) => (
              <Badge key={skill} variant="success">{skill}</Badge>
            ))}
          </div>
        </Panel>
      ) : null}

      {variant === 'full' && experienceHighlights.length > 0 ? (
        <Panel className="p-4">
          <div className={cx(config.titleSize, 'text-[var(--taali-text)]')}>Relevant experience</div>
          <ul className="mt-3 space-y-2">
            {experienceHighlights.map((item, index) => (
              <li key={`experience-${index}`} className="flex gap-2 text-sm text-[var(--taali-text)]">
                <span className="mt-1 h-1.5 w-1.5 rounded-full bg-[var(--taali-success)]" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}

      {missingSkills.length > 0 ? (
        <Panel className="p-4">
          <div className={cx(config.titleSize, 'text-[var(--taali-text)]')}>Gaps</div>
          <div className="mt-3 flex flex-wrap gap-2">
            {missingSkills.map((skill) => (
              <Badge key={skill} variant="warning">{skill}</Badge>
            ))}
          </div>
        </Panel>
      ) : null}

      {concerns.length > 0 ? (
        <Panel className="border-[var(--taali-warning-border)] bg-[var(--taali-warning-soft)] p-4">
          <div className={cx(config.titleSize, 'text-[var(--taali-text)]')}>Risks and concerns</div>
          <ul className="mt-3 space-y-2">
            {concerns.map((item, index) => (
              <li key={`concern-${index}`} className="flex gap-2 text-sm text-[var(--taali-text)]">
                <span className="mt-1 h-1.5 w-1.5 rounded-full bg-[var(--taali-warning)]" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}
    </div>
  );
}

export default RoleFitEvidenceSections;
