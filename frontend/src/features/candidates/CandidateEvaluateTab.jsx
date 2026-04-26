import React from 'react';

import { useToast } from '../../context/ToastContext';
import {
  Badge,
  Button,
  Card,
  Panel,
  Select,
  Textarea,
} from '../../shared/ui/TaaliPrimitives';

const DECISION_OPTIONS = [
  { value: 'advance', label: 'Advance', description: 'Send to panel' },
  { value: 'hold', label: 'Hold', description: 'Keep in pool' },
  { value: 'reject', label: 'Reject', description: 'Send rejection' },
];

const CONFIDENCE_OPTIONS = [
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
];

const NEXT_STEP_OPTIONS = [
  'Schedule panel',
  'Request references',
  'Add to talent pool',
  'Notify hiring manager',
];

const statusMeta = (status) => {
  const normalized = String(status || '').trim().toLowerCase();
  if (normalized === 'met' || normalized === 'strong') {
    return {
      label: 'Strong',
      toneClass: 'text-[var(--taali-success)]',
      barClass: 'bg-[var(--taali-success)]',
      percent: 92,
    };
  }
  if (normalized === 'partially_met' || normalized === 'partial' || normalized === 'meets') {
    return {
      label: 'Meets',
      toneClass: 'text-[var(--taali-purple)]',
      barClass: 'bg-[var(--taali-purple)]',
      percent: 74,
    };
  }
  if (normalized === 'missing' || normalized === 'not_met') {
    return {
      label: 'Gap',
      toneClass: 'text-[var(--taali-warning)]',
      barClass: 'bg-[var(--taali-warning)]',
      percent: 38,
    };
  }
  return {
    label: 'Untested',
    toneClass: 'text-[var(--taali-muted)]',
    barClass: 'bg-[var(--taali-border)]',
    percent: 44,
  };
};

export const CandidateEvaluateTab = ({
  candidate,
  evaluationRubric = null,
  assessmentId,
  aiEvalSuggestion,
  onGenerateAiSuggestions,
  aiEvalLoading = false,
  manualEvalScores,
  setManualEvalScores,
  manualEvalStrengths,
  setManualEvalStrengths,
  manualEvalImprovements,
  setManualEvalImprovements,
  manualEvalSummary,
  setManualEvalSummary,
  manualEvalDecision,
  setManualEvalDecision,
  manualEvalRationale,
  setManualEvalRationale,
  manualEvalConfidence,
  setManualEvalConfidence,
  manualEvalNextSteps,
  setManualEvalNextSteps,
  manualEvalSaving,
  setManualEvalSaving,
  toLineList,
  toEvidenceTextareaValue,
  assessmentsApi,
  roleFitCriteria = [],
  recommendation = null,
  recruiterSummary = '',
  actionPanel = null,
  onFinalizeCandidateFeedback = () => {},
  finalizeFeedbackLoading = false,
  candidateFeedbackReady = false,
  candidateFeedbackSentAt = null,
  canFinalizeCandidateFeedback = false,
}) => {
  const { showToast } = useToast();
  const assessment = candidate?._raw || {};
  const rubric = evaluationRubric || assessment.evaluation_rubric || {};
  const categories = Object.entries(rubric).filter(([, value]) => value && typeof value === 'object');
  const prompts = assessment.ai_prompts || [];
  const normalizedStatus = String(assessment.status || candidate?.status || '').toLowerCase();
  const promptEmptyMessage = (() => {
    if (normalizedStatus.includes('progress')) return 'Assessment in progress — prompt evidence will appear as activity is captured.';
    if (normalizedStatus.includes('complete') || normalizedStatus.includes('timeout')) {
      return 'Scoring and transcript processing are still running. Refresh shortly for prompt evidence.';
    }
    if (normalizedStatus.includes('expire') || normalizedStatus.includes('abandon')) {
      return 'This assessment was not completed, so no prompt evidence is available.';
    }
    return 'No prompt evidence is available for this assessment yet.';
  })();

  const toggleNextStep = (step) => {
    setManualEvalNextSteps((previous) => {
      const next = Array.isArray(previous) ? previous : [];
      return next.includes(step)
        ? next.filter((item) => item !== step)
        : [...next, step];
    });
  };

  const handleSaveManualEval = async () => {
    if (!assessmentId) return;
    const payloadScores = {};
    for (const [key, value] of Object.entries(manualEvalScores || {})) {
      const score = String(value?.score || '').trim().toLowerCase();
      const evidenceList = toLineList(value?.evidence);
      if (score && evidenceList.length === 0) {
        showToast(`Evidence is required for "${String(key).replace(/_/g, ' ')}".`, 'info');
        return;
      }
      payloadScores[key] = { score: score || null, evidence: evidenceList };
    }

    setManualEvalSaving(true);
    try {
      const res = await assessmentsApi.updateManualEvaluation(assessmentId, {
        decision: manualEvalDecision || null,
        rationale: String(manualEvalRationale || '').trim() || null,
        confidence: manualEvalConfidence || null,
        next_steps: Array.isArray(manualEvalNextSteps) ? manualEvalNextSteps : [],
        category_scores: payloadScores,
        strengths: toLineList(manualEvalStrengths),
        improvements: toLineList(manualEvalImprovements),
      });
      const saved = res.data?.evaluation_result || res.data?.manual_evaluation;
      if (saved && typeof saved === 'object') {
        const normalized = {};
        Object.entries(saved.category_scores || {}).forEach(([key, value]) => {
          const item = value && typeof value === 'object' ? value : {};
          normalized[key] = {
            score: item.score || '',
            evidence: toEvidenceTextareaValue(item.evidence),
          };
        });
        setManualEvalScores(normalized);
        setManualEvalDecision(saved.decision || '');
        setManualEvalRationale(saved.rationale || '');
        setManualEvalConfidence(saved.confidence || '');
        setManualEvalNextSteps(Array.isArray(saved.next_steps) ? saved.next_steps : []);
        setManualEvalStrengths(Array.isArray(saved.strengths) ? saved.strengths.join('\n') : '');
        setManualEvalImprovements(Array.isArray(saved.improvements) ? saved.improvements.join('\n') : '');
        setManualEvalSummary(saved);
      }
      showToast('Manual evaluation saved.', 'success');
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to save', 'error');
    } finally {
      setManualEvalSaving(false);
    }
  };

  return (
    <div className="space-y-6">
      {import.meta.env.VITE_AI_ASSISTED_EVAL_ENABLED === 'true' ? (
        <Panel className="border-[var(--taali-purple)] bg-[var(--taali-purple-soft)] p-4">
          <div className="mb-3 flex items-center justify-between gap-3">
            <div className="font-mono text-xs font-bold text-[var(--taali-text)]">AI suggestion (Claude)</div>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={aiEvalLoading}
              onClick={onGenerateAiSuggestions}
            >
              {aiEvalLoading ? 'Generating...' : 'Generate AI Evaluation'}
            </Button>
          </div>
          {aiEvalSuggestion ? (
            <div className="space-y-1 text-sm text-[var(--taali-text)]">
              {aiEvalSuggestion.overall_score != null ? (
                <div className="font-mono text-xs">Suggested score: {aiEvalSuggestion.overall_score}/10</div>
              ) : null}
              {aiEvalSuggestion.verdict ? (
                <div className="font-mono text-xs">Suggested verdict: {aiEvalSuggestion.verdict}</div>
              ) : null}
              <div className="text-xs text-[var(--taali-muted)]">{aiEvalSuggestion.message || 'Suggestion generated. Review before finalizing.'}</div>
            </div>
          ) : (
            <div className="text-xs text-[var(--taali-muted)]">Generate a suggestion to get Claude&apos;s structured second opinion.</div>
          )}
        </Panel>
      ) : null}

      {manualEvalSummary ? (
        <Card className="bg-[var(--taali-surface-subtle)] p-3">
          <div className="font-mono text-xs text-gray-600">
            Manual overall score:{' '}
            <span className="font-bold text-[var(--taali-text)]">
              {manualEvalSummary.overall_score != null ? `${manualEvalSummary.overall_score}/10` : '—'}
            </span>
            {manualEvalSummary.completed_due_to_timeout ? (
              <span className="ml-3 text-amber-700">Assessment auto-submitted on timeout.</span>
            ) : null}
          </div>
        </Card>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.45fr)_340px]">
        <Panel className="bg-[var(--taali-surface-muted)] p-5">
          <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Your evaluation</div>
          <div className="mt-2 text-xl font-semibold text-[var(--taali-text)]">Record your decision.</div>
          <p className="mt-2 text-sm leading-6 text-[var(--taali-muted)]">
            This recruiter evaluation stays attached to the candidate report and becomes the internal source of truth.
          </p>

          <div className="mt-5 grid gap-3 md:grid-cols-3">
            {DECISION_OPTIONS.map((option) => {
              const active = manualEvalDecision === option.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  className={`rounded-[var(--taali-radius-card)] border px-4 py-4 text-left transition ${
                    active
                      ? 'border-[var(--taali-purple)] bg-[var(--taali-purple-soft)] text-[var(--taali-purple)]'
                      : 'border-[var(--taali-border)] bg-[var(--taali-surface)] text-[var(--taali-text)]'
                  }`}
                  onClick={() => setManualEvalDecision(option.value)}
                >
                  <div className="font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--taali-muted)]">
                    {active ? 'Selected' : 'Decision'}
                  </div>
                  <div className="mt-2 text-lg font-semibold">{option.label}</div>
                  <div className="mt-1 text-xs text-[var(--taali-muted)]">{option.description}</div>
                </button>
              );
            })}
          </div>

          <div className="mt-5">
            <label className="mb-2 block font-mono text-[10.5px] uppercase tracking-[0.1em] text-[var(--taali-muted)]">
              Your rationale
            </label>
            <Textarea
              className="min-h-[120px] text-sm"
              placeholder="Why are you advancing, holding, or rejecting this candidate?"
              value={manualEvalRationale}
              onChange={(event) => setManualEvalRationale(event.target.value)}
            />
          </div>

          <div className="mt-5">
            <div className="mb-2 font-mono text-[10.5px] uppercase tracking-[0.1em] text-[var(--taali-muted)]">
              Confidence
            </div>
            <div className="flex flex-wrap gap-2">
              {CONFIDENCE_OPTIONS.map((option) => {
                const active = manualEvalConfidence === option.value;
                return (
                  <button
                    key={option.value}
                    type="button"
                    className={`rounded-full border px-3 py-2 text-sm transition ${
                      active
                        ? 'border-[var(--taali-purple)] bg-[var(--taali-purple)] text-white'
                        : 'border-[var(--taali-border)] bg-[var(--taali-surface)] text-[var(--taali-text)]'
                    }`}
                    onClick={() => setManualEvalConfidence(option.value)}
                  >
                    {option.label}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="mt-5">
            <div className="mb-2 font-mono text-[10.5px] uppercase tracking-[0.1em] text-[var(--taali-muted)]">
              Next steps
            </div>
            <div className="grid gap-2 md:grid-cols-2">
              {NEXT_STEP_OPTIONS.map((option) => {
                const active = Array.isArray(manualEvalNextSteps) && manualEvalNextSteps.includes(option);
                return (
                  <label
                    key={option}
                    className={`flex cursor-pointer items-center gap-2 rounded-full border px-3 py-2 text-sm transition ${
                      active
                        ? 'border-[var(--taali-purple)] bg-[var(--taali-purple-soft)] text-[var(--taali-purple)]'
                        : 'border-[var(--taali-border)] bg-[var(--taali-surface)] text-[var(--taali-text)]'
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={active}
                      onChange={() => toggleNextStep(option)}
                      className="h-4 w-4"
                    />
                    <span>{option}</span>
                  </label>
                );
              })}
            </div>
          </div>

          <div className="mt-5 flex flex-wrap justify-end gap-2">
            <Button
              type="button"
              variant="secondary"
              onClick={handleSaveManualEval}
              disabled={manualEvalSaving}
            >
              {manualEvalSaving ? 'Saving...' : 'Save draft'}
            </Button>
            <Button
              type="button"
              variant="primary"
              onClick={handleSaveManualEval}
              disabled={manualEvalSaving}
            >
              {manualEvalSaving ? 'Saving...' : 'Submit evaluation'}
            </Button>
          </div>
        </Panel>

        <Panel className="p-5">
          <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Role criteria</div>
          <div className="mt-2 text-xl font-semibold text-[var(--taali-text)]">Rate how the candidate meets each must-have.</div>
          <p className="mt-2 text-sm leading-6 text-[var(--taali-muted)]">
            TAALI keeps the underlying role-fit evidence beside the recruiter decision.
          </p>

          <div className="mt-5 space-y-4">
            {roleFitCriteria.length ? roleFitCriteria.map((criterion, index) => {
              const meta = statusMeta(criterion?.status);
              return (
                <div key={`${criterion?.requirement || 'criterion'}-${index}`}>
                  <div className="flex items-center justify-between gap-3">
                    <div className="text-sm font-semibold text-[var(--taali-text)]">
                      {criterion?.requirement || `Criterion ${index + 1}`}
                    </div>
                    <div className={`text-xs font-semibold ${meta.toneClass}`}>{meta.label}</div>
                  </div>
                  <div className="mt-2 h-2 rounded-full bg-[var(--taali-border)]/70">
                    <div
                      className={`h-2 rounded-full ${meta.barClass}`}
                      style={{ width: `${meta.percent}%` }}
                    />
                  </div>
                  {criterion?.evidence ? (
                    <p className="mt-2 text-xs leading-5 text-[var(--taali-muted)]">
                      Evidence: {criterion.evidence}
                    </p>
                  ) : null}
                </div>
              );
            }) : (
              <p className="text-sm text-[var(--taali-muted)]">
                No role criteria were attached to this candidate yet.
              </p>
            )}
          </div>

          {(recommendation?.label || recruiterSummary) ? (
            <div className="mt-5 rounded-[var(--taali-radius-card)] border border-[var(--taali-purple)]/25 bg-[var(--taali-purple-soft)] p-4">
              <div className="font-mono text-[10.5px] uppercase tracking-[0.08em] text-[var(--taali-purple)]">
                Taali recommends
              </div>
              <div className="mt-2 text-sm font-semibold text-[var(--taali-text)]">
                {recommendation?.label || 'Review evidence'}
              </div>
              {recruiterSummary ? (
                <p className="mt-2 text-sm leading-6 text-[var(--taali-text)]">{recruiterSummary}</p>
              ) : null}
            </div>
          ) : null}

          {actionPanel ? <div className="mt-5">{actionPanel}</div> : null}
        </Panel>
      </div>

      <Panel className="bg-[var(--taali-surface-muted)] p-4">
        <div className="mb-2 font-mono text-xs font-bold text-gray-600">Manual rubric evaluation (excellent / good / poor). Add evidence per category.</div>

        {categories.length === 0 ? (
          <p className="font-mono text-sm text-gray-500">No evaluation rubric for this task. Rubric comes from the task definition.</p>
        ) : (
          <>
            {categories.map(([key, config]) => {
              const weight = config.weight != null ? Math.round(Number(config.weight) * 100) : 0;
              const current = manualEvalScores[key] || {};
              const criteria = (config.criteria && typeof config.criteria === 'object')
                ? config.criteria
                : {
                    excellent: config.excellent,
                    good: config.good,
                    poor: config.poor,
                  };
              const hasCriteria = Boolean(criteria.excellent || criteria.good || criteria.poor);
              return (
                <Card key={key} className="mb-3 bg-[var(--taali-surface)] p-3 last:mb-0">
                  <div className="mb-2 flex items-center justify-between">
                    <span className="font-mono text-sm font-bold capitalize">{String(key).replace(/_/g, ' ')}</span>
                    <Badge variant="muted" className="font-mono text-[11px]">{weight}%</Badge>
                  </div>
                  {hasCriteria ? (
                    <div className="mb-2 grid gap-1 border border-[var(--taali-border)] bg-[var(--taali-bg)] p-2">
                      <div className="font-mono text-[11px] text-[var(--taali-muted)]">
                        <span className="font-bold text-[var(--taali-success)]">Excellent:</span> {criteria.excellent || '—'}
                      </div>
                      <div className="font-mono text-[11px] text-[var(--taali-muted)]">
                        <span className="font-bold text-[var(--taali-info)]">Good:</span> {criteria.good || '—'}
                      </div>
                      <div className="font-mono text-[11px] text-[var(--taali-muted)]">
                        <span className="font-bold text-[var(--taali-warning)]">Poor:</span> {criteria.poor || '—'}
                      </div>
                    </div>
                  ) : null}
                  <div className="grid grid-cols-1 gap-2">
                    <Select
                      value={current.score || ''}
                      onChange={(event) => setManualEvalScores((previous) => ({
                        ...previous,
                        [key]: { ...previous[key], score: event.target.value },
                      }))}
                      className="font-mono text-sm"
                    >
                      <option value="">—</option>
                      <option value="excellent">Excellent</option>
                      <option value="good">Good</option>
                      <option value="poor">Poor</option>
                    </Select>
                    <Textarea
                      className="min-h-[70px] font-mono text-xs"
                      placeholder="Evidence (required for this category)"
                      value={current.evidence ?? ''}
                      onChange={(event) => setManualEvalScores((previous) => ({
                        ...previous,
                        [key]: { ...previous[key], evidence: event.target.value },
                      }))}
                    />
                  </div>
                </Card>
              );
            })}
          </>
        )}
      </Panel>

      {canFinalizeCandidateFeedback ? (
        <Panel className="border-[var(--taali-purple)] bg-[var(--taali-purple-soft)] p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="font-mono text-xs font-bold text-[var(--taali-text)]">Candidate Feedback Report</div>
              <div className="mt-1 text-xs text-[var(--taali-muted)]">
                {candidateFeedbackReady
                  ? `Candidate feedback is finalized${candidateFeedbackSentAt ? ` and email sent on ${new Date(candidateFeedbackSentAt).toLocaleString()}` : ''}.`
                  : 'Finalize review to generate and email the candidate report.'}
              </div>
            </div>
            <Button
              type="button"
              variant="secondary"
              onClick={() => onFinalizeCandidateFeedback({
                forceRegenerate: candidateFeedbackReady,
                resendEmail: candidateFeedbackReady,
              })}
              disabled={finalizeFeedbackLoading}
            >
              {finalizeFeedbackLoading
                ? 'Finalizing...'
                : (candidateFeedbackReady ? 'Regenerate + resend' : 'Finalize + send report')}
            </Button>
          </div>
        </Panel>
      ) : null}

      <Panel className="bg-[var(--taali-surface-muted)] p-4">
        <div className="mb-2 font-mono text-xs font-bold text-gray-600">Summary notes</div>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <div>
            <div className="mb-1 font-mono text-xs text-gray-500">Strengths (one per line)</div>
            <Textarea
              className="min-h-[90px] font-mono text-xs"
              placeholder="Strong debugging discipline"
              value={manualEvalStrengths}
              onChange={(event) => setManualEvalStrengths(event.target.value)}
            />
          </div>
          <div>
            <div className="mb-1 font-mono text-xs text-gray-500">Improvements (one per line)</div>
            <Textarea
              className="min-h-[90px] font-mono text-xs"
              placeholder="Add stronger edge-case tests"
              value={manualEvalImprovements}
              onChange={(event) => setManualEvalImprovements(event.target.value)}
            />
          </div>
        </div>
      </Panel>

      <Panel className="p-4">
        <div className="mb-2 font-mono text-xs font-bold text-gray-600">Chat log (for evidence)</div>
        {prompts.length === 0 ? (
          <p className="font-mono text-sm text-gray-500">{promptEmptyMessage}</p>
        ) : (
          <div className="max-h-64 space-y-2 overflow-y-auto">
            {prompts.map((prompt, index) => (
              <Card key={index} className="bg-[var(--taali-surface)] p-2">
                <div className="mb-1 font-mono text-xs text-gray-600">Prompt {index + 1}</div>
                <div className="font-mono text-xs text-gray-800">
                  {(typeof prompt.message === 'string'
                    ? prompt.message
                    : (prompt.message?.content ?? JSON.stringify(prompt.message)) || '').slice(0, 200)}
                  ...
                </div>
                {prompt.response ? (
                  <div className="mt-1 font-mono text-xs text-gray-500">
                    Response: {(typeof prompt.response === 'string' ? prompt.response : '').slice(0, 150)}...
                  </div>
                ) : null}
              </Card>
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
};
