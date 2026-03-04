import React, { useState, useEffect } from 'react';
import { ArrowLeft } from 'lucide-react';
import * as apiClient from '../../shared/api';
import { useToast } from '../../context/ToastContext';
import { getMetricMeta, buildGlossaryFromMetadata } from '../../lib/scoringGlossary';
import { normalizeScores } from '../../scoring/scoringDimensions';
import { ComparisonRadar } from '../../shared/ui/ComparisonRadar';
import {
  Badge,
  Button,
  Input,
  PageContainer,
  Panel,
  Sheet,
  Spinner,
  TabBar,
} from '../../shared/ui/TaaliPrimitives';
import { buildStandingCandidateReportModel } from './assessmentViewModels';
import {
  CandidateAiUsageTab,
  CandidateCodeGitTab,
  CandidateCvFitTab,
  CandidateTimelineTab,
} from './CandidateDetailSecondaryTabs';
import { CandidateEvaluateTab, CandidateResultsTab } from './CandidateDetailPrimaryTabs';
import { CandidateInterviewDebrief } from './CandidateInterviewDebrief';
import { CandidateReportView } from './CandidateReportView';

const RESULTS_ONBOARDING_KEY = 'taali_results_onboarding_seen_v1';

export const AssessmentResultsPage = ({
  candidate,
  onNavigate,
  onDeleted,
  onNoteAdded,
  NavComponent = null,
  backTo = { page: 'assessments', label: 'Back to Assessments' },
}) => {
  const { showToast } = useToast();
  const assessmentsApi = apiClient.assessments;
  const analyticsApi = apiClient.analytics;
  const candidatesApi = apiClient.candidates;
  const tasksApi = apiClient.tasks;
  const scoringApi = 'scoring' in apiClient ? apiClient.scoring : null;
  const [activeTab, setActiveTab] = useState('summary');
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

  const assessmentId = candidate?._raw?.id;
  const taskId = candidate?._raw?.task_id || candidate?._raw?.task?.id || null;
  const roleId = candidate?._raw?.role_id || null;
  const roleName = candidate?._raw?.role_name || null;
  const applicationStatus = candidate?._raw?.application_status || null;
  const normalizedStatus = String(candidate?._raw?.status || candidate?.status || '').toLowerCase();
  const canResendInvite = normalizedStatus === 'pending' || normalizedStatus === 'expired';
  const canGenerateInterviewGuide = normalizedStatus === 'completed' || normalizedStatus === 'completed_due_to_timeout';
  const isVoided = Boolean(candidate?._raw?.is_voided);
  const voidedAt = candidate?._raw?.voided_at || null;
  const voidReason = candidate?._raw?.void_reason || null;
  const supersededByAssessmentId = candidate?._raw?.superseded_by_assessment_id || null;
  const hasCvOnFile = Boolean(
    candidate?._raw?.candidate_cv_filename
    || candidate?._raw?.cv_filename
    || candidate?._raw?.cv_uploaded
  );
  const canRequestCvUpload = Boolean(!hasCvOnFile && assessmentId && candidate?.email);
  const reportModel = buildStandingCandidateReportModel({
    application: null,
    completedAssessment: candidate?._raw,
    identity: {
      assessmentId,
      sectionLabel: 'Assessment results',
      name: candidate?.name || 'Candidate',
      email: candidate?.email || '',
      position: candidate?.position || '',
      taskName: candidate?.task || '',
      roleName: roleName || '',
      applicationStatus: applicationStatus || '',
      durationLabel: candidate?.time || '',
      completedLabel: candidate?.completedDate || '',
    },
  });

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
      <PageContainer className="max-w-5xl" density="compact">
        <Panel className="p-4 font-mono text-sm text-[var(--taali-muted)]">
          Candidate assessment not found.
        </Panel>
      </PageContainer>
    );
  }

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

  const handleRequestCvUpload = async () => {
    if (!assessmentId) return;
    setBusyAction('request-cv');
    try {
      await assessmentsApi.resend(assessmentId);
      showToast('CV request sent. The candidate can upload their CV from the assessment link.', 'success');
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to send CV request.', 'error');
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
      onNavigate('assessments');
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
    { id: 'summary', label: 'Summary', panelId: 'candidate-tabpanel-summary' },
    { id: 'results', label: 'Results', panelId: 'candidate-tabpanel-results' },
    { id: 'ai-usage', label: 'AI Usage', panelId: 'candidate-tabpanel-ai-usage' },
    { id: 'cv-fit', label: 'CV & Fit', panelId: 'candidate-tabpanel-cv-fit' },
    { id: 'code-git', label: 'Code / Git', panelId: 'candidate-tabpanel-code-git' },
    { id: 'evaluate', label: 'Evaluate', panelId: 'candidate-tabpanel-evaluate' },
    { id: 'timeline', label: 'Timeline', panelId: 'candidate-tabpanel-timeline' },
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
      {NavComponent ? <NavComponent currentPage="assessments" onNavigate={onNavigate} /> : null}
      <PageContainer density="compact" width="wide">
        <Button
          variant="ghost"
          size="xs"
          className="mb-4 font-mono"
          onClick={() => onNavigate(backTo.page)}
        >
          <ArrowLeft size={16} /> {backTo.label}
        </Button>

        {isVoided ? (
        <Panel className="mb-3 border-[var(--taali-warning-border)] bg-[var(--taali-warning-soft)] p-3.5 text-sm text-[var(--taali-warning)]">
            <p className="font-semibold">This assessment was voided and superseded.</p>
            <p className="mt-1">
              {voidedAt ? `Voided ${new Date(voidedAt).toLocaleString()}. ` : ''}
              {voidReason ? `Reason: ${voidReason}. ` : ''}
              {supersededByAssessmentId ? `Superseded by assessment #${supersededByAssessmentId}.` : ''}
            </p>
          </Panel>
        ) : null}

        <Panel className="sticky top-20 z-20 mb-4 p-3 backdrop-blur-md">
          <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
            <TabBar tabs={topTabs} activeTab={activeTab} onChange={setActiveTab} density="compact" />
            <div className="flex flex-wrap items-center gap-2">
              {reportModel.source ? (
                <Badge variant={reportModel.source.badgeVariant} className="font-mono text-[11px]">
                  {reportModel.source.label}
                </Badge>
              ) : null}
              <Badge variant={reportModel.recommendation?.variant || 'muted'} className="font-mono text-[11px]">
                {reportModel.recommendation?.label || 'Pending review'}
              </Badge>
            </div>
          </div>
        </Panel>

        {activeTab === 'summary' ? (
          <div role="tabpanel" id="candidate-tabpanel-summary" aria-labelledby="summary" className="space-y-4">
            <CandidateReportView model={reportModel} />

            <Panel className="p-4">
              <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
                <div>
                  <div className="mb-1 text-xs font-semibold uppercase tracking-[0.1em] text-[var(--taali-muted)]">Client report and recruiter actions</div>
                  <p className="text-sm text-[var(--taali-muted)]">
                    Export a client-facing assessment brief, add recruiter notes, or move the candidate into the next system.
                  </p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Button type="button" size="sm" variant="secondary" onClick={handleDownloadReport} disabled={busyAction !== ''}>
                      {busyAction === 'report' ? 'Downloading...' : 'Download client report'}
                    </Button>
                    <Button type="button" size="sm" variant="secondary" onClick={handlePostToWorkable} disabled={busyAction !== ''}>
                      {busyAction === 'workable' ? 'Posting...' : 'Post to Workable'}
                    </Button>
                    {canResendInvite ? (
                      <Button type="button" size="sm" variant="secondary" onClick={handleResendInvite} disabled={busyAction !== ''}>
                        {busyAction === 'resend' ? 'Resending...' : 'Resend Invite'}
                      </Button>
                    ) : null}
                    {canRequestCvUpload ? (
                      <Button type="button" size="sm" variant="secondary" onClick={handleRequestCvUpload} disabled={busyAction !== ''}>
                        {busyAction === 'request-cv' ? 'Sending CV request...' : 'Request CV Upload'}
                      </Button>
                    ) : null}
                    <Button type="button" size="sm" variant="danger" onClick={handleDeleteAssessment} disabled={busyAction !== ''}>
                      {busyAction === 'delete' ? 'Deleting...' : 'Delete'}
                    </Button>
                  </div>

                  <div className="mt-4">
                    <div className="mb-2 text-xs font-semibold uppercase tracking-[0.1em] text-[var(--taali-muted)]">Recruiter notes</div>
                    <div className="flex gap-2">
                      <Input
                        type="text"
                        className="flex-1"
                        placeholder="Add note about this candidate"
                        value={noteText}
                        onChange={(e) => setNoteText(e.target.value)}
                      />
                      <Button type="button" size="sm" variant="secondary" onClick={handleAddNote} disabled={busyAction !== ''}>
                        {busyAction === 'note' ? 'Saving...' : 'Save Note'}
                      </Button>
                    </div>
                  </div>
                </div>

                <div className="rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface-subtle)] px-4 py-4">
                  <div className="text-xs font-semibold uppercase tracking-[0.1em] text-[var(--taali-muted)]">Workable status</div>
                  <div className="mt-3 text-sm text-[var(--taali-text)]">
                    <span className={workableStatus.posted ? 'font-semibold text-[var(--taali-success)]' : 'font-semibold text-[var(--taali-text)]'}>
                      {workableStatus.posted ? 'Posted' : 'Not posted'}
                    </span>
                    {workableStatus.postedAt ? ` on ${new Date(workableStatus.postedAt).toLocaleString()}` : ''}
                  </div>
                </div>
              </div>
            </Panel>
          </div>
        ) : null}

        {activeTab === 'results' ? (
          <div role="tabpanel" id="candidate-tabpanel-results" aria-labelledby="results">
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
          </div>
        ) : null}

        {activeTab === 'ai-usage' ? (
          <div role="tabpanel" id="candidate-tabpanel-ai-usage" aria-labelledby="ai-usage">
            <CandidateAiUsageTab candidate={candidate} avgCalibrationScore={avgCalibrationScore} />
          </div>
        ) : null}

        {activeTab === 'cv-fit' ? (
          <div role="tabpanel" id="candidate-tabpanel-cv-fit" aria-labelledby="cv-fit">
            <CandidateCvFitTab
              candidate={candidate}
              onDownloadCandidateDoc={handleDownloadCandidateDoc}
              onRequestCvUpload={canRequestCvUpload ? handleRequestCvUpload : null}
              requestingCvUpload={busyAction === 'request-cv'}
            />
          </div>
        ) : null}

        {activeTab === 'evaluate' ? (
          <div role="tabpanel" id="candidate-tabpanel-evaluate" aria-labelledby="evaluate">
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
          </div>
        ) : null}

        {activeTab === 'code-git' ? (
          <div role="tabpanel" id="candidate-tabpanel-code-git" aria-labelledby="code-git">
            <CandidateCodeGitTab candidate={candidate} />
          </div>
        ) : null}

        {activeTab === 'timeline' ? (
          <div role="tabpanel" id="candidate-tabpanel-timeline" aria-labelledby="timeline">
            <CandidateTimelineTab candidate={candidate} />
          </div>
        ) : null}

        <Sheet
          open={compareSheetOpen}
          onClose={() => setCompareSheetOpen(false)}
          title="Compare with other candidates"
          description="Select up to 4 completed assessments for the same role."
          footer={(
            <div className="flex items-center justify-between gap-3">
              <span className="font-mono text-xs text-[var(--taali-muted)]">{compareSelectedIds.length} selected</span>
              <Button type="button" size="sm" variant="secondary" onClick={() => setCompareSheetOpen(false)}>
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
              <Button type="button" size="sm" variant="secondary" onClick={() => setInterviewDebriefSheetOpen(false)}>
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
              <Button type="button" size="sm" variant="secondary" onClick={handleDismissResultsOnboarding}>
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

export const CandidateDetailPage = AssessmentResultsPage;
