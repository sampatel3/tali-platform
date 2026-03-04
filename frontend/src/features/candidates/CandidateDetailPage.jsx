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
import { CandidateAssessmentSummaryView } from './CandidateAssessmentSummaryView';
import { buildClientReportFilenameStem } from './clientReportUtils';
import {
  CandidateAiUsageTab,
  CandidateCodeGitTab,
  CandidateCvFitTab,
  CandidateTimelineTab,
} from './CandidateDetailSecondaryTabs';
import { CandidateResultsTab } from './CandidateDetailPrimaryTabs';
import { CandidateInterviewDebrief } from './CandidateInterviewDebrief';

const RESULTS_ONBOARDING_KEY = 'taali_results_onboarding_seen_v1';
const INTERVIEW_GUIDANCE_TIMEOUT_MS = 15000;

const uniqueItems = (items, limit = 4) => Array.from(
  new Set((Array.isArray(items) ? items : []).filter(Boolean))
).slice(0, limit);

const withRequestTimeout = (promise, timeoutMs, timeoutMessage) => new Promise((resolve, reject) => {
  const timeoutId = window.setTimeout(() => {
    reject(new Error(timeoutMessage));
  }, timeoutMs);

  promise
    .then((value) => {
      window.clearTimeout(timeoutId);
      resolve(value);
    })
    .catch((error) => {
      window.clearTimeout(timeoutId);
      reject(error);
    });
});

const CandidateInterviewGuidanceTab = ({
  canGenerateInterviewGuide,
  debrief,
  loading,
  errorMessage = '',
  cached,
  generatedAt,
  onGenerateInterviewGuide,
  onCopyMarkdown,
  onPrint,
  noteText,
  onNoteTextChange,
  onSaveNote,
  busyAction,
}) => (
  <div className="space-y-4">
    <Panel className="p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Interview guidance</div>
          <div className="mt-2 text-xl font-semibold text-[var(--taali-text)]">
            Probe role-fit claims and assessment gaps together.
          </div>
          <p className="mt-2 text-sm leading-6 text-[var(--taali-muted)]">
            This pack is built from the job spec, role-fit evidence, assessment results, and TAALI signals so the interview can focus on validation instead of re-screening.
          </p>
        </div>
        {canGenerateInterviewGuide ? (
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={() => onGenerateInterviewGuide({ forceRegenerate: Boolean(debrief) })}
            disabled={loading}
          >
            {loading ? 'Loading guidance...' : (errorMessage ? 'Retry guidance' : (debrief ? 'Refresh guidance' : 'Generate guidance'))}
          </Button>
        ) : null}
      </div>
    </Panel>

    {errorMessage ? (
      <Panel className="border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-4">
        <div className="text-sm font-semibold text-[var(--taali-danger)]">Interview guidance could not be loaded.</div>
        <p className="mt-2 text-sm text-[var(--taali-text)]">{errorMessage}</p>
      </Panel>
    ) : null}

    <CandidateInterviewDebrief
      debrief={debrief}
      loading={loading}
      cached={cached}
      generatedAt={generatedAt}
      onCopyMarkdown={onCopyMarkdown}
      onPrint={onPrint}
      onRegenerate={() => onGenerateInterviewGuide({ forceRegenerate: true })}
    />

    <Panel className="p-4">
      <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Recruiter feedback</div>
      <p className="mt-2 text-sm leading-6 text-[var(--taali-muted)]">
        Add interview follow-up notes here. Saved notes are appended to the candidate timeline for the team.
      </p>
      <div className="mt-4 flex flex-col gap-3 md:flex-row">
        <Input
          type="text"
          className="flex-1"
          placeholder="Add recruiter feedback from the interview"
          value={noteText}
          onChange={(event) => onNoteTextChange(event.target.value)}
        />
        <Button type="button" size="sm" variant="secondary" onClick={onSaveNote} disabled={busyAction !== ''}>
          {busyAction === 'note' ? 'Saving...' : 'Save feedback'}
        </Button>
      </div>
    </Panel>
  </div>
);

const CandidateClientReportTab = ({
  busyAction,
  handleDownloadReport,
  handlePostToWorkable,
  handleResendInvite,
  handleRequestCvUpload,
  handleDeleteAssessment,
  canResendInvite,
  canRequestCvUpload,
  workableStatus,
}) => (
  <div className="space-y-4">
    <Panel className="p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Client report</div>
          <div className="mt-2 text-xl font-semibold text-[var(--taali-text)]">Download the employer-facing assessment brief.</div>
          <p className="mt-2 text-sm leading-6 text-[var(--taali-muted)]">
            Export the TAALI client report PDF for employer or client review.
          </p>
        </div>
        <Button type="button" size="sm" variant="secondary" onClick={handleDownloadReport} disabled={busyAction !== ''}>
          {busyAction === 'report' ? 'Downloading...' : 'Download client report'}
        </Button>
      </div>
    </Panel>

    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
      <Panel className="p-4">
        <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Recruiter actions</div>
        <div className="mt-4 flex flex-wrap gap-2">
          <Button type="button" size="sm" variant="secondary" onClick={handlePostToWorkable} disabled={busyAction !== ''}>
            {busyAction === 'workable' ? 'Posting...' : 'Post to Workable'}
          </Button>
          {canResendInvite ? (
            <Button type="button" size="sm" variant="secondary" onClick={handleResendInvite} disabled={busyAction !== ''}>
              {busyAction === 'resend' ? 'Resending...' : 'Resend invite'}
            </Button>
          ) : null}
          {canRequestCvUpload ? (
            <Button type="button" size="sm" variant="secondary" onClick={handleRequestCvUpload} disabled={busyAction !== ''}>
              {busyAction === 'request-cv' ? 'Sending CV request...' : 'Request CV upload'}
            </Button>
          ) : null}
          <Button type="button" size="sm" variant="danger" onClick={handleDeleteAssessment} disabled={busyAction !== ''}>
            {busyAction === 'delete' ? 'Deleting...' : 'Delete assessment'}
          </Button>
        </div>
      </Panel>

      <Panel className="p-4">
        <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Workable status</div>
        <div className="mt-3 text-sm text-[var(--taali-text)]">
          <span className={workableStatus.posted ? 'font-semibold text-[var(--taali-success)]' : 'font-semibold text-[var(--taali-text)]'}>
            {workableStatus.posted ? 'Posted' : 'Not posted'}
          </span>
          {workableStatus.postedAt ? ` on ${new Date(workableStatus.postedAt).toLocaleString()}` : ''}
        </div>
      </Panel>
    </div>
  </div>
);

const SourceDocumentRow = ({ label, filename, onDownload = null }) => (
  <Panel className="p-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div>
        <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">{label}</div>
        <div className="mt-2 text-sm text-[var(--taali-text)]">{filename || 'Not available'}</div>
      </div>
      {filename && typeof onDownload === 'function' ? (
        <Button type="button" variant="secondary" size="sm" onClick={onDownload}>
          Download
        </Button>
      ) : null}
    </div>
  </Panel>
);

const CandidateSourceDocumentsTab = ({
  candidate = null,
  application = null,
  reportModel,
  onDownloadCandidateDoc,
}) => {
  const assessment = candidate?._raw || null;
  const sourceRecord = assessment || application || {};
  const documentEvidence = reportModel?.evidenceSections?.documents || null;
  const cvFilename = sourceRecord.candidate_cv_filename || sourceRecord.cv_filename || null;
  const jobSpecFilename = sourceRecord.candidate_job_spec_filename || sourceRecord.role_job_spec_filename || null;
  const sourceItems = uniqueItems(documentEvidence?.items || [], 6);

  return (
    <div className="space-y-4">
      <Panel className="p-4">
        <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Source documents</div>
        <div className="mt-2 text-xl font-semibold text-[var(--taali-text)]">Source material behind this review.</div>
        {documentEvidence?.description ? (
          <p className="mt-2 text-sm leading-6 text-[var(--taali-muted)]">{documentEvidence.description}</p>
        ) : null}
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <SourceDocumentRow
          label="Candidate CV"
          filename={cvFilename}
          onDownload={cvFilename ? () => onDownloadCandidateDoc('cv') : null}
        />
        <SourceDocumentRow
          label="Job specification"
          filename={jobSpecFilename}
          onDownload={jobSpecFilename ? () => onDownloadCandidateDoc('job-spec') : null}
        />
      </div>

      <Panel className="p-4">
        <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Source information list</div>
        {sourceItems.length ? (
          <ul className="mt-3 space-y-2">
            {sourceItems.map((item) => (
              <li key={item} className="flex gap-2 text-sm text-[var(--taali-text)]">
                <span className="mt-1 h-1.5 w-1.5 rounded-full bg-[var(--taali-purple)]" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-3 text-sm text-[var(--taali-muted)]">
            {documentEvidence?.emptyMessage || 'No source documents are available for this candidate yet.'}
          </p>
        )}
      </Panel>
    </div>
  );
};

const buildAssessmentPendingMessage = ({ application, reportModel, label }) => {
  const assessmentHistory = Array.isArray(application?.assessment_history) ? application.assessment_history : [];
  const latestAttempt = assessmentHistory[0] || null;
  const latestStatus = String(
    latestAttempt?.status
    || application?.score_summary?.assessment_status
    || application?.valid_assessment_status
    || ''
  ).trim();
  const latestStatusLabel = latestStatus ? latestStatus.replace(/_/g, ' ') : 'pending assessment';
  const roleName = reportModel?.identity?.roleName || application?.role_name || 'this role';

  return {
    title: `${label} will populate after the assessment is completed.`,
    description: latestAttempt
      ? `Latest attempt is currently ${latestStatusLabel}. TAALI will attach this tab once a completed assessment is available for ${roleName}.`
      : `No completed assessment is available for ${roleName} yet. Send or complete an assessment to unlock this tab.`,
  };
};

const PendingAssessmentTab = ({ title, description }) => (
  <Panel className="p-5">
    <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Pending assessment</div>
    <div className="mt-2 text-xl font-semibold text-[var(--taali-text)]">{title}</div>
    <p className="mt-3 text-sm leading-6 text-[var(--taali-muted)]">{description}</p>
  </Panel>
);

export const AssessmentResultsPage = ({
  candidate,
  application = null,
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
  const [metricGlossary, setMetricGlossary] = useState({});
  const [compareSheetOpen, setCompareSheetOpen] = useState(false);
  const [compareLoading, setCompareLoading] = useState(false);
  const [compareOptions, setCompareOptions] = useState([]);
  const [compareSelectedIds, setCompareSelectedIds] = useState([]);
  const [benchmarksLoading, setBenchmarksLoading] = useState(false);
  const [benchmarksData, setBenchmarksData] = useState(null);
  const [interviewDebriefLoading, setInterviewDebriefLoading] = useState(false);
  const [interviewDebriefData, setInterviewDebriefData] = useState(null);
  const [interviewDebriefError, setInterviewDebriefError] = useState('');
  const [interviewDebriefAutoRequested, setInterviewDebriefAutoRequested] = useState(false);
  const [interviewDebriefCached, setInterviewDebriefCached] = useState(false);
  const [interviewDebriefGeneratedAt, setInterviewDebriefGeneratedAt] = useState(null);
  const [resultsOnboardingOpen, setResultsOnboardingOpen] = useState(false);

  const completedAssessment = candidate?._raw || null;
  const applicationRecord = application && typeof application === 'object' ? application : null;
  const assessmentId = completedAssessment?.id || applicationRecord?.score_summary?.assessment_id || applicationRecord?.valid_assessment_id || null;
  const taskId = completedAssessment?.task_id || completedAssessment?.task?.id || null;
  const roleId = completedAssessment?.role_id || applicationRecord?.role_id || null;
  const roleName = completedAssessment?.role_name || applicationRecord?.role_name || null;
  const applicationStatus = completedAssessment?.application_status || applicationRecord?.status || null;
  const normalizedStatus = String(
    completedAssessment?.status
    || candidate?.status
    || applicationRecord?.score_summary?.assessment_status
    || applicationRecord?.valid_assessment_status
    || applicationRecord?.status
    || ''
  ).toLowerCase();
  const canResendInvite = normalizedStatus === 'pending' || normalizedStatus === 'expired';
  const canGenerateInterviewGuide = Boolean(completedAssessment) && (
    normalizedStatus === 'completed' || normalizedStatus === 'completed_due_to_timeout'
  );
  const isVoided = Boolean(completedAssessment?.is_voided);
  const voidedAt = completedAssessment?.voided_at || null;
  const voidReason = completedAssessment?.void_reason || null;
  const supersededByAssessmentId = completedAssessment?.superseded_by_assessment_id || null;
  const hasCvOnFile = Boolean(
    completedAssessment?.candidate_cv_filename
    || completedAssessment?.cv_filename
    || completedAssessment?.cv_uploaded
    || applicationRecord?.candidate_cv_filename
    || applicationRecord?.cv_filename
    || applicationRecord?.cv_uploaded
  );
  const canRequestCvUpload = Boolean(!hasCvOnFile && assessmentId && (candidate?.email || applicationRecord?.candidate_email));
  const reportModel = buildStandingCandidateReportModel({
    application: applicationRecord,
    completedAssessment,
    identity: {
      assessmentId,
      sectionLabel: 'Assessment results',
      name: candidate?.name || applicationRecord?.candidate_name || applicationRecord?.candidate_email || 'Candidate',
      email: candidate?.email || applicationRecord?.candidate_email || '',
      position: candidate?.position || applicationRecord?.candidate_position || '',
      taskName: candidate?.task || '',
      roleName: roleName || '',
      applicationStatus: applicationStatus || '',
      durationLabel: candidate?.time || '',
      completedLabel: candidate?.completedDate || '',
    },
  });
  const hasCompletedAssessment = Boolean(reportModel?.hasCompletedAssessment);
  const pendingAssessmentResults = buildAssessmentPendingMessage({
    application: applicationRecord,
    reportModel,
    label: 'Assessment results',
  });
  const pendingInterviewGuidance = buildAssessmentPendingMessage({
    application: applicationRecord,
    reportModel,
    label: 'Interview guidance',
  });
  const pendingClientReport = buildAssessmentPendingMessage({
    application: applicationRecord,
    reportModel,
    label: 'Client report',
  });

  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (!hasCompletedAssessment) return;
    if (window.localStorage.getItem(RESULTS_ONBOARDING_KEY) === 'true') return;
    setResultsOnboardingOpen(true);
  }, [hasCompletedAssessment]);

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

  if (!candidate && !applicationRecord) {
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
      const blob = new Blob([res.data], {
        type: res?.headers?.['content-type'] || 'application/pdf',
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${buildClientReportFilenameStem(roleName, candidate?.name)}.pdf`;
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
    setInterviewDebriefError('');
    setInterviewDebriefAutoRequested(true);
    setInterviewDebriefLoading(true);
    try {
      const res = await withRequestTimeout(
        assessmentsApi.generateInterviewDebrief(assessmentId, {
          force_regenerate: forceRegenerate,
        }),
        INTERVIEW_GUIDANCE_TIMEOUT_MS,
        'Interview guidance is taking longer than expected. Please retry.'
      );
      const data = res?.data || {};
      setInterviewDebriefData(data.interview_debrief || null);
      setInterviewDebriefCached(Boolean(data.cached));
      setInterviewDebriefGeneratedAt(data.generated_at || null);
    } catch (err) {
      const detail = err?.response?.data?.detail || err?.message || 'Failed to generate interview guide.';
      showToast(detail, 'error');
      setInterviewDebriefError(detail);
      setInterviewDebriefData(null);
    } finally {
      setInterviewDebriefLoading(false);
    }
  };

  useEffect(() => {
    setInterviewDebriefData(null);
    setInterviewDebriefError('');
    setInterviewDebriefCached(false);
    setInterviewDebriefGeneratedAt(null);
    setInterviewDebriefAutoRequested(false);
  }, [assessmentId]);

  useEffect(() => {
    if (activeTab !== 'interview-guidance') return;
    if (!canGenerateInterviewGuide || !assessmentId) return;
    if (interviewDebriefLoading || interviewDebriefData || interviewDebriefAutoRequested) return;
    if (!assessmentsApi?.generateInterviewDebrief) return;

    let cancelled = false;
    const loadInterviewDebrief = async () => {
      setInterviewDebriefAutoRequested(true);
      setInterviewDebriefError('');
      setInterviewDebriefLoading(true);
      try {
        const res = await withRequestTimeout(
          assessmentsApi.generateInterviewDebrief(assessmentId, {
            force_regenerate: false,
          }),
          INTERVIEW_GUIDANCE_TIMEOUT_MS,
          'Interview guidance is taking longer than expected. Please retry.'
        );
        if (cancelled) return;
        const data = res?.data || {};
        setInterviewDebriefData(data.interview_debrief || null);
        setInterviewDebriefCached(Boolean(data.cached));
        setInterviewDebriefGeneratedAt(data.generated_at || null);
      } catch (err) {
        if (cancelled) return;
        const detail = err?.response?.data?.detail || err?.message || 'Failed to generate interview guide.';
        setInterviewDebriefError(detail);
        setInterviewDebriefData(null);
      } finally {
        if (!cancelled) setInterviewDebriefLoading(false);
      }
    };

    loadInterviewDebrief();
    return () => {
      cancelled = true;
    };
  }, [
    activeTab,
    assessmentId,
    assessmentsApi,
    canGenerateInterviewGuide,
  ]);

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
    const candidateId = completedAssessment?.candidate_id || applicationRecord?.candidate_id || null;
    if (!candidateId) return;
    try {
      const res = await candidatesApi.downloadDocument(candidateId, docType);
      const blob = new Blob([res.data]);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = docType === 'cv'
        ? (completedAssessment?.candidate_cv_filename || completedAssessment?.cv_filename || applicationRecord?.cv_filename || 'candidate-cv')
        : (completedAssessment?.candidate_job_spec_filename || applicationRecord?.role_job_spec_filename || 'job-spec');
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
    { id: 'summary', label: 'SUMMARY', panelId: 'candidate-tabpanel-summary' },
    { id: 'assessment-results', label: 'ASSESSMENT RESULTS', panelId: 'candidate-tabpanel-assessment-results' },
    { id: 'role-fit', label: 'ROLE FIT', panelId: 'candidate-tabpanel-role-fit' },
    { id: 'interview-guidance', label: 'INTERVIEW GUIDANCE', panelId: 'candidate-tabpanel-interview-guidance' },
    { id: 'client-report', label: 'CLIENT REPORT', panelId: 'candidate-tabpanel-client-report' },
    { id: 'source-documents', label: 'SOURCE DOCUMENTS', panelId: 'candidate-tabpanel-source-documents' },
  ];

  const selectedComparisonCandidates = compareOptions.filter((item) =>
    compareSelectedIds.some((id) => Number(id) === Number(item.id))
  );
  const comparisonSeries = [
    {
      id: assessmentId,
      name: candidate?.name || reportModel?.identity?.name || 'Candidate',
      score: candidate?.score ?? reportModel?.summaryModel?.assessmentScore ?? reportModel?.summaryModel?.taaliScore ?? null,
      _raw: completedAssessment || {},
      breakdown: candidate?.breakdown || null,
    },
    ...selectedComparisonCandidates,
  ];

  return (
    <div>
      {NavComponent ? <NavComponent currentPage={backTo.page === 'candidates' ? 'candidates' : 'assessments'} onNavigate={onNavigate} /> : null}
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
            <CandidateAssessmentSummaryView
              reportModel={reportModel}
              variant="page"
              onOpenInterviewGuidance={() => setActiveTab('interview-guidance')}
              showInterviewGuidanceAction={hasCompletedAssessment}
            />
          </div>
        ) : null}

        {activeTab === 'assessment-results' ? (
          <div role="tabpanel" id="candidate-tabpanel-assessment-results" aria-labelledby="assessment-results">
            {hasCompletedAssessment ? (
              <CandidateResultsTab
                candidate={candidate}
                expandedCategory={expandedCategory}
                setExpandedCategory={setExpandedCategory}
                getCategoryScores={getCategoryScores}
                getMetricMetaResolved={getMetricMetaResolved}
                onOpenComparison={handleOpenComparison}
                onOpenOnboarding={() => setResultsOnboardingOpen(true)}
                onOpenInterviewGuidance={() => setActiveTab('interview-guidance')}
                interviewGuideLoading={interviewDebriefLoading}
                canGenerateInterviewGuide={canGenerateInterviewGuide}
                benchmarksLoading={benchmarksLoading}
                benchmarksData={benchmarksData}
                extraSections={[
                  {
                    id: 'candidate-results-ai-usage',
                    label: 'AI usage',
                    title: 'AI usage and prompt quality',
                    description: 'Prompt clarity, calibration, browser focus, and prompt logs stay attached to the assessment review.',
                    content: <CandidateAiUsageTab candidate={candidate} avgCalibrationScore={avgCalibrationScore} />,
                  },
                  {
                    id: 'candidate-results-code-git',
                    label: 'GitHub',
                    title: 'GitHub and code evidence',
                    description: 'Repository traces, diffs, and commit state provide the audit trail for the delivered work.',
                    content: <CandidateCodeGitTab candidate={candidate} />,
                  },
                  {
                    id: 'candidate-results-timeline',
                    label: 'Timeline',
                    title: 'Assessment timeline',
                    description: 'Use the event stream to understand pacing, prompt cadence, and recruiter notes.',
                    content: <CandidateTimelineTab candidate={candidate} />,
                  },
                ]}
              />
            ) : (
              <PendingAssessmentTab
                title={pendingAssessmentResults.title}
                description={pendingAssessmentResults.description}
              />
            )}
          </div>
        ) : null}

        {activeTab === 'role-fit' ? (
          <div role="tabpanel" id="candidate-tabpanel-role-fit" aria-labelledby="role-fit">
            <CandidateCvFitTab
              candidate={candidate}
              application={applicationRecord}
              onDownloadCandidateDoc={handleDownloadCandidateDoc}
              onRequestCvUpload={canRequestCvUpload ? handleRequestCvUpload : null}
              requestingCvUpload={busyAction === 'request-cv'}
              showDocuments={false}
            />
          </div>
        ) : null}

        {activeTab === 'interview-guidance' ? (
          <div role="tabpanel" id="candidate-tabpanel-interview-guidance" aria-labelledby="interview-guidance">
            {hasCompletedAssessment ? (
              <CandidateInterviewGuidanceTab
                canGenerateInterviewGuide={canGenerateInterviewGuide}
                debrief={interviewDebriefData}
                loading={interviewDebriefLoading}
                errorMessage={interviewDebriefError}
                cached={interviewDebriefCached}
                generatedAt={interviewDebriefGeneratedAt}
                onGenerateInterviewGuide={handleGenerateInterviewGuide}
                onCopyMarkdown={handleCopyInterviewDebriefMarkdown}
                onPrint={handlePrintInterviewDebrief}
                noteText={noteText}
                onNoteTextChange={setNoteText}
                onSaveNote={handleAddNote}
                busyAction={busyAction}
              />
            ) : (
              <PendingAssessmentTab
                title={pendingInterviewGuidance.title}
                description={pendingInterviewGuidance.description}
              />
            )}
          </div>
        ) : null}

        {activeTab === 'client-report' ? (
          <div role="tabpanel" id="candidate-tabpanel-client-report" aria-labelledby="client-report">
            {hasCompletedAssessment ? (
              <CandidateClientReportTab
                busyAction={busyAction}
                handleDownloadReport={handleDownloadReport}
                handlePostToWorkable={handlePostToWorkable}
                handleResendInvite={handleResendInvite}
                handleRequestCvUpload={handleRequestCvUpload}
                handleDeleteAssessment={handleDeleteAssessment}
                canResendInvite={canResendInvite}
                canRequestCvUpload={canRequestCvUpload}
                workableStatus={workableStatus}
              />
            ) : (
              <PendingAssessmentTab
                title={pendingClientReport.title}
                description={pendingClientReport.description}
              />
            )}
          </div>
        ) : null}

        {activeTab === 'source-documents' ? (
          <div role="tabpanel" id="candidate-tabpanel-source-documents" aria-labelledby="source-documents">
            <CandidateSourceDocumentsTab
              candidate={candidate}
              application={applicationRecord}
              reportModel={reportModel}
              onDownloadCandidateDoc={handleDownloadCandidateDoc}
            />
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
