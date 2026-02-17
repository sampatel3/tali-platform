import React, { useState, useEffect } from 'react';
import { ArrowLeft } from 'lucide-react';
import * as apiClient from '../../shared/api';
import { useToast } from '../../context/ToastContext';
import { getMetricMeta, buildGlossaryFromMetadata } from '../../lib/scoringGlossary';
import { dimensionOrder, getDimensionById, normalizeScores } from '../../scoring/scoringDimensions';
import {
  Badge,
  Button,
  Input,
  PageContainer,
  Panel,
  cx,
} from '../../shared/ui/TaaliPrimitives';
import {
  CandidateAiUsageTab,
  CandidateCodeGitTab,
  CandidateCvFitTab,
  CandidateTimelineTab,
} from './CandidateDetailSecondaryTabs';
import { CandidateEvaluateTab, CandidateResultsTab } from './CandidateDetailPrimaryTabs';

export const CandidateDetailPage = ({ candidate, onNavigate, onDeleted, onNoteAdded, NavComponent = null }) => {
  const { showToast } = useToast();
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

  const toLineList = (value) => String(value || '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);

  const getRecommendation = (score100) => {
    if (score100 >= 80) return { label: 'STRONG HIRE', color: 'var(--taali-success)' };
    if (score100 >= 65) return { label: 'HIRE', color: 'var(--taali-info)' };
    if (score100 >= 50) return { label: 'CONSIDER', color: 'var(--taali-warning)' };
    return { label: 'NOT RECOMMENDED', color: 'var(--taali-danger)' };
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
    const rawScores = breakdownScores
      || scoreBreakdownScores
      || analyticsCategoryScores
      || analyticsAiScores
      || legacyFlatBreakdownScores
      || {};
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
      showToast(err?.response?.data?.detail || 'Failed to download report', 'error');
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
      showToast(res?.data?.already_posted ? 'Already posted to Workable' : 'Posted to Workable', 'success');
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to post to Workable', 'error');
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
      showToast('AI suggestions generated. Human reviewer must confirm final scores.', 'success');
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to generate AI suggestions', 'error');
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
      showToast(err?.response?.data?.detail || 'Failed to delete assessment', 'error');
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
      a.download = docType === 'cv'
        ? (candidate._raw?.candidate_cv_filename || 'candidate-cv')
        : (candidate._raw?.candidate_job_spec_filename || 'job-spec');
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to download document', 'error');
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
      showToast('Note added', 'success');
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to add note', 'error');
    } finally {
      setBusyAction('');
    }
  };

  const topTabs = [
    { id: 'results', label: 'Results' },
    { id: 'ai-usage', label: 'AI Usage' },
    { id: 'cv-fit', label: 'CV & Fit' },
    { id: 'code-git', label: 'Code / Git' },
    { id: 'evaluate', label: 'Evaluate' },
    { id: 'timeline', label: 'Timeline' },
  ];

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="dashboard" onNavigate={onNavigate} /> : null}
      <PageContainer>
        <Button
          variant="ghost"
          size="sm"
          className="mb-5 font-mono"
          onClick={() => onNavigate('dashboard')}
        >
          <ArrowLeft size={16} /> Back to Dashboard
        </Button>

        <Panel className="mb-6 p-5">
          <div className="grid gap-6 md:grid-cols-[minmax(0,1fr)_340px]">
            <div>
              <h1 className="text-4xl font-bold text-gray-900">{candidate.name}</h1>
              <p className="mb-4 font-mono text-gray-500">{candidate.email}</p>
              <div className="flex flex-wrap gap-2">
                <Badge variant="muted" className="font-mono text-[11px]">{candidate.position}</Badge>
                <Badge variant="muted" className="font-mono text-[11px]">Task: {candidate.task}</Badge>
                {roleName ? <Badge variant="muted" className="font-mono text-[11px]">Role: {roleName}</Badge> : null}
                {applicationStatus ? <Badge variant="muted" className="font-mono text-[11px]">Application: {applicationStatus}</Badge> : null}
                <Badge variant="muted" className="font-mono text-[11px]">Duration: {candidate.time}</Badge>
                {candidate.completedDate ? <Badge variant="muted" className="font-mono text-[11px]">Completed: {candidate.completedDate}</Badge> : null}
              </div>
            </div>

            {(score100 != null || candidate.score) ? (
              <div className="border-2 border-[var(--taali-purple)] bg-[#151122] p-5 text-white">
                <div className="mb-1 text-5xl font-bold text-[var(--taali-purple)]">
                  {score100 != null ? `${Math.round(score100)}` : candidate.score}
                  <span className="text-lg text-gray-400">/{score100 != null ? '100' : '10'}</span>
                </div>

                {rec ? (
                  <div className="mb-3 inline-flex px-3 py-1 font-mono text-xs font-bold text-white" style={{ backgroundColor: rec.color }}>
                    {rec.label}
                  </div>
                ) : null}

                {Object.keys(headerCategoryScores).length > 0 ? (
                  <div className="space-y-1.5 font-mono text-xs">
                    {dimensionOrder.map((key) => {
                      const val = headerCategoryScores[key];
                      const label = getDimensionById(key).label;
                      return val != null ? (
                        <div key={key} className="flex items-center gap-2">
                          <span className="w-36 truncate text-gray-400">{label}</span>
                          <div className="h-1.5 flex-1 overflow-hidden bg-gray-700">
                            <div
                              className="h-full"
                              style={{
                                width: `${(val / 10) * 100}%`,
                                backgroundColor: val >= 7 ? '#16a34a' : val >= 5 ? '#d97706' : '#dc2626',
                              }}
                            />
                          </div>
                          <span className="w-7 text-right">{val}</span>
                        </div>
                      ) : null;
                    })}
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        </Panel>

        <Panel className="mb-4 p-3">
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="secondary" className="font-mono" onClick={handleDownloadReport} disabled={busyAction !== ''}>
              {busyAction === 'report' ? 'Downloading...' : 'Download PDF'}
            </Button>
            <Button type="button" variant="secondary" className="font-mono" onClick={handlePostToWorkable} disabled={busyAction !== ''}>
              {busyAction === 'workable' ? 'Posting...' : 'Post to Workable'}
            </Button>
            {import.meta.env.VITE_AI_ASSISTED_EVAL_ENABLED === 'true' ? (
              <Button type="button" variant="secondary" className="font-mono" onClick={handleGenerateAiSuggestions} disabled={busyAction !== ''}>
                {busyAction === 'ai-eval' ? 'Generating...' : 'Generate AI suggestions'}
              </Button>
            ) : null}
            <Button type="button" variant="danger" className="font-mono" onClick={handleDeleteAssessment} disabled={busyAction !== ''}>
              {busyAction === 'delete' ? 'Deleting...' : 'Delete'}
            </Button>
          </div>
        </Panel>

        {aiEvalSuggestion ? (
          <Panel className="mb-4 bg-[var(--taali-purple-soft)] p-3">
            <div className="mb-1 font-mono text-xs font-bold">AI-assisted suggestions (V2, reviewer final)</div>
            <div className="font-mono text-xs text-gray-700">{aiEvalSuggestion.message}</div>
          </Panel>
        ) : null}

        <Panel className="mb-4 bg-[#faf8ff] p-3">
          <div className="font-mono text-xs">
            <span className="text-gray-500">Workable status:</span>{' '}
            <span className={workableStatus.posted ? 'font-bold text-green-700' : 'text-gray-700'}>
              {workableStatus.posted ? 'Posted' : 'Not posted'}
            </span>
            {workableStatus.postedAt ? (
              <span className="text-gray-500"> on {new Date(workableStatus.postedAt).toLocaleString()}</span>
            ) : null}
          </div>
        </Panel>

        <Panel className="mb-6 p-4">
          <div className="mb-2 font-mono text-xs text-gray-500">Recruiter Notes</div>
          <div className="flex gap-2">
            <Input
              type="text"
              className="flex-1 font-mono"
              placeholder="Add note about this candidate"
              value={noteText}
              onChange={(e) => setNoteText(e.target.value)}
            />
            <Button type="button" variant="secondary" className="font-mono" onClick={handleAddNote} disabled={busyAction !== ''}>
              {busyAction === 'note' ? 'Saving...' : 'Save Note'}
            </Button>
          </div>
        </Panel>

        <Panel className="mb-6 overflow-hidden p-0">
          <div className="flex flex-wrap">
            {topTabs.map((tab, index) => (
              <button
                key={tab.id}
                type="button"
                className={cx(
                  'min-w-[108px] flex-1 px-4 py-3 font-mono text-sm font-bold transition-colors',
                  index < topTabs.length - 1 ? 'border-r border-[#e7e3f4]' : '',
                  activeTab === tab.id ? 'bg-[var(--taali-purple)] text-white' : 'bg-white hover:bg-[#faf8ff]'
                )}
                onClick={() => setActiveTab(tab.id)}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </Panel>

        {activeTab === 'results' ? (
          <CandidateResultsTab
            candidate={candidate}
            expandedCategory={expandedCategory}
            setExpandedCategory={setExpandedCategory}
            getCategoryScores={getCategoryScores}
            getMetricMetaResolved={getMetricMetaResolved}
          />
        ) : null}

        {activeTab === 'ai-usage' ? (
          <CandidateAiUsageTab candidate={candidate} avgCalibrationScore={avgCalibrationScore} />
        ) : null}

        {activeTab === 'cv-fit' ? (
          <CandidateCvFitTab candidate={candidate} onDownloadCandidateDoc={handleDownloadCandidateDoc} />
        ) : null}

        {activeTab === 'evaluate' ? (
          <CandidateEvaluateTab
            candidate={candidate}
            assessmentId={assessmentId}
            manualEvalScores={manualEvalScores}
            setManualEvalScores={setManualEvalScores}
            manualEvalStrengths={manualEvalStrengths}
            setManualEvalStrengths={setManualEvalStrengths}
            manualEvalImprovements={manualEvalImprovements}
            setManualEvalImprovements={setManualEvalImprovements}
            manualEvalSummary={manualEvalSummary}
            setManualEvalSummary={setManualEvalSummary}
            manualEvalSaving={manualEvalSaving}
            setManualEvalSaving={setManualEvalSaving}
            toLineList={toLineList}
            toEvidenceTextareaValue={toEvidenceTextareaValue}
            assessmentsApi={assessmentsApi}
          />
        ) : null}

        {activeTab === 'code-git' ? <CandidateCodeGitTab candidate={candidate} /> : null}

        {activeTab === 'timeline' ? <CandidateTimelineTab candidate={candidate} /> : null}
      </PageContainer>
    </div>
  );
};
