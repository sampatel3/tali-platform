import React, { useCallback, useEffect, useState } from 'react';
import { ArrowLeft, Download, Mail } from 'lucide-react';
import * as apiClient from '../../shared/api';
import { useToast } from '../../context/ToastContext';
import { getMetricMeta, buildGlossaryFromMetadata } from '../../lib/scoringGlossary';
import { normalizeScores } from '../../scoring/scoringDimensions';
import { ComparisonRadar } from '../../shared/ui/ComparisonRadar';
import {
  Badge,
  Button,
  Input,
  Panel,
  Sheet,
  Spinner,
} from '../../shared/ui/TaaliPrimitives';
import { buildStandingCandidateReportModel } from './assessmentViewModels';
import { CandidateAssessmentSummaryView } from './CandidateAssessmentSummaryView';
import { CandidateEvaluateTab } from './CandidateEvaluateTab';
import { buildClientReportFilenameStem } from './clientReportUtils';
import {
  CandidateAiUsageTab,
  CandidateCodeGitTab,
  CandidateCvFitTab,
  CandidateTimelineTab,
} from './CandidateDetailSecondaryTabs';
import { CandidateResultsTab } from './CandidateDetailPrimaryTabs';
import { CandidateInterviewDebrief } from './CandidateInterviewDebrief';
import {
  CandidateStageOneScreeningTab,
  CandidateStageTwoTechnicalTab,
  CandidateTeamNotesTab,
} from './CandidateInterviewStageViews';
import {
  CandidateAvatar,
  WorkableComparisonCard,
  buildStatusHeroPill,
  buildWorkableHeroPill,
} from '../../shared/ui/RecruiterDesignPrimitives';

const RESULTS_ONBOARDING_KEY = 'taali_results_onboarding_seen_v1';
const INTERVIEW_GUIDANCE_TIMEOUT_MS = 15000;
const EMPTY_EVALUATION_RUBRIC = Object.freeze({});

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

const toLineList = (value) => String(value || '')
  .split('\n')
  .map((item) => item.trim())
  .filter(Boolean);

const toEvidenceTextareaValue = (value) => (
  Array.isArray(value)
    ? value.filter(Boolean).join('\n')
    : String(value || '').trim()
);

const buildManualEvaluationDraft = (storedEvaluation = null, evaluationRubric = {}) => {
  const categoryScores = {};
  Object.keys(evaluationRubric || {}).forEach((key) => {
    const entry = storedEvaluation?.category_scores?.[key];
    categoryScores[key] = {
      score: entry?.score || '',
      evidence: toEvidenceTextareaValue(entry?.evidence),
    };
  });
  return {
    categoryScores,
    decision: storedEvaluation?.decision || '',
    rationale: storedEvaluation?.rationale || '',
    confidence: storedEvaluation?.confidence || '',
    nextSteps: Array.isArray(storedEvaluation?.next_steps) ? storedEvaluation.next_steps : [],
    strengths: Array.isArray(storedEvaluation?.strengths) ? storedEvaluation.strengths.join('\n') : '',
    improvements: Array.isArray(storedEvaluation?.improvements) ? storedEvaluation.improvements.join('\n') : '',
  };
};

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
  showRecruiterFeedback = true,
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
            This pack is built from the job spec, role-fit evidence, completed assessment results when available, and TAALI signals so the interview can focus on validation instead of re-screening.
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

    {showRecruiterFeedback ? (
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
    ) : null}
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
  canPostToWorkable = true,
  canDeleteAssessment = true,
}) => (
  <div className="space-y-4">
    <Panel className="p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Report</div>
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
      {canPostToWorkable || canResendInvite || canRequestCvUpload || canDeleteAssessment ? (
        <Panel className="p-4">
          <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Recruiter actions</div>
          <div className="mt-4 flex flex-wrap gap-2">
            {canPostToWorkable ? (
              <Button type="button" size="sm" variant="secondary" onClick={handlePostToWorkable} disabled={busyAction !== ''}>
                {busyAction === 'workable' ? 'Posting...' : 'Post to Workable'}
              </Button>
            ) : null}
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
            {canDeleteAssessment ? (
              <Button type="button" size="sm" variant="danger" onClick={handleDeleteAssessment} disabled={busyAction !== ''}>
                {busyAction === 'delete' ? 'Deleting...' : 'Delete assessment'}
              </Button>
            ) : null}
          </div>
        </Panel>
      ) : (
        <Panel className="p-4">
          <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Report scope</div>
          <p className="mt-2 text-sm leading-6 text-[var(--taali-muted)]">
            This export is available before an assessment is completed, using CV and role-fit evidence already on file.
          </p>
        </Panel>
      )}

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
        <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Documents</div>
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
  const organizationsApi = 'organizations' in apiClient ? apiClient.organizations : null;
  const rolesApi = 'roles' in apiClient ? apiClient.roles : null;
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
  const [applicationDetail, setApplicationDetail] = useState(application || null);
  const [orgData, setOrgData] = useState(null);
  const [firefliesLinkModel, setFirefliesLinkModel] = useState({
    meetingId: '',
    providerUrl: '',
  });
  const [linkingFireflies, setLinkingFireflies] = useState(false);
  const [manualInterviewModel, setManualInterviewModel] = useState({
    stage: 'screening',
    transcriptText: '',
    providerUrl: '',
    meetingDate: '',
    summary: '',
  });
  const [manualInterviewSaving, setManualInterviewSaving] = useState(false);
  const [manualEvalScores, setManualEvalScores] = useState({});
  const [manualEvalStrengths, setManualEvalStrengths] = useState('');
  const [manualEvalImprovements, setManualEvalImprovements] = useState('');
  const [manualEvalSummary, setManualEvalSummary] = useState(null);
  const [manualEvalDecision, setManualEvalDecision] = useState('');
  const [manualEvalRationale, setManualEvalRationale] = useState('');
  const [manualEvalConfidence, setManualEvalConfidence] = useState('');
  const [manualEvalNextSteps, setManualEvalNextSteps] = useState([]);
  const [manualEvalSaving, setManualEvalSaving] = useState(false);
  const [aiEvalSuggestion, setAiEvalSuggestion] = useState(null);
  const [aiEvalLoading, setAiEvalLoading] = useState(false);

  const completedAssessment = candidate?._raw || null;
  const evaluationRubric = (completedAssessment?.evaluation_rubric && typeof completedAssessment.evaluation_rubric === 'object')
    ? completedAssessment.evaluation_rubric
    : EMPTY_EVALUATION_RUBRIC;
  const storedManualEvaluation = completedAssessment?.evaluation_result || completedAssessment?.manual_evaluation || null;
  const linkedApplicationId = (application && typeof application === 'object' ? application.id : null)
    || completedAssessment?.application_id
    || null;
  const applicationRecord = applicationDetail && typeof applicationDetail === 'object' ? applicationDetail : null;
  const applicationId = applicationRecord?.id || linkedApplicationId || null;
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
  const screeningPackAvailable = Boolean((applicationRecord?.screening_pack?.questions || []).length > 0);
  const reportModel = buildStandingCandidateReportModel({
    application: applicationRecord,
    completedAssessment,
    identity: {
      assessmentId,
      sectionLabel: 'Assessment',
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
  const hasInterviewGuideSource = Boolean(hasCompletedAssessment || applicationId);
  const canGenerateInterviewGuide = hasCompletedAssessment
    ? Boolean(completedAssessment) && (normalizedStatus === 'completed' || normalizedStatus === 'completed_due_to_timeout')
    : Boolean(applicationId);
  const canDownloadClientReport = Boolean(hasCompletedAssessment ? assessmentId : applicationId);
  const pendingAssessmentResults = buildAssessmentPendingMessage({
    application: applicationRecord,
    reportModel,
    label: 'Assessment',
  });
  const pendingInterviewGuidance = buildAssessmentPendingMessage({
    application: applicationRecord,
    reportModel,
    label: 'Interview guidance',
  });
  const pendingClientReport = buildAssessmentPendingMessage({
    application: applicationRecord,
    reportModel,
    label: 'Evaluation',
  });
  const workableConnected = Boolean(orgData?.workable_connected);
  const firefliesConnected = Boolean(orgData?.fireflies_config?.connected);
  const roleFitCriteria = reportModel?.roleFitModel?.requirementsAssessment || [];
  const workableSource = Boolean(
    applicationRecord?.workable_sourced
    || applicationRecord?.workable_score_raw != null
    || applicationRecord?.workable_profile_url
  );
  const heroPills = [
    buildStatusHeroPill(`Status · ${normalizedStatus || 'pending'}`),
    roleName ? buildStatusHeroPill(`Role · ${roleName}`) : null,
    ...(workableSource ? [buildWorkableHeroPill()] : []),
  ].filter(Boolean);
  const heroStats = [
    {
      key: 'taali',
      label: 'Taali score',
      value: reportModel?.summaryModel?.taaliScore != null ? `${Math.round(reportModel.summaryModel.taaliScore)}` : '—',
      description: hasCompletedAssessment ? 'Assessment + role fit' : 'Standing role-fit view',
      highlight: true,
    },
    {
      key: 'role-fit',
      label: 'Role fit',
      value: reportModel?.summaryModel?.roleFitScore != null ? `${Math.round(reportModel.summaryModel.roleFitScore)}` : '—',
      description: applicationRecord?.candidate_position || roleName || 'Role evidence',
    },
    {
      key: 'assessment',
      label: 'Assessment',
      value: reportModel?.summaryModel?.assessmentScore != null ? `${Math.round(reportModel.summaryModel.assessmentScore)}` : '—',
      description: hasCompletedAssessment ? 'Completed' : 'Pending completion',
    },
    {
      key: 'workable',
      label: 'Workable raw',
      value: applicationRecord?.workable_score_raw != null ? `${Math.round(applicationRecord.workable_score_raw)}` : '—',
      description: workableSource ? 'Synced candidate' : 'Manual application',
    },
  ];

  const refreshApplicationDetail = useCallback(async () => {
    if (!applicationId || !rolesApi?.getApplication) return null;
    const res = await rolesApi.getApplication(applicationId);
    const nextApplication = res?.data || null;
    setApplicationDetail(nextApplication);
    return nextApplication;
  }, [applicationId, rolesApi]);

  useEffect(() => {
    setApplicationDetail(application && typeof application === 'object' ? application : null);
  }, [application]);

  useEffect(() => {
    if (application && typeof application === 'object') return undefined;
    if (!applicationId || !rolesApi?.getApplication) return undefined;
    let cancelled = false;
    rolesApi.getApplication(applicationId)
      .then((res) => {
        if (!cancelled) setApplicationDetail(res?.data || null);
      })
      .catch(() => {
        if (!cancelled) setApplicationDetail(null);
      });
    return () => {
      cancelled = true;
    };
  }, [application, applicationId, rolesApi]);

  useEffect(() => {
    if (!organizationsApi?.get) return undefined;
    let cancelled = false;
    organizationsApi.get()
      .then((res) => {
        if (!cancelled) setOrgData(res?.data || null);
      })
      .catch(() => {
        if (!cancelled) setOrgData(null);
      });
    return () => {
      cancelled = true;
    };
  }, [organizationsApi]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (!hasCompletedAssessment) return;
    if (window.localStorage.getItem(RESULTS_ONBOARDING_KEY) === 'true') return;
    setResultsOnboardingOpen(true);
  }, [hasCompletedAssessment]);

  useEffect(() => {
    setWorkableStatus({
      posted: Boolean(completedAssessment?.posted_to_workable),
      postedAt: completedAssessment?.posted_to_workable_at || null,
    });
  }, [completedAssessment?.posted_to_workable, completedAssessment?.posted_to_workable_at]);

  useEffect(() => {
    const nextDraft = buildManualEvaluationDraft(storedManualEvaluation, evaluationRubric);
    setManualEvalScores(nextDraft.categoryScores);
    setManualEvalDecision(nextDraft.decision);
    setManualEvalRationale(nextDraft.rationale);
    setManualEvalConfidence(nextDraft.confidence);
    setManualEvalNextSteps(nextDraft.nextSteps);
    setManualEvalStrengths(nextDraft.strengths);
    setManualEvalImprovements(nextDraft.improvements);
    setManualEvalSummary(storedManualEvaluation);
  }, [evaluationRubric, storedManualEvaluation]);

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
      <div className="page">
        <Panel className="p-4 font-mono text-sm text-[var(--taali-muted)]">
          Candidate assessment not found.
        </Panel>
      </div>
    );
  }

  const handleDownloadReport = async () => {
    if (!canDownloadClientReport) return;
    setBusyAction('report');
    try {
      const res = hasCompletedAssessment
        ? await assessmentsApi.downloadReport(assessmentId)
        : await rolesApi?.downloadApplicationReport?.(applicationId);
      if (!res) {
        throw new Error('Client report endpoint is unavailable.');
      }
      const blob = new Blob([res.data], {
        type: res?.headers?.['content-type'] || 'application/pdf',
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${buildClientReportFilenameStem(
        roleName,
        candidate?.name || applicationRecord?.candidate_name || applicationRecord?.candidate_email
      )}.pdf`;
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

  const handleGenerateAiEvalSuggestions = async () => {
    if (!assessmentId || !assessmentsApi?.aiEvalSuggestions) return;
    setAiEvalLoading(true);
    try {
      const res = await assessmentsApi.aiEvalSuggestions(assessmentId);
      setAiEvalSuggestion(res?.data || null);
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to generate AI evaluation suggestion.', 'error');
    } finally {
      setAiEvalLoading(false);
    }
  };

  const handleLinkFirefliesInterview = async () => {
    if (!applicationId || !rolesApi?.linkFirefliesInterview) return;
    const meetingId = String(firefliesLinkModel.meetingId || '').trim();
    if (!meetingId) {
      showToast('Enter a Fireflies meeting ID to link the transcript.', 'error');
      return;
    }
    setLinkingFireflies(true);
    try {
      await rolesApi.linkFirefliesInterview(applicationId, {
        stage: 'screening',
        fireflies_meeting_id: meetingId,
        provider_url: String(firefliesLinkModel.providerUrl || '').trim() || undefined,
      });
      await refreshApplicationDetail();
      setFirefliesLinkModel({ meetingId: '', providerUrl: '' });
      showToast('Fireflies transcript linked.', 'success');
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to link Fireflies transcript.', 'error');
    } finally {
      setLinkingFireflies(false);
    }
  };

  const handleSaveManualInterview = async () => {
    if (!applicationId || !rolesApi?.createManualInterview) return;
    const transcriptText = String(manualInterviewModel.transcriptText || '').trim();
    if (!transcriptText) {
      showToast('Paste the transcript text before saving.', 'error');
      return;
    }
    let meetingDate = undefined;
    const rawMeetingDate = String(manualInterviewModel.meetingDate || '').trim();
    if (rawMeetingDate) {
      const parsed = new Date(rawMeetingDate);
      if (!Number.isNaN(parsed.getTime())) {
        meetingDate = parsed.toISOString();
      }
    }
    setManualInterviewSaving(true);
    try {
      await rolesApi.createManualInterview(applicationId, {
        stage: manualInterviewModel.stage || 'screening',
        transcript_text: transcriptText,
        provider_url: String(manualInterviewModel.providerUrl || '').trim() || undefined,
        meeting_date: meetingDate,
        summary: String(manualInterviewModel.summary || '').trim() || undefined,
      });
      await refreshApplicationDetail();
      setManualInterviewModel({
        stage: 'screening',
        transcriptText: '',
        providerUrl: '',
        meetingDate: '',
        summary: '',
      });
      showToast('Interview transcript saved.', 'success');
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to save interview transcript.', 'error');
    } finally {
      setManualInterviewSaving(false);
    }
  };

  const buildInterviewDebriefMarkdown = (debrief) => {
    if (!debrief || typeof debrief !== 'object') return '';
    const candidateLabel = debrief.candidate_name
      || candidate?.name
      || applicationRecord?.candidate_name
      || applicationRecord?.candidate_email
      || reportModel?.identity?.name
      || 'Candidate';
    if (typeof debrief.markdown === 'string' && debrief.markdown.trim()) return debrief.markdown;
    const lines = [
      `# Interview Guide - ${candidateLabel}`,
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
    if (!hasInterviewGuideSource) return;
    const request = hasCompletedAssessment
      ? assessmentsApi?.generateInterviewDebrief
        ? () => assessmentsApi.generateInterviewDebrief(assessmentId, { force_regenerate: forceRegenerate })
        : null
      : rolesApi?.generateApplicationInterviewDebrief
        ? () => rolesApi.generateApplicationInterviewDebrief(applicationId, { force_regenerate: forceRegenerate })
        : null;
    if (!request) {
      showToast('Interview guide endpoint is unavailable.', 'error');
      return;
    }
    setInterviewDebriefError('');
    setInterviewDebriefAutoRequested(true);
    setInterviewDebriefLoading(true);
    try {
      const res = await withRequestTimeout(
        request(),
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
  }, [assessmentId, applicationId, hasCompletedAssessment]);

  useEffect(() => {
    if (activeTab !== 'interview-guidance') return;
    if (!canGenerateInterviewGuide) return;
    if (interviewDebriefLoading || interviewDebriefData || interviewDebriefAutoRequested) return;
    const autoRequest = hasCompletedAssessment
      ? assessmentsApi?.generateInterviewDebrief
        ? () => assessmentsApi.generateInterviewDebrief(assessmentId, { force_regenerate: false })
        : null
      : rolesApi?.generateApplicationInterviewDebrief
        ? () => rolesApi.generateApplicationInterviewDebrief(applicationId, { force_regenerate: false })
        : null;
    if (!autoRequest) return;

    let cancelled = false;
    const loadInterviewDebrief = async () => {
      setInterviewDebriefAutoRequested(true);
      setInterviewDebriefError('');
      setInterviewDebriefLoading(true);
      try {
        const res = await withRequestTimeout(
          autoRequest(),
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
    applicationId,
    canGenerateInterviewGuide,
    hasCompletedAssessment,
    rolesApi,
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
    { id: 'summary', label: 'Overview', ariaLabel: 'Summary', panelId: 'candidate-tabpanel-summary' },
    { id: 'assessment-results', label: 'Assessment', ariaLabel: 'Assessment', panelId: 'candidate-tabpanel-assessment-results' },
    { id: 'role-fit', label: 'CV & match', ariaLabel: 'Role fit', panelId: 'candidate-tabpanel-role-fit' },
    { id: 'evaluate', label: 'Evaluate', ariaLabel: 'Evaluate', panelId: 'candidate-tabpanel-evaluate' },
    { id: 'stage1-screening', label: 'Stage 1 · screen', ariaLabel: 'Stage 1', panelId: 'candidate-tabpanel-stage1-screening' },
    { id: 'interview-guidance', label: 'Stage 2 · technical', ariaLabel: 'Stage 2', panelId: 'candidate-tabpanel-interview-guidance' },
    { id: 'notes-team', label: 'Notes & team', ariaLabel: 'Notes & team', panelId: 'candidate-tabpanel-notes-team' },
    { id: 'activity', label: 'Activity', ariaLabel: 'Activity', panelId: 'candidate-tabpanel-activity' },
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
  const evaluateActionPanel = (
    <div className="space-y-4">
      {workableConnected && workableSource ? (
        <WorkableComparisonCard
          workableRawScore={applicationRecord?.workable_score_raw}
          taaliScore={reportModel?.summaryModel?.taaliScore}
          posted={workableStatus.posted}
          postedAt={workableStatus.postedAt}
          workableProfileUrl={applicationRecord?.workable_profile_url || ''}
          scorePrecedence={orgData?.workable_config?.score_precedence || 'workable_first'}
          onPost={Boolean(hasCompletedAssessment && assessmentId) ? handlePostToWorkable : null}
          posting={busyAction === 'workable'}
        />
      ) : null}
      <div className="rounded-[var(--taali-radius-card)] border border-[var(--taali-border)] bg-[var(--taali-surface)] p-4">
        <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Recruiter actions</div>
        <div className="mt-3 flex flex-wrap gap-2">
          {canDownloadClientReport ? (
            <Button type="button" size="sm" variant="secondary" onClick={handleDownloadReport} disabled={busyAction !== ''}>
              {busyAction === 'report' ? 'Downloading...' : 'Download report'}
            </Button>
          ) : null}
          {Boolean(hasCompletedAssessment && assessmentId && workableConnected && !workableStatus.posted) ? (
            <Button type="button" size="sm" variant="secondary" onClick={handlePostToWorkable} disabled={busyAction !== ''}>
              {busyAction === 'workable' ? 'Posting...' : 'Post to Workable'}
            </Button>
          ) : null}
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
          {Boolean(hasCompletedAssessment && assessmentId) ? (
            <Button type="button" size="sm" variant="danger" onClick={handleDeleteAssessment} disabled={busyAction !== ''}>
              {busyAction === 'delete' ? 'Deleting...' : 'Delete assessment'}
            </Button>
          ) : null}
        </div>
        <div className="mt-3 text-xs text-[var(--taali-muted)]">
          Workable status:{' '}
          <span className={workableStatus.posted ? 'font-semibold text-[var(--taali-success)]' : 'font-semibold text-[var(--taali-text)]'}>
            {workableStatus.posted ? 'Posted' : 'Not posted'}
          </span>
          {workableStatus.postedAt ? ` · ${new Date(workableStatus.postedAt).toLocaleString()}` : ''}
        </div>
      </div>
    </div>
  );

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="candidates" onNavigate={onNavigate} /> : null}
      <div className="page">
        <button
          type="button"
          className="candidate-detail-back"
          onClick={() => onNavigate(backTo.page)}
        >
          <ArrowLeft size={10} /> {backTo.label}
        </button>

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

        <div className="c-hero">
          <div className="tally-bg" />
          <div className="avatar-shell">
            <CandidateAvatar name={candidate?.name || reportModel?.identity?.name || 'Candidate'} size={64} />
          </div>
          <div>
            <h1>
              {candidate?.name || reportModel?.identity?.name || 'Candidate'}
            </h1>
            <p className="meta">
              {candidate?.position || applicationRecord?.candidate_position || 'Candidate'}
              {' · '}
              Applied to <b>{roleName || 'this role'}</b>
              {applicationRecord?.candidate_location ? ` · ${applicationRecord.candidate_location}` : ''}
            </p>
            <div className="pills">
              {heroPills.map((pill) => (
                <span key={pill.key || pill.label} className={`chip ${pill.color?.includes('success') ? 'green' : pill.color?.includes('warning') ? 'amber' : pill.color?.includes('danger') ? 'red' : pill.color?.includes('workable') ? '' : 'purple'}`}>
                  {pill.label}
                </span>
              ))}
            </div>
          </div>
          <div className="c-actions">
            {candidate?.email ? (
              <a className="icon-btn" href={`mailto:${candidate.email}`} title="Email candidate">
                <Mail size={15} />
              </a>
            ) : null}
            {canDownloadClientReport ? (
              <button type="button" className="icon-btn" title="Download report" onClick={handleDownloadReport} disabled={busyAction !== ''}>
                <Download size={15} />
              </button>
            ) : null}
            {applicationId ? (
              <button
                type="button"
                className="btn btn-outline btn-sm"
                onClick={() => onNavigate('candidate-report', { candidateApplicationId: applicationId })}
              >
                Share report
              </button>
            ) : null}
            <button type="button" className="btn btn-purple btn-sm" disabled>
              Advance to panel <span className="arrow">→</span>
            </button>
          </div>
        </div>

        <div className="sum-row">
          {heroStats.map((item) => (
            <div key={item.key} className="candidate-summary-card">
              <div className="k">{item.label}</div>
              <div className="v">{item.value}{String(item.value).includes('/') ? null : (item.key === 'taali' || item.key === 'assessment' ? <span className="slash">/100</span> : null)}</div>
              <div className="d">{item.description}</div>
            </div>
          ))}
        </div>

        <div className="candidate-top-tabs" role="tablist" aria-label="Candidate detail sections">
          {topTabs.map((tab) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.id}
              aria-controls={tab.panelId}
              aria-label={tab.ariaLabel || tab.label}
              className={activeTab === tab.id ? 'active' : ''}
              onClick={() => setActiveTab(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {activeTab === 'summary' ? (
          <div role="tabpanel" id="candidate-tabpanel-summary" aria-labelledby="summary" className="space-y-4">
            <CandidateAssessmentSummaryView
              reportModel={reportModel}
              variant="page"
              showIdentityTitle={false}
              onOpenInterviewGuidance={() => setActiveTab('interview-guidance')}
              showInterviewGuidanceAction={canGenerateInterviewGuide}
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

        {activeTab === 'stage1-screening' ? (
          <div role="tabpanel" id="candidate-tabpanel-stage1-screening" aria-labelledby="stage1-screening">
            {applicationRecord && screeningPackAvailable ? (
              <CandidateStageOneScreeningTab application={applicationRecord} />
            ) : (
              <PendingAssessmentTab
                title="Stage 1 screening prompts will appear once a candidate application is linked."
                description="This view uses the candidate application record, pre-screen evidence, and role-fit data. Link or load the application record to review the screening pack."
              />
            )}
          </div>
        ) : null}

        {activeTab === 'interview-guidance' ? (
          <div role="tabpanel" id="candidate-tabpanel-interview-guidance" aria-labelledby="interview-guidance">
            {(applicationRecord || hasInterviewGuideSource) ? (
              <CandidateStageTwoTechnicalTab
                application={applicationRecord}
                hasCompletedAssessment={hasCompletedAssessment}
                firefliesConnected={firefliesConnected}
                firefliesLinkSupported={Boolean(applicationId && rolesApi?.linkFirefliesInterview)}
                firefliesLinkModel={firefliesLinkModel}
                onFirefliesLinkChange={(patch) => setFirefliesLinkModel((prev) => ({ ...prev, ...patch }))}
                onLinkFireflies={handleLinkFirefliesInterview}
                linkingFireflies={linkingFireflies}
                manualInterviewSupported={Boolean(applicationId && rolesApi?.createManualInterview)}
                manualInterviewModel={manualInterviewModel}
                onManualInterviewChange={(patch) => setManualInterviewModel((prev) => ({ ...prev, ...patch }))}
                onSaveManualInterview={handleSaveManualInterview}
                manualInterviewSaving={manualInterviewSaving}
                guidanceSlot={hasInterviewGuideSource ? (
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
                    showRecruiterFeedback={false}
                  />
                ) : null}
              />
            ) : (
              <PendingAssessmentTab
                title={pendingInterviewGuidance.title}
                description={pendingInterviewGuidance.description}
              />
            )}
          </div>
        ) : null}

        {activeTab === 'notes-team' ? (
          <div role="tabpanel" id="candidate-tabpanel-notes-team" aria-labelledby="notes-team">
            <CandidateTeamNotesTab
              application={applicationRecord}
              noteText={noteText}
              onNoteTextChange={setNoteText}
              onSaveNote={handleAddNote}
              busyAction={busyAction}
              canSaveNote={Boolean(hasCompletedAssessment && assessmentId)}
            />
          </div>
        ) : null}

        {activeTab === 'evaluate' ? (
          <div role="tabpanel" id="candidate-tabpanel-evaluate" aria-labelledby="evaluate">
            {hasCompletedAssessment ? (
              <CandidateEvaluateTab
                candidate={candidate}
                evaluationRubric={evaluationRubric}
                assessmentId={assessmentId}
                aiEvalSuggestion={aiEvalSuggestion}
                onGenerateAiSuggestions={handleGenerateAiEvalSuggestions}
                aiEvalLoading={aiEvalLoading}
                manualEvalScores={manualEvalScores}
                setManualEvalScores={setManualEvalScores}
                manualEvalStrengths={manualEvalStrengths}
                setManualEvalStrengths={setManualEvalStrengths}
                manualEvalImprovements={manualEvalImprovements}
                setManualEvalImprovements={setManualEvalImprovements}
                manualEvalSummary={manualEvalSummary}
                setManualEvalSummary={setManualEvalSummary}
                manualEvalDecision={manualEvalDecision}
                setManualEvalDecision={setManualEvalDecision}
                manualEvalRationale={manualEvalRationale}
                setManualEvalRationale={setManualEvalRationale}
                manualEvalConfidence={manualEvalConfidence}
                setManualEvalConfidence={setManualEvalConfidence}
                manualEvalNextSteps={manualEvalNextSteps}
                setManualEvalNextSteps={setManualEvalNextSteps}
                manualEvalSaving={manualEvalSaving}
                setManualEvalSaving={setManualEvalSaving}
                toLineList={toLineList}
                toEvidenceTextareaValue={toEvidenceTextareaValue}
                assessmentsApi={assessmentsApi}
                roleFitCriteria={roleFitCriteria}
                recommendation={reportModel?.recommendation}
                recruiterSummary={reportModel?.recruiterSummaryText || ''}
                actionPanel={evaluateActionPanel}
              />
            ) : (
              <PendingAssessmentTab
                title={pendingClientReport.title}
                description={pendingClientReport.description}
              />
            )}
          </div>
        ) : null}

        {activeTab === 'activity' ? (
          <div role="tabpanel" id="candidate-tabpanel-activity" aria-labelledby="activity">
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
      </div>
    </div>
  );
};

export const CandidateDetailPage = AssessmentResultsPage;
