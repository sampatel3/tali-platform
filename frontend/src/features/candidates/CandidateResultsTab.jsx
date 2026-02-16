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
  Card,
  Panel,
} from '../../shared/ui/TaaliPrimitives';
import { ScoringCardGrid } from '../../shared/ui/ScoringCardGrid';

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

const scoreColor = (score) => {
  if (score >= 7) return '#16a34a';
  if (score >= 5) return '#d97706';
  return '#dc2626';
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

export const CandidateResultsTab = ({
  candidate,
  expandedCategory,
  setExpandedCategory,
  getCategoryScores,
  getMetricMetaResolved,
}) => {
  const assessment = candidate._raw || {};
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

  return (
    <div className="space-y-6">
      <Card className="bg-[#faf8ff] px-3 py-2">
        <p className="font-mono text-xs text-[var(--taali-muted)]">Compare this candidate with others from the Dashboard: select 2+ candidates there and use the comparison overlay.</p>
      </Card>

      {hasAnyCategoryScore ? (
        <Panel className="p-4">
          <div className="mb-4 text-base font-bold">Category Breakdown</div>
          <div style={{ width: '100%', height: 350 }}>
            <ResponsiveContainer>
              <RadarChart data={radarData}>
                <PolarGrid stroke="rgba(157, 0, 255, 0.24)" />
                <PolarAngleAxis dataKey="dimension" tick={{ fontSize: 11, fontFamily: 'var(--taali-font)', fill: 'var(--taali-muted)' }} />
                <PolarRadiusAxis domain={[0, 10]} tick={{ fontSize: 10, fill: '#6b7280' }} />
                <Radar name={candidate.name} dataKey="score" stroke="#9D00FF" fill="#9D00FF" fillOpacity={0.2} />
              </RadarChart>
            </ResponsiveContainer>
          </div>
        </Panel>
      ) : null}

      <div className="space-y-3">
        {CATEGORY_CONFIG.map((cat) => {
          const catScore = catScores[cat.key];
          const metrics = detailedScores[cat.key] || {};
          const catExplanations = explanations[cat.key] || {};
          const isExpanded = expandedCategory === cat.key;

          if (catScore == null && Object.keys(metrics).length === 0) return null;

          return (
            <Panel key={cat.key} className="overflow-hidden">
              <button
                type="button"
                className="flex w-full items-center justify-between px-4 py-3 text-left transition hover:bg-[#faf8ff]"
                onClick={() => setExpandedCategory(isExpanded ? null : cat.key)}
              >
                <div className="flex min-w-0 items-center gap-3">
                  <span>{cat.icon}</span>
                  <span className="truncate font-bold" title={cat.description}>{cat.label}</span>
                  <Badge variant="muted" className="font-mono text-[11px]">Weight: {cat.weight}</Badge>
                </div>
                <div className="flex items-center gap-3">
                  {catScore != null ? (
                    <span className="font-mono text-base font-bold" style={{ color: scoreColor(catScore) }}>
                      {catScore}/10
                    </span>
                  ) : null}
                  <span className="font-mono text-xs text-[var(--taali-muted)]">{isExpanded ? '▲' : '▼'}</span>
                </div>
              </button>

              {isExpanded ? (
                <div className="border-t border-[#e7e3f4] bg-[#fcfbff] px-4 py-3">
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
                              width: `${((metricVal || 0) / 10) * 100}%`,
                              backgroundColor: scoreColor(metricVal || 0),
                            }}
                          />
                        </div>
                        <div className="w-14 text-right font-mono text-sm font-bold">
                          {metricVal != null ? `${metricVal}/10` : '—'}
                        </div>
                      </div>
                      {catExplanations[metricKey] ? (
                        <div className="pl-44 font-mono text-xs text-gray-500">{catExplanations[metricKey]}</div>
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

      {(Object.keys(catScores).length === 0 || Object.keys(detailedScores).length === 0) ? (
        <Panel className="border-amber-300 bg-amber-50 p-4">
          <div className="mb-1 font-bold text-amber-800">Partial scoring data</div>
          <div className="font-mono text-xs text-amber-700">
            Some scoring categories or detailed metrics are missing for this assessment. Available results are shown above, and missing components are still being processed or were unavailable.
          </div>
        </Panel>
      ) : null}

      <Panel className="p-4">
        <div className="mb-2 font-bold">Scoring Glossary</div>
        <ScoringCardGrid
          items={CATEGORY_CONFIG.map((cat) => ({
            key: `glossary-${cat.key}`,
            title: cat.label,
            description: cat.description,
          }))}
          className="md:grid-cols-2 lg:grid-cols-2"
          cardClassName="!p-3"
        />
      </Panel>

      {(() => {
        const scoredCategories = CATEGORY_CONFIG
          .map((cat) => ({ ...cat, score: catScores[cat.key] }))
          .filter((cat) => cat.score != null)
          .sort((a, b) => b.score - a.score);

        if (!scoredCategories.length) return null;

        const topStrengths = scoredCategories.slice(0, 3);
        const topRisks = [...scoredCategories].sort((a, b) => a.score - b.score).slice(0, 3);
        const interviewFocus = topRisks.map((risk) => {
          if (risk.key === 'context_provision') return 'Ask for a walkthrough of how they provide debugging context to AI and teammates.';
          if (risk.key === 'independence_efficiency') return 'Probe when they decide to ask AI for help vs investigate independently, and how they keep iteration loops efficient.';
          if (risk.key === 'task_completion') return 'Deep-dive on execution discipline: testing strategy, prioritization, and delivery under time constraints.';
          if (risk.key === 'written_communication') return 'Assess communication clarity by asking them to explain tradeoffs to a non-technical stakeholder.';
          if (risk.key === 'debugging_design') return 'Discuss a recent bug they solved and the hypotheses/experiments they ran, including design tradeoffs.';
          if (risk.key === 'prompt_clarity') return 'Ask them to rewrite a vague AI prompt into a precise, high-signal prompt.';
          if (risk.key === 'response_utilization') return 'Check whether they can evaluate and adapt AI outputs rather than copy blindly.';
          if (risk.key === 'role_fit') return 'Validate role-fit gaps with concrete examples from prior projects.';
          return `Explore weaker area: ${risk.label}.`;
        });

        return (
          <Panel className="p-4">
            <div className="mb-3 font-bold">Recruiter Insight Summary</div>
            <div className="grid gap-4 md:grid-cols-3">
              <div>
                <div className="mb-2 font-mono text-xs text-green-700">Top strengths</div>
                <ul className="space-y-1">
                  {topStrengths.map((s) => (
                    <li key={s.key} className="font-mono text-sm">• {s.label} ({s.score}/10)</li>
                  ))}
                </ul>
              </div>
              <div>
                <div className="mb-2 font-mono text-xs text-red-700">Top risks</div>
                <ul className="space-y-1">
                  {topRisks.map((r) => (
                    <li key={r.key} className="font-mono text-sm">• {r.label} ({r.score}/10)</li>
                  ))}
                </ul>
              </div>
              <div>
                <div className="mb-2 font-mono text-xs text-purple-700">Suggested interview focus</div>
                <ul className="space-y-1">
                  {interviewFocus.map((q, idx) => (
                    <li key={idx} className="font-mono text-sm">• {q}</li>
                  ))}
                </ul>
              </div>
            </div>
          </Panel>
        );
      })()}

      <Panel className="p-4">
        <div className="mb-3 font-bold">Assessment Metadata</div>
        <div className="grid grid-cols-2 gap-3 font-mono text-sm md:grid-cols-3">
          <div><span className="text-gray-500">Duration:</span> {assessment.total_duration_seconds ? `${Math.floor(assessment.total_duration_seconds / 60)}m ${assessment.total_duration_seconds % 60}s` : '—'}</div>
          <div><span className="text-gray-500">Total Prompts:</span> {assessment.total_prompts ?? '—'}</div>
          <div><span className="text-gray-500">Claude Credit Used:</span> {((assessment.total_input_tokens || 0) + (assessment.total_output_tokens || 0)).toLocaleString()}</div>
          <div><span className="text-gray-500">Tests:</span> {assessment.tests_passed ?? 0}/{assessment.tests_total ?? 0}</div>
          <div><span className="text-gray-500">Started:</span> {assessment.started_at ? new Date(assessment.started_at).toLocaleString() : '—'}</div>
          <div><span className="text-gray-500">Submitted:</span> {assessment.completed_at ? new Date(assessment.completed_at).toLocaleString() : '—'}</div>
        </div>
      </Panel>

      {assessment.prompt_fraud_flags && assessment.prompt_fraud_flags.length > 0 ? (
        <Panel className="border-red-300 bg-red-50 p-4">
          <div className="mb-2 flex items-center gap-2 font-bold text-red-700"><AlertTriangle size={18} /> Fraud Flags Detected</div>
          {assessment.prompt_fraud_flags.map((flag, i) => (
            <div key={i} className="mb-1 font-mono text-sm text-red-700">
              • {flag.type}: {flag.evidence} (confidence: {(flag.confidence * 100).toFixed(0)}%)
            </div>
          ))}
        </Panel>
      ) : null}

      {candidate.results.length > 0 ? (
        <div className="space-y-3">
          <div className="font-bold">Test Results</div>
          {candidate.results.map((r, i) => (
            <Panel key={i} className="flex items-start gap-3 bg-green-50 p-4">
              <CheckCircle size={20} style={{ color: '#9D00FF' }} className="mt-0.5 shrink-0" />
              <div>
                <div className="font-bold">{r.title} <span className="font-mono text-sm text-gray-500">({r.score})</span></div>
                <p className="mt-1 font-mono text-sm text-gray-600">{r.description}</p>
              </div>
            </Panel>
          ))}
        </div>
      ) : null}
    </div>
  );
};
