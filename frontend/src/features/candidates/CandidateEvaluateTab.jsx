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
  manualEvalSaving,
  setManualEvalSaving,
  toLineList,
  toEvidenceTextareaValue,
  assessmentsApi,
  onFinalizeCandidateFeedback = () => {},
  finalizeFeedbackLoading = false,
  candidateFeedbackReady = false,
  candidateFeedbackSentAt = null,
  canFinalizeCandidateFeedback = false,
}) => {
  const { showToast } = useToast();
  const assessment = candidate._raw || {};
  const rubric = evaluationRubric || assessment.evaluation_rubric || {};
  const categories = Object.entries(rubric).filter(([, v]) => v && typeof v === 'object');
  const prompts = assessment.ai_prompts || [];
  const normalizedStatus = String(assessment.status || candidate.status || '').toLowerCase();
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
        category_scores: payloadScores,
        strengths: toLineList(manualEvalStrengths),
        improvements: toLineList(manualEvalImprovements),
      });
      const saved = res.data?.evaluation_result || res.data?.manual_evaluation;
      if (saved?.category_scores) {
        const normalized = {};
        Object.entries(saved.category_scores).forEach(([key, value]) => {
          const item = value && typeof value === 'object' ? value : {};
          normalized[key] = {
            score: item.score || '',
            evidence: toEvidenceTextareaValue(item.evidence),
          };
        });
        setManualEvalScores(normalized);
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
        <Card className="bg-[#faf8ff] p-3">
          <div className="font-mono text-xs text-gray-600">
            Manual overall score:{' '}
            <span className="font-bold text-black">
              {manualEvalSummary.overall_score != null ? `${manualEvalSummary.overall_score}/10` : '—'}
            </span>
            {manualEvalSummary.completed_due_to_timeout ? (
              <span className="ml-3 text-amber-700">Assessment auto-submitted on timeout.</span>
            ) : null}
          </div>
        </Card>
      ) : null}

      <Panel className="bg-[#fcfbff] p-4">
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
                <Card key={key} className="mb-3 bg-white p-3 last:mb-0">
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
                      onChange={(e) => setManualEvalScores((prev) => ({
                        ...prev,
                        [key]: { ...prev[key], score: e.target.value },
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
                      onChange={(e) => setManualEvalScores((prev) => ({
                        ...prev,
                        [key]: { ...prev[key], evidence: e.target.value },
                      }))}
                    />
                  </div>
                </Card>
              );
            })}
            <Button
              type="button"
              variant="primary"
              onClick={handleSaveManualEval}
              disabled={manualEvalSaving}
            >
              {manualEvalSaving ? 'Saving...' : 'Save manual evaluation'}
            </Button>
          </>
        )}
      </Panel>

      {canFinalizeCandidateFeedback ? (
        <Panel className="p-4 bg-[var(--taali-purple-soft)] border-[var(--taali-purple)]">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="font-mono text-xs font-bold text-[var(--taali-text)]">Candidate Feedback Report</div>
              <div className="text-xs text-[var(--taali-muted)] mt-1">
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

      <Panel className="bg-[#fcfbff] p-4">
        <div className="mb-2 font-mono text-xs font-bold text-gray-600">Summary notes</div>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <div>
            <div className="mb-1 font-mono text-xs text-gray-500">Strengths (one per line)</div>
            <Textarea
              className="min-h-[90px] font-mono text-xs"
              placeholder="Strong debugging discipline"
              value={manualEvalStrengths}
              onChange={(e) => setManualEvalStrengths(e.target.value)}
            />
          </div>
          <div>
            <div className="mb-1 font-mono text-xs text-gray-500">Improvements (one per line)</div>
            <Textarea
              className="min-h-[90px] font-mono text-xs"
              placeholder="Add stronger edge-case tests"
              value={manualEvalImprovements}
              onChange={(e) => setManualEvalImprovements(e.target.value)}
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
            {prompts.map((p, i) => (
              <Card key={i} className="bg-white p-2">
                <div className="mb-1 font-mono text-xs text-gray-600">Prompt {i + 1}</div>
                <div className="font-mono text-xs text-gray-800">
                  {(typeof p.message === 'string' ? p.message : (p.message?.content ?? JSON.stringify(p.message)) || '').slice(0, 200)}...
                </div>
                {p.response ? (
                  <div className="mt-1 font-mono text-xs text-gray-500">
                    Response: {(typeof p.response === 'string' ? p.response : '').slice(0, 150)}...
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
