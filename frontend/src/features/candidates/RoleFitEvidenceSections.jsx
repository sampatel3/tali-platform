import React, { useState } from 'react';

import { formatScale100Score, scoreTone100 } from '../../lib/scoreDisplay';
import { Badge, Card, Panel, cx } from '../../shared/ui/TaaliPrimitives';

const variantConfig = {
  compact: {
    reasonLimit: 3,
    requirementsLimit: 4,
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

// Purple-forward status palette: "met" reads as brand purple, not green, so a strong
// candidate doesn't render as a wall of green ticks. Only genuine gaps go amber.
const statusStyle = (status) => {
  if (status === 'met') return { background: 'var(--taali-purple-soft)', color: 'var(--taali-purple-hover)' };
  if (status === 'missing') return { background: 'var(--taali-warning-soft)', color: 'color-mix(in oklab, var(--taali-warning) 60%, var(--taali-text))' };
  if (status === 'partially_met') return { background: 'color-mix(in oklab, var(--taali-purple) 9%, var(--taali-surface-subtle))', color: 'var(--taali-muted)' };
  return { background: 'var(--taali-surface-subtle)', color: 'var(--taali-muted)' };
};

const statusDot = (status) => {
  if (status === 'met') return 'var(--taali-purple)';
  if (status === 'missing') return 'var(--taali-warning)';
  if (status === 'partially_met') return 'color-mix(in oklab, var(--taali-purple) 45%, var(--taali-surface-subtle))';
  return 'var(--taali-muted)';
};

const statusLabel = (status) => {
  if (status === 'met') return 'Met';
  if (status === 'missing') return 'Gap';
  if (status === 'partially_met') return 'Partial';
  return 'Unknown';
};

// Gaps surface first, then partials, then met — so concerns are always above the fold.
const statusOrder = (status) => {
  if (status === 'missing') return 0;
  if (status === 'partially_met') return 1;
  if (status === 'met') return 2;
  return 3;
};

const isMustHave = (priority) => priority === 'must_have' || priority === 'constraint';

const coverageFilledCount = (coverage) => (
  (Number(coverage?.met) || 0)
  + (Number(coverage?.partially_met) || 0)
  + (Number(coverage?.missing) || 0)
);

const ScoreCard = ({ label, value }) => (
  <Card className="bg-[var(--taali-surface-subtle)] p-4">
    <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">{label}</div>
    <div className="mt-2 taali-display text-3xl font-semibold" style={{ color: scoreTone100(value) }}>
      {formatScale100Score(value, '0-100')}
    </div>
  </Card>
);

const CoverageLegendItem = ({ dot, count, label }) => (
  <span className="inline-flex items-center gap-1.5">
    <span className="h-2 w-2 rounded-full" style={{ background: dot }} />
    <span className="font-semibold text-[var(--taali-text)]">{count}</span>
    <span>{label}</span>
  </span>
);

export const RequirementCoverageStrip = ({ coverage }) => {
  const met = Number(coverage?.met) || 0;
  const partial = Number(coverage?.partially_met) || 0;
  const missing = Number(coverage?.missing) || 0;
  if (met + partial + missing <= 0) return null;

  return (
    <div>
      <div className="flex h-2.5 gap-[2px] overflow-hidden rounded-full" style={{ background: 'var(--taali-border-soft)' }}>
        {met ? <span style={{ flex: met, background: 'var(--taali-purple)' }} /> : null}
        {partial ? <span style={{ flex: partial, background: statusDot('partially_met') }} /> : null}
        {missing ? <span style={{ flex: missing, background: 'var(--taali-warning)' }} /> : null}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--taali-muted)]">
        <CoverageLegendItem dot="var(--taali-purple)" count={met} label="met" />
        <CoverageLegendItem dot={statusDot('partially_met')} count={partial} label="partial" />
        <CoverageLegendItem dot="var(--taali-warning)" count={missing} label={missing === 1 ? 'gap' : 'gaps'} />
      </div>
    </div>
  );
};

const RequirementRow = ({ item }) => (
  <div
    className="grid grid-cols-[84px_1fr] gap-3 border-b border-[var(--taali-border-soft)] py-3 last:border-b-0"
    style={item.status === 'missing'
      ? { background: 'linear-gradient(0deg, var(--taali-warning-soft) 0%, transparent 88%)' }
      : undefined}
  >
    <span
      className="flex h-fit w-full items-center justify-center gap-1.5 rounded-lg px-2 py-1 text-[11px] font-semibold"
      style={statusStyle(item.status)}
    >
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: statusDot(item.status) }} />
      {statusLabel(item.status)}
    </span>
    <div>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-[var(--taali-text)]">{item.requirement}</span>
        <Badge variant={isMustHave(item.priority) ? 'purple' : 'muted'} className="text-[10px]">
          {isMustHave(item.priority) ? 'Must-have' : 'Nice-to-have'}
        </Badge>
      </div>
      {item.evidence ? (
        <p className="mt-1 text-[13px] leading-relaxed text-[var(--taali-muted)]">{item.evidence}</p>
      ) : null}
      {item.impact && item.status !== 'met' ? (
        <p className="mt-1 text-[13px] leading-relaxed text-[var(--taali-muted)]">
          <span className="font-medium text-[var(--taali-text)]">Impact:</span> {item.impact}
        </p>
      ) : null}
    </div>
  </div>
);

export const RequirementList = ({ requirements = [], limit = Infinity }) => {
  const [expanded, setExpanded] = useState(false);
  if (!requirements.length) return null;

  const sorted = [...requirements].sort((a, b) => statusOrder(a.status) - statusOrder(b.status));
  const cap = limit === Infinity ? sorted.length : limit;
  const visible = expanded ? sorted : sorted.slice(0, cap);
  const hidden = sorted.length - visible.length;

  return (
    <div className="border-t border-[var(--taali-border-soft)]">
      {visible.map((item, index) => (
        <RequirementRow key={`${item.requirement}-${index}`} item={item} />
      ))}
      {hidden > 0 ? (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="w-full py-3 text-left text-[13px] font-semibold text-[var(--taali-purple-hover)] hover:underline"
        >
          Show {hidden} more {hidden === 1 ? 'requirement' : 'requirements'}
        </button>
      ) : null}
    </div>
  );
};

export const SkillChipGroups = ({ matchingSkills = [], missingSkills = [], limit = Infinity }) => {
  const matching = matchingSkills.slice(0, limit);
  const missing = missingSkills.slice(0, limit);
  if (!matching.length && !missing.length) return null;

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <div>
        <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Matching skills</div>
        {matching.length ? (
          <div className="mt-2 flex flex-wrap gap-2">
            {matching.map((skill) => <Badge key={skill} variant="purple">{skill}</Badge>)}
          </div>
        ) : (
          <p className="mt-2 text-sm text-[var(--taali-muted)]">None extracted yet.</p>
        )}
      </div>
      <div>
        <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Skill gaps</div>
        {missing.length ? (
          <div className="mt-2 flex flex-wrap gap-2">
            {missing.map((skill) => <Badge key={skill} variant="warning">{skill}</Badge>)}
          </div>
        ) : (
          <p className="mt-2 text-sm text-[var(--taali-muted)]">None extracted.</p>
        )}
      </div>
    </div>
  );
};

export const ConcernsCallout = ({ concerns = [], limit = Infinity, titleSize = 'text-sm font-semibold' }) => {
  const items = concerns.slice(0, limit);
  if (!items.length) return null;

  return (
    <div className="rounded-[var(--taali-radius-card)] border border-[var(--taali-warning-border)] bg-[var(--taali-warning-soft)] p-4">
      <div className={cx(titleSize, 'text-[var(--taali-text)]')}>Risks &amp; concerns</div>
      <ul className="mt-2 space-y-2">
        {items.map((item, index) => (
          <li key={`concern-${index}`} className="flex gap-2 text-sm text-[var(--taali-text)]">
            <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--taali-warning)]" />
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
};

export function RoleFitEvidenceSections({
  model,
  variant = 'full',
  className = '',
  showScoreCards = true,
  emptyMessage = 'No role-fit evidence is available for this candidate.',
}) {
  const config = variantConfig[variant] || variantConfig.full;
  const rationaleBullets = (model?.rationaleBullets || []).slice(0, config.reasonLimit);
  const requirements = model?.requirementsAssessment || [];
  const coverage = model?.requirementsCoverage || {};
  const matchingSkills = model?.matchingSkills || [];
  const missingSkills = model?.missingSkills || [];
  const concerns = model?.concerns || [];
  const claimsToVerify = (model?.claimsToVerify || []).slice(0, config.reasonLimit);
  const timelineFlags = (model?.timelineFlags || []).slice(0, config.reasonLimit);

  if (!model?.hasAnyEvidence) {
    return (
      <Panel className={cx('p-4 text-sm text-[var(--taali-muted)]', className)}>
        {emptyMessage}
      </Panel>
    );
  }

  const hasCoverage = coverageFilledCount(coverage) > 0;

  return (
    <div className={cx('space-y-4', className)}>
      {showScoreCards ? (
        <div className={cx('grid gap-3', variant === 'compact' ? 'md:grid-cols-2' : 'md:grid-cols-3')}>
          {model?.roleFitScore != null ? <ScoreCard label="Role fit" value={model.roleFitScore} /> : null}
          {model?.cvFitScore != null ? <ScoreCard label="CV fit" value={model.cvFitScore} /> : null}
          {model?.requirementsFitScore != null ? <ScoreCard label="Requirements fit" value={model.requirementsFitScore} /> : null}
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

      {(requirements.length > 0 || hasCoverage) ? (
        <Panel className="p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className={cx(config.titleSize, 'text-[var(--taali-text)]')}>Requirements fit</div>
            {model?.requirementsFitScore != null ? (
              <Badge variant="purple" className="text-[11px]">
                {formatScale100Score(model.requirementsFitScore, '0-100')}
              </Badge>
            ) : null}
          </div>

          {hasCoverage ? (
            <div className="mt-3">
              <RequirementCoverageStrip coverage={coverage} />
            </div>
          ) : null}

          {requirements.length > 0 ? (
            <div className="mt-3">
              <RequirementList requirements={requirements} limit={config.requirementsLimit} />
            </div>
          ) : null}
        </Panel>
      ) : null}

      {(matchingSkills.length > 0 || missingSkills.length > 0) ? (
        <Panel className="p-4">
          <SkillChipGroups
            matchingSkills={matchingSkills}
            missingSkills={missingSkills}
            limit={config.chipLimit}
          />
        </Panel>
      ) : null}

      {concerns.length > 0 ? (
        <ConcernsCallout concerns={concerns} limit={config.reasonLimit} titleSize={config.titleSize} />
      ) : null}

      {(claimsToVerify.length > 0 || timelineFlags.length > 0) ? (
        <Panel className="border-[var(--taali-warning-border)] bg-[var(--taali-warning-soft)] p-4">
          <div className="flex flex-wrap items-center gap-2">
            <div className={cx(config.titleSize, 'text-[var(--taali-text)]')}>Verify before interview</div>
            <Badge variant="warning" className="text-[11px]">unverified</Badge>
          </div>
          <p className="mt-1 text-xs text-[var(--taali-muted)]">
            Claims the agent could not confirm from the CV. Not held against the score beyond a small flag — confirm in screening.
          </p>
          {claimsToVerify.length > 0 ? (
            <ul className="mt-3 space-y-2">
              {claimsToVerify.map((item, index) => (
                <li key={`claim-${index}`} className="flex gap-2 text-sm text-[var(--taali-text)]">
                  <span className="mt-1 h-1.5 w-1.5 rounded-full bg-[var(--taali-warning)]" />
                  <span>
                    {item.claimType ? (
                      <span className="font-medium text-[var(--taali-text)]">{item.claimType.replace(/_/g, ' ')}: </span>
                    ) : null}
                    {item.claimText}
                    {item.reasoning ? (
                      <span className="text-[var(--taali-muted)]"> — {item.reasoning}</span>
                    ) : null}
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
          {timelineFlags.length > 0 ? (
            <div className="mt-3">
              <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Timeline checks</div>
              <ul className="mt-2 space-y-2">
                {timelineFlags.map((item, index) => (
                  <li key={`timeline-${index}`} className="flex gap-2 text-sm text-[var(--taali-text)]">
                    <span className="mt-1 h-1.5 w-1.5 rounded-full bg-[var(--taali-warning)]" />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </Panel>
      ) : null}
    </div>
  );
}

export default RoleFitEvidenceSections;
