import React from 'react';

import { Badge, Button, Panel, cx } from '../../shared/ui/TaaliPrimitives';
import { CandidateReportView } from './CandidateReportView';

const uniqueItems = (items, limit = 4) => Array.from(
  new Set((Array.isArray(items) ? items : []).filter(Boolean))
).slice(0, limit);

const buildRoleFitStrengths = (roleFitModel) => uniqueItems([
  ...(roleFitModel?.requirementsAssessment || [])
    .filter((item) => item.status === 'met')
    .map((item) => item.requirement),
  ...(roleFitModel?.experienceHighlights || []),
  ...(roleFitModel?.rationaleBullets || []),
], 4);

const buildRoleFitGaps = (roleFitModel) => uniqueItems([
  roleFitModel?.firstRequirementGap?.requirement
    ? `Gap vs recruiter requirement: ${roleFitModel.firstRequirementGap.requirement}`
    : null,
  ...(roleFitModel?.concerns || []),
  ...(roleFitModel?.missingSkills || []).map((skill) => `Skill gap: ${skill}`),
], 4);

const SignalList = ({ title, items, emptyLabel, tone = 'default' }) => (
  <div>
    <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">{title}</div>
    {items.length ? (
      <ul className="mt-3 space-y-2">
        {items.map((item) => (
          <li key={`${title}-${item}`} className="flex gap-2 text-sm text-[var(--taali-text)]">
            <span
              className="mt-1 h-1.5 w-1.5 rounded-full"
              style={{ backgroundColor: tone === 'warning' ? 'var(--taali-warning)' : 'var(--taali-purple)' }}
            />
            <span>{item}</span>
          </li>
        ))}
      </ul>
    ) : (
      <p className="mt-3 text-sm text-[var(--taali-muted)]">{emptyLabel}</p>
    )}
  </div>
);

const SkillChips = ({ title, items, badgeVariant = 'success', emptyLabel }) => (
  <div>
    <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">{title}</div>
    {items.length ? (
      <div className="mt-3 flex flex-wrap gap-2">
        {items.map((item) => (
          <Badge key={`${title}-${item}`} variant={badgeVariant}>{item}</Badge>
        ))}
      </div>
    ) : (
      <p className="mt-3 text-sm text-[var(--taali-muted)]">{emptyLabel}</p>
    )}
  </div>
);

const formatDateTime = (value) => {
  if (!value) return 'Not linked yet';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? 'Not linked yet' : parsed.toLocaleString();
};

const RoleFitSummaryPanel = ({ reportModel }) => {
  const roleFitModel = reportModel?.roleFitModel || {};
  const strengths = buildRoleFitStrengths(roleFitModel);
  const gaps = buildRoleFitGaps(roleFitModel);
  const matchingSkills = uniqueItems(roleFitModel.matchingSkills, 6);
  const missingSkills = uniqueItems(roleFitModel.missingSkills, 6);

  return (
    <Panel className="p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Role fit summary</div>
          <div className="mt-2 text-xl font-semibold text-[var(--taali-text)]">
            {roleFitModel.summaryText || 'Role-fit evidence is attached below.'}
          </div>
        </div>
        {reportModel?.summaryModel?.roleFitScore != null ? (
          <Badge variant="purple" className="font-mono text-[11px]">
            Role fit {reportModel.summaryModel.roleFitScore.toFixed(1)}
          </Badge>
        ) : null}
      </div>

      <div className="mt-5 grid gap-5 lg:grid-cols-2">
        <SignalList
          title="Main strengths"
          items={strengths}
          emptyLabel="No strong role-fit positives have been surfaced yet."
        />
        <SignalList
          title="Main gaps"
          items={gaps}
          emptyLabel="No major role-fit gaps were surfaced."
          tone="warning"
        />
      </div>

      <div className="mt-5 grid gap-5 lg:grid-cols-2">
        <SkillChips
          title="Matching skills"
          items={matchingSkills}
          badgeVariant="success"
          emptyLabel="No matching skills have been extracted yet."
        />
        <SkillChips
          title="Skills gaps"
          items={missingSkills}
          badgeVariant="warning"
          emptyLabel="No explicit skills gaps were extracted."
        />
      </div>
    </Panel>
  );
};

const ProbeSummaryPanel = ({
  reportModel,
  onOpenInterviewGuidance = null,
  showInterviewGuidanceAction = false,
}) => {
  const roleFitModel = reportModel?.roleFitModel || {};
  const summaryModel = reportModel?.summaryModel || {};
  const probeItems = uniqueItems([
    reportModel?.probeDescription,
    roleFitModel?.firstRequirementGap?.impact,
    roleFitModel?.concerns?.[0],
    summaryModel?.weakestLabel && summaryModel.weakestLabel !== '—'
      ? `Assessment signal to validate: ${summaryModel.weakestLabel}`
      : null,
  ], 4);

  return (
    <Panel className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">What to probe</div>
          <div className="mt-2 text-xl font-semibold text-[var(--taali-text)]">
            {reportModel?.probeTitle || 'Interview guidance'}
          </div>
        </div>
        {showInterviewGuidanceAction && typeof onOpenInterviewGuidance === 'function' ? (
          <Button type="button" variant="secondary" size="sm" onClick={onOpenInterviewGuidance}>
            Open interview guidance
          </Button>
        ) : null}
      </div>

      <p className="mt-3 text-sm leading-6 text-[var(--taali-muted)]">
        Use the interview to validate the weakest evidence behind the TAALI score, not to re-run the assessment.
      </p>

      <SignalList
        title="Probe next"
        items={probeItems}
        emptyLabel="No priority probe areas have been generated yet."
      />
    </Panel>
  );
};

const FirefliesCapturePanel = ({ reportModel }) => {
  const firefliesModel = reportModel?.firefliesModel || {};

  if (!firefliesModel.shouldSurface) {
    return null;
  }

  return (
    <Panel className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Fireflies capture</div>
          <div className="mt-2 text-xl font-semibold text-[var(--taali-text)]">{firefliesModel.statusLabel}</div>
        </div>
        <Badge variant={firefliesModel.badgeVariant || 'muted'} className="font-mono text-[11px]">
          {firefliesModel.linked ? 'Linked' : (firefliesModel.captureExpected ? 'Workable flow' : 'Optional')}
        </Badge>
      </div>

      {firefliesModel.description ? (
        <p className="mt-3 text-sm leading-6 text-[var(--taali-muted)]">{firefliesModel.description}</p>
      ) : null}

      <div className="mt-5 grid gap-4 lg:grid-cols-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Invite email</div>
          <div className="mt-2 text-sm text-[var(--taali-text)]">{firefliesModel.inviteEmail || 'Not configured'}</div>
        </div>
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Latest source</div>
          <div className="mt-2 text-sm text-[var(--taali-text)]">{firefliesModel.latestSource || 'No transcript linked yet'}</div>
        </div>
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Latest meeting</div>
          <div className="mt-2 text-sm text-[var(--taali-text)]">{formatDateTime(firefliesModel.latestMeetingDate)}</div>
        </div>
      </div>

      {firefliesModel.latestSummary ? (
        <div className="mt-5 rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface-subtle)] p-4">
          <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Latest transcript summary</div>
          <p className="mt-2 text-sm leading-6 text-[var(--taali-text)]">{firefliesModel.latestSummary}</p>
        </div>
      ) : null}

      {firefliesModel.latestProviderUrl ? (
        <a
          href={firefliesModel.latestProviderUrl}
          target="_blank"
          rel="noreferrer"
          className="mt-4 inline-flex text-sm font-medium text-[var(--taali-purple-hover)] underline-offset-2 hover:underline"
        >
          Open transcript
        </a>
      ) : null}
    </Panel>
  );
};

export const CandidateAssessmentSummaryView = ({
  reportModel,
  variant = 'page',
  showIdentityTitle = true,
  onOpenInterviewGuidance = null,
  showInterviewGuidanceAction = false,
  showSupplementalPanels = true,
  showRoleFitMetric = true,
  radarCategoryKeys = null,
}) => (
  <div className="space-y-4">
    <CandidateReportView
      model={reportModel}
      variant={variant}
      showInsights={false}
      showIdentityTitle={showIdentityTitle}
      showRoleFitSection={false}
      showIntegritySection={false}
      showEvidenceSections={false}
      showRoleFitMetric={showRoleFitMetric}
      radarCategoryKeys={radarCategoryKeys}
    />

    {showSupplementalPanels ? (
      <div
        className={cx(
          'grid gap-4',
          variant === 'sheet'
            ? 'grid-cols-1'
            : (reportModel?.firefliesModel?.shouldSurface ? 'xl:grid-cols-3' : 'xl:grid-cols-2')
        )}
      >
        <RoleFitSummaryPanel reportModel={reportModel} />
        <ProbeSummaryPanel
          reportModel={reportModel}
          onOpenInterviewGuidance={onOpenInterviewGuidance}
          showInterviewGuidanceAction={showInterviewGuidanceAction}
        />
        <FirefliesCapturePanel reportModel={reportModel} />
      </div>
    ) : null}
  </div>
);
