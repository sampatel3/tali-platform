import React from 'react';
import { AlertTriangle, CheckCircle } from 'lucide-react';
import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
} from 'recharts';

import { dimensionOrder, getDimensionById, toCanonicalId } from '../../scoring/scoringDimensions';
import {
  Badge,
  Button,
  Card,
  Panel,
} from '../../shared/ui/TaaliPrimitives';
import { ScoringCardGrid } from '../../shared/ui/ScoringCardGrid';
import { ScoringGlossaryPanel, SCORING_GLOSSARY_METRIC_COUNT } from '../../shared/ui/ScoringGlossaryPanel';

const DIMENSION_VISUAL_CONFIG = {
  task_completion: { icon: '✅', weight: '20%' },
  prompt_clarity: { icon: '🎯', weight: '15%' },
  context_provision: { icon: '📎', weight: '15%' },
  independence_efficiency: { icon: '🧠', weight: '20%' },
  response_utilization: { icon: '⚡', weight: '10%' },
  debugging_design: { icon: '🔧', weight: '5%' },
  written_communication: { icon: '✍️', weight: '10%' },
  role_fit: { icon: '📄', weight: '5%' },
};

const FRAUD_FLAG_EXPLANATIONS = {
  paste_ratio_above_70_percent: 'A large share of prompts appears pasted rather than iteratively authored.',
  external_paste_detected: 'Detected long pasted content that may be copied from external sources.',
  solution_dump_detected: 'Detected unusually large multi-function solution dumps in a single prompt.',
  injection_attempt: 'Prompt text attempted to override assistant rules or request full-solution bypass behavior.',
  suspiciously_fast: 'Task completion and test outcomes happened unusually fast for normal workflow pacing.',
  first_prompt_within_30_seconds: 'First prompt was sent almost immediately, with little evidence of problem review.',
  zero_code_changes_after_3plus_prompts: 'Multiple prompts did not lead to code deltas, indicating low response utilization.',
  single_prompt_above_1000_words: 'At least one prompt was extremely long, often linked to copy/paste behavior.',
  severe_unprofessional_language: 'Severe unprofessional language was detected during the assessment.',
};

const scoreColor = (score) => {
  if (score >= 7) return 'var(--taali-success)';
  if (score >= 5) return 'var(--taali-warning)';
  return 'var(--taali-danger)';
};

const scoreLabel = (score) => {
  if (score >= 8.5) return 'Excellent';
  if (score >= 7.0) return 'Strong';
  if (score >= 5.5) return 'Developing';
  if (score >= 4.0) return 'Weak';
  return 'Needs Improvement';
};

const normalizeAssessmentStatus = (status) => {
  const normalized = String(status || '').toLowerCase();
  if (normalized === 'submitted' || normalized === 'graded') return 'completed';
  if (normalized.includes('timeout')) return 'completed_due_to_timeout';
  if (normalized.includes('progress')) return 'in_progress';
  if (normalized.includes('expire')) return 'expired';
  if (normalized.includes('abandon')) return 'abandoned';
  if (normalized.includes('complete')) return 'completed';
  return normalized || 'pending';
};

const normalizeCategoryBucket = (rawBucket) => {
  const normalized = {};
  Object.entries(rawBucket || {}).forEach(([key, value]) => {
    const canonicalId = toCanonicalId(key);
    if (!canonicalId || !value || typeof value !== 'object') return;
    normalized[canonicalId] = {
      ...(normalized[canonicalId] || {}),
      ...value,
    };
  });
  return normalized;
};

const getStatusAwareEmptyMessage = (status) => {
  if (status === 'in_progress') {
    return 'Assessment in progress — results will appear when complete.';
  }
  if (status === 'completed' || status === 'completed_due_to_timeout') {
    return 'Scoring is being processed. This usually takes under a minute. Refresh to check.';
  }
  if (status === 'expired' || status === 'abandoned') {
    return 'This assessment was not completed.';
  }
  return 'Some scoring categories or detailed metrics are unavailable for this assessment.';
};

const fallbackOverallSummary = (catScores) => {
  const scored = Object.entries(catScores || {})
    .filter(([, value]) => Number.isFinite(Number(value)))
    .map(([key, value]) => ({ key, value: Number(value), label: getDimensionById(key)?.label || key }))
    .sort((a, b) => b.value - a.value);
  if (!scored.length) {
    return 'Assessment data is still populating. Use this tab to review score detail once processing completes.';
  }
  const strongest = scored[0];
  const weakest = [...scored].sort((a, b) => a.value - b.value)[0];
  return `This candidate is strongest in ${strongest.label} and needs deeper interview probing in ${weakest.label}.`;
};

export const CandidateResultsTab = ({
  candidate,
  expandedCategory,
  setExpandedCategory,
  getCategoryScores,
  getMetricMetaResolved,
  onOpenComparison = () => {},
  onGenerateInterviewGuide = () => {},
  interviewGuideLoading = false,
  canGenerateInterviewGuide = false,
  onOpenOnboarding = () => {},
  benchmarksLoading = false,
  benchmarksData = null,
}) => {
  const assessment = candidate._raw || {};
  const assessmentStatus = normalizeAssessmentStatus(assessment.status || candidate.status);
  const bd = candidate.breakdown || {};
  const catScores = getCategoryScores(candidate);
  const detailedScores = normalizeCategoryBucket(
    bd.detailedScores
      || assessment.score_breakdown?.detailed_scores
      || assessment.prompt_analytics?.detailed_scores
      || {}
  );
  const explanations = normalizeCategoryBucket(
    bd.explanations
      || assessment.score_breakdown?.explanations
      || assessment.prompt_analytics?.explanations
      || {}
  );
  const CATEGORY_CONFIG = dimensionOrder.map((id) => ({
    key: id,
    icon: DIMENSION_VISUAL_CONFIG[id]?.icon || '•',
    weight: DIMENSION_VISUAL_CONFIG[id]?.weight || '—',
    label: getDimensionById(id).label,
    description: getDimensionById(id).longDescription,
  }));

  const radarData = CATEGORY_CONFIG.map((c) => ({
    dimension: c.label,
    score: catScores[c.key] ?? 0,
    fullMark: 10,
  }));
  const hasAnyCategoryScore = CATEGORY_CONFIG.some((category) => catScores[category.key] != null);
  const hasAnyDetailedMetrics = Object.values(detailedScores).some((bucket) => Object.keys(bucket || {}).length > 0);

  const candidatePercentiles = benchmarksData?.candidate_percentiles || {};
  const overallPercentile = Number.isFinite(Number(candidatePercentiles?.overall))
    ? Number(candidatePercentiles.overall)
    : null;
  const overallTopPercent = overallPercentile != null
    ? Math.max(1, Math.round(100 - overallPercentile))
    : null;

  const scoreBreakdown = assessment.score_breakdown || {};
  const uncappedFinalScore = Number.isFinite(Number(scoreBreakdown?.uncapped_final_score))
    ? Number(scoreBreakdown.uncapped_final_score)
    : null;
  const appliedCaps = Array.isArray(scoreBreakdown?.applied_caps) ? scoreBreakdown.applied_caps : [];
  const severeLanguageCap = appliedCaps.includes('severe_unprofessional_language');
  const fraudCapApplied = appliedCaps.includes('fraud');
  const rawFraudFlags = Array.isArray(assessment.prompt_fraud_flags) ? assessment.prompt_fraud_flags : [];
  const hasFraudFlags = rawFraudFlags.length > 0;
  const heuristicSummary = String(
    bd.heuristicSummary
    || scoreBreakdown.heuristic_summary
    || assessment.prompt_analytics?.heuristic_summary
    || ''
  ).trim();

  const overallScore10 = Number.isFinite(Number(assessment.score))
    ? Number(assessment.score)
    : (Number.isFinite(Number(candidate.score)) ? Number(candidate.score) : null);
  const calibrationScore = Number.isFinite(Number(assessment.calibration_score))
    ? Number(assessment.calibration_score)
    : null;

  const overallSummaryText = heuristicSummary || fallbackOverallSummary(catScores);

  const benchmarkBadgeForCategory = (key) => {
    const percentile = candidatePercentiles?.[key];
    if (!Number.isFinite(Number(percentile))) return null;
    const top = Math.max(1, Math.round(100 - Number(percentile)));
    if (top <= 25) return { variant: 'success', label: `Top ${top}%` };
    if (top <= 60) return { variant: 'warning', label: `Top ${top}%` };
    return null;
  };

  return (
    <div className="space-y-6">
      {severeLanguageCap ? (
        <Panel className="border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-4">
          <div className="font-bold text-[var(--taali-danger)]">Score capped — severe unprofessional language was used during this assessment.</div>
          {uncappedFinalScore != null ? (
            <div className="mt-1 font-mono text-xs text-[var(--taali-danger)]">Original computed score: {uncappedFinalScore.toFixed(2)}/100</div>
          ) : null}
        </Panel>
      ) : null}

      {!severeLanguageCap && (fraudCapApplied || hasFraudFlags) ? (
        <Panel className="border-[var(--taali-warning-border)] bg-[var(--taali-warning-soft)] p-4">
          <div className="font-bold text-[var(--taali-text)]">Score modified — fraud signals detected.</div>
          {uncappedFinalScore != null ? (
            <div className="mt-1 font-mono text-xs text-[var(--taali-muted)]">Original computed score: {uncappedFinalScore.toFixed(2)}/100</div>
          ) : null}
          {hasFraudFlags ? (
            <div className="mt-2 space-y-1">
              {rawFraudFlags.map((flag, index) => (
                <div key={`${flag.type}-${index}`} className="font-mono text-xs text-[var(--taali-text)]">
                  <span title={FRAUD_FLAG_EXPLANATIONS[flag.type] || 'Potential integrity signal. Human review is recommended.'}>
                    • {flag.type}: {flag.evidence}
                  </span>
                </div>
              ))}
            </div>
          ) : null}
        </Panel>
      ) : null}

      <Card className="bg-[var(--taali-purple-soft)] px-3 py-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-[var(--taali-muted)]">Compare this candidate with others in the same role.</p>
          <div className="flex items-center gap-2">
            {overallTopPercent != null ? (
              <Badge variant="purple" className="font-mono text-[11px]">Top {overallTopPercent}%</Badge>
            ) : null}
            {canGenerateInterviewGuide ? (
              <Button type="button" variant="secondary" size="sm" onClick={onGenerateInterviewGuide} disabled={interviewGuideLoading}>
                {interviewGuideLoading ? 'Generating guide...' : 'Generate Interview Guide'}
              </Button>
            ) : null}
            <Button type="button" variant="secondary" size="sm" onClick={onOpenComparison}>
              Compare with...
            </Button>
          </div>
        </div>
      </Card>

      <Panel className="p-4">
        <div className="mb-2 flex items-center justify-between gap-2">
          <div className="font-bold">Recruiter Insight Summary</div>
          <Badge variant="muted" className="font-mono text-[11px]">Auto-generated · AI-assisted analysis · Not a hiring decision</Badge>
        </div>
        <p className="text-sm text-[var(--taali-text)]">{overallSummaryText}</p>
      </Panel>

      {overallScore10 != null ? (
        <Panel className="p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <div className="font-mono text-xs text-[var(--taali-muted)]">Overall score</div>
              <div className="font-mono text-2xl font-bold" style={{ color: scoreColor(overallScore10) }}>
                {overallScore10.toFixed(1)}/10 · {scoreLabel(overallScore10)}
              </div>
            </div>
            <Button type="button" variant="secondary" size="sm" onClick={onOpenOnboarding}>
              What does this score mean?
            </Button>
          </div>
          <p className="mt-2 text-xs text-[var(--taali-muted)]">{overallSummaryText}</p>
        </Panel>
      ) : null}

      {calibrationScore != null ? (
        <Panel className="p-4">
          <div className="font-mono text-xs text-[var(--taali-muted)]">Baseline AI collaboration (calibration)</div>
          <div className="font-mono text-xl font-bold" style={{ color: scoreColor(calibrationScore) }}>
            {calibrationScore.toFixed(1)}/10 · {scoreLabel(calibrationScore)}
          </div>
          <div className="mt-1 text-xs text-[var(--taali-muted)]">
            Captured in the 2-minute warmup before the main task.
          </div>
        </Panel>
      ) : null}

      {hasAnyCategoryScore ? (
        <Panel className="p-4">
          <div className="mb-4 text-base font-bold">Category Breakdown</div>
          <div style={{ width: '100%', height: 350 }}>
            <ResponsiveContainer>
              <RadarChart data={radarData}>
                <PolarGrid stroke="var(--taali-purple-soft)" />
                <PolarAngleAxis dataKey="dimension" tick={{ fontSize: 11, fontFamily: 'var(--taali-font)', fill: 'var(--taali-muted)' }} />
                <PolarRadiusAxis domain={[0, 10]} tick={{ fontSize: 10, fill: 'var(--taali-muted)' }} />
                <Radar name={candidate.name} dataKey="score" stroke="var(--taali-purple)" fill="var(--taali-purple)" fillOpacity={0.2} />
              </RadarChart>
            </ResponsiveContainer>
          </div>
        </Panel>
      ) : (
        <Panel className="border-[var(--taali-warning-border)] bg-[var(--taali-warning-soft)] p-4">
          <div className="font-bold text-[var(--taali-text)]">Score data unavailable</div>
          <div className="text-xs text-[var(--taali-muted)] mt-1">{getStatusAwareEmptyMessage(assessmentStatus)}</div>
        </Panel>
      )}

      <div className="space-y-3">
        {CATEGORY_CONFIG.map((cat) => {
          const catScore = catScores[cat.key];
          const metrics = detailedScores[cat.key] || {};
          const catExplanations = explanations[cat.key] || {};
          const isExpanded = expandedCategory === cat.key;
          const benchmarkBadge = benchmarkBadgeForCategory(cat.key);

          if (catScore == null && Object.keys(metrics).length === 0 && cat.key !== 'role_fit') return null;

          return (
            <Panel key={cat.key} className="overflow-hidden">
              <button
                type="button"
                className="flex w-full items-center justify-between px-4 py-3 text-left transition hover:bg-[var(--taali-purple-soft)]"
                onClick={() => setExpandedCategory(isExpanded ? null : cat.key)}
              >
                <div className="flex min-w-0 items-center gap-3">
                  <span>{cat.icon}</span>
                  <span className="truncate font-bold" title={cat.description}>{cat.label}</span>
                  <Badge variant="muted" className="font-mono text-[11px]">Weight: {cat.weight}</Badge>
                  {benchmarkBadge ? (
                    <Badge variant={benchmarkBadge.variant} className="font-mono text-[11px]">{benchmarkBadge.label}</Badge>
                  ) : null}
                  {cat.key === 'role_fit' && catScore == null ? (
                    <Badge variant="warning" className="font-mono text-[11px]">No CV provided</Badge>
                  ) : null}
                </div>
                <div className="flex items-center gap-3">
                  {catScore != null ? (
                    <span className="font-mono text-base font-bold" style={{ color: scoreColor(catScore) }}>
                      {Number(catScore).toFixed(1)}/10 · {scoreLabel(Number(catScore))}
                    </span>
                  ) : (
                    <span className="font-mono text-xs text-[var(--taali-muted)]">—</span>
                  )}
                  <span className="font-mono text-xs text-[var(--taali-muted)]">{isExpanded ? '▲' : '▼'}</span>
                </div>
              </button>

              {isExpanded ? (
                <div className="border-t border-[var(--taali-border-muted)] bg-[var(--taali-purple-soft)] px-4 py-3">
                  {Object.entries(metrics).map(([metricKey, metricVal]) => (
                    <div key={metricKey} className="mb-3 last:mb-0">
                      <div className="mb-1 flex items-center gap-3">
                        <div className="w-44 font-mono text-sm text-[var(--taali-text)]" title={getMetricMetaResolved(metricKey).description}>
                          {getMetricMetaResolved(metricKey).label}
                        </div>
                        <div className="h-2.5 flex-1 overflow-hidden bg-[var(--taali-border-muted)]">
                          <div
                            className="h-full"
                            style={{
                              width: `${((Number(metricVal) || 0) / 10) * 100}%`,
                              backgroundColor: scoreColor(Number(metricVal) || 0),
                            }}
                          />
                        </div>
                        <div className="w-24 text-right font-mono text-sm font-bold">
                          {metricVal != null ? `${Number(metricVal).toFixed(1)}/10` : '—'}
                        </div>
                      </div>
                      {catExplanations[metricKey] ? (
                        <div className="pl-44 text-xs text-[var(--taali-muted)]">{catExplanations[metricKey]}</div>
                      ) : null}
                    </div>
                  ))}

                  {Object.keys(metrics).length === 0 ? (
                    <div className="font-mono text-sm text-[var(--taali-muted)]">No detailed metrics available for this category.</div>
                  ) : null}
                </div>
              ) : null}
            </Panel>
          );
        })}
      </div>

      {(!hasAnyCategoryScore || !hasAnyDetailedMetrics) ? (
        <Panel className="border-[var(--taali-warning-border)] bg-[var(--taali-warning-soft)] p-4">
          <div className="mb-1 font-bold text-[var(--taali-text)]">Partial scoring data</div>
          <div className="text-xs text-[var(--taali-muted)]">{getStatusAwareEmptyMessage(assessmentStatus)}</div>
        </Panel>
      ) : null}

      <Panel className="p-4">
        <div className="mb-3 font-bold">Scoring Glossary</div>
        <ScoringCardGrid
          items={CATEGORY_CONFIG.map((cat) => ({
            key: `glossary-${cat.key}`,
            title: cat.label,
            description: cat.description,
          }))}
          className="md:grid-cols-2 lg:grid-cols-2"
          cardClassName="!p-3"
        />
        <details className="mt-4 border-t border-[var(--taali-border)] pt-3">
          <summary className="cursor-pointer font-mono text-xs text-[var(--taali-purple)] hover:underline">
            View TAALI scoring glossary ({SCORING_GLOSSARY_METRIC_COUNT} metrics) →
          </summary>
          <ScoringGlossaryPanel className="mt-3" />
        </details>
      </Panel>

      <Panel className="p-4">
        <div className="mb-3 font-bold">Assessment Metadata</div>
        <div className="grid grid-cols-2 gap-3 font-mono text-sm md:grid-cols-3">
          <div><span className="text-[var(--taali-muted)]">Duration:</span> {assessment.total_duration_seconds ? `${Math.floor(assessment.total_duration_seconds / 60)}m ${assessment.total_duration_seconds % 60}s` : '—'}</div>
          <div><span className="text-[var(--taali-muted)]">Total Prompts:</span> {assessment.total_prompts ?? '—'}</div>
          <div><span className="text-[var(--taali-muted)]">Claude Credit Used:</span> {((assessment.total_input_tokens || 0) + (assessment.total_output_tokens || 0)).toLocaleString()}</div>
          <div><span className="text-[var(--taali-muted)]">Tests:</span> {assessment.tests_passed ?? 0}/{assessment.tests_total ?? 0}</div>
          <div><span className="text-[var(--taali-muted)]">Started:</span> {assessment.started_at ? new Date(assessment.started_at).toLocaleString() : '—'}</div>
          <div><span className="text-[var(--taali-muted)]">Submitted:</span> {assessment.completed_at ? new Date(assessment.completed_at).toLocaleString() : '—'}</div>
        </div>
      </Panel>

      <Panel className="p-4">
        <div className="mb-2 font-bold">Task Benchmarks</div>
        {benchmarksLoading ? (
          <div className="font-mono text-xs text-[var(--taali-muted)]">Loading benchmark data...</div>
        ) : benchmarksData?.available ? (
          <div className="space-y-3">
            <div className="font-mono text-xs text-[var(--taali-muted)]">
              How this candidate compares to {benchmarksData.sample_size} others on this task
            </div>
            <div className="grid gap-2">
              {CATEGORY_CONFIG.map((category) => {
                const percentile = candidatePercentiles?.[category.key];
                if (!Number.isFinite(Number(percentile))) return null;
                const percentileNumber = Number(percentile);
                const top = Math.max(1, Math.round(100 - percentileNumber));
                return (
                  <div key={`bench-${category.key}`} className="grid grid-cols-[180px_minmax(0,1fr)_90px] items-center gap-2">
                    <span className="font-mono text-xs text-[var(--taali-text)]">{category.label}</span>
                    <div className="h-2 bg-[var(--taali-border-muted)]">
                      <div className="h-2 bg-[var(--taali-purple)]" style={{ width: `${Math.max(0, Math.min(100, percentileNumber))}%` }} />
                    </div>
                    <span className="font-mono text-xs text-[var(--taali-muted)]">Top {top}%</span>
                  </div>
                );
              })}
            </div>
          </div>
        ) : (
          <div className="font-mono text-xs text-[var(--taali-muted)]">
            {benchmarksData?.message || 'Not enough data for benchmarks yet (need 20+ completions).'}
          </div>
        )}
      </Panel>

      {assessment.prompt_fraud_flags && assessment.prompt_fraud_flags.length > 0 ? (
        <Panel className="border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-4">
          <div className="mb-2 flex items-center gap-2 font-bold text-[var(--taali-danger)]"><AlertTriangle size={18} /> Fraud Flags Detected</div>
          {rawFraudFlags.map((flag, i) => (
            <div key={i} className="mb-1 font-mono text-sm text-[var(--taali-danger)]">
              <span title={FRAUD_FLAG_EXPLANATIONS[flag.type] || 'Potential integrity signal. Human review is recommended.'}>
                • {flag.type}: {flag.evidence} (confidence: {(flag.confidence * 100).toFixed(0)}%)
              </span>
              {FRAUD_FLAG_EXPLANATIONS[flag.type] ? (
                <div className="pl-4 text-xs text-[var(--taali-danger)]/80">{FRAUD_FLAG_EXPLANATIONS[flag.type]}</div>
              ) : null}
            </div>
          ))}
        </Panel>
      ) : null}

      {candidate.results.length > 0 ? (
        <div className="space-y-3">
          <div className="font-bold">Test Results</div>
          {candidate.results.map((r, i) => (
            <Panel key={i} className="flex items-start gap-3 bg-[var(--taali-success-soft)] p-4">
              <CheckCircle size={20} className="mt-0.5 shrink-0 text-[var(--taali-purple)]" />
              <div>
                <div className="font-bold text-[var(--taali-text)]">{r.title} <span className="font-mono text-sm text-[var(--taali-muted)]">({r.score})</span></div>
                <p className="mt-1 text-sm text-[var(--taali-muted)]">{r.description}</p>
              </div>
            </Panel>
          ))}
        </div>
      ) : null}
    </div>
  );
};
