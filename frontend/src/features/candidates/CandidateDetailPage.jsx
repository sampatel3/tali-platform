import React, { useState, useEffect } from 'react';
import { ArrowLeft, AlertTriangle, CheckCircle } from 'lucide-react';
import { RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar, ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip } from 'recharts';
import * as apiClient from '../../shared/api';
import { getMetricMeta, buildGlossaryFromMetadata } from '../../lib/scoringGlossary';
import { dimensionOrder, getDimensionById, normalizeScores, toCanonicalId } from '../../scoring/scoringDimensions';
import { Button, Input, PageContainer, Panel } from '../../shared/ui/TaaliPrimitives';
import { ScoringCardGrid } from '../../shared/ui/ScoringCardGrid';
import {
  CandidateAiUsageTab,
  CandidateCodeGitTab,
  CandidateCvFitTab,
  CandidateTimelineTab,
} from './CandidateDetailSecondaryTabs';

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

export const CandidateDetailPage = ({ candidate, onNavigate, onDeleted, onNoteAdded, NavComponent = null }) => {
  const assessmentsApi = apiClient.assessments;
  const analyticsApi = apiClient.analytics;
  const candidatesApi = apiClient.candidates;
  const scoringApi = 'scoring' in apiClient ? apiClient.scoring : null;
  const [activeTab, setActiveTab] = useState('results');
  const [busyAction, setBusyAction] = useState('');
  const [noteText, setNoteText] = useState('');
  const [avgCalibrationScore, setAvgCalibrationScore] = useState(null);
  const [workableStatus, setWorkableStatus] = useState({
    posted: Boolean(candidate?._raw?.posted_to_workable),
    postedAt: candidate?._raw?.posted_to_workable_at || null,
  });

  const [expandedCategory, setExpandedCategory] = useState(null);
  const [aiEvalSuggestion, setAiEvalSuggestion] = useState(null);
  const [manualEvalScores, setManualEvalScores] = useState({});
  const [manualEvalStrengths, setManualEvalStrengths] = useState('');
  const [manualEvalImprovements, setManualEvalImprovements] = useState('');
  const [manualEvalSummary, setManualEvalSummary] = useState(null);
  const [manualEvalSaving, setManualEvalSaving] = useState(false);
  const [metricGlossary, setMetricGlossary] = useState({});

  const toEvidenceTextareaValue = (value) => {
    if (Array.isArray(value)) return value.filter(Boolean).join('\n');
    if (typeof value === 'string') return value;
    return '';
  };

  const toLineList = (value) =>
    String(value || '')
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean);

  const getRecommendation = (score100) => {
    if (score100 >= 80) return { label: 'STRONG HIRE', color: '#16a34a' };
    if (score100 >= 65) return { label: 'HIRE', color: '#2563eb' };
    if (score100 >= 50) return { label: 'CONSIDER', color: '#d97706' };
    return { label: 'NOT RECOMMENDED', color: '#FF0033' };
  };

  const score100 = candidate?._raw?.final_score || (candidate?.score ? candidate.score * 10 : null);
  const rec = score100 != null ? getRecommendation(score100) : null;
  const assessmentId = candidate?._raw?.id;
  const roleName = candidate?._raw?.role_name || null;
  const applicationStatus = candidate?._raw?.application_status || null;

  useEffect(() => {
    let cancelled = false;
    const loadCalibrationAverage = async () => {
      try {
        const res = await analyticsApi.get();
        if (!cancelled) {
          setAvgCalibrationScore(res.data?.avg_calibration_score ?? null);
        }
      } catch {
        if (!cancelled) setAvgCalibrationScore(null);
      }
    };
    loadCalibrationAverage();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const loadScoringMetadata = async () => {
      if (!scoringApi?.metadata) {
        setMetricGlossary({});
        return;
      }
      try {
        const res = await scoringApi.metadata();
        if (cancelled) return;
        const built = buildGlossaryFromMetadata(res.data);
        setMetricGlossary(built.metrics);
      } catch {
        if (cancelled) return;
        setMetricGlossary({});
      }
    };
    loadScoringMetadata();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const raw = candidate?._raw;
    const evaluationResult = raw?.evaluation_result || raw?.manual_evaluation || {};
    const categoryScores = evaluationResult?.category_scores;
    if (categoryScores && typeof categoryScores === 'object') {
      const normalized = {};
      Object.entries(categoryScores).forEach(([key, value]) => {
        const item = value && typeof value === 'object' ? value : {};
        normalized[key] = {
          score: item.score || '',
          evidence: toEvidenceTextareaValue(item.evidence),
        };
      });
      setManualEvalScores(normalized);
    } else {
      setManualEvalScores({});
    }
    setManualEvalStrengths(Array.isArray(evaluationResult?.strengths) ? evaluationResult.strengths.join('\n') : '');
    setManualEvalImprovements(Array.isArray(evaluationResult?.improvements) ? evaluationResult.improvements.join('\n') : '');
    const hasSavedResult = evaluationResult && typeof evaluationResult === 'object' && Object.keys(evaluationResult).length > 0;
    setManualEvalSummary(hasSavedResult ? evaluationResult : null);
  }, [candidate?._raw?.manual_evaluation, candidate?._raw?.evaluation_result]);

  const getCategoryScores = (candidateData) => {
    const breakdownScores = candidateData?.breakdown?.categoryScores || candidateData?.breakdown?.detailedScores?.category_scores;
    const scoreBreakdownScores = candidateData?._raw?.score_breakdown?.category_scores;
    const analyticsCategoryScores = candidateData?._raw?.prompt_analytics?.detailed_scores?.category_scores;
    const analyticsAiScores = candidateData?._raw?.prompt_analytics?.ai_scores;
    const legacyFlatBreakdownScores = candidateData?.breakdown
      ? {
          task_completion: candidateData.breakdown.taskCompletion,
          prompt_clarity: candidateData.breakdown.promptClarity,
          context_provision: candidateData.breakdown.contextProvision,
          independence: candidateData.breakdown.independence,
          utilization: candidateData.breakdown.utilization,
          communication: candidateData.breakdown.communication,
          approach: candidateData.breakdown.approach,
          cv_match: candidateData.breakdown.cvMatch,
        }
      : {};
    const rawScores =
      breakdownScores ||
      scoreBreakdownScores ||
      analyticsCategoryScores ||
      analyticsAiScores ||
      legacyFlatBreakdownScores ||
      {};
    return normalizeScores(rawScores);
  };

  const getMetricMetaResolved = (metricKey) => metricGlossary[metricKey] || getMetricMeta(metricKey);

  if (!candidate) {
    return (
      <PageContainer className="max-w-5xl">
        <Panel className="p-6 font-mono text-sm text-gray-600">
          Candidate assessment not found.
        </Panel>
      </PageContainer>
    );
  }

  const headerCategoryScores = getCategoryScores(candidate);

  const handleDownloadReport = async () => {
    if (!assessmentId) return;
    setBusyAction('report');
    try {
      const res = await assessmentsApi.downloadReport(assessmentId);
      const blob = new Blob([res.data], { type: 'application/pdf' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `assessment-${assessmentId}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      alert(err?.response?.data?.detail || 'Failed to download report');
    } finally {
      setBusyAction('');
    }
  };

  const handlePostToWorkable = async () => {
    if (!assessmentId) return;
    setBusyAction('workable');
    try {
      const res = await assessmentsApi.postToWorkable(assessmentId);
      const postedAt = res?.data?.posted_to_workable_at || new Date().toISOString();
      setWorkableStatus({ posted: true, postedAt });
      alert(res?.data?.already_posted ? 'Already posted to Workable' : 'Posted to Workable');
    } catch (err) {
      alert(err?.response?.data?.detail || 'Failed to post to Workable');
    } finally {
      setBusyAction('');
    }
  };


  const handleGenerateAiSuggestions = async () => {
    if (!assessmentId) return;
    setBusyAction('ai-eval');
    try {
      const res = await assessmentsApi.aiEvalSuggestions(assessmentId);
      setAiEvalSuggestion(res.data);
      alert('AI suggestions generated. Human reviewer must confirm final scores.');
    } catch (err) {
      alert(err?.response?.data?.detail || 'Failed to generate AI suggestions');
    } finally {
      setBusyAction('');
    }
  };

  const handleDeleteAssessment = async () => {
    if (!assessmentId) return;
    if (!window.confirm('Delete this assessment? This cannot be undone.')) return;
    setBusyAction('delete');
    try {
      await assessmentsApi.remove(assessmentId);
      if (onDeleted) onDeleted();
      onNavigate('dashboard');
    } catch (err) {
      alert(err?.response?.data?.detail || 'Failed to delete assessment');
    } finally {
      setBusyAction('');
    }
  };

  const handleDownloadCandidateDoc = async (docType) => {
    if (!candidate?._raw?.candidate_id) return;
    try {
      const res = await candidatesApi.downloadDocument(candidate._raw.candidate_id, docType);
      const blob = new Blob([res.data]);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = docType === 'cv' ? (candidate._raw?.candidate_cv_filename || 'candidate-cv') : (candidate._raw?.candidate_job_spec_filename || 'job-spec');
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      alert(err?.response?.data?.detail || 'Failed to download document');
    }
  };

  const handleAddNote = async () => {
    if (!assessmentId || !noteText.trim()) return;
    setBusyAction('note');
    try {
      const res = await assessmentsApi.addNote(assessmentId, noteText.trim());
      if (onNoteAdded && Array.isArray(res?.data?.timeline)) {
        onNoteAdded(res.data.timeline);
      }
      setNoteText('');
      alert('Note added');
    } catch (err) {
      alert(err?.response?.data?.detail || 'Failed to add note');
    } finally {
      setBusyAction('');
    }
  };

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="dashboard" onNavigate={onNavigate} /> : null}
      <PageContainer>
        {/* Back button */}
        <Button
          variant="ghost"
          size="sm"
          className="mb-6 font-mono"
          onClick={() => onNavigate('dashboard')}
        >
          <ArrowLeft size={16} /> Back to Dashboard
        </Button>

        {/* Header */}
        <div className="grid md:grid-cols-3 gap-8 mb-8">
          <div className="md:col-span-2">
            <h1 className="text-4xl font-bold mb-2">{candidate.name}</h1>
            <p className="font-mono text-gray-500 mb-4">{candidate.email}</p>
            <div className="flex flex-wrap gap-4 font-mono text-sm text-gray-600">
              <span className="border-2 border-black px-3 py-1">{candidate.position}</span>
              <span className="border-2 border-black px-3 py-1">Task: {candidate.task}</span>
              {roleName && (
                <span className="border-2 border-black px-3 py-1">Role: {roleName}</span>
              )}
              {applicationStatus && (
                <span className="border-2 border-black px-3 py-1">Application: {applicationStatus}</span>
              )}
              <span className="border-2 border-black px-3 py-1">Duration: {candidate.time}</span>
              {candidate.completedDate && (
                <span className="border-2 border-black px-3 py-1">Completed: {candidate.completedDate}</span>
              )}
            </div>
          </div>
          {/* Score card */}
          {(score100 != null || candidate.score) && (
            <div className="border-2 bg-black p-6 text-white" style={{ borderColor: '#9D00FF' }}>
              <div className="text-5xl font-bold mb-1" style={{ color: '#9D00FF' }}>
                {score100 != null ? `${Math.round(score100)}` : candidate.score}<span className="text-lg text-gray-400">/{score100 != null ? '100' : '10'}</span>
              </div>
              {rec && (
                <div
                  className="inline-block px-3 py-1 text-xs font-bold font-mono text-white mb-3"
                  style={{ backgroundColor: rec.color }}
                >
                  {rec.label}
                </div>
              )}
              {Object.keys(headerCategoryScores).length > 0 && (
                <div className="space-y-1.5 font-mono text-xs">
                  {dimensionOrder.map((key) => {
                    const val = headerCategoryScores[key];
                    const label = getDimensionById(key).label;
                    return val != null ? (
                      <div key={key} className="flex items-center gap-2">
                        <span className="text-gray-400 w-40 truncate">{label}</span>
                        <div className="flex-1 bg-gray-700 h-1.5 rounded">
                          <div className="h-full rounded" style={{ width: `${(val / 10) * 100}%`, backgroundColor: val >= 7 ? '#16a34a' : val >= 5 ? '#d97706' : '#dc2626' }} />
                        </div>
                        <span className="w-8 text-right">{val}</span>
                      </div>
                    ) : null;
                  })}
                </div>
              )}
              {Object.keys(headerCategoryScores).length === 0 && candidate.breakdown && (
                <div className="space-y-1.5 font-mono text-xs">
                  <div className="flex justify-between"><span className="text-gray-400">Tests Passed</span><span>{candidate.breakdown.testsPassed}</span></div>
                  <div className="flex justify-between"><span className="text-gray-400">Code Quality</span><span>{candidate.breakdown.codeQuality}/10</span></div>
                  <div className="flex justify-between"><span className="text-gray-400">Time Efficiency</span><span>{candidate.breakdown.timeEfficiency}/10</span></div>
                  <div className="flex justify-between"><span className="text-gray-400">AI Usage</span><span>{candidate.breakdown.aiUsage}/10</span></div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Tabs */}
        <div className="flex flex-wrap gap-3 mb-6">
          <Button
            type="button"
            variant="secondary"
            className="font-mono"
            onClick={handleDownloadReport}
            disabled={busyAction !== ''}
          >
            {busyAction === 'report' ? 'Downloading…' : 'Download PDF'}
          </Button>
          <Button
            type="button"
            variant="secondary"
            className="font-mono"
            onClick={handlePostToWorkable}
            disabled={busyAction !== ''}
          >
            {busyAction === 'workable' ? 'Posting…' : 'Post to Workable'}
          </Button>
          {import.meta.env.VITE_AI_ASSISTED_EVAL_ENABLED === 'true' && (
            <Button
              type="button"
              variant="secondary"
              className="font-mono"
              onClick={handleGenerateAiSuggestions}
              disabled={busyAction !== ''}
            >
              {busyAction === 'ai-eval' ? 'Generating…' : 'Generate AI suggestions'}
            </Button>
          )}
          <Button
            type="button"
            variant="danger"
            className="font-mono"
            onClick={handleDeleteAssessment}
            disabled={busyAction !== ''}
          >
            {busyAction === 'delete' ? 'Deleting…' : 'Delete'}
          </Button>
        </div>
        {aiEvalSuggestion && (
          <Panel className="p-3 mb-6 bg-[var(--taali-purple-soft)]">
            <div className="font-mono text-xs font-bold mb-1">AI-assisted suggestions (V2, reviewer final)</div>
            <div className="font-mono text-xs text-gray-700">{aiEvalSuggestion.message}</div>
          </Panel>
        )}
        <Panel className="p-3 mb-6 bg-[#faf8ff]">
          <div className="font-mono text-xs">
            <span className="text-gray-500">Workable status:</span>{' '}
            <span className={workableStatus.posted ? 'text-green-700 font-bold' : 'text-gray-700'}>
              {workableStatus.posted ? 'Posted' : 'Not posted'}
            </span>
            {workableStatus.postedAt && (
              <span className="text-gray-500">{' '}on {new Date(workableStatus.postedAt).toLocaleString()}</span>
            )}
          </div>
        </Panel>
        <Panel className="p-4 mb-6">
          <div className="font-mono text-xs text-gray-500 mb-2">Recruiter Notes</div>
          <div className="flex gap-2">
            <Input
              type="text"
              className="flex-1 font-mono"
              placeholder="Add note about this candidate"
              value={noteText}
              onChange={(e) => setNoteText(e.target.value)}
            />
            <Button
              type="button"
              variant="secondary"
              className="font-mono"
              onClick={handleAddNote}
              disabled={busyAction !== ''}
            >
              {busyAction === 'note' ? 'Saving…' : 'Save Note'}
            </Button>
          </div>
        </Panel>
        <div className="taali-table-shell mb-6 flex flex-wrap">
          {['results', 'ai-usage', 'cv-fit', 'code-git', 'evaluate', 'timeline'].map((tab) => (
            <button
              key={tab}
              className={`flex-1 min-w-[100px] px-4 py-3 font-mono text-sm font-bold border-r border-[#e7e3f4] last:border-r-0 transition-colors ${
                activeTab === tab ? 'text-white bg-[var(--taali-purple)]' : 'bg-white hover:bg-[#faf8ff]'
              }`}
              onClick={() => setActiveTab(tab)}
            >
              {tab === 'results' && 'Results'}
              {tab === 'ai-usage' && 'AI Usage'}
              {tab === 'cv-fit' && 'CV & Fit'}
              {tab === 'code-git' && 'Code / Git'}
              {tab === 'evaluate' && 'Evaluate'}
              {tab === 'timeline' && 'Timeline'}
            </button>
          ))}
        </div>

        {/* Tab Content */}
        {activeTab === 'results' && (() => {
          const assessment = candidate._raw || {};
          const bd = candidate.breakdown || {};
          const catScores = getCategoryScores(candidate);
          const detailedScores = normalizeCategoryBucket(
            bd.detailedScores ||
            assessment.score_breakdown?.detailed_scores ||
            assessment.prompt_analytics?.detailed_scores ||
            {}
          );
          const explanations = normalizeCategoryBucket(
            bd.explanations ||
            assessment.score_breakdown?.explanations ||
            assessment.prompt_analytics?.explanations ||
            {}
          );
          const CATEGORY_CONFIG = dimensionOrder.map((id) => ({
            key: id,
            icon: DIMENSION_VISUAL_CONFIG[id]?.icon || '•',
            weight: DIMENSION_VISUAL_CONFIG[id]?.weight || '—',
            label: getDimensionById(id).label,
            description: getDimensionById(id).longDescription,
          }));

          const radarData = CATEGORY_CONFIG.map(c => ({
            dimension: c.label,
            score: catScores[c.key] ?? 0,
            fullMark: 10,
          }));
          const hasAnyCategoryScore = CATEGORY_CONFIG.some((category) => catScores[category.key] != null);

          return (
            <div className="space-y-6">
              <p className="font-mono text-xs text-gray-500">Compare this candidate with others from the Dashboard: select 2+ candidates there and use the comparison overlay.</p>
              {hasAnyCategoryScore && (
                <Panel className="p-4">
                  <div className="font-bold mb-4">Category Breakdown</div>
                  <div style={{ width: '100%', height: 350 }}>
                    <ResponsiveContainer>
                      <RadarChart data={radarData}>
                        <PolarGrid stroke="rgba(157, 0, 255, 0.22)" />
                        <PolarAngleAxis dataKey="dimension" tick={{ fontSize: 11, fontFamily: 'monospace', fill: '#4b5563' }} />
                        <PolarRadiusAxis domain={[0, 10]} tick={{ fontSize: 10, fill: '#6b7280' }} />
                        <Radar name={candidate.name} dataKey="score" stroke="#9D00FF" fill="#9D00FF" fillOpacity={0.2} />
                      </RadarChart>
                    </ResponsiveContainer>
                  </div>
                </Panel>
              )}

              {/* Expandable Category Sections */}
              <div className="space-y-3">
                {CATEGORY_CONFIG.map((cat) => {
                  const catScore = catScores[cat.key];
                  const metrics = detailedScores[cat.key] || {};
                  const catExplanations = explanations[cat.key] || {};
                  const isExpanded = expandedCategory === cat.key;

                  if (catScore == null && Object.keys(metrics).length === 0) return null;

                  return (
                    <div key={cat.key} className="border-2 border-black">
                      <button
                        type="button"
                        className="w-full flex items-center justify-between p-4 hover:bg-gray-50 text-left"
                        onClick={() => setExpandedCategory(isExpanded ? null : cat.key)}
                      >
                        <div className="flex items-center gap-3">
                          <span>{cat.icon}</span>
                          <span className="font-bold" title={cat.description}>{cat.label}</span>
                          <span className="font-mono text-xs text-gray-500">(Weight: {cat.weight})</span>
                        </div>
                        <div className="flex items-center gap-3">
                          {catScore != null && (
                            <span className="font-mono font-bold text-lg" style={{ color: catScore >= 7 ? '#16a34a' : catScore >= 5 ? '#d97706' : '#dc2626' }}>
                              {catScore}/10
                            </span>
                          )}
                          <span className="font-mono text-gray-400">{isExpanded ? '▲' : '▼'}</span>
                        </div>
                      </button>
                      {isExpanded && (
                        <div className="border-t-2 border-black p-4 space-y-3 bg-gray-50">
                          {Object.entries(metrics).map(([metricKey, metricVal]) => (
                            <div key={metricKey}>
                              <div className="flex items-center gap-3 mb-1">
                                <div className="font-mono text-sm w-44 text-gray-700" title={getMetricMetaResolved(metricKey).description}>{getMetricMetaResolved(metricKey).label}</div>
                                <div className="flex-1 bg-gray-200 h-2.5 border border-gray-300 rounded">
                                  <div
                                    className="h-full rounded"
                                    style={{
                                      width: `${((metricVal || 0) / 10) * 100}%`,
                                      backgroundColor: (metricVal || 0) >= 7 ? '#16a34a' : (metricVal || 0) >= 5 ? '#d97706' : '#dc2626',
                                    }}
                                  />
                                </div>
                                <div className="font-mono text-sm w-14 text-right font-bold">
                                  {metricVal != null ? `${metricVal}/10` : '—'}
                                </div>
                              </div>
                              {catExplanations[metricKey] && (
                                <div className="font-mono text-xs text-gray-500 ml-0 pl-44 mt-0.5">{catExplanations[metricKey]}</div>
                              )}
                            </div>
                          ))}
                          {Object.keys(metrics).length === 0 && (
                            <div className="font-mono text-sm text-gray-500">No detailed metrics available for this category.</div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              {(Object.keys(catScores).length === 0 || Object.keys(detailedScores).length === 0) && (
                <div className="border-2 border-yellow-500 bg-yellow-50 p-4"> 
                  <div className="font-bold text-yellow-800 mb-1">Partial scoring data</div>
                  <div className="font-mono text-xs text-yellow-700">Some scoring categories or detailed metrics are missing for this assessment. Available results are shown above, and missing components are still being processed or were unavailable.</div>
                </div>
              )}

              <Panel className="p-4">
                <div className="font-bold mb-2">Scoring Glossary</div>
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

              {/* Recruiter Insight Summary */}
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
                  <div className="border-2 border-black p-4">
                    <div className="font-bold mb-3">Recruiter Insight Summary</div>
                    <div className="grid md:grid-cols-3 gap-4">
                      <div>
                        <div className="font-mono text-xs text-green-700 mb-2">Top strengths</div>
                        <ul className="space-y-1">
                          {topStrengths.map((s) => (
                            <li key={s.key} className="font-mono text-sm">• {s.label} ({s.score}/10)</li>
                          ))}
                        </ul>
                      </div>
                      <div>
                        <div className="font-mono text-xs text-red-700 mb-2">Top risks</div>
                        <ul className="space-y-1">
                          {topRisks.map((r) => (
                            <li key={r.key} className="font-mono text-sm">• {r.label} ({r.score}/10)</li>
                          ))}
                        </ul>
                      </div>
                      <div>
                        <div className="font-mono text-xs text-purple-700 mb-2">Suggested interview focus</div>
                        <ul className="space-y-1">
                          {interviewFocus.map((q, idx) => (
                            <li key={idx} className="font-mono text-sm">• {q}</li>
                          ))}
                        </ul>
                      </div>
                    </div>
                  </div>
                );
              })()}

              {/* Assessment Metadata */}
              <div className="border-2 border-black p-4">
                <div className="font-bold mb-3">Assessment Metadata</div>
                <div className="grid grid-cols-2 md:grid-cols-3 gap-3 font-mono text-sm">
                  <div><span className="text-gray-500">Duration:</span> {assessment.total_duration_seconds ? `${Math.floor(assessment.total_duration_seconds / 60)}m ${assessment.total_duration_seconds % 60}s` : '—'}</div>
                  <div><span className="text-gray-500">Total Prompts:</span> {assessment.total_prompts ?? '—'}</div>
                  <div><span className="text-gray-500">Tokens Used:</span> {((assessment.total_input_tokens || 0) + (assessment.total_output_tokens || 0)).toLocaleString()}</div>
                  <div><span className="text-gray-500">Tests:</span> {assessment.tests_passed ?? 0}/{assessment.tests_total ?? 0}</div>
                  <div><span className="text-gray-500">Started:</span> {assessment.started_at ? new Date(assessment.started_at).toLocaleString() : '—'}</div>
                  <div><span className="text-gray-500">Submitted:</span> {assessment.completed_at ? new Date(assessment.completed_at).toLocaleString() : '—'}</div>
                </div>
              </div>

              {/* Fraud Flags */}
              {assessment.prompt_fraud_flags && assessment.prompt_fraud_flags.length > 0 && (
                <div className="border-2 border-red-500 bg-red-50 p-4">
                  <div className="font-bold text-red-700 mb-2 flex items-center gap-2"><AlertTriangle size={18} /> Fraud Flags Detected</div>
                  {assessment.prompt_fraud_flags.map((flag, i) => (
                    <div key={i} className="font-mono text-sm text-red-700 mb-1">
                      • {flag.type}: {flag.evidence} (confidence: {(flag.confidence * 100).toFixed(0)}%)
                    </div>
                  ))}
                </div>
              )}

              {/* Legacy results */}
              {candidate.results.length > 0 && (
                <div className="space-y-3">
                  <div className="font-bold">Test Results</div>
                  {candidate.results.map((r, i) => (
                    <div key={i} className="border-2 border-black bg-green-50 p-4 flex items-start gap-3">
                      <CheckCircle size={20} style={{ color: '#9D00FF' }} className="mt-0.5 flex-shrink-0" />
                      <div>
                        <div className="font-bold">{r.title} <span className="font-mono text-sm text-gray-500">({r.score})</span></div>
                        <p className="font-mono text-sm text-gray-600 mt-1">{r.description}</p>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })()}

        {activeTab === 'ai-usage' && (
          <CandidateAiUsageTab candidate={candidate} avgCalibrationScore={avgCalibrationScore} />
        )}

        {activeTab === 'cv-fit' && (
          <CandidateCvFitTab candidate={candidate} onDownloadCandidateDoc={handleDownloadCandidateDoc} />
        )}

        {activeTab === 'evaluate' && (() => {
          const assessment = candidate._raw || {};
          const rubric = assessment.evaluation_rubric || {};
          const categories = Object.entries(rubric).filter(([, v]) => v && typeof v === 'object');
          const prompts = assessment.ai_prompts || [];
          const handleSaveManualEval = async () => {
            if (!assessmentId) return;
            const payloadScores = {};
            for (const [key, value] of Object.entries(manualEvalScores || {})) {
              const score = String(value?.score || '').trim().toLowerCase();
              const evidenceList = toLineList(value?.evidence);
              if (score && evidenceList.length === 0) {
                alert(`Evidence is required for "${String(key).replace(/_/g, ' ')}".`);
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
              alert('Manual evaluation saved.');
            } catch (err) {
              alert(err?.response?.data?.detail || 'Failed to save');
            } finally {
              setManualEvalSaving(false);
            }
          };
          return (
            <div className="space-y-6">
              {manualEvalSummary && (
                <div className="border-2 border-black p-3 bg-white">
                  <div className="font-mono text-xs text-gray-600">
                    Manual overall score:{' '}
                    <span className="font-bold text-black">
                      {manualEvalSummary.overall_score != null ? `${manualEvalSummary.overall_score}/10` : '—'}
                    </span>
                    {manualEvalSummary.completed_due_to_timeout && (
                      <span className="ml-3 text-amber-700">Assessment auto-submitted on timeout.</span>
                    )}
                  </div>
                </div>
              )}
              <div className="border-2 border-black p-4 bg-gray-50">
                <div className="font-mono text-xs font-bold text-gray-600 mb-2">Manual rubric evaluation (excellent / good / poor). Add evidence per category.</div>
                {categories.length === 0 ? (
                  <p className="font-mono text-sm text-gray-500">No evaluation rubric for this task. Rubric comes from the task definition.</p>
                ) : (
                  <>
                    {categories.map(([key, config]) => {
                      const weight = config.weight != null ? Math.round(Number(config.weight) * 100) : 0;
                      const current = manualEvalScores[key] || {};
                      return (
                        <div key={key} className="border border-gray-300 p-3 mb-3 bg-white">
                          <div className="flex items-center justify-between mb-2">
                            <span className="font-mono text-sm font-bold capitalize">{String(key).replace(/_/g, ' ')}</span>
                            <span className="font-mono text-xs text-gray-500">{weight}%</span>
                          </div>
                          <div className="grid grid-cols-1 gap-2">
                            <select
                              className="border-2 border-black px-2 py-1 font-mono text-sm"
                              value={current.score || ''}
                              onChange={(e) => setManualEvalScores((prev) => ({
                                ...prev,
                                [key]: { ...prev[key], score: e.target.value },
                              }))}
                            >
                              <option value="">—</option>
                              <option value="excellent">Excellent</option>
                              <option value="good">Good</option>
                              <option value="poor">Poor</option>
                            </select>
                            <textarea
                              className="border-2 border-black px-2 py-1 font-mono text-xs min-h-[60px]"
                              placeholder="Evidence (required for this category)"
                              value={current.evidence ?? ''}
                              onChange={(e) => setManualEvalScores((prev) => ({
                                ...prev,
                                [key]: { ...prev[key], evidence: e.target.value },
                              }))}
                            />
                          </div>
                        </div>
                      );
                    })}
                    <button
                      type="button"
                      className="border-2 border-black px-4 py-2 font-mono text-sm font-bold bg-black text-white hover:bg-gray-800 disabled:opacity-50"
                      onClick={handleSaveManualEval}
                      disabled={manualEvalSaving}
                    >
                      {manualEvalSaving ? 'Saving…' : 'Save manual evaluation'}
                    </button>
                  </>
                )}
              </div>
              <div className="border-2 border-black p-4 bg-gray-50">
                <div className="font-mono text-xs font-bold text-gray-600 mb-2">Summary notes</div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div>
                    <div className="font-mono text-xs text-gray-500 mb-1">Strengths (one per line)</div>
                    <textarea
                      className="w-full border-2 border-black px-2 py-1 font-mono text-xs min-h-[90px]"
                      placeholder="Strong debugging discipline"
                      value={manualEvalStrengths}
                      onChange={(e) => setManualEvalStrengths(e.target.value)}
                    />
                  </div>
                  <div>
                    <div className="font-mono text-xs text-gray-500 mb-1">Improvements (one per line)</div>
                    <textarea
                      className="w-full border-2 border-black px-2 py-1 font-mono text-xs min-h-[90px]"
                      placeholder="Add stronger edge-case tests"
                      value={manualEvalImprovements}
                      onChange={(e) => setManualEvalImprovements(e.target.value)}
                    />
                  </div>
                </div>
              </div>
              <div className="border-2 border-black p-4">
                <div className="font-mono text-xs font-bold text-gray-600 mb-2">Chat log (for evidence)</div>
                {prompts.length === 0 ? (
                  <p className="font-mono text-sm text-gray-500">No prompts recorded.</p>
                ) : (
                  <div className="space-y-2 max-h-64 overflow-y-auto">
                    {prompts.map((p, i) => (
                      <div key={i} className="border border-gray-200 p-2 font-mono text-xs bg-white">
                        <div className="text-gray-600 mb-1">Prompt {i + 1}</div>
                        <div className="text-gray-800">{typeof p.message === 'string' ? p.message : (p.message?.content ?? JSON.stringify(p.message))?.slice(0, 200)}…</div>
                        {p.response && (
                          <div className="mt-1 text-gray-500">Response: {(typeof p.response === 'string' ? p.response : '').slice(0, 150)}…</div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          );
        })()}

        {activeTab === 'code-git' && <CandidateCodeGitTab candidate={candidate} />}

        {activeTab === 'timeline' && <CandidateTimelineTab candidate={candidate} />}
      </PageContainer>
    </div>
  );
};
