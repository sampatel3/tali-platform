import React, { useEffect, useMemo, useState } from 'react';

import { Badge, Button, Panel, Select, Sheet, Spinner, Textarea } from '../../shared/ui/TaaliPrimitives';
import { ComparisonRadar } from '../../shared/ui/ComparisonRadar';
import { getDimensionById } from '../../scoring/scoringDimensions';
import { CandidateScoreRing } from './CandidateScoreRing';
import { CandidateSidebarHeader } from './CandidateSidebarHeader';
import {
  buildApplicationStatusMeta,
  formatCvScore100,
  formatDateTime,
} from './candidatesUiUtils';

const COMPLETED_ASSESSMENT_STATUSES = new Set(['completed', 'completed_due_to_timeout']);

const modeMeta = (mode) => {
  if (mode === 'assessment_plus_cv') return { label: 'Assessment + CV', variant: 'purple' };
  if (mode === 'assessment_only_fallback') return { label: 'Assessment only', variant: 'warning' };
  if (mode === 'pending') return { label: 'Pending', variant: 'muted' };
  return { label: 'CV fit only', variant: 'muted' };
};

const formatScore = (value, { includeScale = true, empty = '—' } = {}) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return empty;
  const rounded = Math.round(numeric * 10) / 10;
  const display = Number.isInteger(rounded) ? rounded.toFixed(0) : rounded.toFixed(1);
  return includeScale ? `${display}/100` : display;
};

const compactText = (value, maxChars = 180) => {
  const cleaned = String(value || '').replace(/\s+/g, ' ').trim();
  if (!cleaned) return '';
  if (cleaned.length <= maxChars) return cleaned;
  return `${cleaned.slice(0, maxChars - 1).trimEnd()}…`;
};

const buildScoreWhySections = (app) => {
  const details = (app?.cv_match_details && typeof app.cv_match_details === 'object')
    ? app.cv_match_details
    : {};
  const normalizeList = (value, maxItems = 4) => (
    Array.isArray(value)
      ? value
        .map((item) => String(item || '').trim())
        .filter(Boolean)
        .slice(0, maxItems)
      : []
  );
  const toReason = (text) => {
    const cleaned = String(text || '').trim();
    if (!cleaned) return null;
    return cleaned.endsWith('.') ? cleaned : `${cleaned}.`;
  };

  const cvReasons = [];
  const requirementsReasons = [];

  const matchingSkills = normalizeList(details.matching_skills, 4);
  const experienceHighlights = normalizeList(details.experience_highlights, 2);
  const missingSkills = normalizeList(details.missing_skills, 4);
  const concerns = normalizeList(details.concerns, 2);

  if (matchingSkills.length > 0) {
    cvReasons.push(toReason(`Strong skill alignment: ${matchingSkills.join(', ')}`));
  }
  if (experienceHighlights.length > 0) {
    cvReasons.push(toReason(`Relevant experience evidence: ${experienceHighlights.join('; ')}`));
  }
  if (missingSkills.length > 0) {
    cvReasons.push(toReason(`Gaps vs role spec: ${missingSkills.join(', ')}`));
  }
  if (concerns.length > 0) {
    cvReasons.push(toReason(`Risk signals from CV: ${concerns.join('; ')}`));
  }

  const requirementsCoverage = (details.requirements_coverage && typeof details.requirements_coverage === 'object')
    ? details.requirements_coverage
    : {};
  const requirementsAssessment = Array.isArray(details.requirements_assessment)
    ? details.requirements_assessment
    : [];

  if (typeof details.requirements_match_score_100 === 'number') {
    requirementsReasons.push(
      toReason(`Additional requirements fit score: ${formatCvScore100(details.requirements_match_score_100, details)}`)
    );
  }

  const statusRank = (status) => {
    if (status === 'met') return 0;
    if (status === 'partially_met') return 1;
    if (status === 'missing') return 2;
    return 3;
  };
  const priorityRank = (priority) => {
    if (priority === 'must_have') return 0;
    if (priority === 'constraint') return 1;
    if (priority === 'strong_preference') return 2;
    return 3;
  };

  const requirementEvidenceReasons = requirementsAssessment
    .map((item) => {
      const requirement = compactText(item?.requirement, 150);
      if (!requirement) return null;
      const status = String(item?.status || 'unknown').toLowerCase();
      const priority = String(item?.priority || 'nice_to_have').toLowerCase();
      const evidence = compactText(item?.evidence, 180);
      const impact = compactText(item?.impact, 180);
      const whyText = evidence || impact;
      let prefix = 'Unclear evidence';
      if (status === 'met') prefix = 'Met';
      else if (status === 'partially_met') prefix = 'Partially met';
      else if (status === 'missing') prefix = 'Missing';

      let sentence = `${prefix}: ${requirement}`;
      if (whyText) {
        sentence += ` because ${whyText}`;
      } else if (status === 'missing') {
        sentence += ' because no clear CV evidence was found';
      }

      return {
        text: toReason(sentence),
        statusRank: statusRank(status),
        priorityRank: priorityRank(priority),
      };
    })
    .filter(Boolean)
    .sort((a, b) => (
      (a.statusRank - b.statusRank)
      || (a.priorityRank - b.priorityRank)
    ))
    .slice(0, 3)
    .map((item) => item.text);

  if (requirementEvidenceReasons.length > 0) {
    requirementsReasons.push(...requirementEvidenceReasons);
  }

  const totalReq = Number(requirementsCoverage.total || 0);
  if (totalReq > 0 && requirementsReasons.length < 4 && requirementEvidenceReasons.length === 0) {
    requirementsReasons.push(
      toReason(
        `Coverage: ${requirementsCoverage.met ?? 0}/${totalReq} met, ${requirementsCoverage.partially_met ?? 0} partial, ${requirementsCoverage.missing ?? 0} missing`
      )
    );
  }

  const modelRationale = normalizeList(details.score_rationale_bullets, 6);
  const isGenericRequirementRationale = (text) => {
    const lower = String(text || '').toLowerCase();
    return (
      lower.includes('matched recruiter requirements')
      || lower.includes('recruiter requirements coverage')
      || lower.startsWith('coverage:')
    );
  };
  modelRationale.forEach((bullet) => {
    const lower = bullet.toLowerCase();
    if (lower.includes('requirement') && requirementsReasons.length < 4) {
      if (requirementEvidenceReasons.length > 0 && isGenericRequirementRationale(bullet)) return;
      requirementsReasons.push(toReason(bullet));
    } else if (cvReasons.length < 3) {
      cvReasons.push(toReason(bullet));
    }
  });

  const dedupe = (items, maxItems = 4) => {
    const seen = new Set();
    const out = [];
    for (const item of items) {
      const text = String(item || '').trim();
      if (!text) continue;
      const key = text.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(text);
      if (out.length >= maxItems) break;
    }
    return out;
  };

  return {
    cvFit: dedupe(cvReasons, 3),
    additionalRequirementsFit: dedupe(requirementsReasons, 4),
  };
};

const InfoCard = ({ label, value }) => (
  <div className="border border-[var(--taali-border-muted)] bg-[var(--taali-surface-subtle)] px-3 py-3">
    <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">{label}</p>
    <p className="mt-2 text-sm font-semibold text-[var(--taali-text)]">{value}</p>
  </div>
);

const BreakdownCard = ({ label, value, badge = null, children }) => (
  <Panel className="overflow-hidden p-0">
    <div className="border-b border-[var(--taali-border-muted)] bg-[var(--taali-surface-subtle)] px-4 py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">{label}</p>
          <p className="mt-2 font-mono text-3xl font-bold text-[var(--taali-text)]">{value}</p>
        </div>
        {badge ? <Badge variant={badge.variant}>{badge.label}</Badge> : null}
      </div>
    </div>
    <div className="space-y-4 px-4 py-4">{children}</div>
  </Panel>
);

const ReasonList = ({ title, items }) => {
  if (!Array.isArray(items) || items.length === 0) return null;
  return (
    <div>
      <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">{title}</p>
      <ul className="mt-2 space-y-2">
        {items.map((item, index) => (
          <li key={`${title}-${index}`} className="flex gap-2 text-sm text-[var(--taali-text)]">
            <span className="text-[var(--taali-success)]">•</span>
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
};

const toDimensionLabel = (dimensionId) => {
  const dimension = getDimensionById(dimensionId);
  return dimension?.label || compactText(dimensionId, 40) || '—';
};

const toAssessmentStatusText = (status) => {
  const cleaned = String(status || '').trim();
  if (!cleaned) return 'not started';
  return cleaned.replace(/_/g, ' ');
};

export function CandidateScoreSummarySheet({
  open,
  loading,
  application,
  roleTasks,
  creatingAssessmentId,
  onClose,
  onLaunchAssessment,
  onOpenCvSidebar,
  onViewResults,
}) {
  const [selectedTask, setSelectedTask] = useState('');
  const [retakeReason, setRetakeReason] = useState('');

  useEffect(() => {
    if (!open) return;
    if (roleTasks.length === 1) {
      setSelectedTask(String(roleTasks[0].id));
    } else {
      setSelectedTask('');
    }
    setRetakeReason('');
  }, [open, roleTasks]);

  const scoreSummary = application?.score_summary || {};
  const assessmentPreview = application?.assessment_preview || null;
  const assessmentHistory = Array.isArray(application?.assessment_history) ? application.assessment_history : [];
  const scoreWhy = buildScoreWhySections(application);
  const mode = modeMeta(scoreSummary.mode);
  const hasCompletedAssessment = COMPLETED_ASSESSMENT_STATUSES.has(String(scoreSummary.assessment_status || '').toLowerCase())
    && Boolean(scoreSummary.assessment_id);
  const hasValidAssessment = Boolean(application?.valid_assessment_id);
  const hasCv = Boolean(application?.cv_filename || application?.cv_text);
  const statusMeta = buildApplicationStatusMeta(application?.status, application?.workable_stage);

  const currentAssessmentPreview = useMemo(() => {
    if (!assessmentPreview?.assessment_id) return [];
    return [
      {
        id: assessmentPreview.assessment_id,
        name: application?.candidate_name || 'Current assessment',
        _raw: {
          score_breakdown: {
            category_scores: assessmentPreview.category_scores || {},
          },
        },
      },
    ];
  }, [application?.candidate_name, assessmentPreview]);

  const strongestLabel = toDimensionLabel(assessmentPreview?.strongest_dimension);
  const weakestLabel = toDimensionLabel(assessmentPreview?.weakest_dimension);
  const updatedAt = formatDateTime(application?.updated_at || application?.created_at);
  const assessmentValue = hasCompletedAssessment
    ? formatScore(scoreSummary.assessment_score)
    : (hasValidAssessment ? 'In progress' : 'Not started');

  const footer = loading ? (
    <div className="flex items-center gap-2 text-sm text-[var(--taali-muted)]">
      <Spinner size={16} />
      Loading candidate summary...
    </div>
  ) : application ? (
    <div className="space-y-3">
      {roleTasks.length > 0 ? (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <Select
              value={selectedTask}
              onChange={(event) => setSelectedTask(event.target.value)}
              className="min-w-[240px]"
            >
              <option value="">Select task...</option>
              {roleTasks.map((task) => (
                <option key={task.id} value={task.id}>{task.name}</option>
              ))}
            </Select>
            <Button
              type="button"
              variant="primary"
              disabled={!selectedTask || creatingAssessmentId === application.id}
              onClick={() => onLaunchAssessment?.(
                application,
                selectedTask,
                { retake: hasValidAssessment, voidReason: retakeReason }
              )}
            >
              {creatingAssessmentId === application.id
                ? (hasValidAssessment ? 'Creating retake...' : 'Sending...')
                : (hasValidAssessment ? 'Retake assessment' : 'Send assessment')}
            </Button>
          </div>
          {hasValidAssessment ? (
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
                Void reason (optional)
              </p>
              <Textarea
                value={retakeReason}
                onChange={(event) => setRetakeReason(event.target.value)}
                rows={3}
                className="mt-2"
                placeholder="Why is this attempt being replaced?"
              />
            </div>
          ) : null}
        </div>
      ) : (
        <p className="text-sm text-amber-700">Link a task to this role before sending an assessment.</p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {hasCompletedAssessment ? (
          <Button
            type="button"
            variant="secondary"
            onClick={() => onViewResults?.(scoreSummary.assessment_id, application)}
          >
            View full assessment results
          </Button>
        ) : null}
        {hasCv ? (
          <Button type="button" variant="secondary" onClick={() => onOpenCvSidebar?.(application)}>
            View CV
          </Button>
        ) : null}
      </div>
    </div>
  ) : (
    <div className="text-sm text-[var(--taali-muted)]">No candidate selected.</div>
  );

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title={application?.candidate_name || application?.candidate_email || 'Candidate summary'}
      description={application?.role_name || application?.candidate_position || 'Role scoring summary'}
      headerContent={<CandidateSidebarHeader application={application} />}
      footer={footer}
    >
      {loading ? (
        <div className="flex min-h-[240px] items-center justify-center">
          <Spinner size={22} />
        </div>
      ) : !application ? (
        <Panel className="p-4 text-sm text-[var(--taali-muted)]">
          Candidate summary unavailable.
        </Panel>
      ) : (
        <div className="space-y-4">
          <Panel className="overflow-hidden border-2 border-[var(--taali-border)] bg-[linear-gradient(135deg,rgba(190,171,255,0.18),rgba(255,255,255,0.98))] p-5">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-center gap-4">
                <CandidateScoreRing
                  score={scoreSummary.taali_score}
                  details={{ score_scale: '0-100' }}
                  size={112}
                  strokeWidth={10}
                  label={`TAALI Score for ${application?.candidate_name || application?.candidate_email || 'candidate'}`}
                  valueClassName="text-[1.75rem]"
                />
                <div>
                  <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">TAALI Score</p>
                  <p className="mt-2 font-mono text-4xl font-bold text-[var(--taali-text)]">
                    {formatScore(scoreSummary.taali_score)}
                  </p>
                  <p className="mt-2 max-w-[320px] text-sm text-[var(--taali-muted)]">
                    {scoreSummary.formula_label || 'Current recruiter-facing score.'}
                  </p>
                </div>
              </div>
              <div className="space-y-3 sm:text-right">
                <Badge variant={mode.variant}>{mode.label}</Badge>
                <p className="text-xs text-[var(--taali-muted)]">Updated {updatedAt}</p>
              </div>
            </div>
          </Panel>

          <Panel className="p-4">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {statusMeta.map((item) => (
                <InfoCard key={item.label} label={item.label} value={item.value} />
              ))}
              <InfoCard label="Role" value={application.role_name || application.candidate_position || '—'} />
            </div>
          </Panel>

          <BreakdownCard
            label="Assessment score"
            value={assessmentValue}
            badge={hasCompletedAssessment
              ? { label: 'Completed', variant: 'purple' }
              : { label: hasValidAssessment ? 'Active attempt' : 'Awaiting assessment', variant: 'muted' }}
          >
            {hasCompletedAssessment && assessmentPreview ? (
              <>
                <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_240px]">
                  <div className="space-y-4">
                    <div>
                      <p className="text-sm leading-6 text-[var(--taali-text)]">
                        {assessmentPreview.heuristic_summary || 'Assessment completed. Review the dimension breakdown and strongest areas before moving this candidate forward.'}
                      </p>
                    </div>
                    <div className="grid gap-3 sm:grid-cols-2">
                      <InfoCard label="Strongest dimension" value={strongestLabel} />
                      <InfoCard label="Weakest dimension" value={weakestLabel} />
                    </div>
                    {scoreSummary.mode === 'assessment_only_fallback' ? (
                      <p className="text-sm text-amber-700">
                        CV fit unavailable for this completed attempt; TAALI Score currently reflects assessment only.
                      </p>
                    ) : null}
                    <div className="flex flex-wrap items-center gap-2">
                      <Button
                        type="button"
                        variant="secondary"
                        onClick={() => onViewResults?.(scoreSummary.assessment_id, application)}
                      >
                        View full assessment results
                      </Button>
                    </div>
                  </div>
                  <div className="border border-[var(--taali-border-muted)] bg-[var(--taali-surface-subtle)] p-3">
                    <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Assessment chart</p>
                    <ComparisonRadar
                      assessments={currentAssessmentPreview}
                      highlightAssessmentId={assessmentPreview.assessment_id}
                      showLegend={false}
                      height={220}
                      className="-mx-1"
                    />
                  </div>
                </div>
              </>
            ) : (
              <div className="space-y-3">
                <p className="text-sm leading-6 text-[var(--taali-text)]">
                  {hasValidAssessment
                    ? `Current assessment is ${toAssessmentStatusText(scoreSummary.assessment_status)}. Until it completes, the TAALI Score continues to reflect CV fit.`
                    : 'No assessment has been completed for this role yet. The current TAALI Score is driven by CV fit until a recruiter sends and the candidate completes one assessment.'}
                </p>
                {roleTasks.length === 0 ? (
                  <p className="text-sm text-amber-700">Link a task to this role to enable assessment sending.</p>
                ) : null}
              </div>
            )}
          </BreakdownCard>

          <BreakdownCard label="CV fit" value={formatScore(scoreSummary.cv_fit_score)}>
            <ReasonList title="CV evidence" items={scoreWhy.cvFit} />
            {scoreWhy.cvFit.length === 0 ? (
              <p className="text-sm text-[var(--taali-muted)]">
                CV-fit rationale is not available yet. Upload a CV or regenerate scoring to populate this summary.
              </p>
            ) : null}
          </BreakdownCard>

          <BreakdownCard label="Requirements fit" value={formatScore(scoreSummary.requirements_fit_score)}>
            <ReasonList title="Requirements evidence" items={scoreWhy.additionalRequirementsFit} />
            {scoreWhy.additionalRequirementsFit.length === 0 ? (
              <p className="text-sm text-[var(--taali-muted)]">
                Recruiter requirement coverage is not available yet for this role.
              </p>
            ) : null}
          </BreakdownCard>

          <Panel className="p-4">
            <div className="mb-3 flex items-center justify-between gap-2">
              <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Assessment history</p>
              {scoreSummary.has_voided_attempts ? <Badge variant="warning">Includes voided attempts</Badge> : null}
            </div>
            {assessmentHistory.length === 0 ? (
              <p className="text-sm text-[var(--taali-muted)]">No assessment attempts yet for this role.</p>
            ) : (
              <div className="space-y-3">
                {assessmentHistory.map((item) => {
                  const canViewItem = Boolean(item.assessment_id) && (
                    COMPLETED_ASSESSMENT_STATUSES.has(String(item.status || '').toLowerCase()) || Boolean(item.is_voided)
                  );
                  return (
                    <div key={item.assessment_id} className="border border-[var(--taali-border-muted)] bg-[var(--taali-surface-subtle)] px-3 py-3">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="font-semibold text-[var(--taali-text)]">{item.task_name || `Assessment #${item.assessment_id}`}</p>
                            {item.is_voided ? <Badge variant="warning">Voided</Badge> : <Badge variant="muted">Current</Badge>}
                          </div>
                          <p className="mt-1 text-sm text-[var(--taali-muted)]">
                            Status: {toAssessmentStatusText(item.status)}
                            {item.completed_at ? ` • Completed ${formatDateTime(item.completed_at)}` : ''}
                            {!item.completed_at && item.created_at ? ` • Created ${formatDateTime(item.created_at)}` : ''}
                          </p>
                          {item.void_reason ? (
                            <p className="mt-1 text-sm text-amber-700">Void reason: {item.void_reason}</p>
                          ) : null}
                        </div>
                        <div className="space-y-2 text-right">
                          <p className="font-mono text-sm text-[var(--taali-text)]">TAALI {formatScore(item.taali_score)}</p>
                          {canViewItem ? (
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              onClick={() => onViewResults?.(item.assessment_id, application)}
                            >
                              View
                            </Button>
                          ) : null}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </Panel>
        </div>
      )}
    </Sheet>
  );
}
