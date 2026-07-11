import React from 'react';

import {
  Badge,
  Card,
  Panel,
  Select,
  Textarea,
} from '../../shared/ui/TaaliPrimitives';
import { DecisionRecorder } from './DecisionRecorder';

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
  manualEvalScores,
  setManualEvalScores,
  manualEvalStrengths,
  setManualEvalStrengths,
  manualEvalImprovements,
  setManualEvalImprovements,
  manualEvalSummary,
  manualEvalDecision,
  setManualEvalDecision,
  manualEvalRationale,
  setManualEvalRationale,
  manualEvalConfidence,
  setManualEvalConfidence,
  manualEvalNextSteps,
  setManualEvalNextSteps,
  // Lifecycle bundle from the owner: { persisted, dirty, saving, savingMode,
  // conflict, onReload, onSaveDraft, onSubmit }. The owner runs the PATCH (it
  // holds assessmentsApi + assessmentId) so this tab stays presentational.
  decisionState = {},
  roleFitCriteria = [],
  recommendation = null,
  recruiterSummary = '',
  actionPanel = null,
  // When this rubric is hosted inside the Assessment tab (`hideDecision`),
  // everything that duplicates another surface is dropped: the DecisionRecorder
  // (decision lives in the DecisionRail), the role-criteria panel + "Taali
  // recommends" (the Requirements tab and Overview verdict), and the chat log
  // (the Prompts evidence panel). What remains is the recruiter's own input:
  // the manual excellent/good/poor rubric and the strengths/improvements notes.
  hideDecision = false,
}) => {
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

  return (
    <div className="space-y-6">
      {manualEvalSummary ? (
        <Card className="bg-[var(--taali-surface-subtle)] p-3">
          <div className="font-mono text-xs text-gray-600">
            Manual overall score:{' '}
            <span className="font-bold text-[var(--taali-text)]">
              {manualEvalSummary.overall_score != null ? `${manualEvalSummary.overall_score}/100` : '—'}
            </span>
            {manualEvalSummary.completed_due_to_timeout ? (
              <span className="ml-3 text-amber-700">Assessment auto-submitted on timeout.</span>
            ) : null}
          </div>
        </Card>
      ) : null}

      <div className={hideDecision ? '' : 'grid gap-4 xl:grid-cols-[minmax(0,1.45fr)_340px]'}>
        {hideDecision ? null : (
          <DecisionRecorder
            decision={manualEvalDecision}
            onDecisionChange={setManualEvalDecision}
            rationale={manualEvalRationale}
            onRationaleChange={setManualEvalRationale}
            confidence={manualEvalConfidence}
            onConfidenceChange={setManualEvalConfidence}
            nextSteps={manualEvalNextSteps}
            onToggleNextStep={toggleNextStep}
            persisted={decisionState.persisted}
            dirty={decisionState.dirty}
            saving={decisionState.saving}
            savingMode={decisionState.savingMode}
            conflict={decisionState.conflict}
            onReload={decisionState.onReload}
            onSaveDraft={decisionState.onSaveDraft}
            onSubmit={decisionState.onSubmit}
          />
        )}

        {hideDecision ? null : (
        <Panel className="p-5">
          <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Role criteria</div>
          <div className="mt-2 text-xl font-semibold text-[var(--taali-text)]">Rate how the candidate meets each must-have.</div>
          <p className="mt-2 text-sm leading-6 text-[var(--taali-muted)]">
            Taali keeps the underlying role-fit evidence beside the recruiter decision.
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
                  <div className="mt-2 h-2 rounded-full bg-[var(--taali-border-soft)]">
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
            <div className="mt-5 rounded-[var(--taali-radius-card)] border border-[color-mix(in_srgb,var(--taali-purple)_25%,transparent)] bg-[var(--taali-purple-soft)] p-4">
              <div className="font-mono text-[0.65625rem] uppercase tracking-[0.08em] text-[var(--taali-purple)]">
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
        )}
      </div>

      <Panel className="bg-[var(--taali-surface-muted)] p-4">
        <div className="mb-2 font-mono text-xs font-bold text-gray-600">Manual rubric evaluation (excellent / good / poor). Add evidence per category.</div>

        {categories.length === 0 ? (
          <p className="font-mono text-sm text-gray-500">This task doesn&apos;t have an evaluation rubric yet. Once one is added to the task, you can grade the assessment here.</p>
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
                    <Badge variant="muted" className="font-mono text-[0.6875rem]">{weight}%</Badge>
                  </div>
                  {hasCriteria ? (
                    <div className="mb-2 grid gap-1 border border-[var(--taali-border)] bg-[var(--taali-bg)] p-2">
                      <div className="font-mono text-[0.6875rem] text-[var(--taali-muted)]">
                        <span className="font-bold text-[var(--taali-success)]">Excellent:</span> {criteria.excellent || '—'}
                      </div>
                      <div className="font-mono text-[0.6875rem] text-[var(--taali-muted)]">
                        <span className="font-bold text-[var(--taali-info)]">Good:</span> {criteria.good || '—'}
                      </div>
                      <div className="font-mono text-[0.6875rem] text-[var(--taali-muted)]">
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
                      className="min-h-[4.375rem] font-mono text-xs"
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

      <Panel className="bg-[var(--taali-surface-muted)] p-4">
        <div className="mb-2 font-mono text-xs font-bold text-gray-600">Summary notes</div>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <div>
            <div className="mb-1 font-mono text-xs text-gray-500">Strengths (one per line)</div>
            <Textarea
              className="min-h-[5.625rem] font-mono text-xs"
              placeholder="Strong debugging discipline"
              value={manualEvalStrengths}
              onChange={(event) => setManualEvalStrengths(event.target.value)}
            />
          </div>
          <div>
            <div className="mb-1 font-mono text-xs text-gray-500">Improvements (one per line)</div>
            <Textarea
              className="min-h-[5.625rem] font-mono text-xs"
              placeholder="Add stronger edge-case tests"
              value={manualEvalImprovements}
              onChange={(event) => setManualEvalImprovements(event.target.value)}
            />
          </div>
        </div>
      </Panel>

      {hideDecision ? null : (
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
      )}
    </div>
  );
};
