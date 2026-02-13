import React, { useState, useEffect } from 'react';
import { ArrowLeft, AlertTriangle, CheckCircle } from 'lucide-react';
import { RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar, ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip } from 'recharts';
import { assessments as assessmentsApi, analytics as analyticsApi, candidates as candidatesApi } from '../lib/api';
import { SCORING_CATEGORY_GLOSSARY, getMetricMeta } from '../lib/scoringGlossary';

export const CandidateDetailPage = ({ candidate, onNavigate, onDeleted, onNoteAdded, NavComponent = null }) => {
  const [activeTab, setActiveTab] = useState('results');
  const [busyAction, setBusyAction] = useState('');
  const [noteText, setNoteText] = useState('');
  const [avgCalibrationScore, setAvgCalibrationScore] = useState(null);
  const [workableStatus, setWorkableStatus] = useState({
    posted: Boolean(candidate?._raw?.posted_to_workable),
    postedAt: candidate?._raw?.posted_to_workable_at || null,
  });

  const [expandedCategory, setExpandedCategory] = useState(null);
  const [comparisonCandidates, setComparisonCandidates] = useState([]);
  const [comparisonCandidateId, setComparisonCandidateId] = useState('');
  const [comparisonAssessment, setComparisonAssessment] = useState(null);
  const [comparisonMode, setComparisonMode] = useState('overlay');
  const [aiEvalSuggestion, setAiEvalSuggestion] = useState(null);

  const getRecommendation = (score100) => {
    if (score100 >= 80) return { label: 'STRONG HIRE', color: '#16a34a' };
    if (score100 >= 65) return { label: 'HIRE', color: '#2563eb' };
    if (score100 >= 50) return { label: 'CONSIDER', color: '#d97706' };
    return { label: 'NOT RECOMMENDED', color: '#FF0033' };
  };

  const score100 = candidate._raw?.final_score || (candidate.score ? candidate.score * 10 : null);
  const rec = score100 != null ? getRecommendation(score100) : null;
  const assessmentId = candidate?._raw?.id;

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
    const loadComparisonCandidates = async () => {
      try {
        const res = await candidatesApi.list({ limit: 100, offset: 0 });
        if (cancelled) return;
        const currentCandidateId = candidate?._raw?.candidate_id;
        const items = (res.data?.items || []).filter((item) => item.id !== currentCandidateId);
        setComparisonCandidates(items);
      } catch {
        if (!cancelled) setComparisonCandidates([]);
      }
    };
    loadComparisonCandidates();
    return () => {
      cancelled = true;
    };
  }, [candidate?._raw?.candidate_id]);

  const getCategoryScores = (candidateData) => {
    const breakdownScores = candidateData?.breakdown?.categoryScores;
    const detailedCategoryScores = candidateData?.breakdown?.detailedScores?.category_scores;
    const analyticsCategoryScores = candidateData?._raw?.prompt_analytics?.detailed_scores?.category_scores;
    return breakdownScores || detailedCategoryScores || analyticsCategoryScores || {};
  };

  const loadComparisonAssessment = async (selectedCandidateId) => {
    if (!selectedCandidateId) {
      setComparisonAssessment(null);
      return;
    }
    try {
      const res = await assessmentsApi.list({ candidate_id: Number(selectedCandidateId), limit: 1, offset: 0 });
      const item = res.data?.items?.[0] || null;
      setComparisonAssessment(item ? {
        name: item.candidate_name || item.candidate_email || `Candidate ${selectedCandidateId}`,
        score: item.score ?? item.overall_score ?? null,
        breakdown: item.breakdown || null,
        _raw: item,
      } : null);
    } catch {
      setComparisonAssessment(null);
    }
  };

  if (!candidate) return null;

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
      <div className="max-w-7xl mx-auto px-6 py-8">
        {/* Back button */}
        <button
          className="flex items-center gap-2 font-mono text-sm mb-6 hover:underline"
          onClick={() => onNavigate('dashboard')}
        >
          <ArrowLeft size={16} /> Back to Dashboard
        </button>

        {/* Header */}
        <div className="grid md:grid-cols-3 gap-8 mb-8">
          <div className="md:col-span-2">
            <h1 className="text-4xl font-bold mb-2">{candidate.name}</h1>
            <p className="font-mono text-gray-500 mb-4">{candidate.email}</p>
            <div className="flex flex-wrap gap-4 font-mono text-sm text-gray-600">
              <span className="border-2 border-black px-3 py-1">{candidate.position}</span>
              <span className="border-2 border-black px-3 py-1">Task: {candidate.task}</span>
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
              {candidate.breakdown?.categoryScores && (
                <div className="space-y-1.5 font-mono text-xs">
                  {[
                    ['Task Completion', 'task_completion'],
                    ['Prompt Clarity', 'prompt_clarity'],
                    ['Context', 'context_provision'],
                    ['Independence', 'independence'],
                    ['Utilization', 'utilization'],
                    ['Communication', 'communication'],
                    ['Approach', 'approach'],
                    ['CV Match', 'cv_match'],
                  ].map(([label, key]) => {
                    const val = candidate.breakdown.categoryScores[key];
                    return val != null ? (
                      <div key={key} className="flex items-center gap-2">
                        <span className="text-gray-400 w-28 truncate">{label}</span>
                        <div className="flex-1 bg-gray-700 h-1.5 rounded">
                          <div className="h-full rounded" style={{ width: `${(val / 10) * 100}%`, backgroundColor: val >= 7 ? '#16a34a' : val >= 5 ? '#d97706' : '#dc2626' }} />
                        </div>
                        <span className="w-8 text-right">{val}</span>
                      </div>
                    ) : null;
                  })}
                </div>
              )}
              {!candidate.breakdown?.categoryScores && candidate.breakdown && (
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
          <button
            type="button"
            className="border-2 border-black px-4 py-2 font-mono text-sm font-bold hover:bg-black hover:text-white"
            onClick={handleDownloadReport}
            disabled={busyAction !== ''}
          >
            {busyAction === 'report' ? 'Downloading…' : 'Download PDF'}
          </button>
          <button
            type="button"
            className="border-2 border-black px-4 py-2 font-mono text-sm font-bold hover:bg-black hover:text-white"
            onClick={handlePostToWorkable}
            disabled={busyAction !== ''}
          >
            {busyAction === 'workable' ? 'Posting…' : 'Post to Workable'}
          </button>
          {import.meta.env.VITE_AI_ASSISTED_EVAL_ENABLED === 'true' && (
            <button
              type="button"
              className="border-2 border-black px-4 py-2 font-mono text-sm font-bold hover:bg-black hover:text-white"
              onClick={handleGenerateAiSuggestions}
              disabled={busyAction !== ''}
            >
              {busyAction === 'ai-eval' ? 'Generating…' : 'Generate AI suggestions'}
            </button>
          )}
          <button
            type="button"
            className="border-2 border-red-600 text-red-700 px-4 py-2 font-mono text-sm font-bold hover:bg-red-600 hover:text-white"
            onClick={handleDeleteAssessment}
            disabled={busyAction !== ''}
          >
            {busyAction === 'delete' ? 'Deleting…' : 'Delete'}
          </button>
        </div>
        {aiEvalSuggestion && (
          <div className="border-2 border-black p-3 mb-6 bg-purple-50">
            <div className="font-mono text-xs font-bold mb-1">AI-assisted suggestions (V2, reviewer final)</div>
            <div className="font-mono text-xs text-gray-700">{aiEvalSuggestion.message}</div>
          </div>
        )}
        <div className="border-2 border-black p-3 mb-6 bg-gray-50">
          <div className="font-mono text-xs">
            <span className="text-gray-500">Workable status:</span>{' '}
            <span className={workableStatus.posted ? 'text-green-700 font-bold' : 'text-gray-700'}>
              {workableStatus.posted ? 'Posted' : 'Not posted'}
            </span>
            {workableStatus.postedAt && (
              <span className="text-gray-500">{' '}on {new Date(workableStatus.postedAt).toLocaleString()}</span>
            )}
          </div>
        </div>
        <div className="border-2 border-black p-4 mb-6">
          <div className="font-mono text-xs text-gray-500 mb-2">Recruiter Notes</div>
          <div className="flex gap-2">
            <input
              type="text"
              className="flex-1 border-2 border-black px-3 py-2 font-mono text-sm"
              placeholder="Add note about this candidate"
              value={noteText}
              onChange={(e) => setNoteText(e.target.value)}
            />
            <button
              type="button"
              className="border-2 border-black px-4 py-2 font-mono text-sm font-bold hover:bg-black hover:text-white"
              onClick={handleAddNote}
              disabled={busyAction !== ''}
            >
              {busyAction === 'note' ? 'Saving…' : 'Save Note'}
            </button>
          </div>
        </div>
        <div className="flex border-2 border-black mb-6">
          {['results', 'ai-usage', 'cv-fit', 'timeline'].map((tab) => (
            <button
              key={tab}
              className={`flex-1 px-6 py-3 font-mono text-sm font-bold border-r-2 border-black last:border-r-0 transition-colors ${
                activeTab === tab ? 'text-white' : 'bg-white hover:bg-gray-100'
              }`}
              style={activeTab === tab ? { backgroundColor: '#9D00FF' } : {}}
              onClick={() => setActiveTab(tab)}
            >
              {tab === 'results' && 'Results'}
              {tab === 'ai-usage' && 'AI Usage'}
              {tab === 'cv-fit' && 'CV & Fit'}
              {tab === 'timeline' && 'Timeline'}
            </button>
          ))}
        </div>

        {/* Tab Content */}
        {activeTab === 'results' && (() => {
          const assessment = candidate._raw || {};
          const bd = candidate.breakdown || {};
          const catScores = getCategoryScores(candidate);
          const detailedScores = bd.detailedScores || assessment.prompt_analytics?.detailed_scores || {};
          const explanations = bd.explanations || assessment.prompt_analytics?.explanations || {};
          const comparisonScores = getCategoryScores(comparisonAssessment);

          const CATEGORY_CONFIG = [
            { key: 'task_completion', icon: '✅', weight: '20%' },
            { key: 'prompt_clarity', icon: '🎯', weight: '15%' },
            { key: 'context_provision', icon: '📎', weight: '15%' },
            { key: 'independence', icon: '🧠', weight: '20%' },
            { key: 'utilization', icon: '⚡', weight: '10%' },
            { key: 'communication', icon: '✍️', weight: '10%' },
            { key: 'approach', icon: '🔧', weight: '5%' },
            { key: 'cv_match', icon: '📄', weight: '5%' },
          ].map((category) => ({
            ...category,
            label: SCORING_CATEGORY_GLOSSARY[category.key]?.label || category.key,
            description: SCORING_CATEGORY_GLOSSARY[category.key]?.description || 'No description available yet.',
          }));

          const radarData = CATEGORY_CONFIG.filter(c => catScores[c.key] != null).map(c => ({
            signal: c.label.split(' ')[0],
            score: catScores[c.key] || 0,
            comparisonScore: comparisonScores[c.key] || 0,
            fullMark: 10,
          }));

          return (
            <div className="space-y-6">
              {/* Category Radar Chart */}
              <div className="border-2 border-black p-4 bg-gray-50">
                <div className="font-bold mb-3">Candidate Comparison</div>
                <div className="grid md:grid-cols-3 gap-3 items-end">
                  <div>
                    <div className="font-mono text-xs text-gray-500 mb-1">Candidate A</div>
                    <div className="border-2 border-black px-3 py-2 font-mono text-sm bg-white">{candidate.name}</div>
                  </div>
                  <div>
                    <label className="font-mono text-xs text-gray-500 mb-1 block">Candidate B</label>
                    <select
                      aria-label="Candidate B"
                      className="w-full border-2 border-black px-3 py-2 font-mono text-sm bg-white"
                      value={comparisonCandidateId}
                      onChange={(e) => {
                        const nextId = e.target.value;
                        setComparisonCandidateId(nextId);
                        loadComparisonAssessment(nextId);
                      }}
                    >
                      <option value="">Select candidate</option>
                      {comparisonCandidates.map((opt) => (
                        <option key={opt.id} value={opt.id}>{opt.full_name || opt.email}</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="font-mono text-xs text-gray-500 mb-1 block">Comparison mode</label>
                    <select
                      className="w-full border-2 border-black px-3 py-2 font-mono text-sm bg-white"
                      value={comparisonMode}
                      onChange={(e) => setComparisonMode(e.target.value)}
                    >
                      <option value="overlay">Radar overlay</option>
                      <option value="side-by-side">Side-by-side</option>
                    </select>
                  </div>
                </div>
                {comparisonAssessment && (
                  <div className="font-mono text-xs mt-3 text-green-700">Comparison active: {candidate.name} vs {comparisonAssessment.name}</div>
                )}
              </div>

              {radarData.length > 0 && (
                <div className="border-2 border-black p-4">
                  <div className="font-bold mb-4">Category Breakdown</div>
                  <div style={{ width: '100%', height: 350 }}>
                    <ResponsiveContainer>
                      <RadarChart data={radarData}>
                        <PolarGrid />
                        <PolarAngleAxis dataKey="signal" tick={{ fontSize: 11, fontFamily: 'monospace' }} />
                        <PolarRadiusAxis domain={[0, 10]} tick={{ fontSize: 10 }} />
                        <Radar name={candidate.name} dataKey="score" stroke="#9D00FF" fill="#9D00FF" fillOpacity={0.25} />
                        {comparisonAssessment && comparisonMode === 'overlay' && (
                          <Radar name={comparisonAssessment.name} dataKey="comparisonScore" stroke="#111827" fill="#111827" fillOpacity={0.12} />
                        )}
                      </RadarChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              )}

              {comparisonAssessment && comparisonMode === 'side-by-side' && (
                <div className="border-2 border-black p-4">
                  <div className="font-bold mb-4">Side-by-side category comparison</div>
                  <div className="space-y-2">
                    {CATEGORY_CONFIG.map((cat) => {
                      const aScore = catScores[cat.key];
                      const bScore = comparisonScores[cat.key];
                      if (aScore == null && bScore == null) return null;
                      const delta = (aScore ?? 0) - (bScore ?? 0);
                      return (
                        <div key={cat.key} className="grid grid-cols-4 gap-3 border border-gray-300 p-2 font-mono text-sm">
                          <div>{cat.label}</div>
                          <div>{candidate.name}: {aScore ?? '—'}/10</div>
                          <div>{comparisonAssessment.name}: {bScore ?? '—'}/10</div>
                          <div className={delta >= 0 ? 'text-green-700' : 'text-red-700'}>Δ {delta.toFixed(1)}</div>
                        </div>
                      );
                    })}
                  </div>
                </div>
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
                                <div className="font-mono text-sm w-44 text-gray-700" title={getMetricMeta(metricKey).description}>{getMetricMeta(metricKey).label}</div>
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

              <div className="border-2 border-black p-4">
                <div className="font-bold mb-2">Scoring Glossary</div>
                <div className="grid md:grid-cols-2 gap-2">
                  {CATEGORY_CONFIG.map((cat) => (
                    <div key={`glossary-${cat.key}`} className="border border-gray-300 p-2">
                      <div className="font-mono text-xs font-bold">{cat.label}</div>
                      <div className="font-mono text-xs text-gray-600">{cat.description}</div>
                    </div>
                  ))}
                </div>
              </div>

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
                  if (risk.key === 'independence') return 'Probe when they decide to ask AI for help vs investigate independently.';
                  if (risk.key === 'task_completion') return 'Deep-dive on execution discipline: testing strategy, prioritization, and delivery under time constraints.';
                  if (risk.key === 'communication') return 'Assess communication clarity by asking them to explain tradeoffs to a non-technical stakeholder.';
                  if (risk.key === 'approach') return 'Discuss a recent bug they solved and the hypotheses/experiments they ran.';
                  if (risk.key === 'prompt_clarity') return 'Ask them to rewrite a vague AI prompt into a precise, high-signal prompt.';
                  if (risk.key === 'utilization') return 'Check whether they can evaluate and adapt AI outputs rather than copy blindly.';
                  if (risk.key === 'cv_match') return 'Validate role-fit gaps with concrete examples from prior projects.';
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

        {activeTab === 'ai-usage' && (() => {
          const assessment = candidate._raw || {};

          return (
            <div className="space-y-6">
              {/* Summary Stats */}
              <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
                <div className="border-2 border-black p-4">
                  <div className="font-mono text-xs text-gray-500">Avg Prompt Quality</div>
                  <div className="text-2xl font-bold">{assessment.prompt_quality_score?.toFixed(1) || '--'}<span className="text-sm text-gray-500">/10</span></div>
                </div>
                <div className="border-2 border-black p-4">
                  <div className="font-mono text-xs text-gray-500">Time to First Prompt</div>
                  <div className="text-2xl font-bold">{assessment.time_to_first_prompt_seconds ? `${Math.floor(assessment.time_to_first_prompt_seconds / 60)}m ${Math.round(assessment.time_to_first_prompt_seconds % 60)}s` : '--'}</div>
                </div>
                <div className="border-2 border-black p-4">
                  <div className="font-mono text-xs text-gray-500">Browser Focus</div>
                  <div className="text-2xl font-bold" style={assessment.browser_focus_ratio != null && assessment.browser_focus_ratio < 0.8 ? { color: '#dc2626' } : {}}>{assessment.browser_focus_ratio != null ? `${Math.round(assessment.browser_focus_ratio * 100)}%` : '--'}</div>
                </div>
                <div className="border-2 border-black p-4">
                  <div className="font-mono text-xs text-gray-500">Tab Switches</div>
                  <div className="text-2xl font-bold" style={assessment.tab_switch_count > 5 ? { color: '#dc2626' } : {}}>{assessment.tab_switch_count ?? '--'}</div>
                </div>
                <div className="border-2 border-black p-4">
                  <div className="font-mono text-xs text-gray-500">Calibration</div>
                  <div className="text-2xl font-bold">{assessment.calibration_score != null ? `${assessment.calibration_score.toFixed(1)}/10` : '--'}</div>
                  <div className="font-mono text-xs text-gray-500 mt-1">
                    vs avg {avgCalibrationScore != null ? `${avgCalibrationScore.toFixed(1)}/10` : '--'}
                  </div>
                </div>
              </div>

              {/* Browser Focus Warning */}
              {assessment.browser_focus_ratio != null && assessment.browser_focus_ratio < 0.8 && (
                <div className="border-2 border-yellow-500 bg-yellow-50 p-4">
                  <div className="font-bold text-yellow-700 flex items-center gap-2"><AlertTriangle size={18} /> Low Browser Focus ({Math.round(assessment.browser_focus_ratio * 100)}%)</div>
                  <div className="font-mono text-xs text-yellow-600 mt-1">Candidate spent less than 80% of assessment time with the browser in focus. {assessment.tab_switch_count > 5 ? `${assessment.tab_switch_count} tab switches recorded.` : ''}</div>
                </div>
              )}

              {/* Prompt Progression Chart */}
              {assessment.prompt_analytics?.per_prompt_scores?.length > 0 && (
                <div className="border-2 border-black p-4">
                  <div className="font-bold mb-4">Prompt Quality Progression</div>
                  <div style={{ width: '100%', height: 200 }}>
                    <ResponsiveContainer>
                      <LineChart data={assessment.prompt_analytics.per_prompt_scores.map((p, i) => ({ name: `#${i + 1}`, clarity: p.clarity || 0, specificity: p.specificity || 0, efficiency: p.efficiency || 0 }))}>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey="name" tick={{ fontSize: 10, fontFamily: 'monospace' }} />
                        <YAxis domain={[0, 10]} tick={{ fontSize: 10 }} />
                        <Tooltip />
                        <Line type="monotone" dataKey="clarity" stroke="#9D00FF" strokeWidth={2} dot={{ r: 3 }} />
                        <Line type="monotone" dataKey="specificity" stroke="#000" strokeWidth={1} />
                        <Line type="monotone" dataKey="efficiency" stroke="#666" strokeWidth={1} />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              )}

              {/* Prompt Log */}
              <div className="border-2 border-black p-4">
                <div className="font-bold mb-4">Prompt Log ({(candidate.promptsList || []).length} prompts)</div>
                <div className="space-y-3">
                  {(candidate.promptsList || []).map((p, i) => {
                    const perPrompt = assessment.prompt_analytics?.per_prompt_scores?.[i];
                    return (
                      <div key={i} className="border border-gray-300 p-3">
                        <div className="flex items-center justify-between mb-2">
                          <div className="flex items-center gap-3">
                            <span className="font-mono text-xs font-bold bg-black text-white px-2 py-0.5">#{i + 1}</span>
                            {p.timestamp && <span className="font-mono text-xs text-gray-400">{new Date(p.timestamp).toLocaleTimeString()}</span>}
                            {perPrompt && <span className="font-mono text-xs text-gray-500">{perPrompt.word_count} words</span>}
                          </div>
                          <div className="flex items-center gap-2">
                            {perPrompt && (
                              <>
                                <span className="font-mono text-xs px-2 py-0.5 border" style={{ borderColor: '#9D00FF', color: '#9D00FF' }}>C:{perPrompt.clarity}</span>
                                <span className="font-mono text-xs px-2 py-0.5 border border-gray-400">S:{perPrompt.specificity}</span>
                                <span className="font-mono text-xs px-2 py-0.5 border border-gray-400">E:{perPrompt.efficiency}</span>
                              </>
                            )}
                          </div>
                        </div>
                        <div className="font-mono text-sm bg-gray-50 p-2 rounded">{p.message || p.text}</div>
                        <div className="flex items-center gap-2 mt-2">
                          {perPrompt?.has_context && <span className="text-xs font-mono px-2 py-0.5 bg-green-100 text-green-700 border border-green-300">Has Context</span>}
                          {perPrompt?.is_vague && <span className="text-xs font-mono px-2 py-0.5 bg-red-100 text-red-700 border border-red-300">Vague</span>}
                          {p.paste_detected && <span className="text-xs font-mono px-2 py-0.5 bg-yellow-100 text-yellow-700 border border-yellow-400">PASTED</span>}
                          {p.response_latency_ms && <span className="text-xs font-mono px-2 py-0.5 bg-gray-100 border border-gray-300">{p.response_latency_ms}ms</span>}
                        </div>
                      </div>
                    );
                  })}
                  {(candidate.promptsList || []).length === 0 && (
                    <div className="border-2 border-black p-8 text-center font-mono text-gray-500">
                      No prompt data available yet
                    </div>
                  )}
                </div>
              </div>

              {/* Prompt Statistics */}
              {(candidate.promptsList || []).length > 0 && assessment.prompt_analytics && (
                <div className="border-2 border-black p-4">
                  <div className="font-bold mb-3">Prompt Statistics</div>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3 font-mono text-sm">
                    <div><span className="text-gray-500">Avg Words:</span> {assessment.prompt_analytics.metric_details?.word_count_avg || '—'}</div>
                    <div><span className="text-gray-500">Questions:</span> {assessment.prompt_analytics.metric_details?.question_presence ? `${(assessment.prompt_analytics.metric_details.question_presence * 100).toFixed(0)}%` : '—'}</div>
                    <div><span className="text-gray-500">Code Context:</span> {assessment.prompt_analytics.metric_details?.code_snippet_rate ? `${(assessment.prompt_analytics.metric_details.code_snippet_rate * 100).toFixed(0)}%` : '—'}</div>
                    <div><span className="text-gray-500">Paste Detected:</span> {assessment.prompt_analytics.metric_details?.paste_ratio ? `${(assessment.prompt_analytics.metric_details.paste_ratio * 100).toFixed(0)}%` : '0%'}</div>
                  </div>
                </div>
              )}
            </div>
          );
        })()}

        {activeTab === 'cv-fit' && (() => {
          const assessment = candidate._raw || {};
          const cvMatch = assessment.cv_job_match_details || assessment.prompt_analytics?.cv_job_match?.details || {};
          const matchScores = assessment.prompt_analytics?.cv_job_match || {};
          const overall = matchScores.overall || assessment.cv_job_match_score;
          const skills = matchScores.skills;
          const experience = matchScores.experience;

          return (
            <div className="space-y-6">
              {/* Fit Score Cards */}
              {overall != null ? (
                <>
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <div className="border-2 border-black p-6 text-center">
                      <div className="font-mono text-xs text-gray-500 mb-1">Overall Match</div>
                      <div className="text-4xl font-bold" style={{ color: overall >= 7 ? '#16a34a' : overall >= 5 ? '#d97706' : '#dc2626' }}>{overall}/10</div>
                    </div>
                    <div className="border-2 border-black p-6 text-center">
                      <div className="font-mono text-xs text-gray-500 mb-1">Skills Match</div>
                      <div className="text-4xl font-bold" style={{ color: skills >= 7 ? '#16a34a' : skills >= 5 ? '#d97706' : '#dc2626' }}>{skills != null ? `${skills}/10` : '—'}</div>
                    </div>
                    <div className="border-2 border-black p-6 text-center">
                      <div className="font-mono text-xs text-gray-500 mb-1">Experience</div>
                      <div className="text-4xl font-bold" style={{ color: experience >= 7 ? '#16a34a' : experience >= 5 ? '#d97706' : '#dc2626' }}>{experience != null ? `${experience}/10` : '—'}</div>
                    </div>
                  </div>

                  {/* Matching Skills */}
                  {cvMatch.matching_skills?.length > 0 && (
                    <div className="border-2 border-black p-4">
                      <div className="font-bold mb-3 text-green-700">Matching Skills</div>
                      <div className="flex flex-wrap gap-2">
                        {cvMatch.matching_skills.map((skill, i) => (
                          <span key={i} className="px-3 py-1 bg-green-100 text-green-800 font-mono text-sm border border-green-300">{skill}</span>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Missing Skills */}
                  {cvMatch.missing_skills?.length > 0 && (
                    <div className="border-2 border-black p-4">
                      <div className="font-bold mb-3 text-red-700">Missing Skills</div>
                      <div className="flex flex-wrap gap-2">
                        {cvMatch.missing_skills.map((skill, i) => (
                          <span key={i} className="px-3 py-1 bg-red-100 text-red-800 font-mono text-sm border border-red-300">{skill}</span>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Experience Highlights */}
                  {cvMatch.experience_highlights?.length > 0 && (
                    <div className="border-2 border-black p-4">
                      <div className="font-bold mb-3">Relevant Experience</div>
                      <ul className="space-y-1">
                        {cvMatch.experience_highlights.map((exp, i) => (
                          <li key={i} className="font-mono text-sm text-gray-700 flex items-start gap-2">
                            <span className="text-green-600 mt-0.5">•</span>{exp}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* Concerns */}
                  {cvMatch.concerns?.length > 0 && (
                    <div className="border-2 border-yellow-500 bg-yellow-50 p-4">
                      <div className="font-bold mb-3 text-yellow-700">Concerns</div>
                      <ul className="space-y-1">
                        {cvMatch.concerns.map((concern, i) => (
                          <li key={i} className="font-mono text-sm text-yellow-800 flex items-start gap-2">
                            <AlertTriangle size={14} className="mt-0.5 flex-shrink-0 text-yellow-600" />{concern}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* Summary */}
                  {cvMatch.summary && (
                    <div className="border-2 border-black p-4">
                      <div className="font-bold mb-2">Summary</div>
                      <p className="font-mono text-sm text-gray-700 italic">&quot;{cvMatch.summary}&quot;</p>
                    </div>
                  )}
                </>
              ) : (
                <div className="border-2 border-black p-8 text-center">
                  <div className="font-mono text-gray-500 mb-2">No CV-Job fit analysis available</div>
                  <div className="font-mono text-xs text-gray-400">
                    Fit analysis requires both a CV and a job specification to be uploaded for this candidate.
                    Upload documents on the Candidates page.
                  </div>
                </div>
              )}

              {/* Document Status */}
              <div className="border-2 border-black p-4">
                <div className="font-bold mb-3">Documents</div>
                <div className="space-y-3 font-mono text-sm">
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-3">
                      <span>{assessment.cv_uploaded ? '✅' : '❌'}</span>
                      <span>CV: {assessment.candidate_cv_filename || assessment.cv_filename || 'Not uploaded'}</span>
                    </div>
                    {(assessment.candidate_cv_filename || assessment.cv_filename) && (
                      <button
                        type="button"
                        className="border border-black px-2 py-1 text-xs hover:bg-black hover:text-white"
                        onClick={() => handleDownloadCandidateDoc('cv')}
                      >
                        Download
                      </button>
                    )}
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-3">
                      <span>{assessment.candidate_job_spec_filename ? '✅' : '❌'}</span>
                      <span>Job Specification: {assessment.candidate_job_spec_filename || 'Not uploaded'}</span>
                    </div>
                    {assessment.candidate_job_spec_filename && (
                      <button
                        type="button"
                        className="border border-black px-2 py-1 text-xs hover:bg-black hover:text-white"
                        onClick={() => handleDownloadCandidateDoc('job-spec')}
                      >
                        Download
                      </button>
                    )}
                  </div>
                </div>
              </div>
            </div>
          );
        })()}

        {activeTab === 'timeline' && (
          <div className="relative pl-8">
            <div className="absolute left-3 top-0 bottom-0 w-0.5" style={{ backgroundColor: '#9D00FF' }} />
            {candidate.timeline.map((t, i) => (
              <div key={i} className="relative mb-6 pl-8">
                <div
                  className="absolute -left-5 top-1 w-4 h-4 border-2 border-black"
                  style={{ backgroundColor: '#9D00FF' }}
                />
                <div className="font-mono text-xs text-gray-500 mb-1">{t.time}</div>
                <div className="font-bold">{t.event}</div>
                {t.prompt && (
                  <div className="font-mono text-sm text-gray-500 italic mt-1">&quot;{t.prompt}&quot;</div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
