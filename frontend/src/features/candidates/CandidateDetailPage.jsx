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
  Sheet,
  Spinner,
  cx,
} from '../../shared/ui/TaaliPrimitives';
import { ComparisonRadar } from '../../shared/ui/ComparisonRadar';
import {
  CandidateAiUsageTab,
  CandidateCodeGitTab,
  CandidateCvFitTab,
  CandidateTimelineTab,
} from './CandidateDetailSecondaryTabs';
import { CandidateEvaluateTab, CandidateResultsTab } from './CandidateDetailPrimaryTabs';
import { CandidateInterviewDebrief } from './CandidateInterviewDebrief';

const RESULTS_ONBOARDING_KEY = 'taali_results_onboarding_seen_v1';

export const CandidateDetailPage = ({
  candidate,
  onNavigate,
  onDeleted,
  onNoteAdded,
  NavComponent = null,
  backTo = { page: 'dashboard', label: 'Back to Assessments' },
}) => {
  const { showToast } = useToast();
  const assessmentsApi = apiClient.assessments;
  const analyticsApi = apiClient.analytics;
  const candidatesApi = apiClient.candidates;
  const tasksApi = apiClient.tasks;
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
  const [taskRubric, setTaskRubric] = useState(null);
  const [compareSheetOpen, setCompareSheetOpen] = useState(false);
  const [compareLoading, setCompareLoading] = useState(false);
  const [compareOptions, setCompareOptions] = useState([]);
  const [compareSelectedIds, setCompareSelectedIds] = useState([]);
  const [benchmarksLoading, setBenchmarksLoading] = useState(false);
  const [benchmarksData, setBenchmarksData] = useState(null);
  const [candidateFeedbackMeta, setCandidateFeedbackMeta] = useState({
    ready: Boolean(candidate?._raw?.candidate_feedback_ready),
    generatedAt: candidate?._raw?.candidate_feedback_generated_at || null,
    sentAt: candidate?._raw?.candidate_feedback_sent_at || null,
    url: candidate?._raw?.token ? `/assessment/${candidate._raw.token}/feedback` : null,
  });
  const [interviewDebriefSheetOpen, setInterviewDebriefSheetOpen] = useState(false);
  const [interviewDebriefLoading, setInterviewDebriefLoading] = useState(false);
  const [interviewDebriefData, setInterviewDebriefData] = useState(null);
  const [interviewDebriefCached, setInterviewDebriefCached] = useState(false);
  const [interviewDebriefGeneratedAt, setInterviewDebriefGeneratedAt] = useState(null);
  const [resultsOnboardingOpen, setResultsOnboardingOpen] = useState(false);

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
  const taskId = candidate?._raw?.task_id || candidate?._raw?.task?.id || null;
  const roleId = candidate?._raw?.role_id || null;
  const roleName = candidate?._raw?.role_name || null;
  const applicationStatus = candidate?._raw?.application_status || null;
  const normalizedStatus = String(candidate?._raw?.status || candidate?.status || '').toLowerCase();
  const canResendInvite = normalizedStatus === 'pending' || normalizedStatus === 'expired';
  const canGenerateInterviewGuide = normalizedStatus === 'completed' || normalizedStatus === 'completed_due_to_timeout';

  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (window.localStorage.getItem(RESULTS_ONBOARDING_KEY) === 'true') return;
    setResultsOnboardingOpen(true);
  }, []);

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
    let cancelled = false;
    const loadBenchmarks = async () => {
      if (!taskId || !analyticsApi?.benchmarks) {
        setBenchmarksData(null);
        return;
      }
      setBenchmarksLoading(true);
      try {
        const res = await analyticsApi.benchmarks(taskId, assessmentId ? { assessment_id: assessmentId } : {});
        if (!cancelled) setBenchmarksData(res?.data || null);
      } catch {
        if (!cancelled) setBenchmarksData(null);
      } finally {
        if (!cancelled) setBenchmarksLoading(false);
      }
    };
    loadBenchmarks();
    return () => {
      cancelled = true;
    };
  }, [analyticsApi, assessmentId, taskId]);

  useEffect(() => {
    let cancelled = false;
    const loadTaskRubric = async () => {
      if (!taskId || !tasksApi?.rubric) {
        setTaskRubric(null);
        return;
      }
      try {
        const res = await tasksApi.rubric(taskId);
        if (cancelled) return;
        const rubric = res?.data?.evaluation_rubric;
        setTaskRubric(rubric && typeof rubric === 'object' ? rubric : null);
      } catch {
        if (cancelled) return;
        setTaskRubric(null);
      }
    };
    loadTaskRubric();
    return () => {
      cancelled = true;
    };
  }, [taskId, tasksApi]);

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

  useEffect(() => {
    setCandidateFeedbackMeta({
      ready: Boolean(candidate?._raw?.candidate_feedback_ready),
      generatedAt: candidate?._raw?.candidate_feedback_generated_at || null,
      sentAt: candidate?._raw?.candidate_feedback_sent_at || null,
      url: candidate?._raw?.token ? `/assessment/${candidate._raw.token}/feedback` : null,
    });
  }, [
    candidate?._raw?.candidate_feedback_generated_at,
    candidate?._raw?.candidate_feedback_ready,
    candidate?._raw?.candidate_feedback_sent_at,
    candidate?._raw?.token,
  ]);

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

  const handleResendInvite = async () => {
    if (!assessmentId) return;
    setBusyAction('resend');
    try {
      await assessmentsApi.resend(assessmentId);
      showToast('Assessment invite resent.', 'success');
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to resend invite.', 'error');
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

  const handleFinalizeCandidateFeedback = async ({ resendEmail = false, forceRegenerate = false } = {}) => {
    if (!assessmentId) return;
    if (!assessmentsApi?.finalizeCandidateFeedback) {
      showToast('Candidate feedback endpoint is unavailable.', 'error');
      return;
    }
    setBusyAction('feedback-finalize');
    try {
      const res = await assessmentsApi.finalizeCandidateFeedback(assessmentId, {
        resend_email: resendEmail,
        force_regenerate: forceRegenerate,
        include_feedback: false,
      });
      const data = res?.data || {};
      setCandidateFeedbackMeta((prev) => ({
        ...prev,
        ready: Boolean(data.feedback_ready),
        generatedAt: data.feedback_generated_at || prev.generatedAt || null,
        sentAt: data.feedback_sent_at || prev.sentAt || null,
      }));
      if (data.email_dispatched) {
        showToast('Candidate feedback finalized and email sent.', 'success');
      } else {
        showToast('Candidate feedback finalized.', 'success');
      }
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to finalize candidate feedback.', 'error');
    } finally {
      setBusyAction('');
    }
  };

  const buildInterviewDebriefMarkdown = (debrief) => {
    if (!debrief || typeof debrief !== 'object') return '';
    if (typeof debrief.markdown === 'string' && debrief.markdown.trim()) return debrief.markdown;
    const lines = [
      `# Interview Guide - ${debrief.candidate_name || candidate.name}`,
      '',
      debrief.summary || '',
      '',
      '## Probing Questions',
    ];
    (debrief.probing_questions || []).forEach((item) => {
      lines.push(`### ${item.dimension || 'Dimension'} (${item.score ?? 'N/A'}/10)`);
      if (item.pattern) lines.push(item.pattern);
      if (item.question) lines.push(`- Question: ${item.question}`);
      if (item.what_to_listen_for) lines.push(`- What to listen for: ${item.what_to_listen_for}`);
      lines.push('');
    });
    lines.push('## Strengths To Validate');
    (debrief.strengths_to_validate || []).forEach((item) => lines.push(`- ${item.text || item.dimension_id || ''}`));
    lines.push('');
    lines.push('## Red Flags To Follow Up');
    (debrief.red_flags || []).forEach((item) => {
      lines.push(`- ${item.text || item.dimension_id || ''}`);
      if (item.follow_up_question) lines.push(`  - Follow-up: ${item.follow_up_question}`);
    });
    return lines.join('\n').trim();
  };

  const handleCopyInterviewDebriefMarkdown = async () => {
    const markdown = buildInterviewDebriefMarkdown(interviewDebriefData);
    if (!markdown) return;
    try {
      await navigator.clipboard.writeText(markdown);
      showToast('Interview guide copied to clipboard.', 'success');
    } catch {
      showToast('Failed to copy interview guide.', 'error');
    }
  };

  const handlePrintInterviewDebrief = () => {
    const markdown = buildInterviewDebriefMarkdown(interviewDebriefData);
    if (!markdown) return;
    const printWindow = window.open('', '_blank', 'noopener,noreferrer,width=900,height=700');
    if (!printWindow) {
      showToast('Pop-up blocked. Please allow pop-ups to print.', 'error');
      return;
    }
    const safeText = markdown
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
    printWindow.document.write(`
      <html>
        <head><title>Interview Guide</title></head>
        <body style="font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; padding: 24px; line-height: 1.5;">
          <pre style="white-space: pre-wrap; font-size: 13px;">${safeText}</pre>
        </body>
      </html>
    `);
    printWindow.document.close();
    printWindow.focus();
    printWindow.print();
  };

  const handleGenerateInterviewGuide = async ({ forceRegenerate = false } = {}) => {
    if (!assessmentId) return;
    if (!assessmentsApi?.generateInterviewDebrief) {
      showToast('Interview guide endpoint is unavailable.', 'error');
      return;
    }
    setInterviewDebriefSheetOpen(true);
    setInterviewDebriefLoading(true);
    try {
      const res = await assessmentsApi.generateInterviewDebrief(assessmentId, {
        force_regenerate: forceRegenerate,
      });
      const data = res?.data || {};
      setInterviewDebriefData(data.interview_debrief || null);
      setInterviewDebriefCached(Boolean(data.cached));
      setInterviewDebriefGeneratedAt(data.generated_at || null);
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to generate interview guide.', 'error');
      setInterviewDebriefData(null);
    } finally {
      setInterviewDebriefLoading(false);
    }
  };

  const handleOpenComparison = async () => {
    setCompareSheetOpen(true);
    setCompareLoading(true);
    setCompareSelectedIds([]);
    try {
      const params = { limit: 50, offset: 0 };
      if (roleId) params.role_id = roleId;
      const res = await assessmentsApi.list(params);
      const payload = res?.data || {};
      const items = Array.isArray(payload) ? payload : (payload.items || []);
      const normalized = items
        .filter((item) => Number(item.id) !== Number(assessmentId))
        .filter((item) => {
          const status = String(item.status || '').toLowerCase();
          return status === 'completed' || status === 'completed_due_to_timeout';
        })
        .map((item) => ({
          id: item.id,
          name: (item.candidate_name || item.candidate?.full_name || item.candidate_email || '').trim() || `Assessment ${item.id}`,
          role: item.role_name || item.task?.role || '',
          task: item.task_name || item.task?.name || '',
          score: item.score,
          _raw: item,
        }));
      setCompareOptions(normalized);
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to load candidates for comparison.', 'error');
      setCompareOptions([]);
    } finally {
      setCompareLoading(false);
    }
  };

  const handleDismissResultsOnboarding = () => {
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(RESULTS_ONBOARDING_KEY, 'true');
    }
    setResultsOnboardingOpen(false);
  };

  const toggleCompareCandidate = (candidateId, checked) => {
    setCompareSelectedIds((prev) => {
      if (checked) {
        if (prev.includes(candidateId) || prev.length >= 4) return prev;
        return [...prev, candidateId];
      }
      return prev.filter((id) => Number(id) !== Number(candidateId));
    });
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

  const selectedComparisonCandidates = compareOptions.filter((item) =>
    compareSelectedIds.some((id) => Number(id) === Number(item.id))
  );
  const comparisonSeries = [
    {
      id: assessmentId,
      name: candidate.name,
      score: candidate.score,
      _raw: candidate?._raw || {},
      breakdown: candidate?.breakdown || null,
    },
    ...selectedComparisonCandidates,
  ];

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="dashboard" onNavigate={onNavigate} /> : null}
      <PageContainer>
        <Button
          variant="ghost"
          size="sm"
          className="mb-5 font-mono"
          onClick={() => onNavigate(backTo.page)}
        >
          <ArrowLeft size={16} /> {backTo.label}
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
            {canResendInvite ? (
              <Button type="button" variant="secondary" className="font-mono" onClick={handleResendInvite} disabled={busyAction !== ''}>
                {busyAction === 'resend' ? 'Resending...' : 'Resend Invite'}
              </Button>
            ) : null}
            <Button type="button" variant="danger" className="font-mono" onClick={handleDeleteAssessment} disabled={busyAction !== ''}>
              {busyAction === 'delete' ? 'Deleting...' : 'Delete'}
            </Button>
          </div>
        </Panel>

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
            onOpenComparison={handleOpenComparison}
            onOpenOnboarding={() => setResultsOnboardingOpen(true)}
            onGenerateInterviewGuide={handleGenerateInterviewGuide}
            interviewGuideLoading={interviewDebriefLoading}
            canGenerateInterviewGuide={canGenerateInterviewGuide}
            benchmarksLoading={benchmarksLoading}
            benchmarksData={benchmarksData}
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
            evaluationRubric={taskRubric || candidate?._raw?.evaluation_rubric || null}
            assessmentId={assessmentId}
            aiEvalSuggestion={aiEvalSuggestion}
            onGenerateAiSuggestions={handleGenerateAiSuggestions}
            aiEvalLoading={busyAction === 'ai-eval'}
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
            onFinalizeCandidateFeedback={handleFinalizeCandidateFeedback}
            finalizeFeedbackLoading={busyAction === 'feedback-finalize'}
            candidateFeedbackReady={candidateFeedbackMeta.ready}
            candidateFeedbackSentAt={candidateFeedbackMeta.sentAt}
            canFinalizeCandidateFeedback={canGenerateInterviewGuide}
          />
        ) : null}

        {activeTab === 'code-git' ? <CandidateCodeGitTab candidate={candidate} /> : null}

        {activeTab === 'timeline' ? <CandidateTimelineTab candidate={candidate} /> : null}

        <Sheet
          open={compareSheetOpen}
          onClose={() => setCompareSheetOpen(false)}
          title="Compare with other candidates"
          description="Select up to 4 completed assessments for the same role."
          footer={(
            <div className="flex items-center justify-between gap-3">
              <span className="font-mono text-xs text-[var(--taali-muted)]">{compareSelectedIds.length} selected</span>
              <Button type="button" variant="secondary" onClick={() => setCompareSheetOpen(false)}>
                Close
              </Button>
            </div>
          )}
        >
          {compareLoading ? (
            <div className="flex items-center gap-2 text-sm text-[var(--taali-muted)]">
              <Spinner size={16} />
              Loading candidates...
            </div>
          ) : (
            <div className="space-y-4">
              {compareOptions.length === 0 ? (
                <Panel className="p-4 text-sm text-[var(--taali-muted)]">
                  No completed assessments found for comparison.
                </Panel>
              ) : (
                <div className="max-h-56 overflow-auto border border-[var(--taali-border)]">
                  {compareOptions.map((option) => {
                    const checked = compareSelectedIds.some((id) => Number(id) === Number(option.id));
                    return (
                      <label key={option.id} className="flex items-start gap-3 px-3 py-2 border-b border-[var(--taali-border-muted)] last:border-b-0">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={(event) => toggleCompareCandidate(option.id, event.target.checked)}
                          disabled={!checked && compareSelectedIds.length >= 4}
                        />
                        <div className="min-w-0">
                          <div className="font-medium text-[var(--taali-text)]">{option.name}</div>
                          <div className="font-mono text-xs text-[var(--taali-muted)]">
                            {option.task}{option.role ? ` · ${option.role}` : ''}{option.score != null ? ` · ${option.score}/10` : ''}
                          </div>
                        </div>
                      </label>
                    );
                  })}
                </div>
              )}

              <Panel className="p-3">
                <ComparisonRadar
                  assessments={comparisonSeries}
                  highlightAssessmentId={assessmentId}
                />
              </Panel>
              {comparisonSeries.length < 2 ? (
                <div className="text-xs text-[var(--taali-muted)]">
                  Add a second candidate from the same role to overlay profiles side-by-side.
                </div>
              ) : null}
            </div>
          )}
        </Sheet>

        <Sheet
          open={interviewDebriefSheetOpen}
          onClose={() => setInterviewDebriefSheetOpen(false)}
          title={`Interview Guide - ${candidate.name}`}
          description="Generated from TAALI assessment evidence."
          footer={(
            <div className="flex items-center justify-end gap-2">
              <Button type="button" variant="secondary" onClick={() => setInterviewDebriefSheetOpen(false)}>
                Close
              </Button>
            </div>
          )}
        >
          <CandidateInterviewDebrief
            debrief={interviewDebriefData}
            loading={interviewDebriefLoading}
            cached={interviewDebriefCached}
            generatedAt={interviewDebriefGeneratedAt}
            onCopyMarkdown={handleCopyInterviewDebriefMarkdown}
            onPrint={handlePrintInterviewDebrief}
            onRegenerate={() => handleGenerateInterviewGuide({ forceRegenerate: true })}
          />
        </Sheet>

        <Sheet
          open={resultsOnboardingOpen}
          onClose={handleDismissResultsOnboarding}
          title="What does this score mean?"
          description="A quick guide to reading TAALI results."
          footer={(
            <div className="flex items-center justify-end gap-2">
              <Button type="button" variant="secondary" onClick={handleDismissResultsOnboarding}>
                Got it
              </Button>
            </div>
          )}
        >
          <div className="space-y-4">
            <Panel className="p-3">
              <div className="font-bold text-sm text-[var(--taali-text)] mb-1">1. What TAALI measures</div>
              <p className="text-sm text-[var(--taali-text)]">
                TAALI measures how candidates collaborate with AI while delivering real engineering work, not just final code output.
              </p>
            </Panel>
            <Panel className="p-3">
              <div className="font-bold text-sm text-[var(--taali-text)] mb-1">2. The 8 dimensions</div>
              <p className="text-sm text-[var(--taali-text)]">
                Each dimension captures one behavior signal: task delivery, prompt quality, context sharing, independence, utilization, debugging/design, communication, and role fit.
              </p>
            </Panel>
            <Panel className="p-3">
              <div className="font-bold text-sm text-[var(--taali-text)] mb-1">3. What to prioritize</div>
              <p className="text-sm text-[var(--taali-text)]">
                Strong candidates usually score well on Independence &amp; Efficiency and Context Provision, then maintain consistency across the remaining dimensions.
              </p>
            </Panel>
            <Panel className="p-3">
              <div className="font-bold text-sm text-[var(--taali-text)] mb-1">4. How to use this</div>
              <p className="text-sm text-[var(--taali-text)]">
                Use these scores to guide targeted interview questions and evidence review. They are decision support, not automatic hiring decisions.
              </p>
            </Panel>
          </div>
        </Sheet>
      </PageContainer>
    </div>
  );
};
