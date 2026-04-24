import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ArrowLeft,
  Download,
  ExternalLink,
  Loader2,
  Mail,
  Share2,
  Sparkles,
} from 'lucide-react';
import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
} from 'recharts';

import {
  assessments as assessmentsApi,
  candidates as candidatesApi,
  roles as rolesApi,
} from '../../shared/api';
import { useToast } from '../../context/ToastContext';
import { AppShell } from '../../shared/layout/TaaliLayout';
import {
  Badge,
  Button,
  Input,
  Panel,
  Spinner,
  Textarea,
} from '../../shared/ui/TaaliPrimitives';
import { CandidateAssessmentSummaryView } from './CandidateAssessmentSummaryView';
import { CandidateCvFitTab } from './CandidateDetailSecondaryTabs';
import { CandidateEvaluateTab } from './CandidateEvaluateTab';
import { CandidateInterviewDebrief } from './CandidateInterviewDebrief';
import { buildStandingCandidateReportModel } from './assessmentViewModels';
import {
  aiCollabBand,
  buildAssessmentEvidenceCards,
  buildAssessmentTimelineRows,
  buildSixAxisMetrics,
  copyText,
  formatClockDuration,
  formatDateTime,
  formatScale100,
  formatShortDate,
  formatStatusLabel,
  initialsFor,
  recommendationFromScore,
} from './redesignUtils';

const TAB_ITEMS = [
  { id: 'summary', label: 'Summary' },
  { id: 'assessment', label: 'Assessment' },
  { id: 'role-fit', label: 'Role fit' },
  { id: 'interview', label: 'Interview prep' },
  { id: 'evaluate', label: 'Evaluate' },
  { id: 'report', label: 'Report' },
];

const toneStyles = {
  success: {
    text: 'var(--green)',
    fill: 'linear-gradient(90deg, rgba(68, 187, 112, 0.96), rgba(103, 210, 131, 0.92))',
    solid: 'var(--green)',
  },
  warning: {
    text: 'var(--amber)',
    fill: 'linear-gradient(90deg, rgba(245, 158, 11, 0.92), rgba(251, 191, 36, 0.88))',
    solid: 'var(--amber)',
  },
  danger: {
    text: 'var(--red)',
    fill: 'linear-gradient(90deg, rgba(230, 74, 74, 0.94), rgba(248, 113, 113, 0.88))',
    solid: 'var(--red)',
  },
  muted: {
    text: 'var(--ink-2)',
    fill: 'linear-gradient(90deg, rgba(117, 107, 138, 0.9), rgba(155, 146, 176, 0.86))',
    solid: 'var(--ink-2)',
  },
  info: {
    text: 'var(--purple)',
    fill: 'linear-gradient(90deg, rgba(127, 57, 251, 0.95), rgba(182, 137, 255, 0.9))',
    solid: 'var(--purple)',
  },
};

const safeWindowOpen = (url) => {
  if (!url) return;
  window.open(url, '_blank', 'noopener,noreferrer');
};

const mapAssessmentToCandidateView = (assessment) => ({
  id: assessment.id,
  name: (assessment.candidate_name || assessment.candidate?.full_name || assessment.candidate_email || '').trim() || 'Unknown',
  email: assessment.candidate_email || assessment.candidate?.email || '',
  task: assessment.task_name || assessment.task?.name || 'Assessment',
  status: assessment.status || 'pending',
  score: assessment.score ?? assessment.overall_score ?? null,
  time: assessment.duration_taken ? formatClockDuration(assessment.duration_taken) : '—',
  position: assessment.role_name || assessment.candidate?.position || assessment.candidate_position || '',
  completedDate: assessment.completed_at ? formatShortDate(assessment.completed_at) : null,
  breakdown: assessment.breakdown || null,
  promptsList: assessment.prompts_list || [],
  timeline: assessment.timeline || [],
  _raw: assessment,
});

const resolveApplicationId = (assessment, applicationDetail = null) => (
  applicationDetail?.id
  || assessment?.candidate_application_id
  || assessment?.application_id
  || assessment?.candidate_application?.id
  || null
);

const buildIdentity = ({
  candidate,
  assessment,
  application,
}) => ({
  sectionLabel: 'Candidate report',
  name: candidate?.name || 'Candidate',
  email: candidate?.email || '',
  position: candidate?.position || application?.candidate_position || '',
  taskName: candidate?.task || assessment?.task_name || assessment?.task?.name || '',
  roleName: application?.role_name || assessment?.role_name || '',
  applicationStatus: application?.application_outcome || assessment?.application_status || '',
  durationLabel: candidate?.time || '—',
  completedLabel: candidate?.completedDate || (assessment?.completed_at ? formatShortDate(assessment.completed_at) : ''),
  assessmentId: assessment?.id || null,
});

const SummaryCard = ({ label, value, description, valueStyle = null }) => (
  <div className="rounded-[var(--radius)] border border-[var(--line)] bg-[var(--bg-2)] px-4 py-4 shadow-[var(--shadow-sm)]">
    <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.1em] text-[var(--mute)]">{label}</div>
    <div className="mt-2 font-[var(--font-display)] text-[28px] leading-none tracking-[-0.03em]" style={valueStyle || undefined}>
      {value}
    </div>
    <div className="mt-1.5 text-[12.5px] text-[var(--mute)]">{description}</div>
  </div>
);

const DimensionRow = ({ item }) => {
  const tone = toneStyles[item.tone] || toneStyles.muted;
  return (
    <div className="border-b border-[var(--line-2)] py-3 last:border-b-0">
      <div className="mb-1.5 flex items-end justify-between gap-3">
        <span className="text-[14px] font-medium">{item.label}</span>
        <span className="font-[var(--font-mono)] text-[12.5px]" style={{ color: tone.text }}>{item.displayValue}</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-[var(--bg)]">
        <div
          className="h-full rounded-full"
          style={{
            width: `${Math.max(0, Math.min(100, Number(item.percent || 0)))}%`,
            background: tone.fill,
          }}
        />
      </div>
      <p className="mt-2 text-[12.5px] leading-6 text-[var(--mute)]">{item.note}</p>
    </div>
  );
};

const EvidenceCard = ({ item, index }) => (
  <div className="grid grid-cols-[26px_minmax(0,1fr)] gap-3 rounded-[14px] border border-[var(--line-2)] bg-[var(--bg)] px-4 py-3">
    <div className="grid h-[26px] w-[26px] place-items-center rounded-full bg-[var(--purple-soft)] font-[var(--font-mono)] text-[11.5px] font-semibold text-[var(--purple-2)]">
      {String.fromCharCode(65 + index)}
    </div>
    <div>
      <h4 className="text-[14px] font-semibold">{item.title}</h4>
      <p className="mt-1 text-[13px] leading-6 text-[var(--ink-2)]">{item.body}</p>
      <div className="mt-2 font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)]">{item.badge}</div>
    </div>
  </div>
);

const TimelineRow = ({ row }) => {
  const tone = toneStyles[row.tone] || toneStyles.muted;
  return (
    <div className="grid grid-cols-[70px_minmax(0,1fr)] gap-3 rounded-[10px] border border-[var(--line-2)] bg-[var(--bg)] px-4 py-3">
      <div className="font-[var(--font-mono)] text-[11px] text-[var(--mute)]">
        {row.timestamp ? new Date(row.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '—'}
      </div>
      <div>
        <div className="text-[13.5px] font-medium">{row.label}</div>
        <div className="mt-1 text-[12.5px] leading-6 text-[var(--ink-2)]">{row.detail}</div>
        {row.timestamp ? (
          <div className="mt-1.5 font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.08em]" style={{ color: tone.text }}>
            {formatDateTime(row.timestamp)}
          </div>
        ) : null}
      </div>
    </div>
  );
};

const FocusQuestionCard = ({ item, index }) => (
  <div className="rounded-[14px] border border-[var(--line)] bg-[var(--bg)] px-4 py-4">
    <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.1em] text-[var(--purple)]">{`Q${index + 1}`}</div>
    <div className="mt-2 text-[15px] font-semibold tracking-[-0.01em]">{item?.question || 'Interview question'}</div>
    {Array.isArray(item?.what_to_listen_for) && item.what_to_listen_for.length > 0 ? (
      <p className="mt-2 text-[13px] leading-6 text-[var(--ink-2)]">
        <span className="font-medium">Listen for:</span>
        {' '}
        {item.what_to_listen_for.join(' • ')}
      </p>
    ) : null}
    {Array.isArray(item?.concerning_signals) && item.concerning_signals.length > 0 ? (
      <p className="mt-2 text-[13px] leading-6 text-[var(--mute)]">
        <span className="font-medium text-[var(--ink-2)]">Watch out for:</span>
        {' '}
        {item.concerning_signals.join(' • ')}
      </p>
    ) : null}
  </div>
);

const tabButtonClass = (active) => (
  active
    ? 'rounded-full bg-[var(--ink)] px-4 py-2 text-[13.5px] font-medium text-[var(--bg)]'
    : 'rounded-full px-4 py-2 text-[13.5px] font-medium text-[var(--mute)] transition-colors hover:text-[var(--ink)]'
);

const buildAiCollabText = (assessment) => {
  const band = aiCollabBand(assessment);
  if (band.score == null) return { value: 'Pending', description: 'AI-collaboration signal appears once scoring finishes.' };
  return {
    value: `${band.label} · ${Math.round(band.score * 10)}`,
    description: 'Average across prompt quality, recovery, independence, context, and design judgment.',
  };
};

const notesFromTimeline = (timeline) => (
  (Array.isArray(timeline) ? timeline : [])
    .filter((item) => String(item?.event_type || item?.type || item?.event || '').toLowerCase().includes('note'))
    .map((item, index) => ({
      id: `${item?.timestamp || 'note'}-${index}`,
      author: item?.author || 'Hiring team',
      timestamp: item?.timestamp || null,
      text: item?.message || item?.note || item?.text || item?.preview || 'Recruiter note',
    }))
);

export const AssessmentResultsPage = ({
  candidate: initialCandidate = null,
  assessmentId: assessmentIdProp = null,
  onNavigate,
  backTo = { page: 'candidates', label: 'Back to candidates' },
  onDeleted,
  onNoteAdded,
}) => {
  const { showToast } = useToast();
  const [candidate, setCandidate] = useState(initialCandidate);
  const [applicationDetail, setApplicationDetail] = useState(null);
  const [role, setRole] = useState(null);
  const [loading, setLoading] = useState(!initialCandidate);
  const [error, setError] = useState('');
  const [activeTab, setActiveTab] = useState('summary');
  const [busyAction, setBusyAction] = useState('');
  const [noteText, setNoteText] = useState('');
  const [manualEvalScores, setManualEvalScores] = useState({});
  const [manualEvalStrengths, setManualEvalStrengths] = useState('');
  const [manualEvalImprovements, setManualEvalImprovements] = useState('');
  const [manualEvalSummary, setManualEvalSummary] = useState(null);
  const [manualEvalSaving, setManualEvalSaving] = useState(false);
  const [aiEvalSuggestion, setAiEvalSuggestion] = useState(null);
  const [interviewDebriefLoading, setInterviewDebriefLoading] = useState(false);
  const [interviewDebriefData, setInterviewDebriefData] = useState(null);
  const [interviewDebriefCached, setInterviewDebriefCached] = useState(false);
  const [interviewDebriefGeneratedAt, setInterviewDebriefGeneratedAt] = useState(null);

  const assessmentId = assessmentIdProp || candidate?._raw?.id || candidate?.id || null;
  const assessment = candidate?._raw || null;
  const applicationId = resolveApplicationId(assessment, applicationDetail);
  const roleId = applicationDetail?.role_id || assessment?.role_id || null;

  useEffect(() => {
    setCandidate(initialCandidate);
  }, [initialCandidate]);

  useEffect(() => {
    const evaluationResult = assessment?.evaluation_result || assessment?.manual_evaluation || {};
    const categoryScores = evaluationResult?.category_scores;
    if (categoryScores && typeof categoryScores === 'object') {
      const normalized = {};
      Object.entries(categoryScores).forEach(([key, value]) => {
        const item = value && typeof value === 'object' ? value : {};
        normalized[key] = {
          score: item.score || '',
          evidence: Array.isArray(item.evidence) ? item.evidence.join('\n') : String(item.evidence || ''),
        };
      });
      setManualEvalScores(normalized);
    } else {
      setManualEvalScores({});
    }
    setManualEvalStrengths(Array.isArray(evaluationResult?.strengths) ? evaluationResult.strengths.join('\n') : '');
    setManualEvalImprovements(Array.isArray(evaluationResult?.improvements) ? evaluationResult.improvements.join('\n') : '');
    setManualEvalSummary(Object.keys(evaluationResult || {}).length ? evaluationResult : null);
  }, [assessment?.evaluation_result, assessment?.manual_evaluation]);

  useEffect(() => {
    if (!assessmentId) {
      setLoading(false);
      setError('Candidate assessment not found.');
      return;
    }

    if (candidate && Number(candidate.id) === Number(assessmentId)) {
      setLoading(false);
      setError('');
      return;
    }

    let cancelled = false;

    const loadAssessment = async () => {
      setLoading(true);
      setError('');
      try {
        const res = await assessmentsApi.get(assessmentId);
        if (!cancelled) {
          setCandidate(mapAssessmentToCandidateView(res?.data || {}));
        }
      } catch (err) {
        if (!cancelled) {
          setCandidate(null);
          setError(err?.response?.data?.detail || 'Failed to load assessment.');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    void loadAssessment();
    return () => {
      cancelled = true;
    };
  }, [assessmentId, candidate]);

  useEffect(() => {
    if (!applicationId) {
      setApplicationDetail(null);
      return;
    }

    let cancelled = false;
    const loadApplication = async () => {
      try {
        const res = await rolesApi.getApplication(Number(applicationId), {
          params: { include_cv_text: true },
        });
        if (!cancelled) {
          setApplicationDetail(res?.data || null);
        }
      } catch {
        if (!cancelled) {
          setApplicationDetail(null);
        }
      }
    };

    void loadApplication();
    return () => {
      cancelled = true;
    };
  }, [applicationId]);

  useEffect(() => {
    if (!roleId) {
      setRole(null);
      return;
    }

    let cancelled = false;
    const loadRole = async () => {
      try {
        const res = await rolesApi.get(Number(roleId));
        if (!cancelled) setRole(res?.data || null);
      } catch {
        if (!cancelled) setRole(null);
      }
    };

    void loadRole();
    return () => {
      cancelled = true;
    };
  }, [roleId]);

  const dimensionMetrics = useMemo(() => buildSixAxisMetrics(assessment), [assessment]);
  const evidenceCards = useMemo(() => buildAssessmentEvidenceCards(assessment), [assessment]);
  const timelineRows = useMemo(() => buildAssessmentTimelineRows(assessment), [assessment]);
  const notes = useMemo(() => notesFromTimeline(candidate?.timeline), [candidate?.timeline]);
  const identity = useMemo(() => buildIdentity({ candidate, assessment, application: applicationDetail }), [candidate, assessment, applicationDetail]);
  const reportModel = useMemo(() => buildStandingCandidateReportModel({
    application: applicationDetail,
    completedAssessment: assessment,
    identity,
  }), [applicationDetail, assessment, identity]);
  const recommendation = useMemo(() => recommendationFromScore(reportModel?.summaryModel?.taaliScore), [reportModel]);
  const aiCollab = useMemo(() => buildAiCollabText(assessment), [assessment]);

  const quickFacts = useMemo(() => ([
    ['Role', applicationDetail?.role_name || assessment?.role_name || '—'],
    ['Status', formatStatusLabel(assessment?.status || applicationDetail?.application_outcome)],
    ['Completed', assessment?.completed_at ? formatDateTime(assessment.completed_at) : '—'],
    ['Tests', assessment?.tests_total != null ? `${assessment.tests_passed ?? 0}/${assessment.tests_total}` : '—'],
    ['Prompts', assessment?.total_prompts ?? candidate?.promptsList?.length ?? '—'],
    ['Browser focus', assessment?.browser_focus_ratio != null ? `${Math.round(assessment.browser_focus_ratio * 100)}%` : '—'],
    ['CV', assessment?.candidate_cv_filename || assessment?.cv_filename || applicationDetail?.cv_filename || 'Not uploaded'],
    ['Source', applicationDetail?.source || assessment?.source || 'Direct'],
  ]), [applicationDetail, assessment, candidate?.promptsList?.length]);

  const focusQuestions = Array.isArray(role?.interview_focus?.questions) ? role.interview_focus.questions : [];
  const focusTriggers = Array.isArray(role?.interview_focus?.manual_screening_triggers) ? role.interview_focus.manual_screening_triggers : [];

  const radarData = useMemo(() => dimensionMetrics.map((item) => ({
    metric: item.label,
    score: item.key === 'time_to_first_prompt'
      ? Number(((item.percent || 0) / 10).toFixed(1))
      : (item.value != null ? Number(item.value.toFixed(1)) : 0),
    fullMark: 10,
  })), [dimensionMetrics]);

  const getCategoryScores = useCallback((candidateData) => {
    const raw = candidateData?._raw || {};
    const scoreBreakdown = raw.score_breakdown?.category_scores || {};
    const promptAnalytics = raw.prompt_analytics?.detailed_scores?.category_scores || {};
    const fallback = {};
    Object.entries({ ...scoreBreakdown, ...promptAnalytics }).forEach(([key, value]) => {
      const numeric = Number(value);
      if (Number.isFinite(numeric)) fallback[key] = numeric;
    });
    return fallback;
  }, []);

  const handleDownloadBlob = async (request, filename, fallbackMessage) => {
    try {
      const res = await request();
      const blob = new Blob([res.data], { type: 'application/pdf' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      showToast(err?.response?.data?.detail || fallbackMessage, 'error');
    }
  };

  const handleDownloadReport = async () => {
    if (!assessmentId) return;
    setBusyAction('download-report');
    await handleDownloadBlob(
      () => assessmentsApi.downloadReport(assessmentId),
      `assessment-${assessmentId}.pdf`,
      'Failed to download assessment report.',
    );
    setBusyAction('');
  };

  const handleDownloadCv = async () => {
    if (!assessment?.candidate_id) return;
    setBusyAction('download-cv');
    await handleDownloadBlob(
      () => candidatesApi.downloadDocument(assessment.candidate_id, 'cv'),
      assessment?.candidate_cv_filename || assessment?.cv_filename || 'candidate-cv.pdf',
      'Failed to download CV.',
    );
    setBusyAction('');
  };

  const handleAddNote = async () => {
    const text = String(noteText || '').trim();
    if (!assessmentId || !text) return;
    setBusyAction('note');
    try {
      const res = await assessmentsApi.addNote(assessmentId, text);
      const updatedTimeline = Array.isArray(res?.data?.timeline) ? res.data.timeline : null;
      if (updatedTimeline) {
        setCandidate((prev) => (prev ? { ...prev, timeline: updatedTimeline, _raw: { ...prev._raw, timeline: updatedTimeline } } : prev));
        onNoteAdded?.(updatedTimeline);
      }
      setNoteText('');
      showToast('Note added.', 'success');
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to add note.', 'error');
    } finally {
      setBusyAction('');
    }
  };

  const handleAdvanceToPanel = async () => {
    if (!applicationDetail?.id || !applicationDetail?.version) {
      showToast('Application context is unavailable for this candidate.', 'error');
      return;
    }

    setBusyAction('advance');
    try {
      await rolesApi.updateApplicationStage(applicationDetail.id, {
        pipeline_stage: 'review',
        expected_version: applicationDetail.version,
        reason: 'Advanced from candidate assessment detail',
        idempotency_key: typeof crypto !== 'undefined' && crypto.randomUUID
          ? crypto.randomUUID()
          : `advance-${Date.now()}`,
      });
      const refreshed = await rolesApi.getApplication(applicationDetail.id, {
        params: { include_cv_text: true },
      });
      setApplicationDetail(refreshed?.data || null);
      showToast('Candidate advanced to panel review.', 'success');
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to update candidate stage.', 'error');
    } finally {
      setBusyAction('');
    }
  };

  const handleShareReport = async () => {
    if (applicationId) {
      onNavigate('candidate-report', { candidateApplicationId: applicationId });
      return;
    }

    try {
      await copyText(window.location.href);
      showToast('Assessment link copied to clipboard.', 'success');
    } catch {
      showToast('Could not copy the current link.', 'error');
    }
  };

  const handleCopyCurrentLink = async () => {
    try {
      await copyText(window.location.href);
      showToast('Link copied to clipboard.', 'success');
    } catch {
      showToast('Failed to copy link.', 'error');
    }
  };

  const buildInterviewDebriefMarkdown = useCallback((debrief) => {
    if (!debrief || typeof debrief !== 'object') return '';
    if (typeof debrief.markdown === 'string' && debrief.markdown.trim()) return debrief.markdown;

    const lines = [
      `# Interview guide - ${candidate?.name || 'Candidate'}`,
      '',
      debrief.summary || '',
      '',
      '## Probing questions',
    ];

    (debrief.probing_questions || []).forEach((item) => {
      lines.push(`### ${item.dimension || 'Dimension'}`);
      if (item.question) lines.push(`- Question: ${item.question}`);
      if (item.what_to_listen_for) lines.push(`- What to listen for: ${item.what_to_listen_for}`);
      lines.push('');
    });

    return lines.join('\n').trim();
  }, [candidate?.name]);

  const handleCopyInterviewMarkdown = async () => {
    const markdown = buildInterviewDebriefMarkdown(interviewDebriefData);
    if (!markdown) return;
    try {
      await copyText(markdown);
      showToast('Interview guide copied.', 'success');
    } catch {
      showToast('Failed to copy interview guide.', 'error');
    }
  };

  const handlePrintInterviewDebrief = () => {
    const markdown = buildInterviewDebriefMarkdown(interviewDebriefData);
    if (!markdown) return;
    const next = window.open('', '_blank', 'noopener,noreferrer,width=900,height=700');
    if (!next) {
      showToast('Pop-up blocked. Please allow pop-ups to print.', 'error');
      return;
    }
    next.document.write(`
      <html>
        <head><title>Interview guide</title></head>
        <body style="font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; padding: 24px; line-height: 1.6;">
          <pre style="white-space: pre-wrap;">${markdown.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</pre>
        </body>
      </html>
    `);
    next.document.close();
    next.focus();
    next.print();
  };

  const handleGenerateInterviewGuide = async ({ forceRegenerate = false } = {}) => {
    if (!assessmentId) return;
    setInterviewDebriefLoading(true);
    try {
      const res = await assessmentsApi.generateInterviewDebrief(assessmentId, { force_regenerate: forceRegenerate });
      const payload = res?.data || {};
      setInterviewDebriefData(payload.interview_debrief || null);
      setInterviewDebriefCached(Boolean(payload.cached));
      setInterviewDebriefGeneratedAt(payload.generated_at || null);
      if (!payload.interview_debrief) {
        showToast('Interview guidance was not returned for this assessment.', 'error');
      }
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to generate interview guide.', 'error');
      setInterviewDebriefData(null);
    } finally {
      setInterviewDebriefLoading(false);
    }
  };

  const handleGenerateAiSuggestions = async () => {
    if (!assessmentId) return;
    setBusyAction('ai-eval');
    try {
      const res = await assessmentsApi.aiEvalSuggestions(assessmentId);
      setAiEvalSuggestion(res?.data || null);
      showToast('AI evaluation suggestion generated.', 'success');
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to generate AI suggestion.', 'error');
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
      onDeleted?.();
      onNavigate(backTo.page);
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to delete assessment.', 'error');
    } finally {
      setBusyAction('');
    }
  };

  if (loading) {
    return (
      <AppShell currentPage="candidates" onNavigate={onNavigate}>
        <div className="page">
          <div className="grid min-h-[360px] place-items-center rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] shadow-[var(--shadow-sm)]">
            <div className="flex items-center gap-3 text-sm text-[var(--mute)]">
              <Spinner size={20} />
              Loading assessment detail...
            </div>
          </div>
        </div>
      </AppShell>
    );
  }

  if (error || !candidate || !assessment) {
    return (
      <AppShell currentPage="candidates" onNavigate={onNavigate}>
        <div className="page">
          <div className="rounded-[var(--radius-lg)] border border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-4 text-sm text-[var(--taali-danger)]">
            {error || 'Candidate assessment not found.'}
          </div>
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell currentPage="candidates" onNavigate={onNavigate}>
      <div className="page">
        <button
          type="button"
          className="mb-4 inline-flex items-center gap-2 font-[var(--font-mono)] text-[11.5px] uppercase tracking-[0.08em] text-[var(--mute)] transition-colors hover:text-[var(--purple)]"
          onClick={() => onNavigate(backTo.page)}
        >
          <ArrowLeft size={12} />
          {backTo.label}
        </button>

        <section className="relative overflow-hidden rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] px-6 py-7 shadow-[var(--shadow-sm)] md:px-8">
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-0 opacity-60"
            style={{
              backgroundImage:
                'linear-gradient(var(--line-2) 1px, transparent 1px), linear-gradient(90deg, var(--line-2) 1px, transparent 1px)',
              backgroundSize: '48px 48px',
              maskImage: 'radial-gradient(60% 100% at 88% 50%, black, transparent 70%)',
            }}
          />

          <div className="relative grid gap-5 lg:grid-cols-[64px_minmax(0,1fr)_auto] lg:items-center">
            <div className="grid h-16 w-16 place-items-center rounded-full bg-[var(--purple-soft)] text-[22px] font-semibold text-[var(--purple)]">
              {initialsFor(candidate.name, candidate.email)}
            </div>

            <div>
              <h1 className="font-[var(--font-display)] text-[36px] font-semibold leading-none tracking-[-0.035em]">
                {candidate.name.split(' ')[0]} <span className="text-[var(--purple)]">{candidate.name.split(' ').slice(1).join(' ')}</span>
              </h1>
              <p className="mt-2 text-[14px] text-[var(--mute)]">
                {candidate.position || 'Candidate'}
                {applicationDetail?.role_name || assessment?.role_name ? (
                  <>
                    {' '}
                    · Applied to <b>{applicationDetail?.role_name || assessment?.role_name}</b>
                  </>
                ) : null}
                {candidate.email ? ` · ${candidate.email}` : ''}
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                {aiCollab.value !== 'Pending' ? (
                  <span className="chip purple">
                    <span className="dot" />
                    AI-collab {aiCollab.value}
                  </span>
                ) : null}
                <Badge variant={recommendation.variant}>{recommendation.label}</Badge>
                <Badge variant="muted">{assessment?.completed_at ? `Submitted ${formatShortDate(assessment.completed_at)}` : formatStatusLabel(assessment?.status)}</Badge>
                {assessment?.candidate_experience_years ? <Badge variant="muted">{`${assessment.candidate_experience_years} yrs exp`}</Badge> : null}
              </div>
            </div>

            <div className="flex flex-wrap gap-2 lg:justify-end">
              <button type="button" className="icon-btn" title="Email candidate" onClick={() => safeWindowOpen(candidate.email ? `mailto:${candidate.email}` : '')}>
                <Mail size={15} />
              </button>
              <button type="button" className="icon-btn" title="Download report" onClick={handleDownloadReport} disabled={busyAction !== ''}>
                {busyAction === 'download-report' ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />}
              </button>
              <button type="button" className="btn btn-outline btn-sm" onClick={handleShareReport}>
                Share report
              </button>
              <button
                type="button"
                className="btn btn-purple btn-sm"
                onClick={handleAdvanceToPanel}
                disabled={busyAction !== '' || !applicationDetail?.id}
              >
                {busyAction === 'advance' ? 'Advancing...' : 'Advance to panel'}
                <span className="arrow">→</span>
              </button>
            </div>
          </div>
        </section>

        <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <SummaryCard
            label="Composite"
            value={reportModel?.summaryModel?.taaliScore != null ? formatScale100(reportModel.summaryModel.taaliScore) : '—'}
            description={reportModel?.source?.label || 'Assessment-derived signal'}
          />
          <SummaryCard
            label="AI-collab"
            value={aiCollab.value}
            description={aiCollab.description}
            valueStyle={aiCollab.value === 'Pending' ? null : { color: 'var(--green)' }}
          />
          <SummaryCard
            label="Assessment time"
            value={candidate.time}
            description={assessment?.duration_minutes ? `${assessment.duration_minutes} min target` : 'Session duration'}
          />
          <SummaryCard
            label="Recommendation"
            value={recommendation.ctaLabel}
            description={recommendation.label === 'Pending' ? 'Confidence builds as scoring finalizes.' : 'Confidence: review attached evidence before moving.'}
            valueStyle={recommendation.label === 'Pending' ? null : { color: 'var(--purple)' }}
          />
        </div>

        <div className="mt-4 inline-flex flex-wrap gap-1 rounded-full border border-[var(--line)] bg-[var(--bg-2)] p-1 shadow-[var(--shadow-sm)]">
          {TAB_ITEMS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              className={tabButtonClass(activeTab === tab.id)}
              onClick={() => setActiveTab(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>

        <div className="mt-5">
          {activeTab === 'summary' ? (
            <div className="grid gap-4 xl:grid-cols-[1.35fr_1fr]">
              <div className="space-y-4">
                <Panel className="p-6">
                  <h2 className="font-[var(--font-display)] text-[26px] font-semibold tracking-[-0.025em]">One-line <span className="text-[var(--purple)]">summary</span></h2>
                  <p className="mt-1 text-[13px] text-[var(--mute)]">Generated from the captured assessment session and the role-fit evidence already on file.</p>
                  <p className="mt-5 text-[17px] leading-[1.5] tracking-[-0.01em] text-[var(--ink)]">
                    {reportModel?.recruiterSummaryText || reportModel?.summaryModel?.heuristicSummary || 'This assessment is attached to the candidate record and ready for review.'}
                  </p>
                  <p className="mt-3 text-[14px] leading-7 text-[var(--mute)]">
                    {reportModel?.probeDescription || 'Open the assessment tab for dimension-level scoring, evidence cards, and a full session timeline.'}
                  </p>
                  <div className="mt-5 flex flex-wrap gap-2">
                    <button type="button" className="btn btn-outline btn-sm" onClick={handleCopyCurrentLink}>
                      Copy link
                    </button>
                    {applicationId ? (
                      <button type="button" className="btn btn-outline btn-sm" onClick={() => onNavigate('candidate-report', { candidateApplicationId: applicationId })}>
                        Standing report <span className="arrow">→</span>
                      </button>
                    ) : null}
                  </div>
                </Panel>

                <Panel className="p-6">
                  <h2 className="font-[var(--font-display)] text-[24px] font-semibold tracking-[-0.025em]">Scored <span className="text-[var(--purple)]">dimensions</span></h2>
                  <p className="mt-1 text-[13px] text-[var(--mute)]">The six axes called out in the redesign spec for recruiter review.</p>
                  <div className="mt-4">
                    {dimensionMetrics.map((item) => (
                      <DimensionRow key={item.key} item={item} />
                    ))}
                  </div>
                </Panel>
              </div>

              <div className="space-y-4">
                <Panel className="p-6">
                  <h2 className="font-[var(--font-display)] text-[24px] font-semibold tracking-[-0.025em]">Quick <span className="text-[var(--purple)]">facts</span></h2>
                  <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-1">
                    {quickFacts.map(([label, value]) => (
                      <div key={label} className="border-b border-[var(--line-2)] pb-3 last:border-b-0 last:pb-0">
                        <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)]">{label}</div>
                        <div className="mt-1 text-[14px]">{value || '—'}</div>
                      </div>
                    ))}
                  </div>
                </Panel>

                <Panel className="p-6">
                  <h2 className="font-[var(--font-display)] text-[24px] font-semibold tracking-[-0.025em]">Links <span className="text-[var(--purple)]">& docs</span></h2>
                  <div className="mt-4 grid gap-2">
                    {candidate.email ? (
                      <button type="button" className="filter-chip justify-between text-left" onClick={() => safeWindowOpen(`mailto:${candidate.email}`)}>
                        <span>{candidate.email}</span>
                        <ExternalLink size={13} />
                      </button>
                    ) : null}
                    <button type="button" className="filter-chip justify-between text-left" onClick={handleDownloadReport}>
                      <span>{`Assessment PDF · ${assessmentId}`}</span>
                      <Download size={13} />
                    </button>
                    {assessment?.candidate_id ? (
                      <button type="button" className="filter-chip justify-between text-left" onClick={handleDownloadCv}>
                        <span>{assessment?.candidate_cv_filename || assessment?.cv_filename || 'Candidate CV'}</span>
                        <Download size={13} />
                      </button>
                    ) : null}
                    {applicationId ? (
                      <button
                        type="button"
                        className="filter-chip justify-between text-left"
                        onClick={() => onNavigate('candidate-report', { candidateApplicationId: applicationId })}
                      >
                        <span>Standing report</span>
                        <Share2 size={13} />
                      </button>
                    ) : null}
                  </div>
                </Panel>

                <Panel className="p-6">
                  <h2 className="font-[var(--font-display)] text-[24px] font-semibold tracking-[-0.025em]">Hiring team <span className="text-[var(--purple)]">read</span></h2>
                  <p className="mt-3 text-[14px] leading-7 text-[var(--ink-2)]">
                    {role?.interview_focus?.role_summary || reportModel?.strongestSignalDescription || 'Use this space for recruiter notes and the strongest signal attached to the role.'}
                  </p>
                  {notes.length > 0 ? (
                    <div className="mt-4 space-y-3">
                      {notes.slice(0, 3).map((item) => (
                        <div key={item.id} className="rounded-[12px] border border-[var(--line-2)] bg-[var(--bg)] px-4 py-3">
                          <div className="text-[13px] font-semibold">
                            {item.author}
                            {item.timestamp ? (
                              <span className="ml-2 font-[var(--font-mono)] text-[11px] font-normal text-[var(--mute)]">
                                {formatDateTime(item.timestamp)}
                              </span>
                            ) : null}
                          </div>
                          <p className="mt-1 text-[13.5px] leading-6 text-[var(--ink-2)]">{item.text}</p>
                        </div>
                      ))}
                    </div>
                  ) : null}
                  <div className="mt-4 grid gap-2">
                    <Textarea
                      className="min-h-[84px]"
                      placeholder="Add a recruiter note for the next reviewer"
                      value={noteText}
                      onChange={(event) => setNoteText(event.target.value)}
                    />
                    <div className="flex justify-end">
                      <Button type="button" size="sm" variant="primary" onClick={handleAddNote} disabled={busyAction === 'note'}>
                        {busyAction === 'note' ? 'Posting...' : 'Post note'}
                      </Button>
                    </div>
                  </div>
                </Panel>
              </div>
            </div>
          ) : null}

          {activeTab === 'assessment' ? (
            <div className="grid gap-4 xl:grid-cols-[1.35fr_1fr]">
              <div className="space-y-4">
                <Panel className="p-6">
                  <h2 className="font-[var(--font-display)] text-[28px] font-semibold tracking-[-0.02em]">Scored <span className="text-[var(--purple)]">dimensions</span></h2>
                  <div className="mt-4">
                    {dimensionMetrics.map((item) => (
                      <DimensionRow key={item.key} item={item} />
                    ))}
                  </div>
                </Panel>

                <Panel className="p-6">
                  <h2 className="font-[var(--font-display)] text-[28px] font-semibold tracking-[-0.02em]">Live <span className="text-[var(--purple)]">evidence</span></h2>
                  <p className="mt-1 text-[13px] text-[var(--mute)]">Direct extracts from the recorded assessment session and final report payload.</p>
                  <div className="mt-4 space-y-3">
                    {evidenceCards.length ? evidenceCards.map((item, index) => (
                      <EvidenceCard key={item.id} item={item} index={index} />
                    )) : (
                      <div className="rounded-[14px] border border-dashed border-[var(--line)] bg-[var(--bg)] px-4 py-10 text-center text-sm text-[var(--mute)]">
                        No evidence cards are available yet for this assessment.
                      </div>
                    )}
                  </div>
                </Panel>
              </div>

              <div className="space-y-4">
                <Panel className="p-6">
                  <h2 className="font-[var(--font-display)] text-[28px] font-semibold tracking-[-0.02em]">AI-collaboration <span className="text-[var(--purple)]">fingerprint</span></h2>
                  <div className="mt-4 h-[300px]">
                    <ResponsiveContainer width="100%" height="100%">
                      <RadarChart data={radarData}>
                        <PolarGrid stroke="var(--line)" />
                        <PolarAngleAxis dataKey="metric" tick={{ fill: 'var(--ink-2)', fontSize: 11 }} />
                        <PolarRadiusAxis angle={90} domain={[0, 10]} tick={false} axisLine={false} />
                        <Radar dataKey="score" stroke="var(--purple)" fill="var(--purple)" fillOpacity={0.18} strokeWidth={2.4} />
                      </RadarChart>
                    </ResponsiveContainer>
                  </div>
                </Panel>

                <Panel className="p-6">
                  <h2 className="font-[var(--font-display)] text-[26px] font-semibold tracking-[-0.02em]">Session <span className="text-[var(--purple)]">timeline</span></h2>
                  <div className="mt-4 space-y-3">
                    {timelineRows.length ? timelineRows.map((row) => (
                      <TimelineRow key={row.id} row={row} />
                    )) : (
                      <div className="rounded-[14px] border border-dashed border-[var(--line)] bg-[var(--bg)] px-4 py-10 text-center text-sm text-[var(--mute)]">
                        Timeline events are not available for this assessment yet.
                      </div>
                    )}
                  </div>
                </Panel>
              </div>
            </div>
          ) : null}

          {activeTab === 'role-fit' ? (
            <CandidateCvFitTab
              candidate={candidate}
              onDownloadCandidateDoc={async (docType) => {
                if (!assessment?.candidate_id) return;
                await handleDownloadBlob(
                  () => candidatesApi.downloadDocument(assessment.candidate_id, docType),
                  docType === 'cv'
                    ? (assessment?.candidate_cv_filename || assessment?.cv_filename || 'candidate-cv.pdf')
                    : 'job-spec.pdf',
                  'Failed to download candidate document.',
                );
              }}
              onRequestCvUpload={async () => {
                if (!assessmentId) return;
                setBusyAction('request-cv');
                try {
                  await assessmentsApi.resend(assessmentId);
                  showToast('CV upload request sent.', 'success');
                } catch (err) {
                  showToast(err?.response?.data?.detail || 'Failed to send CV request.', 'error');
                } finally {
                  setBusyAction('');
                }
              }}
              requestingCvUpload={busyAction === 'request-cv'}
            />
          ) : null}

          {activeTab === 'interview' ? (
            <div className="grid gap-4 xl:grid-cols-[1.2fr_1fr]">
              <div className="space-y-4">
                <Panel className="p-6">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <h2 className="font-[var(--font-display)] text-[28px] font-semibold tracking-[-0.025em]">Questions the <span className="text-[var(--purple)]">panel</span> should ask</h2>
                      <p className="mt-1 text-[13px] text-[var(--mute)]">Role-level interview focus, anchored to the current hiring loop.</p>
                    </div>
                    <Button
                      type="button"
                      size="sm"
                      variant="primary"
                      onClick={() => handleGenerateInterviewGuide({ forceRegenerate: Boolean(interviewDebriefData) })}
                      disabled={interviewDebriefLoading}
                    >
                      <Sparkles size={14} />
                      {interviewDebriefLoading ? 'Generating...' : interviewDebriefData ? 'Regenerate guide' : 'Generate guide'}
                    </Button>
                  </div>

                  {role?.interview_focus?.role_summary ? (
                    <p className="mt-4 text-[14px] leading-7 text-[var(--ink-2)]">{role.interview_focus.role_summary}</p>
                  ) : null}

                  {focusTriggers.length > 0 ? (
                    <div className="mt-4 flex flex-wrap gap-2">
                      {focusTriggers.map((item) => (
                        <Badge key={item} variant="muted">{item}</Badge>
                      ))}
                    </div>
                  ) : null}

                  <div className="mt-5 space-y-3">
                    {focusQuestions.length ? focusQuestions.map((item, index) => (
                      <FocusQuestionCard key={`${item.question}-${index}`} item={item} index={index} />
                    )) : (
                      <div className="rounded-[14px] border border-dashed border-[var(--line)] bg-[var(--bg)] px-4 py-10 text-center text-sm text-[var(--mute)]">
                        No role-specific interview focus has been generated yet.
                      </div>
                    )}
                  </div>
                </Panel>
              </div>

              <div className="space-y-4">
                <Panel className="p-6">
                  <h2 className="font-[var(--font-display)] text-[26px] font-semibold tracking-[-0.025em]">Generated <span className="text-[var(--purple)]">interview guide</span></h2>
                  <div className="mt-4">
                    {interviewDebriefData || interviewDebriefLoading ? (
                      <CandidateInterviewDebrief
                        debrief={interviewDebriefData}
                        loading={interviewDebriefLoading}
                        cached={interviewDebriefCached}
                        generatedAt={interviewDebriefGeneratedAt}
                        onCopyMarkdown={handleCopyInterviewMarkdown}
                        onPrint={handlePrintInterviewDebrief}
                        onRegenerate={() => handleGenerateInterviewGuide({ forceRegenerate: true })}
                      />
                    ) : (
                      <div className="rounded-[14px] border border-dashed border-[var(--line)] bg-[var(--bg)] px-4 py-10 text-center text-sm text-[var(--mute)]">
                        Generate a guide to turn the assessment evidence into structured panel questions.
                      </div>
                    )}
                  </div>
                </Panel>
              </div>
            </div>
          ) : null}

          {activeTab === 'evaluate' ? (
            <CandidateEvaluateTab
              candidate={candidate}
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
              toLineList={(value) => String(value || '').split('\n').map((line) => line.trim()).filter(Boolean)}
              toEvidenceTextareaValue={(value) => (Array.isArray(value) ? value.filter(Boolean).join('\n') : String(value || ''))}
              assessmentsApi={assessmentsApi}
              onFinalizeCandidateFeedback={() => {}}
              finalizeFeedbackLoading={false}
              candidateFeedbackReady={false}
              candidateFeedbackSentAt={null}
              canFinalizeCandidateFeedback={false}
            />
          ) : null}

          {activeTab === 'report' ? (
            <div className="space-y-4">
              <CandidateAssessmentSummaryView reportModel={reportModel} />
              <div className="flex flex-wrap gap-2">
                {applicationId ? (
                  <Button type="button" variant="primary" onClick={() => onNavigate('candidate-report', { candidateApplicationId: applicationId })}>
                    Open standing report
                  </Button>
                ) : null}
                <Button type="button" variant="secondary" onClick={handleDownloadReport} disabled={busyAction !== ''}>
                  {busyAction === 'download-report' ? 'Downloading...' : 'Download PDF'}
                </Button>
                <Button type="button" variant="danger" onClick={handleDeleteAssessment} disabled={busyAction === 'delete'}>
                  {busyAction === 'delete' ? 'Deleting...' : 'Delete assessment'}
                </Button>
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </AppShell>
  );
};

export const CandidateDetailPage = AssessmentResultsPage;

export default CandidateDetailPage;
