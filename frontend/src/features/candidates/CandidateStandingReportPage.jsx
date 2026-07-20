import React, { Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { useLocation, useParams, useSearchParams } from 'react-router-dom';
import { AlertTriangle, Copy, ExternalLink, Eye, Flag, MoreHorizontal, ShieldAlert, Sparkles } from 'lucide-react';

import '../../styles/08-candidate-detail.css';
import '../../styles/09-standing-report.css';

import * as apiClient from '../../shared/api';
import { viewShareLink } from '../../shared/api';
import {
  AgentLoop,
  MOTION_DURATION,
  MOTION_STAGGER,
  MotionNumber,
  MotionDisclosure,
  MotionProgress,
  MotionStagger,
} from '../../shared/motion';
import { useToast } from '../../context/ToastContext';
import {
  Button,
  Card,
  Input,
  Panel,
  PageLoader,
  Select,
  Textarea,
} from '../../shared/ui/TaaliPrimitives';
import { FocusedSectionNav } from '../../shared/ui/SectionNavigation';
import { BreadcrumbsRow } from '../../shared/ui/Breadcrumbs';
import { DecisionRail } from './DecisionRail';
import { useReportInFlight } from './useReportInFlight';
import { OverrideModal } from '../home/OverrideModal';
import { TeachModal } from '../home/TeachModal';
import { DECISION_ACTIONS } from '../../shared/decisions/decisionActions';
import {
  asProcessingDecision,
  createApprovalReceiptOverlay,
  reconcileProcessingDecision,
} from '../../shared/decisions/approvalReceipt';
import {
  APPROVAL_OUTCOME_UNKNOWN_MESSAGE,
  approveDecisionWithReconciliation,
  isApprovalOutcomeUnknownError,
} from '../../shared/decisions/approvalReconciliation';
import {
  isApprovalBlockingStale,
  isEngineOnlyStale,
} from '../../shared/decisions/decisionStaleness';
import { normaliseDecisionText } from '../../shared/decisions/decisionText';
import { buildClientReportFilenameStem } from './clientReportUtils';
import { computeScorecard } from '../../shared/assessment/fluency4d';
import { ErrorBoundary } from '../../shared/ui/ErrorBoundary';
import { buildStandingCandidateReportModel, COMPLETED_ASSESSMENT_STATUSES, mapAssessmentToCandidateView } from './assessmentViewModels';
// ApplicationDecisionPanel intentionally NOT imported — PR3 retired the decision
// recorder from the report body; the candidate's decision now lives in the
// DecisionRail (the dossier's left column). The component file is kept for reference.
// Lazy-load the assessment-evidence panels. They (via CandidateDetailSecondaryTabs)
// statically pull the ~383KB recharts vendor chunk, but they only render inside
// the Assessment / Notes panes — secondary tabs many recruiters never open. A
// dynamic import boundary keeps charts_vendor off the critical path of the
// most-opened drill-down, loading it only when the tab is activated.
const AssessmentEvidencePanels = React.lazy(() =>
  import('./CandidateAssessmentDetailPanels').then((m) => ({ default: m.AssessmentEvidencePanels })));
const EvaluatePanel = React.lazy(() =>
  import('./CandidateAssessmentDetailPanels').then((m) => ({ default: m.EvaluatePanel })));
import { AssessmentScorecard, readGradedRubricDimensions } from './AssessmentScorecard';
import { CandidateSnapshotCard } from './CandidateSnapshotCard';
import { CvDocumentViewer } from './CvDocumentViewer';
import { CvMatchReview } from './CvMatchReview';
import { PrepQuestionCard } from './PrepQuestionCard';
import { InterviewFeedbackSection } from './InterviewFeedbackSection';
import { TranscriptPanel } from './CandidateInterviewStageViews';
import { VerdictDetail } from './VerdictDetail';
import {
  getErrorMessage,
  reqGradeKey,
  resolveCvMatchDetails,
} from './candidatesUiUtils';

const resolveAssessmentId = (application) => (
  application?.score_summary?.assessment_id
  || application?.valid_assessment_id
  || null
);

const resolveAssessmentStatus = (application) => (
  String(application?.score_summary?.assessment_status || application?.valid_assessment_status || '').toLowerCase()
);

const positiveIntegerOrNull = (value) => {
  if (value == null || String(value).trim() === '') return null;
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
};

// Candidate file is the single canonical candidate page. Base tabs are
// always present; assessment-only tabs (requiresAssessment) reveal once a
// completed assessment is linked — replacing the separate /assessments/:id
// page. Visibility flags:
//   internalOnly  — recruiter app only; hidden on every share link.
//   recruiterOnly — recruiter app + recruiter share link; hidden from
//                   external client shares.
const REPORT_TABS = [
  { id: 'overview', label: 'Overview' },
  // Requirements & fit — the per-requirement match breakdown (the CvMatchReview
  // rows) lives in its own tab, matching report-preview. Client-visible: the
  // requirement coverage is part of the candidate's standing story, not an
  // internal-only surface.
  { id: 'requirements', label: 'Requirements' },
  // PR3 (decision-surface unification): the standalone Evaluate tab is retired.
  // The candidate's DECISION lives in the DecisionRail (the dossier's left
  // column), and the Evaluate tab's assessment EVIDENCE (criteria ratings, manual
  // rubric, strengths/improvements, chat log) now renders inside this Assessment
  // pane via <EvaluatePanel hideDecision />.
  { id: 'assessment', label: 'Assessment', internalOnly: true, requiresAssessment: true },
  { id: 'cv', label: 'CV' },
  { id: 'prep', label: 'Interview', recruiterOnly: true },
  // Notes & timeline is the single place for hiring-team context, structured
  // interview feedback, and the candidate's activity history.
  { id: 'notes', label: 'Notes & timeline', recruiterOnly: true },
];

const INTERNAL_TABS = new Set(REPORT_TABS.filter((tab) => tab.internalOnly).map((tab) => tab.id));
const CLIENT_HIDDEN_TABS = new Set(
  REPORT_TABS.filter((tab) => tab.internalOnly || tab.recruiterOnly).map((tab) => tab.id),
);
const REPORT_TAB_IDS = new Set(REPORT_TABS.map((tab) => tab.id));

// Stable empty-rubric reference so the Evaluate panel's draft-init effect
// (keyed on the rubric identity) doesn't reset recruiter input every render.
const EMPTY_RUBRIC = Object.freeze({});

// Overview "Flags" — the claims/signals the agent couldn't corroborate. Shows
// the first 3 with a toggle pinned at the BOTTOM ("+N more flags" / "Show
// fewer") so the control never sits in the middle of the list (the old
// <details><summary> did). All data is real (claims_to_verify + integrity).
const OverviewFlags = ({ flags }) => {
  const [showAll, setShowAll] = useState(false);
  if (!flags.length) return null;
  const visible = showAll ? flags : flags.slice(0, 3);
  const hidden = flags.length - 3;
  return (
    <section className="mc-flags" aria-label="Flags to verify">
      <div className="mc-flags-head">
        <span className="mc-kicker mc-kicker-amber">Flags</span>
        <span className="mc-flags-chip"><Flag size={12} aria-hidden="true" /> {flags.length} to verify</span>
      </div>
      <p className="mc-flags-sub">Claims and signals the agent couldn&apos;t corroborate — verify before deciding.</p>
      {visible.map((f, i) => (
        <div className="mc-flag" key={`flag-${i}`}>
          <AlertTriangle size={15} className="mc-flag-i" aria-hidden="true" />
          <span>
            {f.label ? <b>{f.label}</b> : null}{f.label ? ' — ' : ''}{f.text}
            {f.why ? <span className="mc-flag-why"> — {f.why}</span> : null}
          </span>
        </div>
      ))}
      {hidden > 0 ? (
        <button type="button" className="mc-flags-toggle" onClick={() => setShowAll((v) => !v)}>
          {showAll ? 'Show fewer' : `+ ${hidden} more flag${hidden === 1 ? '' : 's'}`}
        </button>
      ) : null}
    </section>
  );
};

// Compact "Integrity" chip — the recruiter-facing trust readout that sits BESIDE
// the match score (never lowers it). Renders ONLY when the server's triangulation
// verdict is `review` (one soft disagreement) or `strong_review` (a deterministic
// artifact or >=2 disagreements); a clean `ok` verdict shows nothing. Purple-scale
// intensity, never red/green (per the design system). Click to expand the
// canonical warnings + corroboration notes + unverified employer names.
export const IntegrityChip = ({ verdict, trustBand, warnings, corroborations, unverifiedEmployers }) => {
  const [open, setOpen] = useState(false);
  if (verdict !== 'review' && verdict !== 'strong_review') return null;
  const strong = verdict === 'strong_review';
  const count = (warnings?.length || 0) + (unverifiedEmployers?.length || 0);
  // Only render the trust-band pill for a KNOWN band. A `review` verdict can
  // arrive with a null/unknown band; defaulting to "High trust" beside a
  // warning chip was a false reassurance, so omit the pill instead.
  const bandLabel = trustBand === 'low'
    ? 'Low'
    : (trustBand === 'medium' ? 'Medium' : (trustBand === 'high' ? 'High' : null));
  return (
    <section className={`mc-integrity ${strong ? 'mc-integrity-strong' : ''}`.trim()} aria-label="Integrity check">
      <button
        type="button"
        className="mc-integrity-chip"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <ShieldAlert size={14} aria-hidden="true" />
        <span className="mc-integrity-label">Integrity</span>
        {bandLabel ? <span className="mc-integrity-band">{bandLabel} trust</span> : null}
        {count > 0 ? <span className="mc-integrity-count">{count} to verify</span> : null}
      </button>
      {open ? (
        <div className="mc-integrity-body">
          {warnings?.length ? (
            <ul className="mc-integrity-list">
              {warnings.map((w, i) => (
                <li key={`iw-${i}`}><AlertTriangle size={13} aria-hidden="true" /> {w}</li>
              ))}
            </ul>
          ) : null}
          {unverifiedEmployers?.length ? (
            <p className="mc-integrity-emp">
              Employer{unverifiedEmployers.length === 1 ? '' : 's'} not verbatim in the CV text:{' '}
              {unverifiedEmployers.map((c) => `"${c}"`).join(', ')}.
            </p>
          ) : null}
          {corroborations?.length ? (
            <ul className="mc-integrity-corrob">
              {corroborations.map((c, i) => (
                <li key={`ic-${i}`}>{c}</li>
              ))}
            </ul>
          ) : null}
          <p className="mc-integrity-note">Advisory only — this never changes the match score. Verify before deciding.</p>
        </div>
      ) : null}
    </section>
  );
};

// One 5-Ds axis score. Changes interpolate from the previously rendered score;
// reduced motion settles immediately and missing signals stay non-numeric.
export const DimScore = ({ score, hasSignal }) => (
  <span className="mc-overview-dim-score">
    {hasSignal ? <MotionNumber value={Math.round(score)} /> : '—'}
    {hasSignal ? <span className="mc-overview-dim-suffix">/100</span> : null}
  </span>
);

export const CandidateStandingReportPage = ({ onNavigate, NavComponent = null }) => {
  const { showToast } = useToast();
  const location = useLocation();
  // ``shareToken`` is set when the SPA is mounted via the public
  // ``/share/:shareToken`` route. ``applicationId`` is set on the
  // recruiter-side ``/c/:applicationId`` and ``/candidates/:applicationId``
  // routes. Exactly one is present at a time.
  const { applicationId, shareToken: routeShareToken } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const rolesApi = 'roles' in apiClient ? apiClient.roles : null;
  const assessmentsApi = 'assessments' in apiClient ? apiClient.assessments : null;
  const candidatesApi = 'candidates' in apiClient ? apiClient.candidates : null;

  const [application, setApplication] = useState(null);
  const [completedAssessment, setCompletedAssessment] = useState(null);
  const [loading, setLoading] = useState(true);
  // Silent revalidate after a recruiter action (approve, note save, re-evaluate).
  // Keeps the rendered report on screen (no full-page spinner, no scroll loss)
  // while the fresh data lands. Only the cold load blanks to a skeleton.
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [busyAction, setBusyAction] = useState('');
  // A full CV evaluation was queued for a pre-screened-out candidate and the
  // score hasn't landed yet. Drives the in-flight banner + the poll below,
  // so the recruiter never has to guess-and-refresh a paid job.
  const [evaluating, setEvaluating] = useState(false);
  // Assessment-tab admin actions (resend invite / request CV / delete) live in
  // a small overflow menu so destructive controls no longer lead the pane.
  const [assessmentActionsOpen, setAssessmentActionsOpen] = useState(false);
  const assessmentActionsRef = React.useRef(null);
  // Close the assessment Actions overflow menu on outside click / Escape so a
  // destructive "Delete assessment" item can't hang over the pane after the
  // recruiter clicks elsewhere.
  useEffect(() => {
    if (!assessmentActionsOpen) return undefined;
    const onPointerDown = (e) => {
      if (!assessmentActionsRef.current?.contains(e.target)) setAssessmentActionsOpen(false);
    };
    const onKeyDown = (e) => { if (e.key === 'Escape') setAssessmentActionsOpen(false); };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [assessmentActionsOpen]);
  // Tracks which share button is mid-mint so we can disable it + show a
  // "Copying…" label. '' when idle, 'recruiter' or 'client' when busy.
  const [sharingMode, setSharingMode] = useState('');
  const [applicationEvents, setApplicationEvents] = useState([]);
  // Notes & context tab — local note draft + a tick that lets us refetch
  // the events feed after a successful save without a full page reload.
  const [noteDraft, setNoteDraft] = useState('');
  const [savingNote, setSavingNote] = useState(false);
  // Per-candidate notes default to agent-visible — they're almost always
  // guidance the agent should weigh ("already interviewed — not suitable").
  // Untick for pure team chatter the agent shouldn't read.
  const [noteForAgent, setNoteForAgent] = useState(true);
  const [eventsRefetchTick, setEventsRefetchTick] = useState(0);
  const [supportingLinkOpen, setSupportingLinkOpen] = useState(false);
  // Optional supporting link, stored via the same note endpoint with kind
  // `link`. It defaults to agent-visible alongside the freeform note box.
  const [linkUrl, setLinkUrl] = useState('');
  const [linkLabel, setLinkLabel] = useState('');
  const [savingLink, setSavingLink] = useState(false);
  // View mode received from the backend when loaded via /share/:token —
  // "client" (scrubbed external view) or "recruiter" (full report). Null
  // when not on a share route (recruiter is logged in and viewing /c/:id).
  const [shareViewMode, setShareViewMode] = useState(null);
  // PR2 (decision-surface unification): the candidate's own pending agent
  // decision, surfaced in the header strip with the SAME Approve / Override /
  // Teach controls as the home hub. Recruiter-view only (the fetch + render
  // are both gated on !isClientView && !isInterviewView below).
  const [agentDecision, setAgentDecision] = useState(null);
  const decisionApprovalReceiptRef = React.useRef(null);
  const decisionReadSequenceRef = React.useRef(0);
  const [decisionBusy, setDecisionBusy] = useState(false);
  // Modal targets — mirrors HomeNow's teachFor / alternativeFor. ``alternativeFor``
  // drives OverrideModal for both overrides AND the primary-advance confirm.
  const [teachFor, setTeachFor] = useState(null);
  const [alternativeFor, setAlternativeFor] = useState(null);

  const routeApplicationKey = String(applicationId || '').trim();
  const sharedRouteToken = String(routeShareToken || '').trim();
  const isShareRoute = Boolean(sharedRouteToken);
  const numericApplicationId = Number(routeApplicationKey);
  const isClientView = shareViewMode === 'client';
  // Any share-route recipient (client OR recruiter view) hides internal
  // recruiter-only controls like "Rescore" and "Share" actions.
  const isInterviewView = isShareRoute;

  const hiddenTabs = isClientView
    ? CLIENT_HIDDEN_TABS
    : (isInterviewView ? INTERNAL_TABS : new Set());
  const requestedTab = searchParams.get('tab') || 'overview';
  // Back-link source of truth is ?from. ?from=jobs/<id> → role pipeline;
  // anything else (including ?from=home or absent) → /home. Using
  // application.role_id here would always go to the job pipeline since
  // every application has a role, even when the user arrived from /home.
  const backFromRoleId = useMemo(() => {
    const match = (searchParams.get('from') || '').match(/^jobs\/(\d+)$/);
    return match ? Number(match[1]) : null;
  }, [searchParams]);
  // Related-role decisions reuse the source application id, so Home must carry
  // the role whose score was shown. Job links already carry the same context in
  // `from=jobs/<id>`; keep that as the backwards-compatible fallback.
  const viewRoleId = useMemo(() => {
    const explicitViewRoleId = positiveIntegerOrNull(searchParams.get('view_role_id'));
    if (explicitViewRoleId) return explicitViewRoleId;
    return backFromRoleId;
  }, [backFromRoleId, searchParams]);

  // Route params can change without remounting this component. Every async
  // report/decision completion carries this generation so a slower request for
  // candidate A can never repaint candidate B after navigation.
  const reportScopeKey = `${routeApplicationKey}|${sharedRouteToken}|${viewRoleId || ''}`;
  const reportScopeRef = React.useRef(reportScopeKey);
  reportScopeRef.current = reportScopeKey;
  const isCurrentReportScope = useCallback(
    (scope) => reportScopeRef.current === scope,
    [],
  );
  const applyCanonicalAgentDecision = useCallback((canonicalDecision, scope, readSequence = null) => {
    if (!isCurrentReportScope(scope)) return false;
    if (readSequence !== null && decisionReadSequenceRef.current !== readSequence) return false;
    // Local mutation receipts are authoritative and invalidate reads that began
    // before they arrived. Server reads pass their sequence and are last-started
    // wins, preventing a slower stale response from reviving an older state.
    if (readSequence === null) decisionReadSequenceRef.current += 1;
    const reconciled = reconcileProcessingDecision(
      canonicalDecision,
      decisionApprovalReceiptRef.current,
    );
    decisionApprovalReceiptRef.current = reconciled.overlay;
    setAgentDecision(reconciled.decision);
    return true;
  }, [isCurrentReportScope]);
  const freezeAgentDecision = useCallback(
    (source, row, scope = reportScopeRef.current) => {
      if (!isCurrentReportScope(scope)) return false;
      decisionReadSequenceRef.current += 1;
      decisionApprovalReceiptRef.current = createApprovalReceiptOverlay(source, row);
      setAgentDecision(row);
      return true;
    },
    [isCurrentReportScope],
  );
  const restoreAgentDecision = useCallback((source, scope) => {
    if (!isCurrentReportScope(scope)) return false;
    decisionReadSequenceRef.current += 1;
    decisionApprovalReceiptRef.current = null;
    setAgentDecision(source);
    return true;
  }, [isCurrentReportScope]);

  // React Router reuses this component when only the candidate/role params
  // change. Never carry a prior candidate's decision or local approval receipt
  // into the next dossier while its own decision read is still loading (or if
  // that non-critical read fails).
  React.useLayoutEffect(() => {
    decisionReadSequenceRef.current += 1;
    decisionApprovalReceiptRef.current = null;
    setAgentDecision(null);
    setTeachFor(null);
    setAlternativeFor(null);
    setDecisionBusy(false);
  }, [reportScopeKey]);
  const [activeTab, setActiveTab] = useState(
    REPORT_TAB_IDS.has(requestedTab) ? requestedTab : 'overview'
  );
  // Keep the expensive assessment module out of the initial report bundle.
  // Once opened it stays mounted so an in-progress manual evaluation is not
  // discarded when the recruiter briefly checks another tab.
  const [assessmentContentMounted, setAssessmentContentMounted] = useState(
    requestedTab === 'assessment'
  );

  useEffect(() => {
    if (activeTab === 'assessment') setAssessmentContentMounted(true);
  }, [activeTab]);

  useEffect(() => {
    setAssessmentContentMounted(requestedTab === 'assessment');
    setSupportingLinkOpen(false);
  }, [routeApplicationKey, sharedRouteToken]);

  // Assessment-only tabs reveal once a completed assessment is linked.
  // `completedAssessment` is only fetched when the latest attempt is in a
  // completed status (see loadStandingReport), so this mirrors "appears on
  // completion" without an extra flag.
  const hasAssessmentDetail = Boolean(completedAssessment);
  const availableTabIds = useMemo(() => new Set(
    REPORT_TABS
      .filter((tab) => !hiddenTabs.has(tab.id) && (!tab.requiresAssessment || hasAssessmentDetail))
      .map((tab) => tab.id)
  ), [hiddenTabs, hasAssessmentDetail]);

  useEffect(() => {
    document.body.classList.toggle('interview-view', isInterviewView);
    return () => {
      document.body.classList.remove('interview-view');
    };
  }, [isInterviewView]);

  useEffect(() => {
    const nextTab = REPORT_TAB_IDS.has(requestedTab) ? requestedTab : 'overview';
    const safeTab = availableTabIds.has(nextTab) ? nextTab : 'overview';
    setActiveTab(safeTab);
    // If the URL asked for a tab that isn't available (e.g. ?tab=assessment on
    // a candidate with no completed assessment), rewrite the search param so a
    // shared link doesn't keep advertising a tab the recipient can't see.
    // Crucially, wait for the cold load to finish: during it completedAssessment
    // is still null, so `availableTabIds` doesn't yet include the assessment
    // tab. Rewriting then would delete a deep link's ?tab=assessment before the
    // data lands, dropping scored candidates onto Overview. Only heal once the
    // report has actually loaded and the tab is genuinely unavailable.
    if (loading) return;
    const currentTabParam = searchParams.get('tab');
    const desiredTabParam = safeTab === 'overview' ? null : safeTab;
    if (currentTabParam !== desiredTabParam) {
      const nextParams = new URLSearchParams(searchParams);
      if (desiredTabParam) nextParams.set('tab', desiredTabParam);
      else nextParams.delete('tab');
      setSearchParams(nextParams, { replace: true });
    }
  }, [availableTabIds, requestedTab, searchParams, setSearchParams, loading]);

  const activateTab = useCallback((tabId) => {
    const safeTab = availableTabIds.has(tabId) ? tabId : 'overview';
    setActiveTab(safeTab);
    const nextParams = new URLSearchParams(searchParams);
    if (safeTab === 'overview') {
      nextParams.delete('tab');
    } else {
      nextParams.set('tab', safeTab);
    }
    setSearchParams(nextParams);
  }, [availableTabIds, searchParams, setSearchParams]);

  const reportNavigationItems = useMemo(() => (
    REPORT_TABS
      .filter((tab) => availableTabIds.has(tab.id))
      .map((tab) => {
        const nextParams = new URLSearchParams(searchParams);
        if (tab.id === 'overview') nextParams.delete('tab');
        else nextParams.set('tab', tab.id);
        const query = nextParams.toString();
        return {
          id: tab.id,
          label: tab.label,
          to: `${location.pathname}${query ? `?${query}` : ''}${location.hash}`,
          className: tab.internalOnly ? 'is-internal-only' : '',
        };
      })
  ), [availableTabIds, location.hash, location.pathname, searchParams]);

  const loadStandingReport = useCallback(async ({ silent = false } = {}) => {
    const loadScope = reportScopeKey;
    if (!isCurrentReportScope(loadScope)) return;
    const decisionReadSequence = !isShareRoute
      ? ++decisionReadSequenceRef.current
      : null;
    if (routeApplicationKey === 'demo') {
      const {
        AI_SHOWCASE_APPLICATION,
        AI_SHOWCASE_APPLICATION_EVENTS,
        AI_SHOWCASE_AGENT_DECISION,
        AI_SHOWCASE_COMPLETED_ASSESSMENT,
      } = await import('../demo/productWalkthroughModels');
      if (!isCurrentReportScope(loadScope)) return;
      setApplication(AI_SHOWCASE_APPLICATION);
      setCompletedAssessment(AI_SHOWCASE_COMPLETED_ASSESSMENT);
      setApplicationEvents(AI_SHOWCASE_APPLICATION_EVENTS);
      // Show the agent's deterministic recommendation (the demo previously fell
      // back to the "not yet decided" placeholder despite a completed score).
      applyCanonicalAgentDecision(AI_SHOWCASE_AGENT_DECISION, loadScope);
      setShareViewMode(null);
      setError('');
      setLoading(false);
      return;
    }

    const canLoadById = !isShareRoute && rolesApi?.getApplication && Number.isFinite(numericApplicationId);
    const canLoadByShare = Boolean(isShareRoute && sharedRouteToken);
    if (!canLoadById && !canLoadByShare) {
      if (!isCurrentReportScope(loadScope)) return;
      setApplication(null);
      setCompletedAssessment(null);
      setError('Candidate report unavailable.');
      setLoading(false);
      return;
    }

    // Cold load blanks to a skeleton; a silent revalidate keeps the report
    // painted and only dims the affected sections (refreshing flag).
    if (silent) setRefreshing(true); else setLoading(true);
    setError('');
    try {
      const canUseInternalApis = !isShareRoute;
      let nextApplication = null;

      if (isShareRoute) {
        // /share/:token unauth flow — backend returns the full application
        // payload plus the view mode in one round-trip; nothing else to fetch.
        const shareRes = await viewShareLink(sharedRouteToken);
        if (!isCurrentReportScope(loadScope)) return;
        const payload = shareRes?.data || {};
        nextApplication = payload.application || null;
        setShareViewMode(payload.view === 'client' ? 'client' : 'recruiter');
        setApplication(nextApplication);
        const sharedEvents = Array.isArray(nextApplication?.application_events)
          ? nextApplication.application_events
          : [];
        setCompletedAssessment(null);
        applyCanonicalAgentDecision(null, loadScope);
        setApplicationEvents(sharedEvents);
      } else {
        // Recruiter path: role-aware links can fetch the projected application,
        // events and matching decision in the SAME wave. A legacy bare link has
        // no role context, so its decision request waits for the application and
        // uses that canonical role rather than accidentally selecting a newer
        // decision from a related role that shares the application id.
        // Drop include_cv_text: the CV tab is one of six and CvDocumentViewer
        // lazy-gates its own preview, so shipping the full parsed CV text on
        // every open was pure over-fetch.
        const appRequest = rolesApi.getApplication(
          numericApplicationId,
          viewRoleId ? { params: { view_role_id: viewRoleId } } : {},
        );
        const decisionRequest = apiClient.agent?.listDecisions && Number.isFinite(numericApplicationId)
          ? (viewRoleId
              ? apiClient.agent.listDecisions({
                  application_id: numericApplicationId,
                  role_id: viewRoleId,
                  status: 'current',
                  limit: 1,
                })
              : appRequest.then((appResponse) => {
                  const applicationRoleId = positiveIntegerOrNull(appResponse?.data?.role_id);
                  if (!applicationRoleId) return null;
                  return apiClient.agent.listDecisions({
                    application_id: numericApplicationId,
                    role_id: applicationRoleId,
                    status: 'current',
                    limit: 1,
                  });
                }))
              .catch(() => null)
          : Promise.resolve(null);
        const [appRes, eventsRes, initialDecisionRes] = await Promise.all([
          appRequest,
          rolesApi?.listApplicationEvents && Number.isFinite(numericApplicationId)
            ? rolesApi.listApplicationEvents(numericApplicationId).catch(() => null)
            : Promise.resolve(null),
          // Include resolved rows so approving a decision does not erase why it
          // was made from the standing report. Failure must not blank the report.
          decisionRequest,
        ]);
        if (!isCurrentReportScope(loadScope)) return;
        const returnedRoleId = positiveIntegerOrNull(appRes?.data?.role_id);
        let decisionRes = initialDecisionRes;
        // The detail endpoint intentionally falls back to the canonical
        // application when a requested sister role is no longer projectable.
        // Reconcile the decision to the role actually returned so a stale link
        // can never pair one role's score with another role's recommendation.
        if (viewRoleId && returnedRoleId !== viewRoleId) {
          decisionRes = returnedRoleId && apiClient.agent?.listDecisions
            ? await apiClient.agent.listDecisions({
                application_id: numericApplicationId,
                role_id: returnedRoleId,
                status: 'current',
                limit: 1,
              }).catch(() => null)
            : null;
          if (!isCurrentReportScope(loadScope)) return;
        }
        nextApplication = appRes?.data || null;
        setShareViewMode(null);
        setApplication(nextApplication);

        const assessmentId = resolveAssessmentId(nextApplication);
        const hasCompletedAssessment = Boolean(
          assessmentId
          && COMPLETED_ASSESSMENT_STATUSES.has(resolveAssessmentStatus(nextApplication))
        );
        const assessmentRes = canUseInternalApis && hasCompletedAssessment && assessmentsApi?.get
          ? await assessmentsApi.get(Number(assessmentId))
          : null;
        if (!isCurrentReportScope(loadScope)) return;

        setCompletedAssessment(assessmentRes?.data || null);
        // A decision read failure is converted to null above so it cannot blank
        // the report. Preserve any local approval receipt in that case; an
        // actual successful empty response is `{ data: [] }` and clears it.
        if (decisionRes !== null) {
          applyCanonicalAgentDecision(
            Array.isArray(decisionRes?.data) ? (decisionRes.data[0] || null) : null,
            loadScope,
            decisionReadSequence,
          );
        }
        const sharedEvents = Array.isArray(nextApplication?.application_events)
          ? nextApplication.application_events
          : [];
        setApplicationEvents(
          Array.isArray(eventsRes?.data)
            ? eventsRes.data
            : (eventsRes?.data?.items || sharedEvents)
        );
      }
    } catch (err) {
      if (!isCurrentReportScope(loadScope)) return;
      const message = getErrorMessage(err, 'Failed to load candidate report.');
      setApplication(null);
      setCompletedAssessment(null);
      setApplicationEvents([]);
      setError(message);
      // Don't toast on share-route failures — the page is unauth and
      // the visible error message is the whole story. Toast was a
      // recruiter-side affordance.
      if (!isShareRoute) showToast(message, 'error');
    } finally {
      if (!isCurrentReportScope(loadScope)) return;
      setLoading(false);
      setRefreshing(false);
    }
  }, [applyCanonicalAgentDecision, assessmentsApi, isCurrentReportScope, isShareRoute, numericApplicationId, reportScopeKey, rolesApi, routeApplicationKey, sharedRouteToken, showToast, viewRoleId]);

  // Refetch JUST the candidate's latest decision (after an approve / override /
  // teach) without reloading the whole report. Recruiter-view only.
  const loadAgentDecision = useCallback(async () => {
    const loadScope = reportScopeKey;
    if (isShareRoute || !apiClient.agent?.listDecisions || !numericApplicationId) return;
    const decisionRoleId = positiveIntegerOrNull(application?.role_id) || viewRoleId;
    if (!decisionRoleId) return;
    const decisionReadSequence = ++decisionReadSequenceRef.current;
    try {
      const res = await apiClient.agent.listDecisions({
        application_id: numericApplicationId,
        role_id: decisionRoleId,
        status: 'current',
        limit: 1,
      });
      applyCanonicalAgentDecision(
        Array.isArray(res?.data) ? (res.data[0] || null) : null,
        loadScope,
        decisionReadSequence,
      );
    } catch {
      // A refetch failure shouldn't surface — the strip just keeps its
      // last-known state until the next full report load reconciles it.
    }
  }, [application?.role_id, applyCanonicalAgentDecision, isShareRoute, numericApplicationId, reportScopeKey, viewRoleId]);

  // 409 decision_stale — same shape HomeNow keys its stale messaging on.
  const isDecisionStaleError = useCallback((err) => {
    const detail = err?.response?.data?.detail;
    const code = typeof detail === 'object' && detail !== null ? detail.code : detail;
    return err?.response?.status === 409 && code === 'decision_stale';
  }, []);

  // Approve — mirrors HomeNow.handleApprove. Decision types whose action spec
  // carries a ``primary`` (i.e. advance_to_interview) open OverrideModal in
  // approve mode so the recruiter picks the Workable stage; everything else
  // approves the recommendation directly. No optimistic queue mechanics here —
  // there's a single decision on this page, not a queue to advance through.
  const handleDecisionApprove = useCallback(async (decision) => {
    if (!decision) return;
    const actionScope = reportScopeKey;
    if (isApprovalBlockingStale(decision)) {
      showToast("This decision's inputs changed — re-evaluate before approving.", 'warning');
      return;
    }
    const spec = DECISION_ACTIONS[decision.decision_type];
    if (spec?.primary) {
      setAlternativeFor({ decision, alternative: spec.primary, reportScope: actionScope });
      return;
    }
    setDecisionBusy(true);
    try {
      const { receipt, matchedDecision } = await approveDecisionWithReconciliation(
        apiClient.agent,
        decision,
        {},
        { force: isEngineOnlyStale(decision) },
      );
      if (!freezeAgentDecision(
        decision,
        asProcessingDecision(decision, matchedDecision || receipt?.data),
        actionScope,
      )) return;
      showToast('Accepted for processing.', 'success');
      await loadStandingReport({ silent: true });
    } catch (err) {
      if (!isCurrentReportScope(actionScope)) return;
      if (isApprovalOutcomeUnknownError(err)) {
        freezeAgentDecision(
          decision,
          asProcessingDecision(decision, {
            ...(err.observedDecision || {}),
            outcome_unknown: true,
          }),
          actionScope,
        );
        showToast(APPROVAL_OUTCOME_UNKNOWN_MESSAGE, 'error');
      } else if (isDecisionStaleError(err)) {
        showToast("This decision's inputs changed — re-evaluate to refresh it.", 'warning');
      } else {
        showToast(getErrorMessage(err, "Couldn't approve this decision."), 'error');
      }
    } finally {
      if (isCurrentReportScope(actionScope)) setDecisionBusy(false);
    }
  }, [freezeAgentDecision, isCurrentReportScope, isDecisionStaleError, loadStandingReport, reportScopeKey, showToast]);

  // Override — open OverrideModal for the chosen alternative (the POST happens
  // inside the modal once the recruiter fills in the required "why").
  const handleDecisionAlternative = useCallback((decision, alternative) => {
    setAlternativeFor({ decision, alternative, reportScope: reportScopeKey });
  }, [reportScopeKey]);

  const handleDecisionSnooze = useCallback(async (decision) => {
    if (!decision) return;
    const actionScope = reportScopeKey;
    setDecisionBusy(true);
    try {
      await apiClient.agent.snoozeDecision(decision.id);
      if (!isCurrentReportScope(actionScope)) return;
      showToast('Snoozed for 1h.', 'success');
      await loadAgentDecision();
    } catch (err) {
      if (isCurrentReportScope(actionScope)) {
        showToast(getErrorMessage(err, 'Snooze failed'), 'error');
      }
    } finally {
      if (isCurrentReportScope(actionScope)) setDecisionBusy(false);
    }
  }, [isCurrentReportScope, loadAgentDecision, reportScopeKey, showToast]);

  const handleDecisionReEvaluate = useCallback(async (decision) => {
    if (!decision) return;
    const actionScope = reportScopeKey;
    setDecisionBusy(true);
    try {
      await apiClient.agent.reEvaluateDecision(decision.id);
      if (!isCurrentReportScope(actionScope)) return;
      showToast('Re-evaluating with fresh inputs…', 'success');
      await loadStandingReport({ silent: true });
    } catch (err) {
      if (isCurrentReportScope(actionScope)) {
        showToast(getErrorMessage(err, 'Re-evaluate failed'), 'error');
      }
    } finally {
      if (isCurrentReportScope(actionScope)) setDecisionBusy(false);
    }
  }, [isCurrentReportScope, loadStandingReport, reportScopeKey, showToast]);

  // `eventsRefetchTick` is bumped after a recruiter saves a note so the report
  // picks up the new timeline event. That refetch must be SILENT — saving a
  // note should never blank the whole dossier to a spinner. The very first run
  // (mount / applicationId change) is a cold load; every tick-driven run after
  // is silent. loadStandingReport identity changes with the route id, so a
  // route change resets to a cold load correctly.
  const coldLoadedRef = React.useRef(false);
  useEffect(() => {
    coldLoadedRef.current = false;
  }, [loadStandingReport]);
  useEffect(() => {
    const silent = coldLoadedRef.current;
    coldLoadedRef.current = true;
    void loadStandingReport({ silent });
  }, [loadStandingReport, eventsRefetchTick]);

  // In-flight polling (full evaluation / re-score) + lazy CV-text fetch live in
  // a sibling hook so this page stays under the architecture line cap.
  useReportInFlight({
    rolesApi,
    numericApplicationId,
    viewRoleId,
    isShareRoute,
    activeTab,
    application,
    agentDecision,
    evaluating,
    setEvaluating,
    setApplication,
    loadAgentDecision,
    loadStandingReport,
  });

  const reportModel = useMemo(() => (
    application ? buildStandingCandidateReportModel({
      application,
      completedAssessment,
      identity: {
        assessmentId: completedAssessment?.id || resolveAssessmentId(application),
        sectionLabel: 'Standing report',
        name: application?.candidate_name || application?.candidate_email || 'Candidate',
        email: application?.candidate_email || '',
        position: application?.candidate_position || '',
        roleName: application?.role_name || '',
        applicationStatus: application?.application_outcome || application?.status || '',
      },
    }) : null
  ), [application, completedAssessment]);

  const assessmentId = completedAssessment?.id || resolveAssessmentId(application);
  const canOpenAssessmentDetail = Boolean(completedAssessment?.id);
  // Mapped assessment view for the Assessment + Evaluate tabs (shared shape
  // with the legacy /assessments page). Memoized so the leaf components and
  // the Evaluate draft-init effect see a stable `candidate` reference.
  const candidateView = useMemo(
    () => mapAssessmentToCandidateView(completedAssessment),
    [completedAssessment]
  );
  const evaluationRubric = (completedAssessment?.evaluation_rubric && typeof completedAssessment.evaluation_rubric === 'object')
    ? completedAssessment.evaluation_rubric
    : EMPTY_RUBRIC;
  // Strengths and risks are now derived from the same
  // requirements_assessment data that drives the Matched / Missing
  // cards on the CV & match tab — so what shows on Overview matches
  // what shows on CV & match. Recruiter-added crit_* surfaces ahead of
  // JD-extracted jd_req_* (recruiter signal > scraped signal).
  const cvMatchDetails = resolveCvMatchDetails({
    application,
    completedAssessment,
    fallback: reportModel?.roleFitModel,
  });
  // A pre-screen reject is deterministic and is recorded on the application
  // (``pre_screen_recommendation`` / ``pre_screen_evidence``) the moment the
  // cheap Stage-1 gate runs — independent of the agent, and of whether the
  // expensive full cv_match score ever ran. Surface it here even when
  // ``cv_match_details`` is empty (the Stage-1-only path deliberately never
  // writes cv_match_*), so a screened-out candidate shows the verdict + reason
  // instead of a blank "No Hire / 0.0".
  const preScreenEvidence = (application?.pre_screen_evidence && typeof application.pre_screen_evidence === 'object')
    ? application.pre_screen_evidence
    : {};
  const hasFullScore = application?.cv_match_score != null;
  const preScreenDecision = String(
    cvMatchDetails?.pre_screen_decision
    || preScreenEvidence.decision
    || ''
  ).toLowerCase();
  const isPreScreenedOut = !hasFullScore && (
    preScreenDecision === 'no'
    || String(application?.pre_screen_recommendation || '').trim().toLowerCase() === 'below threshold'
  );
  // Field names that the API actually serializes (ApplicationResponse):
  // top-level ``pre_screen_score`` (populated for fully/filtered-scored rows),
  // else the genuine LLM score carried in ``pre_screen_evidence.llm_score_100``
  // (the Stage-1-only path — where cv_match_* is empty), else the cv_match copy.
  const preScreenScore = (
    application?.pre_screen_score
    ?? preScreenEvidence.llm_score_100
    ?? cvMatchDetails?.pre_screen_score_100
    ?? null
  );
  const preScreenReason = String(
    cvMatchDetails?.pre_screen_reason
    || preScreenEvidence.summary
    || ''
  ).trim();
  const handleRunFullEvaluation = useCallback(async () => {
    if (!application?.id || !rolesApi?.scoreSelected || !application?.role_id) return;
    setBusyAction('rescore');
    try {
      // ``bypassPreScreen`` is the whole point of this button: the candidate
      // is sitting here *because* the cheap pre-screen filtered them, so a
      // plain rescore would just re-filter on the same evidence. Force the
      // full v3 cv_match score past the gate.
      await rolesApi.scoreSelected(application.role_id, [application.id], { force: true, bypassPreScreen: true });
      // Real in-flight state instead of "refresh in a few seconds": mark the
      // evaluation running (the banner flips to a spinner) and let the poll
      // below swap in the fresh score in place when it lands.
      setEvaluating(true);
      showToast('Running full evaluation — the report updates when the score lands.', 'info');
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to start full evaluation.'), 'error');
    } finally {
      setBusyAction('');
    }
  }, [application?.id, application?.role_id, rolesApi, showToast]);
  // Sort so recruiter-added criteria (id prefix ``crit_``) surface
  // ahead of JD-extracted ones (``jd_req_``), then by priority. Show
  // every requirement — silently truncating recruiter must-haves at 4
  // was hiding the user's own criteria from their own report.
  const PRIORITY_RANK = { must_have: 0, strong_preference: 1, nice_to_have: 2, constraint: 3 };
  const sortRequirements = (items) => [...items].sort((a, b) => {
    const aRecruiter = String(a?.requirement_id || '').startsWith('crit_') ? 0 : 1;
    const bRecruiter = String(b?.requirement_id || '').startsWith('crit_') ? 0 : 1;
    if (aRecruiter !== bRecruiter) return aRecruiter - bRecruiter;
    const aPri = PRIORITY_RANK[String(a?.priority || '').toLowerCase()] ?? 4;
    const bPri = PRIORITY_RANK[String(b?.priority || '').toLowerCase()] ?? 4;
    return aPri - bPri;
  });
  const matchedRequirements = useMemo(() => {
    const requirements = Array.isArray(cvMatchDetails?.requirements_assessment)
      ? cvMatchDetails.requirements_assessment
      : [];
    return sortRequirements(
      requirements.filter((item) => reqGradeKey(item) === 'met')
    );
  }, [cvMatchDetails]);
  const missingRequirements = useMemo(() => {
    const requirements = Array.isArray(cvMatchDetails?.requirements_assessment)
      ? cvMatchDetails.requirements_assessment
      : [];
    return sortRequirements(
      requirements.filter((item) => reqGradeKey(item) !== 'met')
    );
  }, [cvMatchDetails]);
  const strengthItems = useMemo(() => {
    const met = matchedRequirements.slice(0, 4).map((item, idx) => ({
      key: `strength-${item.requirement_id || idx}`,
      label: item.requirement || '',
      value: null,
      source: String(item.requirement_id || '').startsWith('crit_') ? 'recruiter' : 'jd',
      detail: item.impact || item.reasoning || '',
    })).filter((item) => item.label);
    if (met.length) return met;
    // Fallback when no requirements are scored yet (pre-scoring state).
    const highlights = Array.isArray(reportModel?.roleFitModel?.experienceHighlights)
      ? reportModel.roleFitModel.experienceHighlights
      : [];
    return highlights
      .map((label, idx) => ({
        key: `cv-highlight-${idx}`,
        label: String(label || '').trim(),
        value: null,
        source: 'cv_match',
      }))
      .filter((item) => item.label)
      .slice(0, 4);
  }, [matchedRequirements, reportModel?.roleFitModel?.experienceHighlights]);
  const riskItems = useMemo(() => {
    // Top non-met requirements (missing > partial > unknown), recruiter
    // criteria first. Mirrors the order the user sees on the Missing /
    // Partial / Unclear card.
    const STATUS_RANK = { missing: 0, partially_met: 1, unknown: 2 };
    const ranked = [...missingRequirements].sort((a, b) => {
      const aRecruiter = String(a?.requirement_id || '').startsWith('crit_') ? 0 : 1;
      const bRecruiter = String(b?.requirement_id || '').startsWith('crit_') ? 0 : 1;
      if (aRecruiter !== bRecruiter) return aRecruiter - bRecruiter;
      const aSt = STATUS_RANK[String(a?.status || '').toLowerCase()] ?? 3;
      const bSt = STATUS_RANK[String(b?.status || '').toLowerCase()] ?? 3;
      return aSt - bSt;
    });
    // Drop rows without requirement text — interviewQuestions calls
    // item.title.toLowerCase() unguarded (crashed on candidate 55112/140).
    return ranked
      .filter((item) => item.requirement)
      .slice(0, 3)
      .map((item) => ({
        title: item.requirement,
        description: item.impact || item.reasoning || 'Validate this gap during the panel loop.',
      }));
  }, [missingRequirements]);
  const interviewQuestions = useMemo(() => {
    const override = application?.interview_prep;
    if (override && (Array.isArray(override.stageOne) || Array.isArray(override.stageTwo))) {
      return {
        stageOne: Array.isArray(override.stageOne) ? override.stageOne : [],
        stageTwo: Array.isArray(override.stageTwo) ? override.stageTwo : [],
      };
    }
    const stageOne = [
      {
        question: `Walk me through the strongest evidence that ${application?.role_name || 'this role'} matches your recent work.`,
        listenFor: 'Specific examples tied to the CV and role requirements.',
        source: 'CV + job spec',
      },
      ...(riskItems.length ? riskItems.map((item) => ({
        question: `How would you de-risk ${item.title.toLowerCase()} before the next stage?`,
        listenFor: item.description,
        source: 'Taali signal',
      })) : []),
    ].slice(0, 4);
    const stageTwo = [
      ...(strengthItems.length ? strengthItems.map((item) => ({
        question: `Show us a project where ${item.label.toLowerCase()} mattered under real delivery pressure.`,
        listenFor: 'Evidence of judgment, tradeoffs, and ownership rather than generic tool use.',
        source: 'Assessment',
      })) : []),
      {
        question: 'Where did AI help, and where did you deliberately slow down or reject its suggestion?',
        listenFor: 'Clear boundaries around AI assistance, verification, and accountability.',
        source: 'Taali',
      },
    ].slice(0, 4);
    return { stageOne, stageTwo };
  }, [application?.interview_prep, application?.role_name, riskItems, strengthItems]);
  const timelineItems = useMemo(() => {
    if (applicationEvents.length) {
      return applicationEvents.slice(0, 8).map((event) => {
        const type = String(event?.event_type || '').toLowerCase();
        let title;
        if (type === 'cv_scored') {
          const meta = event?.metadata || {};
          const score = Number(meta.role_fit_score);
          const rec = String(meta.recommendation || '').replace(/_/g, ' ').trim();
          const scoreLabel = Number.isFinite(score) ? `${Math.round(score)}%` : '—';
          title = `CV scored — ${rec ? `${rec} ` : ''}(${scoreLabel})`;
        } else {
          title = String(event?.event_type || 'Activity').replace(/_/g, ' ');
        }
        return {
          title,
          detail: event?.reason || event?.description || event?.metadata?.note || 'Candidate activity recorded.',
          when: event?.created_at,
        };
      });
    }
    return [
      {
        title: 'Application created',
        detail: `${application?.candidate_name || application?.candidate_email || 'Candidate'} entered the Taali workflow.`,
        when: application?.created_at,
      },
      {
        title: completedAssessment ? 'Assessment completed' : 'Assessment pending',
        detail: completedAssessment
          ? 'Technical assessment signal is available in the report.'
          : 'This standing report is currently anchored to CV and role-fit evidence.',
        when: completedAssessment?.completed_at || application?.updated_at,
      },
    ].filter((item) => item.when || item.detail);
  }, [application, applicationEvents, completedAssessment]);

  // Report PDF export removed per HANDOFF v2 §3 — share links replace PDFs
  // entirely; do not reintroduce a download path. All sharing now flows
  // through ShareModal → the share_links table → the public /share/:token
  // SPA route.

  // Save a recruiter note as a `recruiter_note` event on the application
  // timeline — works with or without a linked assessment (the legacy
  // assessment-timeline path dead-ended when none was linked). When
  // `noteForAgent` the note rides in the agent's get_application payload as
  // standing per-candidate guidance. We fall back to the assessment-note
  // endpoint only if there's no application id. After save we bump
  // eventsRefetchTick so the timeline picks up the new event.
  const handleSaveNote = useCallback(async () => {
    const note = noteDraft.trim();
    if (!note) return;
    const appId = application?.id;
    if (!appId && !(assessmentId && assessmentsApi?.addNote)) {
      showToast('Could not save the note — no candidate record is linked yet.', 'info');
      return;
    }
    setSavingNote(true);
    try {
      if (appId && rolesApi?.addApplicationNote) {
        await rolesApi.addApplicationNote(appId, note, noteForAgent);
      } else {
        await assessmentsApi.addNote(assessmentId, note);
      }
      setNoteDraft('');
      setEventsRefetchTick((prev) => prev + 1);
      showToast(
        noteForAgent ? 'Note saved — your hiring agent will see it.' : 'Note added to the timeline.',
        'success',
      );
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to add note.'), 'error');
    } finally {
      setSavingNote(false);
    }
  }, [application?.id, rolesApi, assessmentId, assessmentsApi, noteDraft, noteForAgent, showToast]);

  // Link quick-add — a URL + optional label, stored as a `link` note
  // (kind: 'link'). The note body is the label (or URL) so it's readable in the
  // timeline; the structured url/label ride in metadata for the clickable render.
  const handleSaveLink = useCallback(async () => {
    const appId = application?.id;
    const url = linkUrl.trim();
    if (!appId || !rolesApi?.addApplicationNote) return;
    if (!url) {
      showToast('Enter a URL to add a link.', 'info');
      return;
    }
    const label = linkLabel.trim();
    setSavingLink(true);
    try {
      await rolesApi.addApplicationNote(appId, label || url, noteForAgent, {
        kind: 'link',
        link_url: url,
        link_label: label || undefined,
      });
      setLinkUrl('');
      setLinkLabel('');
      setEventsRefetchTick((prev) => prev + 1);
      showToast('Link added.', 'success');
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to add link.'), 'error');
    } finally {
      setSavingLink(false);
    }
  }, [application?.id, rolesApi, linkUrl, linkLabel, noteForAgent, showToast]);

  // One-click share: mint a fresh 7-day share-link of the requested mode
  // and copy the URL to the clipboard. Replaces the previous ShareModal
  // (which still exposed expiry presets, revoke, and audit history) —
  // user feedback was "just click share internally / share with client
  // and have a link copied." If revoke / manage-links is needed later
  // the backend endpoints (POST/GET/DELETE share-links) are untouched.
  //
  // Mint and clipboard-copy are deliberately separate try/catch blocks:
  // if the link is minted but the clipboard write fails (permission
  // denied, non-secure context, no clipboard API), we still surface the
  // URL so the user can copy manually. Treating clipboard errors as
  // mint errors would cause repeated retries to spawn orphan active
  // links on the backend (one per click).
  const handleMintAndCopyShareLink = useCallback(async (mode, successMessage) => {
    if (!application?.id || !rolesApi?.createApplicationShareLink) return;
    setSharingMode(mode);
    let url = '';
    try {
      const res = await rolesApi.createApplicationShareLink(application.id, { mode, expiry: '7d' });
      const token = res?.data?.token;
      if (!token || typeof window === 'undefined') throw new Error('Share link unavailable.');
      url = `${window.location.origin}/share/${token}`;
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to create share link.'), 'error');
      setSharingMode('');
      return;
    }
    try {
      await navigator.clipboard.writeText(url);
      showToast(successMessage, 'success');
    } catch {
      // Clipboard API unavailable / blocked — surface the URL so the
      // user can copy it manually instead of silently throwing away a
      // minted link.
      showToast(`Couldn't copy automatically — here's your link: ${url}`, 'info');
    } finally {
      setSharingMode('');
    }
  }, [application?.id, rolesApi, showToast]);

  // Recruiter lifecycle actions migrated from the legacy /assessments page.
  // Rendered in the (recruiter-only) Assessment pane, so they never reach a
  // share route. `resend` doubles as the candidate CV-request trigger.
  const normalizedAssessmentStatus = String(
    completedAssessment?.status || resolveAssessmentStatus(application) || ''
  ).toLowerCase();
  const canResendInvite = Boolean(assessmentId)
    && (normalizedAssessmentStatus === 'pending' || normalizedAssessmentStatus === 'expired');
  const hasCvOnFile = Boolean(
    application?.cv_filename || completedAssessment?.candidate_cv_filename || application?.cv_uploaded_at
  );
  const canRequestCvUpload = Boolean(
    assessmentId && !hasCvOnFile && (application?.candidate_email || completedAssessment?.candidate_email)
  );

  const handleResendInvite = useCallback(async () => {
    if (!assessmentId || !assessmentsApi?.resend) return;
    setBusyAction('resend');
    try {
      await assessmentsApi.resend(assessmentId);
      showToast('Assessment invite resent.', 'success');
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to resend invite.'), 'error');
    } finally {
      setBusyAction('');
    }
  }, [assessmentId, assessmentsApi, showToast]);

  const handleRequestCvUpload = useCallback(async () => {
    if (!assessmentId || !assessmentsApi?.resend) return;
    setBusyAction('request-cv');
    try {
      await assessmentsApi.resend(assessmentId);
      showToast('CV request sent. The candidate can upload from the assessment link.', 'success');
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to send CV request.'), 'error');
    } finally {
      setBusyAction('');
    }
  }, [assessmentId, assessmentsApi, showToast]);

  const handleDeleteAssessment = useCallback(async () => {
    if (!assessmentId || !assessmentsApi?.remove) return;
    if (typeof window !== 'undefined'
      && !window.confirm('Delete this assessment? This cannot be undone.')) return;
    setBusyAction('delete');
    try {
      await assessmentsApi.remove(assessmentId);
      showToast('Assessment deleted.', 'success');
      onNavigate('jobs');
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to delete assessment.'), 'error');
      setBusyAction('');
    }
  }, [assessmentId, assessmentsApi, showToast, onNavigate]);

  // Cold load only — a refreshing revalidate keeps the report painted (see the
  // `refreshing` flag threaded into the DecisionRail + panes below).
  if (loading) {
    return (
      <div>
        {NavComponent && !isInterviewView ? <NavComponent currentPage="candidates" onNavigate={onNavigate} /> : null}
        <div className="page">
          <PageLoader />
        </div>
      </div>
    );
  }

  if (error || !application || !reportModel) {
    return (
      <div>
        {NavComponent && !isInterviewView ? <NavComponent currentPage="candidates" onNavigate={onNavigate} /> : null}
        <div className="page">
          <Panel className="border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-4 text-sm text-[var(--taali-danger)]">
            {error || 'Candidate report unavailable.'}
          </Panel>
          {/* A stale candidate link (deleted application, revoked share,
              transient network) shouldn't dead-end. Give a way forward.
              Share/interview routes are unauth, so only offer nav in the app. */}
          {!isInterviewView ? (
            <div style={{ display: 'flex', gap: '0.75rem', marginTop: '1rem' }}>
              <Button type="button" variant="primary" onClick={() => { void loadStandingReport(); }}>
                Retry
              </Button>
              <Button type="button" variant="ghost" onClick={() => onNavigate('jobs')}>
                Back to Jobs
              </Button>
            </div>
          ) : null}
        </div>
      </div>
    );
  }

  // Back-link destination prefers the explicit ?from tag, then falls back
  // to the candidate's own role. Many entry points (the role board, the
  // triage drawer's new-tab link, search, deep links, …) don't attach
  // ?from, and defaulting those to "home" sent recruiters who opened a
  // candidate from a job back to the Hub. The role fallback only kicks in
  // when there is no origin tag, so explicit ?from=home still wins.
  //   ?from=jobs/<id> → "Back to job: <role_name>"
  //   ?from=home       → "Back to home" (explicit Hub origin)
  //   (no from)        → "Back to job: <role_name>" via application.role_id
  const cameFromHome = (searchParams.get('from') || '').trim() === 'home';
  const backTargetRoleId = backFromRoleId
    ?? (cameFromHome ? null : (application?.role_id ?? null));
  const targetRoleName = application?.role_name || 'job';
  const candidateLabel = application?.candidate_name || application?.candidate_email || 'Candidate';
  const candidateInitials = (() => {
    const seed = String(candidateLabel).trim();
    if (!seed) return 'C';
    const letters = seed.split(/\s+/).filter(Boolean).map((w) => w[0]).join('');
    return letters.slice(0, 2).toUpperCase() || 'C';
  })();
  const metaParts = [
    application?.candidate_email,
    application?.candidate_location,
    application?.role_name,
    application?.pipeline_stage
      ? `Application: ${String(application.pipeline_stage).replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())}`
      : null,
  ].filter(Boolean);

  const breadcrumbItems = !isInterviewView
    ? (backTargetRoleId != null
        ? [
            { label: 'Jobs', page: 'jobs' },
            { label: targetRoleName, page: 'job-pipeline', options: { roleId: backTargetRoleId } },
            { label: candidateLabel },
          ]
        : [
            { label: 'Home', page: 'home' },
            { label: candidateLabel },
          ])
    : null;

  return (
    <div>
      {NavComponent && !isInterviewView ? <NavComponent currentPage="candidates" onNavigate={onNavigate} /> : null}
      {/* No page header band — the candidate's identity AND the page
          actions (Workable / share) all live on the DecisionRail card,
          the one surface every view renders. Only the breadcrumb strip
          remains for navigation. */}
      {!isInterviewView && breadcrumbItems ? (
        <BreadcrumbsRow items={breadcrumbItems} />
      ) : null}
      {/* The agent's recommendation + decision controls now live in the
          DecisionRail (left column of the dossier below), not a full-width
          strip. */}
      <div className="page">
        {isInterviewView ? (
          <div className="iv-banner">
            <Eye size={16} />
            {isClientView ? (
              <span><b>Client view.</b> External, client-safe summary — recruiter notes, scoring breakdown, and interview prep are hidden.</span>
            ) : (
              <span><b>Recruiter view.</b> Full internal report — includes recruiter notes, timeline, and interview prep. Don&apos;t share with candidates.</span>
            )}
          </div>
        ) : null}
        {/* The pre-screen escalation used to be a bespoke top-of-page banner.
            It now lives in the DecisionRail as a standard dr-btn action (see the
            preScreenedOut props on <DecisionRail> below), consistent with every
            other decision control. */}

        {isClientView && application?.client_share_summary ? (
          <div className="report-card" style={{ marginTop: 18, borderLeft: '4px solid var(--taali-accent, #4f46e5)' }}>
            <div className="kicker">Why we&apos;re sharing this candidate</div>
            <h2 style={{ fontSize: '20px', margin: '8px 0 6px' }}>
              {application.client_share_summary.verdict}
            </h2>
            <p style={{ fontSize: '14px', color: 'var(--ink-2)', margin: '0 0 12px' }}>
              {`Shared for ${application.client_share_summary.role}.`}
              {Number.isFinite(Number(application.client_share_summary.score_100))
                ? ` Taali score: ${Math.round(Number(application.client_share_summary.score_100))}/100.`
                : ''}
            </p>
            {Array.isArray(application.client_share_summary.highlights)
              && application.client_share_summary.highlights.length > 0 ? (
                <ul style={{ paddingLeft: 18, margin: '0 0 8px', fontSize: '14px', lineHeight: 1.6 }}>
                  {application.client_share_summary.highlights.map((highlight, idx) => (
                    <li key={idx}>{highlight}</li>
                  ))}
                </ul>
              ) : null}
          </div>
        ) : null}

        {/* refreshing = a silent revalidate after an action. Dim + mark busy so
            the recruiter sees "updating" without the report unmounting. */}
        <div
          className={`dossier ${refreshing ? 'is-refreshing' : ''}`.trim()}
          aria-busy={refreshing || undefined}
        >
          <DecisionRail
            candidateName={candidateLabel}
            candidateInitials={candidateInitials}
            candidateMeta={metaParts}
            footerActions={!isClientView && !isInterviewView ? (
              <>
                {application?.workable_profile_url ? (
                  <button
                    type="button"
                    className="btn btn-outline btn-sm"
                    onClick={() => window.open(application.workable_profile_url, '_blank', 'noopener,noreferrer')}
                  >
                    <ExternalLink size={13} />
                    Open in Workable
                  </button>
                ) : null}
                <button
                  type="button"
                  className="btn btn-outline btn-sm"
                  onClick={() => handleMintAndCopyShareLink('recruiter', 'Internal share link copied (expires in 7 days).')}
                  disabled={!application?.id || sharingMode === 'recruiter'}
                >
                  <Copy size={13} />
                  {sharingMode === 'recruiter' ? 'Copying…' : 'Share internally'}
                </button>
                <button
                  type="button"
                  className="btn btn-purple btn-sm"
                  onClick={() => handleMintAndCopyShareLink('client', 'Client share link copied (expires in 7 days).')}
                  disabled={!application?.id || sharingMode === 'client'}
                >
                  {sharingMode === 'client' ? 'Copying…' : 'Share with client'}
                </button>
              </>
            ) : null}
            taaliScore={reportModel?.summaryModel?.taaliScore}
            roleFitScore={reportModel?.summaryModel?.roleFitScore}
            assessmentScore={reportModel?.summaryModel?.assessmentScore}
            reqMet={matchedRequirements.length}
            reqTotal={matchedRequirements.length + missingRequirements.length}
            experienceLabel={reportModel?.candidateSnapshot?.yearsLabel || ''}
            decision={agentDecision}
            application={application}
            flagCount={(reportModel?.roleFitModel?.claimsToVerify?.length || 0) + (reportModel?.roleFitModel?.integrityFlags?.length || 0)}
            provenance={application?.score_summary?.score_provenance}
            canDecide={!isClientView && !isInterviewView}
            busy={decisionBusy}
            preScreenedOut={isPreScreenedOut}
            preScreenScore={preScreenScore}
            preScreenReason={preScreenReason}
            evaluating={evaluating}
            runFullEvaluationBusy={busyAction === 'rescore'}
            onApprove={handleDecisionApprove}
            onAlternative={handleDecisionAlternative}
            onTeach={(decision) => setTeachFor({ decision, reportScope: reportScopeKey })}
            onSnooze={handleDecisionSnooze}
            onReEvaluate={handleDecisionReEvaluate}
            onRunFullEvaluation={handleRunFullEvaluation}
          />
          <main className="dossier-main">
        <FocusedSectionNav
          items={reportNavigationItems}
          activeId={activeTab}
          className="report-tabs"
          ariaLabel="Candidate report sections"
          idPrefix="candidate-report-view"
          variant="bar"
          sticky={false}
        />

        <div
          className={`pane ${activeTab === 'overview' ? 'active' : ''}`}
          data-p="overview"
          id="report-pane-overview"
          role="region"
          aria-labelledby="candidate-report-view-item-overview"
        >
        {/* HANDOFF v2 §5.1 / canvas cand-overview — Overview tab is:
            (1) hero band: ScoreRing | RECOMMENDATION + body | SIGNAL list,
            (2) two-up: STRONGEST SIGNAL · WORTH PROBING,
            (3) SCORECARD — the 5 canonical axes (4 Ds + Deliverable), 0–100,
            (4) four evidence cards: AI USAGE · CODE & GIT · TIMELINE · DOCUMENTS.
            All scores render as integer "nn / 100" per HANDOFF v2 §6. */}
        {(() => {
          // THE canonical scorecard: the 5 axes (4 Ds + Deliverable), scored
          // from the graded rubric ONLY — there is no heuristic fallback (#1065);
          // an ungraded axis reads "—". This is the only top-level scorecard on
          // the page — the per-rubric dimensions and the ~30 heuristic metrics
          // hang under it as evidence (see below).
          const scorecard = computeScorecard(completedAssessment);

          return (
            <>
              {/* (0) At-a-glance snapshot strip — years exp, tech stack, recent roles.
                  Sits above the hero band so recruiters and external clients can
                  scan candidate basics in 3 seconds without scrolling the full CV. */}
              {reportModel?.candidateSnapshot ? (
                <div className="mb-3">
                  <CandidateSnapshotCard snapshot={reportModel.candidateSnapshot} variant="report" />
                </div>
              ) : null}

              {/* (1) Why this verdict — recruiters with a live decision see the
                  report-density narrative (verdict + causal reason + summary);
                  the decision explanation is recruiter-only, so it never renders
                  on client/share views. The score ring, recommendation, flags and
                  the demoted scores now live in the DecisionRail (left). */}
              {!isClientView && agentDecision ? (
                <VerdictDetail decision={agentDecision} />
              ) : null}

              {/* Standalone candidate summary — suppressed when the recruiter's
                  report-density narrative above already carries the decision's
                  candidate_summary (so the same synthesis never appears twice).
                  Kept for client/share views and any recruiter view without a
                  decision summary, where it may be the only "why" surface. */}
              {(() => {
                const verdictShowsSummary = !isClientView && agentDecision
                  && Boolean(normaliseDecisionText(agentDecision.candidate_summary));
                if (verdictShowsSummary || !reportModel?.recruiterSummaryText) return null;
                return (
                  <section className="mc-why" aria-label="Candidate summary">
                    <div className="mc-kicker">CANDIDATE SUMMARY</div>
                    <p className="mc-why-reason">
                      {normaliseDecisionText(reportModel.recruiterSummaryText)}
                    </p>
                  </section>
                );
              })()}

              {/* (1b) Flags — claims & signals the agent couldn't corroborate
                  (cv_match_details.claims_to_verify + score_summary.integrity),
                  surfaced first-class so a recruiter verifies before deciding.
                  Recruiter-only. */}
              {!isClientView ? (() => {
                const claims = Array.isArray(reportModel?.roleFitModel?.claimsToVerify)
                  ? reportModel.roleFitModel.claimsToVerify : [];
                const integrity = Array.isArray(reportModel?.roleFitModel?.integrityFlags)
                  ? reportModel.roleFitModel.integrityFlags : [];
                const flags = [
                  ...claims.map((c) => ({
                    label: c.claimType ? String(c.claimType).replace(/_/g, ' ') : '',
                    text: c.claimText || '',
                    why: c.reasoning || '',
                  })),
                  ...integrity.map((s) => ({ label: '', text: String(s), why: '' })),
                ].filter((f) => f.text);
                if (!flags.length) return null;
                return <OverviewFlags flags={flags} />;
              })() : null}

              {/* (1c) Integrity chip — the compact trust readout beside the score.
                  Renders only on a review / strong_review verdict; expands to the
                  canonical warnings + corroborations + unverified employers.
                  Recruiter-only (stripped from client shares server-side). */}
              {!isClientView ? (
                <IntegrityChip
                  verdict={reportModel?.roleFitModel?.integrityVerdict}
                  trustBand={reportModel?.roleFitModel?.integrityTrustBand}
                  warnings={reportModel?.roleFitModel?.integrityFlags}
                  corroborations={reportModel?.roleFitModel?.corroborations}
                  unverifiedEmployers={reportModel?.roleFitModel?.unverifiedEmployers}
                />
              ) : null}

              {/* (2) CV match review moved to its own Requirements tab (matches
                  report-preview's 6-tab layout). The Overview keeps the verdict,
                  flags and scorecard; the per-requirement breakdown lives one
                  click away. A compact "jump to Requirements" cue keeps it
                  discoverable from the verdict. Only render once there are
                  requirements to break down — "0 of 0 met" is noise pre-score. */}
              {(matchedRequirements.length + missingRequirements.length) > 0 ? (
                <button
                  type="button"
                  className="taali-text-btn mc-overview-reqjump"
                  onClick={() => activateTab('requirements')}
                >
                  See the full requirement breakdown · {matchedRequirements.length} of {matchedRequirements.length + missingRequirements.length} met →
                </button>
              ) : null}

              {/* (3) Scorecard — the ONE canonical scorecard: the 5 axes
                  (Anthropic's 4 Ds + Deliverable). Scores come from the graded
                  rubric ONLY (see computeScorecard); an axis with no rubric
                  criterion reads "—" rather than borrowing a heuristic.
                  Each axis is label + 0–100 bar + blurb tooltip.
                  The per-rubric dimensions + ~30 heuristic metrics hang under
                  this as evidence on the Assessment tab — not a rival scorecard. */}
              {scorecard ? (
                <div className="mc-overview-dimensions mc-overview-dimensions--stacked">
                  <div className="mc-overview-dim-head">
                    <span className="mc-kicker">SCORECARD · THE 5 Ds</span>
                    <span className="mc-overview-dim-note">from the assessment</span>
                  </div>
                  <MotionStagger
                    active={activeTab === 'overview'}
                    className="mc-overview-dimensions-grid"
                    data-motion-stagger="standing-report-scorecard"
                  >
                    {scorecard.map((axis, i) => {
                      const pct = axis.hasSignal ? Math.max(0, Math.min(100, Math.round(axis.score))) : 0;
                      // Lavender (low) variant when the axis is a weak signal —
                      // mirrors report-preview's `.fill.low` (< 45 reads as weak).
                      const isLow = axis.hasSignal && pct < 45;
                      return (
                        <div key={axis.key} className="mc-overview-dim-row" title={axis.blurb}>
                          <span className="mc-overview-dim-label">{axis.label}</span>
                          <div className="mc-overview-dim-bar" aria-hidden="true">
                            <MotionProgress
                              className={isLow ? 'low' : ''}
                              delay={MOTION_DURATION.fast + (i * MOTION_STAGGER.default)}
                              value={pct / 100}
                              style={{ width: '100%' }}
                            />
                          </div>
                          <DimScore score={axis.score} hasSignal={axis.hasSignal} />
                        </div>
                      );
                    })}
                  </MotionStagger>
                </div>
              ) : !completedAssessment ? (
                // Pre-assessment: the scorecard AND the two assessment-gated
                // evidence cards (AI USAGE, CODE & GIT) all say the same "appears
                // once completed" thing. Collapse them into ONE compact line so
                // the Overview isn't three stacked placeholders. TIMELINE and
                // DOCUMENTS still render below with real CV-on-file content.
                <div className="mc-overview-dimensions-empty">
                  <div className="mc-kicker">ASSESSMENT EVIDENCE</div>
                  <p className="mc-overview-dim-empty">
                    The 5-Ds scorecard, AI usage, and code &amp; git evidence appear once the
                    candidate completes the assessment.
                  </p>
                </div>
              ) : null}

              {/* (4) Evidence row. Once an assessment exists all four cards
                  carry signal; before then the two assessment-gated cards
                  (AI USAGE, CODE & GIT) collapse into the compact card above,
                  leaving TIMELINE + DOCUMENTS (which can carry pre-assessment
                  CV-on-file content). */}
              <div className="mc-overview-evidence">
                {[
                  { kicker: 'AI USAGE', section: reportModel?.evidenceSections?.aiUsage, assessmentGated: true },
                  { kicker: 'CODE & GIT', section: reportModel?.evidenceSections?.codeAndGit, assessmentGated: true },
                  { kicker: 'TIMELINE', section: reportModel?.evidenceSections?.timeline },
                  { kicker: 'DOCUMENTS', section: reportModel?.evidenceSections?.documents },
                ].filter(({ assessmentGated }) => completedAssessment || !assessmentGated)
                  .map(({ kicker, section }) => {
                  const headline = section?.items?.[0]
                    || section?.title
                    || 'Evidence pending';
                  const description = section?.description
                    || 'Evidence appears here once the candidate is scored.';
                  return (
                    <div key={kicker} className="mc-overview-evidence-card">
                      <div className="mc-kicker">{kicker}</div>
                      <div className="mc-overview-evidence-headline">{headline}</div>
                      <p className="mc-overview-evidence-body">{description}</p>
                    </div>
                  );
                })}
              </div>

            </>
          );
        })()}
        </div>

        <div
          className={`pane ${activeTab === 'requirements' ? 'active' : ''}`}
          data-p="requirements"
          id="report-pane-requirements"
          role="region"
          aria-labelledby="candidate-report-view-item-requirements"
        >
          {/* Requirements & fit — per-requirement match confidence (0–100) with
              expandable evidence rows. Moved out of Overview to match the
              report-preview 6-tab layout. */}
          <CvMatchReview
            application={application}
            cvMatchDetails={cvMatchDetails}
            matchedRequirements={matchedRequirements}
            missingRequirements={missingRequirements}
            fitScore={reportModel?.summaryModel?.roleFitScore}
            onJumpToPrep={() => activateTab('prep')}
          />
        </div>

        <div
          className={`pane ${activeTab === 'assessment' ? 'active' : ''}`}
          data-p="assessment"
          id="report-pane-assessment"
          role="region"
          aria-labelledby="candidate-report-view-item-assessment"
        >
          {/* THE 5 Ds scorecard is the spine of this pane — each axis expands
              into the graded rubric criteria (score_breakdown.rubric_grading
              .dimensions) that produced it. Admin actions (resend / request CV
              / delete) live in an overflow menu so destructive controls no
              longer lead the page. */}
          {(() => {
            const gradedDimensions = readGradedRubricDimensions(completedAssessment);
            const firstName = String(application?.candidate_name || '').trim().split(/\s+/)[0];
            const score = reportModel?.summaryModel?.assessmentScore;
            if (gradedDimensions.length === 0 && !assessmentId) return null;
            return (
              <div className="assessment-head" data-internal-only>
                {gradedDimensions.length > 0 ? (
                  <div className="abar abar-on abar-block">
                    <AgentLoop kind="flow" className="abar-flow-layer" />
                    <span className="ab-spark"><Sparkles size={15} strokeWidth={2} /></span>
                    <span className="ab-label">Agent assessed</span>
                    <span className="ab-tick">
                      {firstName
                        ? `Graded ${gradedDimensions.length} rubric criteria from ${firstName}’s work sample`
                        : `Graded ${gradedDimensions.length} rubric criteria from the work sample`}
                    </span>
                    <span className="ab-assess">
                      <b>{score != null ? Math.round(score) : '—'}</b><span>/100</span>
                    </span>
                  </div>
                ) : null}
                {assessmentId ? (
                  <div className="assessment-actions" ref={assessmentActionsRef}>
                    <button
                      type="button"
                      className="btn btn-outline btn-sm"
                      aria-haspopup="menu"
                      aria-expanded={assessmentActionsOpen}
                      onClick={() => setAssessmentActionsOpen((open) => !open)}
                    >
                      <MoreHorizontal size={15} /> Actions
                    </button>
                    {assessmentActionsOpen ? (
                      <div className="assessment-actions-menu" role="menu">
                        {canResendInvite ? (
                          <button
                            type="button"
                            role="menuitem"
                            disabled={busyAction !== ''}
                            onClick={() => { setAssessmentActionsOpen(false); handleResendInvite(); }}
                          >
                            {busyAction === 'resend' ? 'Resending…' : 'Resend invite'}
                          </button>
                        ) : null}
                        {canRequestCvUpload ? (
                          <button
                            type="button"
                            role="menuitem"
                            disabled={busyAction !== ''}
                            onClick={() => { setAssessmentActionsOpen(false); handleRequestCvUpload(); }}
                          >
                            {busyAction === 'request-cv' ? 'Sending…' : 'Request CV upload'}
                          </button>
                        ) : null}
                        <button
                          type="button"
                          role="menuitem"
                          className="danger"
                          disabled={busyAction !== ''}
                          onClick={() => { setAssessmentActionsOpen(false); handleDeleteAssessment(); }}
                        >
                          {busyAction === 'delete' ? 'Deleting…' : 'Delete assessment'}
                        </button>
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </div>
            );
          })()}

          <AssessmentScorecard assessment={completedAssessment} />

          {/* Full assessment evidence migrated from the legacy /assessments
              page: AI-usage analytics, code/git, and the prompt-by-prompt
              timeline. Recruiter-only (this pane is internalOnly). */}
          {candidateView && assessmentContentMounted ? (
            <ErrorBoundary
              fallback={
                <div className="mc-notes-empty">
                  Scoring is incomplete for this assessment, so the evidence can’t be rendered.
                  Try “Rescore” from the assessment, or refresh.
                </div>
              }
            >
              <Suspense fallback={<PageLoader minHeight="12rem" />}>
                <AssessmentEvidencePanels candidate={candidateView} />
              </Suspense>
            </ErrorBoundary>
          ) : null}

          {/* The recruiter's own manual evaluation (excellent/good/poor rubric
              + strengths / improvements), collapsed by default — it's optional
              input, not evidence, and it cost a screen of scroll. The decision
              recorder, role criteria and chat log it used to carry are dropped:
              they duplicate the DecisionRail, the Requirements tab and the
              Prompts evidence panel. */}
          {candidateView && assessmentContentMounted ? (
            <ErrorBoundary
              fallback={
                <div className="mc-notes-empty">
                  This evaluation can’t be rendered — the assessment scoring may be incomplete.
                  Try “Rescore”, or refresh.
                </div>
              }
            >
              <details className="report-eval-drawer mt-4" data-internal-only>
                <summary>
                  <span className="mc-kicker">YOUR EVALUATION</span>
                  <span className="report-eval-drawer-hint">
                    Optional manual rubric, strengths and improvements
                  </span>
                </summary>
                <Suspense fallback={<PageLoader minHeight="8rem" />}>
                  <EvaluatePanel
                    candidate={candidateView}
                    evaluationRubric={evaluationRubric}
                    assessmentId={assessmentId}
                    assessmentsApi={assessmentsApi}
                    roleFitCriteria={reportModel?.roleFitModel?.requirementsAssessment || []}
                    recommendation={reportModel?.recommendation}
                    recruiterSummary={reportModel?.recruiterSummaryText || ''}
                    hideDecision
                  />
                </Suspense>
              </details>
            </ErrorBoundary>
          ) : null}
        </div>

        <div
          className={`pane ${activeTab === 'cv' ? 'active' : ''}`}
          data-p="cv"
          id="report-pane-cv"
          role="region"
          aria-labelledby="candidate-report-view-item-cv"
        >
          <div className="cv-doc-actions">
            <span className="name">
              {(application?.candidate_name || application?.candidate_email || 'Candidate')} · CV
              {(() => {
                // Guard on validity, not just truthiness — a malformed date
                // string rendered "uploaded Invalid Date". Omit the segment
                // when the parse fails (see fmtDate in ScoreProvenance.jsx).
                const uploaded = application?.cv_uploaded_at ? new Date(application.cv_uploaded_at) : null;
                return uploaded && !Number.isNaN(uploaded.getTime())
                  ? ` · uploaded ${uploaded.toLocaleDateString()}`
                  : '';
              })()}
            </span>
            {application?.workable_profile_url ? (
              <button
                type="button"
                className="btn btn-outline btn-sm"
                data-internal-only
                onClick={() => window.open(application.workable_profile_url, '_blank', 'noopener,noreferrer')}
              >
                <ExternalLink size={13} />
                Open in Workable
              </button>
            ) : null}
          </div>
          <div className="cv-layout">
            <CvDocumentViewer
              applicationId={application?.id || null}
              candidateId={application?.candidate_id || completedAssessment?.candidate_id || null}
              filename={application?.cv_filename || completedAssessment?.candidate_cv_filename || ''}
              uploadedAt={application?.cv_uploaded_at || null}
              rolesApi={rolesApi}
              candidatesApi={candidatesApi}
              parsedSections={application?.cv_sections || null}
              cvText={application?.cv_text || ''}
              application={application}
              cvMatchDetails={cvMatchDetails}
              autoPreview={activeTab === 'cv'}
            />
          </div>
        </div>

        <div
          className={`pane ${activeTab === 'prep' ? 'active' : ''}`}
          data-p="prep"
          id="report-pane-prep"
          role="region"
          aria-labelledby="candidate-report-view-item-prep"
        >
          {/* HANDOFF v2 §5.1 / canvas cand-prep — Interview is:
              (1) purple-soft hero banner: READY FOR YOUR PANEL · {N} questions,
                  anchored in {candidate}'s actual evidence
              (2) STAGE 1 · RECRUITER SCREEN kicker + question cards
              (3) STAGE 2 · HIRING PANEL kicker + question cards
              Each card: mono kicker "QUESTION NN · {source}" + question +
              two-column LISTEN FOR (green) / CONCERNING IF (red). */}
          {(() => {
            const totalQs = (interviewQuestions.stageOne?.length || 0) + (interviewQuestions.stageTwo?.length || 0);
            const candidateFirstName = String(application?.candidate_name || '').trim().split(/\s+/)[0] || 'this candidate';
            // The stage-1/stage-2 arrays ALWAYS carry hardcoded template
            // questions, so totalQs is never 0 — the old "builds after scoring"
            // branch was dead AND the hero falsely claimed every question was
            // "anchored in evidence" / "cites the assessment" even for wholly
            // unscored candidates. Gate the confident copy on real evidence.
            const hasEvidence = Boolean(application?.interview_prep)
              || riskItems.length > 0
              || strengthItems.length > 0;
            return (
              <div className="mc-prep-hero">
                <div className="mc-kicker">READY FOR YOUR PANEL</div>
                <div className="mc-prep-hero-title">
                  {hasEvidence
                    ? <>{totalQs} questions, anchored in {candidateFirstName}'s actual <em>evidence</em>.</>
                    : <>Starter questions — evidence-anchored prep <em>builds</em> after scoring.</>}
                </div>
                <p className="mc-prep-hero-body">
                  {hasEvidence
                    ? 'Each question cites the moment in the assessment it came from. Listen-for and concerning-if are calibrated to your role rubric.'
                    : 'These are role-level starters. Once the candidate is scored, they’re replaced with questions tied to their evidence and calibrated to your role rubric.'}
                </p>
              </div>
            );
          })()}

          <div className="mc-prep-stage">
            <div className="mc-kicker">STAGE 1 · RECRUITER SCREEN</div>
            <div className="mc-prep-stage-grid">
              {interviewQuestions.stageOne.map((item, index) => (
                <PrepQuestionCard
                  key={`${item.question}-${index}`}
                  item={item}
                  number={index + 1}
                  listenLabel="LISTEN FOR"
                  concernLabel="CONCERNING IF"
                  fallbackConcern="Ask for one concrete example, artifact, or tradeoff."
                />
              ))}
            </div>
          </div>

          <div className="mc-prep-stage">
            <div className="mc-kicker">STAGE 2 · HIRING PANEL</div>
            <div className="mc-prep-stage-grid">
              {interviewQuestions.stageTwo.map((item, index) => (
                <PrepQuestionCard
                  key={`${item.question}-${index}`}
                  item={item}
                  number={index + 1}
                  listenLabel="LISTEN FOR"
                  concernLabel="CONCERNING IF"
                  fallbackConcern="Vague answers without links to code, prompts, or decisions."
                />
              ))}
            </div>
          </div>
          {/* Transcript evidence is read-only here. Provider connections and
              matching belong to the workspace transcription service, not the
              candidate report. */}
          {!isClientView ? (
            <div className="mc-prep-stage" data-section="interview-record">
              <div className="mc-kicker">INTERVIEW RECORD</div>
              <div className="mt-2">
                <TranscriptPanel application={application} />
              </div>
            </div>
          ) : null}

        </div>

        <div
          className={`pane ${activeTab === 'notes' ? 'active' : ''}`}
          data-p="notes"
          data-internal-only={isClientView ? '' : undefined}
          id="report-pane-notes"
          role="region"
          aria-labelledby="candidate-report-view-item-notes"
        >
          {/* HANDOFF v2 §5.1 / canvas cand-notes — Notes is the
              hiring-team context surface:
              (1) HIRING TEAM NOTES column — note cards (who · role · time + body),
                  the freeform note box + agent-visible toggle and link quick-add.
              (2) AUDIT TIMELINE column — vertical line + colored dots,
                  each event has TIME · title · description.
              We synthesize "hiring team notes" from `recruiter_note` events on
              the application timeline; freeform notes + the ranking/link
              quick-adds all save via rolesApi.addApplicationNote (a
              `recruiter_note` event, optionally carrying a `kind`) and bump
              eventsRefetchTick so the timeline reloads. */}
          {(() => {
            // Recruiter notes are persisted by POST /assessments/{id}/notes,
            // which appends `{event_type: "note", text, author, timestamp}`
            // to `assessment.timeline` (a JSON column). They are NOT
            // emitted to the application_events table. So we read both
            // sources: assessment.timeline first (real persisted notes)
            // and applicationEvents as a fallback for any future
            // recruiter_note event-type emissions.
            const timelineNotes = (() => {
              // Recruiter shares don't fetch the assessment (auth-only), so the
              // backend embeds the note-type timeline entries on the payload.
              const entries = Array.isArray(completedAssessment?.timeline)
                ? completedAssessment.timeline
                : (Array.isArray(application?.recruiter_notes_timeline)
                  ? application.recruiter_notes_timeline
                  : []);
              return entries
                .filter((entry) => {
                  const type = String(entry?.event_type || entry?.type || '').toLowerCase();
                  if (type !== 'note' && type !== 'recruiter_note') return false;
                  return Boolean((entry?.text || entry?.prompt || '').trim());
                })
                .map((entry, idx) => ({
                  key: `tl-note-${entry.timestamp || entry.time || idx}-${idx}`,
                  who: entry?.author || 'Recruiter',
                  role: 'Hiring team',
                  time: entry?.timestamp || entry?.time,
                  body: entry?.text || entry?.prompt || '',
                }))
                .filter((note) => note.body && note.body.trim());
            })();
            const eventNotes = applicationEvents
              .filter((event) => {
                const type = String(event?.event_type || '').toLowerCase();
                return type === 'recruiter_note'
                  || type === 'note_added'
                  || (event?.metadata && typeof event.metadata.note === 'string' && event.metadata.note.trim());
              })
              .map((event) => {
                const meta = event?.metadata || {};
                const kind = String(meta.kind || 'note').toLowerCase();
                const linkUrlMeta = String(meta.link_url || '').trim();
                const linkLabelMeta = String(meta.link_label || '').trim();
                // A link note may have an empty comment — fall back to the
                // label, then the URL, so the card always shows something.
                const body = kind === 'link'
                  ? (String(meta.note || '').trim() || linkLabelMeta || linkUrlMeta)
                  : (meta.note || event?.reason || event?.description || '');
                return {
                  key: `evt-note-${event.id || event.created_at}`,
                  who: event?.actor_name || meta.actor_name || 'Recruiter',
                  role: event?.actor_role || meta.actor_role || 'Hiring team',
                  time: event?.created_at,
                  body,
                  kind,
                  ranking: meta.ranking != null ? Number(meta.ranking) : null,
                  linkUrl: linkUrlMeta,
                  linkLabel: linkLabelMeta,
                  transcriptUrl: String(meta.transcript_url || '').trim(),
                };
              })
              .filter((note) => note.body && note.body.trim());
            // Newest first across both sources.
            const recruiterNotes = [...timelineNotes, ...eventNotes].sort((a, b) => {
              const ta = a.time ? new Date(a.time).getTime() : 0;
              const tb = b.time ? new Date(b.time).getTime() : 0;
              return tb - ta;
            });

            const fmtRelative = (ts) => {
              if (!ts) return '';
              const diffMs = Date.now() - new Date(ts).getTime();
              if (Number.isNaN(diffMs)) return '';
              const diffMin = Math.round(diffMs / 60000);
              if (diffMin < 1) return 'just now';
              if (diffMin < 60) return `${diffMin}m ago`;
              const diffHr = Math.round(diffMin / 60);
              if (diffHr < 24) return `${diffHr}h ago`;
              const diffDay = Math.round(diffHr / 24);
              if (diffDay < 14) return `${diffDay}d ago`;
              return new Date(ts).toLocaleDateString();
            };

            const eventDotColor = (event) => {
              const type = String(event?.event_type || '').toLowerCase();
              // reject/advance keep the established red/green verdict semantics;
              // everything else stays on the purple token scale (the raw blue
              // hex previously used for assess events was off the design system
              // — no blue token exists).
              if (type.includes('reject')) return 'var(--red)';
              if (type.includes('advance') || type.includes('approved')) return 'var(--green)';
              if (type.includes('assess')) return 'var(--purple)';
              if (type.includes('cv_scored') || type.includes('invite')) return 'var(--purple)';
              return 'var(--mute)';
            };

            // Synced-from-Workable surfaces, exposed on ApplicationDetailResponse.
            // These are read-only here: Workable comments + the activity log come
            // from the recruiter's Workable account, questionnaire answers are the
            // candidate's own LinkedIn/Workable-apply responses. The hiring-team
            // note box above stays Tali-internal (never posted back to Workable).
            const workableComments = Array.isArray(application?.workable_comments)
              ? application.workable_comments
              : [];
            const workableAnswers = Array.isArray(application?.workable_questionnaire_answers)
              ? application.workable_questionnaire_answers
              : [];
            const workableActivity = Array.isArray(application?.workable_activity_log)
              ? application.workable_activity_log
              : [];

            return (
              <div className="mc-notes-layout">
                <section className="mc-notes-workspace" aria-labelledby="candidate-notes-heading">
                  <div className="mc-notes-section-head">
                    <div>
                      <div id="candidate-notes-heading" className="mc-kicker">HIRING TEAM NOTES</div>
                      <p className="mc-notes-section-copy">
                        Shared team context and structured interview outcomes for this candidate.
                      </p>
                    </div>
                  </div>
                  {recruiterNotes.length === 0 ? (
                    <Card className="mc-notes-empty-card">
                      <div className="mc-notes-empty-title">No hiring team notes yet</div>
                      <p className="mc-notes-empty-copy">
                        {isInterviewView
                          ? 'The hiring team has not added any shared context for this candidate.'
                          : 'Add context for the hiring team, or guidance the Taali agent should consider.'}
                      </p>
                    </Card>
                  ) : (
                    <div className="mc-notes-feed">
                      {recruiterNotes.map((note) => {
                        const isRanking = note.kind === 'ranking' && Number.isFinite(note.ranking);
                        const isLink = note.kind === 'link' && note.linkUrl;
                        return (
                          <Card as="article" key={note.key} className="mc-notes-card" data-kind={note.kind || 'note'}>
                            <div className="mc-notes-card-head">
                              <span className="mc-notes-card-who">
                                {note.who}
                                <span className="mc-notes-card-role"> · {note.role}</span>
                              </span>
                              <span className="mc-notes-card-time">{fmtRelative(note.time)}</span>
                            </div>
                            <div className="mc-notes-card-body">
                              {isRanking ? (
                                <span
                                  className="mc-notes-rank"
                                  title={`Ranked ${note.ranking} out of 5`}
                                >
                                  ★ {note.ranking}/5
                                </span>
                              ) : null}
                              {isLink ? (
                                <a
                                  href={note.linkUrl}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="mc-notes-link"
                                >
                                  {note.linkLabel || note.body || note.linkUrl}
                                </a>
                              ) : (
                                // For ranking, the body is the optional comment;
                                // don't repeat it if it was only the auto label.
                                (isRanking && note.body === `Ranking ${note.ranking}/5`)
                                  ? null
                                  : note.body
                              )}
                              {note.transcriptUrl && !isLink ? (
                                <a
                                  href={note.transcriptUrl}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="mc-notes-link mc-notes-transcript-link"
                                >
                                  View full transcript ↗
                                </a>
                              ) : null}
                            </div>
                          </Card>
                        );
                      })}
                    </div>
                  )}
                  {/* Adding notes hits an auth-only endpoint, so the input is
                      recruiter-app only — share recipients see notes read-only.
                      Notes save against the application, so they work with or
                      without a linked assessment. */}
                  {isInterviewView ? null : (() => {
                    const canAddNote = Boolean(application?.id || assessmentId);
                    return (
                    <Card as="section" className="mc-note-composer" aria-labelledby="candidate-note-composer-label">
                      <label
                        id="candidate-note-composer-label"
                        className="mc-note-composer-label"
                        htmlFor="candidate-team-note"
                      >
                        Add a hiring team note
                      </label>
                      <p id="candidate-team-note-help" className="mc-note-composer-help">
                        Internal to Taali. Capture context for the hiring team; this note is not sent to Workable, Bullhorn, or the candidate.
                      </p>
                      <Textarea
                        id="candidate-team-note"
                        value={noteDraft}
                        onChange={(event) => setNoteDraft(event.target.value)}
                        placeholder={canAddNote
                          ? 'Write a note for the hiring team…'
                          : 'Notes open once this candidate has an application record.'}
                        disabled={!canAddNote || savingNote}
                        rows={4}
                        aria-describedby="candidate-team-note-help"
                      />
                      <div className="mc-note-composer-footer">
                        <label className="mc-notes-agent-toggle">
                          <input
                            type="checkbox"
                            checked={noteForAgent}
                            onChange={(event) => setNoteForAgent(event.target.checked)}
                            disabled={!canAddNote || savingNote}
                          />
                          <span>
                            <strong>Use as Taali agent guidance</strong>
                            <small>The agent will consider this note in future recommendations.</small>
                          </span>
                        </label>
                        <Button
                          variant="primary"
                          size="sm"
                          onClick={handleSaveNote}
                          disabled={!canAddNote || savingNote || !noteDraft.trim()}
                          loading={savingNote}
                          loadingLabel="Adding…"
                        >
                          Add note
                        </Button>
                      </div>
                    </Card>
                    );
                  })()}

                  {/* Optional supporting link, stored via the note endpoint and
                      visible to the agent alongside freeform notes. This needs
                      a real application id (the structured-kind endpoint is
                      application-scoped), so it is hidden on share routes and
                      when no application record exists. */}
                  {!isInterviewView && application?.id ? (
                    <div
                      className="mc-audit-collapse mc-notes-addinfo"
                      data-open={supportingLinkOpen ? 'true' : 'false'}
                      style={{ marginTop: 14 }}
                    >
                      <button
                        type="button"
                        className="mc-audit-summary"
                        aria-expanded={supportingLinkOpen}
                        aria-controls="candidate-supporting-link"
                        onClick={() => setSupportingLinkOpen((open) => !open)}
                      >
                        <span className="mc-kicker" style={{ margin: 0 }}>ADD SUPPORTING LINK</span>
                        <span className="mc-audit-count">Optional</span>
                      </button>
                      <MotionDisclosure open={supportingLinkOpen} id="candidate-supporting-link">
                        <Card className="mc-supporting-link-card">
                          <div className="mc-supporting-link-fields">
                            <label className="mc-note-composer-label" htmlFor="candidate-supporting-link-url">
                              URL
                            </label>
                            <Input
                              id="candidate-supporting-link-url"
                              type="url"
                              value={linkUrl}
                              onChange={(event) => setLinkUrl(event.target.value)}
                              placeholder="https://… (portfolio, GitHub, reference)"
                              disabled={savingLink}
                            />
                            <label className="mc-note-composer-label" htmlFor="candidate-supporting-link-label">
                              Label <span className="mc-field-optional">Optional</span>
                            </label>
                            <Input
                              id="candidate-supporting-link-label"
                              type="text"
                              value={linkLabel}
                              onChange={(event) => setLinkLabel(event.target.value)}
                              placeholder="Optional label (e.g. “Portfolio”)"
                              disabled={savingLink}
                            />
                          </div>
                          <div className="mc-notes-input-actions">
                            <Button
                              variant="secondary"
                              size="sm"
                              onClick={handleSaveLink}
                              disabled={savingLink || !linkUrl.trim()}
                              loading={savingLink}
                              loadingLabel="Adding…"
                            >
                              Add link
                            </Button>
                          </div>
                        </Card>
                      </MotionDisclosure>
                    </div>
                  ) : null}

                  {/* Structured feedback belongs with team notes and outcomes,
                      while remaining a distinct record (round, recommendation,
                      probes). Client shares hide it; recruiter shares are
                      read-only. The candidate-scoped key clears local drafts
                      when navigating between candidate records. */}
                  {!isClientView && application?.id ? (
                    <InterviewFeedbackSection
                      key={`interview-feedback-${application.id}`}
                      applicationId={application.id}
                      interviewKit={application?.candidate_interview_kit}
                      initialFeedback={application?.interview_feedback}
                      rolesApi={rolesApi}
                      readOnly={isInterviewView}
                    />
                  ) : null}

                  {/* Questionnaire — the candidate's own Workable/LinkedIn-apply
                      answers (read-only). Above Workable comments per preview. */}
                  {workableAnswers.length > 0 ? (
                    <>
                      <div className="mc-kicker" style={{ marginTop: 18 }}>QUESTIONNAIRE RESPONSES</div>
                      {workableAnswers.map((entry, idx) => {
                        const question = String(entry?.question || '').trim();
                        const answer = String(entry?.answer || '').trim();
                        if (!question && !answer) return null;
                        return (
                          <Card as="article" key={`wk-answer-${idx}`} className="mc-notes-card">
                            {question ? <div className="mc-notes-card-who">{question}</div> : null}
                            {answer ? <div className="mc-notes-card-body">{answer}</div> : null}
                          </Card>
                        );
                      })}
                    </>
                  ) : null}

                  {/* Workable comments — recruiter comments synced from Workable. */}
                  {workableComments.length > 0 ? (
                    <>
                      <div className="mc-kicker" style={{ marginTop: 18 }}>WORKABLE COMMENTS</div>
                      {workableComments.map((comment, idx) => {
                        const body = String(comment?.body || '').trim();
                        if (!body) return null;
                        const author = String(comment?.author || '').trim() || 'Workable';
                        return (
                          <Card as="article" key={`wk-comment-${comment?.created_at || idx}-${idx}`} className="mc-notes-card">
                            <div className="mc-notes-card-head">
                              <span className="mc-notes-card-who">
                                {author}
                                <span className="mc-notes-card-role"> · Workable</span>
                              </span>
                              <span className="mc-notes-card-time">{fmtRelative(comment?.created_at)}</span>
                            </div>
                            <div className="mc-notes-card-body">{body}</div>
                          </Card>
                        );
                      })}
                    </>
                  ) : null}
                </section>

                {/* The timeline is a permanent right-hand rail on wide report
                    layouts. Main content remains first in the DOM, and CSS
                    stacks this aside underneath it on narrower viewports. */}
                <Card
                  as="aside"
                  className="mc-notes-timeline-rail"
                  aria-labelledby="candidate-timeline-heading"
                >
                  <div className="mc-notes-timeline-head">
                    <div>
                      <div id="candidate-timeline-heading" className="mc-kicker">ACTIVITY TIMELINE</div>
                      <p className="mc-notes-timeline-copy">Pipeline updates in chronological context.</p>
                    </div>
                    <span className="mc-audit-count">
                      {applicationEvents.length + workableActivity.length} event{(applicationEvents.length + workableActivity.length) === 1 ? '' : 's'}
                    </span>
                  </div>

                  {(applicationEvents.length + workableActivity.length) === 0 ? (
                    <p className="mc-notes-timeline-empty">
                      Activity will appear here as the candidate moves through the pipeline.
                    </p>
                  ) : null}

                  {applicationEvents.length > 0 ? (
                    <div className="mc-audit-timeline">
                      {applicationEvents.slice(0, 12).map((event, idx) => {
                        const type = String(event?.event_type || 'activity').replace(/_/g, ' ');
                        const meta = event?.metadata || {};
                        let title = type.charAt(0).toUpperCase() + type.slice(1);
                        if (String(event?.event_type || '').toLowerCase() === 'cv_scored') {
                          const score = Number(meta.role_fit_score);
                          if (Number.isFinite(score)) title = `CV scored — ${Math.round(score)} / 100`;
                        }
                        const detail = event?.reason || event?.description || meta.note || '';
                        return (
                          <div key={event.id || `${event.event_type}-${idx}`} className="mc-audit-row">
                            <span
                              className="mc-audit-dot"
                              aria-hidden="true"
                              style={{ background: eventDotColor(event) }}
                            />
                            <div>
                              <div className="mc-audit-time">{fmtRelative(event?.created_at).toUpperCase()}</div>
                              <div className="mc-audit-title">{title}</div>
                              {detail ? <div className="mc-audit-detail">{detail}</div> : null}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  ) : null}

                  {workableActivity.length > 0 ? (
                    <section className="mc-notes-workable-activity" aria-labelledby="candidate-workable-activity-heading">
                      <div id="candidate-workable-activity-heading" className="mc-kicker">WORKABLE ACTIVITY</div>
                      <div className="mc-audit-timeline">
                        {workableActivity.map((entry, idx) => {
                          const action = String(entry?.action || '').replace(/_/g, ' ').trim();
                          const stage = String(entry?.stage || '').trim();
                          const body = String(entry?.body || '').trim();
                          const title = [action, stage].filter(Boolean).join(' · ')
                            || (body ? 'Comment' : 'Workable activity');
                          return (
                            <div key={`wk-activity-${entry?.created_at || idx}`} className="mc-audit-row">
                              <span
                                className="mc-audit-dot"
                                aria-hidden="true"
                                style={{ background: 'var(--purple)' }}
                              />
                              <div>
                                <div className="mc-audit-time">{fmtRelative(entry?.created_at).toUpperCase()}</div>
                                <div className="mc-audit-title">{title}</div>
                                {body && body !== title ? <div className="mc-audit-detail">{body}</div> : null}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </section>
                  ) : null}
                </Card>
              </div>
            );
          })()}
        </div>

          </main>
        </div>
      </div>

      {/* Decision modals — mirror HomeNow's wiring. Rendered at the page root
          so the strip's Override / Teach actions open the SAME flows as the
          home hub. On submit, refetch the candidate's decision + reload the
          report so the strip reflects the new state. */}
      {teachFor ? (
        <TeachModal
          decision={teachFor.decision}
          defaultScope="decision"
          onClose={() => setTeachFor(null)}
          onSubmitted={async () => {
            if (!isCurrentReportScope(teachFor.reportScope)) return;
            showToast('Feedback recorded. Decision returned to the queue.', 'success');
            await loadStandingReport({ silent: true });
          }}
        />
      ) : null}

      {alternativeFor ? (
        <OverrideModal
          decision={alternativeFor.decision}
          alternative={alternativeFor.alternative}
          // The candidate report doesn't carry the per-shortcode Workable-stage
          // map the home hub lazy-loads; pass the application's own stage list
          // when present, else [] (OverrideModal advances on the internal stage
          // when there are no Workable stages to pick).
          workableStages={application?.workable_stages || []}
          onClose={() => setAlternativeFor(null)}
          onSubmitting={() => {
            if (alternativeFor.alternative.action === 'skip_assessment_advance') return;
            freezeAgentDecision(
              alternativeFor.decision,
              asProcessingDecision(alternativeFor.decision),
              alternativeFor.reportScope,
            );
          }}
          onRejected={() => {
            restoreAgentDecision(alternativeFor.decision, alternativeFor.reportScope);
          }}
          onSubmitted={async (receipt) => {
            const modalScope = alternativeFor.reportScope;
            if (!isCurrentReportScope(modalScope)) return;
            const reclassified = alternativeFor.alternative.action === 'skip_assessment_advance';
            if (reclassified) {
              applyCanonicalAgentDecision(receipt, modalScope);
            } else {
              if (!freezeAgentDecision(
                alternativeFor.decision,
                asProcessingDecision(alternativeFor.decision, receipt),
                modalScope,
              )) return;
            }
            showToast(
              reclassified
                ? `${alternativeFor.alternative.confirmLabel || 'Decision'} reclassified.`
                : `${alternativeFor.alternative.confirmLabel || 'Decision'} accepted for processing.`,
              'success',
            );
            await loadStandingReport({ silent: true });
          }}
          onOutcomeUnknown={(receipt) => {
            const modalScope = alternativeFor.reportScope;
            if (!freezeAgentDecision(
              alternativeFor.decision,
              asProcessingDecision(alternativeFor.decision, receipt),
              modalScope,
            )) return;
            showToast(APPROVAL_OUTCOME_UNKNOWN_MESSAGE, 'error');
            void loadAgentDecision();
          }}
        />
      ) : null}
    </div>
  );
};

export default CandidateStandingReportPage;
