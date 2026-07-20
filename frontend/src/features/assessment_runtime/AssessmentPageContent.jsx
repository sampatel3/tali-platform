import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { BRAND } from '../../config/brand';
import { assessments } from '../../shared/api';
import { motionSafeScrollBehavior } from '../../shared/motion';
import { AssessmentContextWindow } from './AssessmentContextWindow';
import { AssessmentRuntimeAlerts } from './AssessmentRuntimeAlerts';
import { AssessmentStatusScreen } from './AssessmentStatusScreen';
import { AssessmentTopBar } from './AssessmentTopBar';
import { AssessmentWorkspace } from './AssessmentWorkspace';
import { AssessmentStagePanel } from './AssessmentStagePanel';
import {
  clearCandidateSessionKey,
  getOrCreateCandidateSessionKey,
} from './assessmentSessionBinding';
import {
  CandidateProofUnavailableError,
  clearCandidateProofBinding,
  clearCandidateRuntimeRecovery,
  rememberCandidateRuntime,
  scrubCandidateInviteTokenFromUrl,
} from '../../shared/assessment/candidateProofBinding';
import {
  AssessmentWorkspaceSecurityProvider,
  WorkspacePrintBlocker,
  WorkspaceSecurityBanner,
  WorkspaceSecurityWatermark,
  createOpaqueWorkspaceMarker,
  createProtectedRootHandlers,
  useAssessmentWorkspaceSecurity,
} from './AssessmentWorkspaceSecurity';
import './assessmentWorkspaceSecurity.css';
import {
  buildRepoFileTree,
  extractRepoFiles,
  formatBudgetUsd,
  formatTime,
  formatUsd,
  hydrateRepoFile,
  isRepoFileModified,
  isRepoFileUnsynced,
  languageFromPath,
  markRepoFileSynced,
  mergeEditorContentIntoRepoFiles,
  normalizeStartData,
  normalizeRepoPathInput,
  upsertRepoFile,
} from './assessmentRuntimeHelpers';

const ASSESSMENT_THEME_STORAGE_KEY = 'taali_assessment_theme';
const AUTOSAVE_DELAY_MS = 1200;
const KEEPALIVE_INTERVAL_MS = 5 * 60 * 1000;

const candidateProofErrorMessage = (error) => (
  error instanceof CandidateProofUnavailableError ? error.message : null
);

// Default orientation path shown when a task ships no two_stage config —
// a visible way through the first minutes (where most drop-off happens).
// Purely presentational: the stepper never locks the workspace.
const DEFAULT_ORIENTATION_STAGES = {
  parts: [
    {
      title: 'Get oriented',
      minutes: 5,
      blurb:
        "Skim the brief, then ask Claude to run the tests to see where things stand — it already has the repo open and knows the task.",
    },
    {
      title: 'Decide & direct',
      blurb:
        'Own the key decisions Claude raises, then direct it to build to your calls. You are scored on how you steer, not on typing the code yourself.',
    },
    {
      title: 'Verify & submit',
      blurb:
        'Re-run the tests, check the result matches your decisions, then submit. Verifying before you call it done is part of the score.',
    },
  ],
  note: 'This path is guidance, not a lock — work however suits you.',
};

const readAssessmentLightModePreference = () => {
  if (typeof window === 'undefined') return true;
  try {
    return window.localStorage.getItem(ASSESSMENT_THEME_STORAGE_KEY) !== 'dark';
  } catch {
    return true;
  }
};

const persistAssessmentLightModePreference = (lightMode) => {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(
      ASSESSMENT_THEME_STORAGE_KEY,
      lightMode ? 'light' : 'dark'
    );
  } catch {
    // Ignore storage failures so browser privacy settings cannot crash the runtime.
  }
};

const buildExecutionOutput = (result) => {
  const command = String(result?.command || '').trim();
  const workingDir = String(result?.working_dir || '').trim();
  const stdout = String(result?.stdout || '').trim();
  const stderr = String(result?.stderr || '').trim();
  const error = String(result?.error || '').trim();
  const richResults = Array.isArray(result?.results)
    ? result.results.map((entry) => String(entry || '').trim()).filter(Boolean)
    : [];
  const sections = [];

  if (command) {
    sections.push(`--- command ---\n$ ${command}${workingDir ? `\n# cwd: ${workingDir}` : ''}`);
  }
  if (stdout) {
    sections.push(stdout);
  }
  if (richResults.length > 0) {
    sections.push(`--- results ---\n${richResults.join('\n')}`);
  }
  if (stderr) {
    sections.push(`--- stderr ---\n${stderr}`);
  }
  if (error) {
    sections.push(`--- error ---\n${error}`);
  }

  if (sections.length > 0) {
    return sections.join('\n\n');
  }

  if (result?.success === false) {
    return 'Execution failed, but the runtime did not return stdout or stderr.';
  }

  return 'Code executed successfully. No stdout/stderr was produced.';
};

const initializeRepoEditorState = (runtimeData, { lazyLoadRepoContents = false } = {}) => {
  const files = extractRepoFiles(runtimeData?.repo_structure, {
    contentsLoaded: !lazyLoadRepoContents,
  });
  const starter = runtimeData?.starter_code || '';
  if (files.length === 0) {
    return {
      repoFiles: [],
      selectedRepoFile: null,
      editorContent: starter,
    };
  }
  // An explicit initial_selected_repo_path (set only by the demo/showcase
  // fixtures) opens that file immediately, so the preview lands on the code
  // workspace instead of chat-only. Live assessments never set it, so the
  // chat-centred default below is unchanged for real candidates.
  const explicitPath = String(runtimeData?.initial_selected_repo_path || '').trim();
  const explicitFile = explicitPath ? files.find((file) => file.path === explicitPath) : null;
  if (explicitFile) {
    return {
      repoFiles: files,
      selectedRepoFile: explicitFile.path,
      editorContent: explicitFile.loaded ? (explicitFile.content ?? '') : '',
    };
  }
  // Chat-centred init (2026-06-01): code-kind tasks land with NO file
  // selected so the editor pane stays hidden and the candidate's first
  // surface is chat only. Doc-kind tasks (PM, Scrum Master) auto-open
  // their deliverable's primary_artifact so the markdown editor is
  // visible from the start. Selecting the first file alphabetically
  // (the old default) put .gitignore in front of every engineering
  // candidate — wrong framing.
  const deliverable = runtimeData?.deliverable;
  const primary = (deliverable && typeof deliverable === 'object')
    ? String(deliverable.primary_artifact || '').trim()
    : '';
  const primaryFile = primary
    ? files.find((file) => file.path === primary)
    : null;
  if (primaryFile) {
    return {
      repoFiles: files,
      selectedRepoFile: primaryFile.path,
      editorContent: primaryFile.loaded ? (primaryFile.content ?? '') : '',
    };
  }
  return {
    repoFiles: files,
    selectedRepoFile: null,
    editorContent: '',
  };
};

// Demo transcripts are authored as alternating { role, content } turns (see
// PRODUCT_WALKTHROUGH.runtime.claudeConversation). The live agentic chat
// hydrates from ai_prompts ({ message, response }), not that shape — so flatten
// the turns into user→assistant pairs here. A leading assistant turn (an
// unprompted opener) becomes { message: '', response }.
const conversationToAiPrompts = (conversation) => {
  if (!Array.isArray(conversation)) return [];
  const prompts = [];
  for (let i = 0; i < conversation.length; i += 1) {
    const turn = conversation[i];
    const role = String(turn?.role || '').toLowerCase();
    const content = String(turn?.content || '');
    if (role === 'assistant') {
      prompts.push({ message: '', response: content });
      continue;
    }
    const next = conversation[i + 1];
    if (next && String(next.role || '').toLowerCase() === 'assistant') {
      prompts.push({ message: content, response: String(next.content || '') });
      i += 1;
    } else {
      prompts.push({ message: content, response: '' });
    }
  }
  return prompts;
};

export default function AssessmentPage({
  assessmentId,
  token,
  taskData,
  startData,
  demoMode = false,
  demoProfile = null,
  onDemoRestart = null,
  onJoinTaali = null,
}) {
  const [assessment, setAssessment] = useState(null);
  const [candidateSessionKey, setCandidateSessionKey] = useState(null);
  const [loading, setLoading] = useState(true);
  const [startError, setStartError] = useState(null);
  const [startAttempt, setStartAttempt] = useState(0);
  const [output, setOutput] = useState("");
  const [executing, setExecuting] = useState(false);
  const [timeLeft, setTimeLeft] = useState(0);
  const [submitted, setSubmitted] = useState(false);
  // The submit request is in flight. We keep the candidate in the workspace
  // (NOT on the submitted screen) until the API resolves 2xx, so a failure
  // never flashes the "Task submitted" screen and then silently reverts.
  const [submitting, setSubmitting] = useState(false);
  // Prominent submit-failure surface rendered near the submit action (a
  // failed submit must be impossible to miss — the output dock is closed).
  const [submitError, setSubmitError] = useState(null);
  const [tabSwitchCount, setTabSwitchCount] = useState(0);
  const [proctoringEnabled, setProctoringEnabled] = useState(false);
  const [showTabWarning, setShowTabWarning] = useState(false);
  const [isTimerPaused, setIsTimerPaused] = useState(false);
  const [pauseReason, setPauseReason] = useState(null);
  const [pauseMessage, setPauseMessage] = useState("");
  const [timeMilestoneNotice, setTimeMilestoneNotice] = useState(null);
  const [submittedAtIso, setSubmittedAtIso] = useState(null);
  const [submitConfirmOpen, setSubmitConfirmOpen] = useState(false);
  const [claudeBudget, setClaudeBudget] = useState(null);
  const [repoFilesState, setRepoFilesState] = useState([]);
  const [selectedRepoFile, setSelectedRepoFile] = useState(null);
  const [editorContent, setEditorContent] = useState("");
  const [repoFileLoadErrors, setRepoFileLoadErrors] = useState({});
  const [savingRepoFile, setSavingRepoFile] = useState(false);
  const [repoFileSaveStates, setRepoFileSaveStates] = useState({});
  const [lastSavedAtIso, setLastSavedAtIso] = useState(null);
  const [claudePending, setClaudePending] = useState(false);
  const [refreshingClaudeChanges, setRefreshingClaudeChanges] = useState(false);
  const [pendingClaudeChanges, setPendingClaudeChanges] = useState([]);
  const [creatingRepoFile, setCreatingRepoFile] = useState(false);
  const [newRepoFilePath, setNewRepoFilePath] = useState('');
  const [demoRunCount, setDemoRunCount] = useState(0);
  const [demoSaveCount, setDemoSaveCount] = useState(0);
  const [outputPanelOpen, setOutputPanelOpen] = useState(false);
  const [assessmentLightMode, setAssessmentLightMode] = useState(readAssessmentLightModePreference);
  // Demo/deck showcase opens TWO-PART: the repo file-tree starts collapsed so
  // the workspace reads as Claude + the editor (the two work surfaces) rather
  // than a cramped three-column layout. Live candidates keep the repo expanded.
  const [repoPanelCollapsed, setRepoPanelCollapsed] = useState(demoMode);
  // `demoMode` can resolve AFTER the initial mount, so the static initial state
  // above only catches the synchronous case — collapse the repo once demoMode
  // is known. (Runs once; the recruiter/candidate can still toggle it open.)
  useEffect(() => { if (demoMode) setRepoPanelCollapsed(true); }, [demoMode]);
  const [assistantPanelCollapsed, setAssistantPanelCollapsed] = useState(false);
  const [collapsedRepoDirs, setCollapsedRepoDirs] = useState({});
  const codeRef = useRef("");
  const selectedRepoFileRef = useRef(null);
  const repoFileLoadsRef = useRef(new Map());
  const repoLoadGenerationRef = useRef(0);
  const repoFilesRef = useRef([]);
  const autosaveFailureKeyRef = useRef(null);
  const contextWindowRef = useRef(null);
  const timerRef = useRef(null);
  const milestoneFlagsRef = useRef({ halfway: false, warning80: false, warning90: false });
  const milestoneTimerRef = useRef(null);
  // Always points at the latest handleSubmit so the timer interval doesn't
  // capture a stale closure when handleSubmit's deps change mid-assessment.
  const handleSubmitRef = useRef(null);
  // Same pattern for the pre-timeout snapshot push — declared early so the
  // shared timer effect can read it via ref without circular dependencies.
  const preTimeoutSnapshotRef = useRef(null);
  const preTimeoutSnapshotFlushedRef = useRef(false);
  const autoSubmitAttemptedRef = useRef(false);
  const submitCancelButtonRef = useRef(null);
  const submitConfirmButtonRef = useRef(null);

  useEffect(() => {
    if (!submitConfirmOpen || typeof document === 'undefined') return undefined;
    const previouslyFocused = document.activeElement;
    submitCancelButtonRef.current?.focus();

    const handleDialogKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        setSubmitConfirmOpen(false);
        return;
      }
      if (event.key !== 'Tab') return;
      const buttons = [submitCancelButtonRef.current, submitConfirmButtonRef.current]
        .filter((button) => button && !button.disabled);
      if (buttons.length === 0) return;
      const first = buttons[0];
      const last = buttons[buttons.length - 1];
      if (event.shiftKey && (document.activeElement === first || !buttons.includes(document.activeElement))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !buttons.includes(document.activeElement))) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', handleDialogKeyDown);
    return () => {
      document.removeEventListener('keydown', handleDialogKeyDown);
      previouslyFocused?.focus?.();
    };
  }, [submitConfirmOpen]);

  const showTimeMilestoneNotice = useCallback((message, tone) => {
    setTimeMilestoneNotice({ message, tone });
    if (milestoneTimerRef.current) {
      clearTimeout(milestoneTimerRef.current);
    }
    milestoneTimerRef.current = setTimeout(() => {
      setTimeMilestoneNotice(null);
      milestoneTimerRef.current = null;
    }, 7000);
  }, []);

  useEffect(() => {
    setLoading(true);
    setStartError(null);
    setCandidateSessionKey(null);
    setSubmitted(false);
    setSubmitting(false);
    setSubmitError(null);
    setDemoRunCount(0);
    setDemoSaveCount(0);
    setOutputPanelOpen(false);
    // Demo/deck opens two-part (repo collapsed to a slim rail); live expands.
    setRepoPanelCollapsed(demoMode);
    setAssistantPanelCollapsed(false);
    milestoneFlagsRef.current = { halfway: false, warning80: false, warning90: false };
    setTimeMilestoneNotice(null);
    setSubmittedAtIso(null);
    setSubmitConfirmOpen(false);
    setOutput('');
    setRepoFilesState([]);
    setSelectedRepoFile(null);
    selectedRepoFileRef.current = null;
    setEditorContent('');
    setRepoFileLoadErrors({});
    repoFileLoadsRef.current.clear();
    repoLoadGenerationRef.current += 1;
    setCollapsedRepoDirs({});
    setSavingRepoFile(false);
    setRepoFileSaveStates({});
    setLastSavedAtIso(null);
    setClaudePending(false);
    setRefreshingClaudeChanges(false);
    setPendingClaudeChanges([]);
    autosaveFailureKeyRef.current = null;
    setCreatingRepoFile(false);
    setNewRepoFilePath('');

    if (startData) {
      const normalized = normalizeStartData(startData);
      setAssessment(normalized);
      if (!demoMode && (token || startData.token)) {
        try {
          setCandidateSessionKey(getOrCreateCandidateSessionKey(token || startData.token));
        } catch {
          setStartError("Couldn't establish this browser session. Reopen the invite link in this browser or contact support.");
          setLoading(false);
          return;
        }
      }
      const repoState = initializeRepoEditorState(normalized, {
        lazyLoadRepoContents: !demoMode && Boolean(token || startData.token),
      });
      setRepoFilesState(repoState.repoFiles);
      setSelectedRepoFile(repoState.selectedRepoFile);
      selectedRepoFileRef.current = repoState.selectedRepoFile;
      setEditorContent(repoState.editorContent);
      codeRef.current = repoState.editorContent;
      setTimeLeft(normalized.time_remaining);
      setProctoringEnabled(startData.task?.proctoring_enabled || false);
      setIsTimerPaused(Boolean(normalized.is_timer_paused));
      setPauseReason(normalized.pause_reason || null);
      setClaudeBudget(normalized.claude_budget || null);
      setLoading(false);
      return;
    }
    if (taskData) {
      setAssessment(taskData);
      if (!demoMode && token) {
        try {
          setCandidateSessionKey(getOrCreateCandidateSessionKey(token));
        } catch {
          setStartError("Couldn't establish this browser session. Reopen the invite link in this browser or contact support.");
          setLoading(false);
          return;
        }
      }
      const repoState = initializeRepoEditorState(taskData, {
        lazyLoadRepoContents: !demoMode && Boolean(token),
      });
      setRepoFilesState(repoState.repoFiles);
      setSelectedRepoFile(repoState.selectedRepoFile);
      selectedRepoFileRef.current = repoState.selectedRepoFile;
      setEditorContent(repoState.editorContent);
      codeRef.current = repoState.editorContent;
      setTimeLeft((taskData.duration_minutes || 30) * 60);
      setProctoringEnabled(taskData.proctoring_enabled || false);
      setIsTimerPaused(false);
      setPauseReason(null);
      setClaudeBudget(taskData.claude_budget || null);
      setLoading(false);
      return;
    }
    if (!token) {
      setLoading(false);
      setStartError('This assessment link is incomplete. Reopen the original invite email or contact support.');
      return;
    }
    const startAssessment = async () => {
      try {
        const sessionKey = getOrCreateCandidateSessionKey(token);
        setCandidateSessionKey(sessionKey);
        const res = await assessments.start(token, {
          candidate_session_key: sessionKey,
        });
        const data = res.data;
        rememberCandidateRuntime(token, data.assessment_id);
        scrubCandidateInviteTokenFromUrl();
        const normalized = normalizeStartData({ ...data, token });
        setAssessment(normalized);
        const repoState = initializeRepoEditorState(normalized, {
          lazyLoadRepoContents: true,
        });
        setRepoFilesState(repoState.repoFiles);
        setSelectedRepoFile(repoState.selectedRepoFile);
        selectedRepoFileRef.current = repoState.selectedRepoFile;
        setEditorContent(repoState.editorContent);
        codeRef.current = repoState.editorContent;
        setTimeLeft(normalized.time_remaining);
        setProctoringEnabled(data.task?.proctoring_enabled || false);
        setIsTimerPaused(Boolean(normalized.is_timer_paused));
        setPauseReason(normalized.pause_reason || null);
        setClaudeBudget(normalized.claude_budget || null);
      } catch (error) {
        setStartError(
          error instanceof CandidateProofUnavailableError
            ? error.message
            : (error instanceof Error && /browser session/i.test(error.message))
              ? "Couldn't establish this browser session. Reopen the invite link in this tab or contact support."
            : "Couldn't load the assessment. Refresh the page to try again.",
        );
      } finally {
        setLoading(false);
      }
    };
    startAssessment();
  }, [demoMode, token, taskData, startData, startAttempt]);

  useEffect(() => {
    return () => {
      if (milestoneTimerRef.current) {
        clearTimeout(milestoneTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (loading || submitted || timeLeft <= 0 || isTimerPaused) return;

    timerRef.current = setInterval(() => {
      setTimeLeft((prev) => {
        if (prev <= 1) {
          clearInterval(timerRef.current);
          return 0;
        }
        // 30s before zero, sync only candidate-edited files to the sandbox so
        // the server-side timeout artifact captures the latest work without a
        // browser round-trip of the untouched repository.
        if (prev <= 31 && !preTimeoutSnapshotFlushedRef.current) {
          preTimeoutSnapshotFlushedRef.current = true;
          preTimeoutSnapshotRef.current?.();
        }
        return prev - 1;
      });
    }, 1000);

    return () => clearInterval(timerRef.current);
  }, [loading, submitted, isTimerPaused]);

  useEffect(() => {
    if (loading || submitted || isTimerPaused) return;
    const totalSeconds = Math.max(1, Number((assessment?.duration_minutes || 30) * 60));
    const elapsedRatio = Math.max(0, Math.min(1, (totalSeconds - timeLeft) / totalSeconds));
    if (elapsedRatio >= 0.5 && !milestoneFlagsRef.current.halfway) {
      milestoneFlagsRef.current.halfway = true;
      showTimeMilestoneNotice('Halfway through — prioritize highest-impact tasks now.', 'info');
    }
    if (elapsedRatio >= 0.8 && !milestoneFlagsRef.current.warning80) {
      milestoneFlagsRef.current.warning80 = true;
      showTimeMilestoneNotice('20% time remaining — move to verification and final checks.', 'warning');
    }
    if (elapsedRatio >= 0.9 && !milestoneFlagsRef.current.warning90) {
      milestoneFlagsRef.current.warning90 = true;
      showTimeMilestoneNotice('10% time remaining — finalize and prepare to submit.', 'danger');
    }
  }, [assessment?.duration_minutes, isTimerPaused, loading, showTimeMilestoneNotice, submitted, timeLeft]);

  useEffect(() => {
    if (!proctoringEnabled) return undefined;

    const handleVisibilityChange = () => {
      if (
        typeof document !== "undefined" &&
        document.visibilityState === "hidden"
      ) {
        setTabSwitchCount((prev) => prev + 1);
        if (proctoringEnabled) {
          setShowTabWarning(true);
          setTimeout(() => setShowTabWarning(false), 3000);
        }
      }
    };

    if (typeof document !== "undefined" && "visibilityState" in document) {
      document.addEventListener("visibilitychange", handleVisibilityChange);
    }

    return () => {
      if (typeof document !== "undefined") {
        document.removeEventListener(
          "visibilitychange",
          handleVisibilityChange,
        );
      }
    };
  }, [proctoringEnabled]);

  const assessmentTokenForApi = assessment?.token ?? token;
  const liveAssessmentId = assessment?.id ?? assessmentId ?? startData?.assessment_id;
  const workspaceMarker = useMemo(
    () => createOpaqueWorkspaceMarker(liveAssessmentId),
    [liveAssessmentId],
  );
  // An approved accommodation can opt this browser-only deterrence layer out.
  // It is intentionally not treated as an enforcement boundary: candidates
  // control their browsers, while the server remains authoritative.
  const externalClipboardAccommodation = Boolean(
    assessment?.allow_external_clipboard
      || assessment?.task?.allow_external_clipboard
      || assessment?.task?.accommodations?.allow_external_clipboard,
  );
  const workspaceProtectionEnabled = Boolean(
    !demoMode && assessmentTokenForApi && !externalClipboardAccommodation,
  );

  const advisoryEventThrottleRef = useRef(new Map());
  const emitAdvisoryIntegrityEvent = useCallback((eventType, fields = {}) => {
    const id = assessment?.id ?? assessmentId;
    if (!workspaceProtectionEnabled || !id || !assessmentTokenForApi || !candidateSessionKey) return;
    const source = String(fields.source || 'workspace')
      .toLowerCase()
      .replace(/[^a-z0-9_-]+/g, '_')
      .replace(/^_+|_+$/g, '')
      .slice(0, 40) || 'workspace';
    const throttleKey = `${eventType}:${source}`;
    const now = Date.now();
    const previous = advisoryEventThrottleRef.current.get(throttleKey) || 0;
    // Browser events can arrive in small bursts (capture + synthetic editor
    // behavior). Keep telemetry useful without turning it into a keystroke log.
    if (now - previous < 300) return;
    advisoryEventThrottleRef.current.set(throttleKey, now);
    assessments.runtimeEvent(
      id,
      eventType,
      assessmentTokenForApi,
      {
        source,
        length: Math.max(0, Math.min(2_000_000, Number(fields.length) || 0)),
        ...(fields.file_path ? { file_path: String(fields.file_path).slice(0, 500) } : {}),
      },
      candidateSessionKey,
    ).catch(() => {});
  }, [
    assessment?.id,
    assessmentId,
    assessmentTokenForApi,
    candidateSessionKey,
    workspaceProtectionEnabled,
  ]);

  const workspaceSecurity = useAssessmentWorkspaceSecurity({
    enabled: workspaceProtectionEnabled,
    sessionMarker: workspaceMarker,
    emitEvent: emitAdvisoryIntegrityEvent,
    resetKey: liveAssessmentId,
  });
  const protectedRootHandlers = useMemo(
    () => createProtectedRootHandlers(workspaceSecurity),
    [workspaceSecurity],
  );

  // In demo mode the chat is read-only and pre-seeded from the walkthrough
  // transcript; the live runtime uses the candidate's real ai_prompts.
  const demoInitialAiPrompts = useMemo(
    () => (demoMode ? conversationToAiPrompts(demoProfile?.claudeConversation) : null),
    [demoMode, demoProfile?.claudeConversation],
  );
  const taskContext = assessment?.scenario || assessment?.description || "";
  const repoFiles = mergeEditorContentIntoRepoFiles(
    repoFilesState,
    selectedRepoFile,
    editorContent,
  );
  repoFilesRef.current = repoFiles;
  const unsyncedRepoFiles = repoFiles.filter(isRepoFileUnsynced);
  const hasUnsavedEdits = unsyncedRepoFiles.length > 0;
  // Chat-centred default: nothing is selected at mount, so the editor
  // pane stays hidden and the candidate lands on a chat-dominant
  // workspace. The editor reveals only when (a) the candidate clicks
  // a file in the repo tree, or (b) a doc-kind task declares a
  // ``deliverable.primary_artifact`` — in which case we auto-select
  // it so the deliverable opens immediately (Scrum Master HANDBACK.md,
  // PM DECISION_MEMO.md). Code-kind engineering tasks land on
  // chat-only; the candidate browses to a file when they need to.
  const deliverable = assessment?.deliverable || null;
  const deliverablePrimary = deliverable?.primary_artifact || null;
  const repoHasPrimary = Boolean(
    deliverablePrimary && repoFiles.some((file) => file.path === deliverablePrimary),
  );
  const selectedRepoPath =
    selectedRepoFile && repoFiles.some((file) => file.path === selectedRepoFile)
      ? selectedRepoFile
      : repoHasPrimary
        ? deliverablePrimary
        : null;
  const selectedRepoEntry = selectedRepoPath
    ? repoFiles.find((fileEntry) => fileEntry.path === selectedRepoPath)
    : null;
  const selectedRepoFileKnown = Boolean(selectedRepoEntry);
  const selectedRepoFileLoaded = Boolean(selectedRepoEntry?.loaded);
  const selectedRepoFileLoading = Boolean(
    selectedRepoPath
      && selectedRepoFileKnown
      && !selectedRepoFileLoaded
      && !repoFileLoadErrors[selectedRepoPath],
  );
  const selectedRepoFileLoadError = selectedRepoPath
    ? repoFileLoadErrors[selectedRepoPath] || null
    : null;
  const repoFileTree = buildRepoFileTree(repoFiles);
  const modifiedRepoPaths = repoFiles
    .filter(isRepoFileModified)
    .map((fileEntry) => fileEntry.path);
  const hasRepoStructure = repoFiles.length > 0;
  // ``task.role`` is a DB enum slug (``data_engineer``); render it as
  // a human title for the candidate-facing meta line. Sam called this
  // out on assessment 79 (2026-05-26).
  const formatRoleLabel = (slug) => {
    const raw = String(slug || '').trim();
    if (!raw) return '';
    return raw
      .replace(/[_-]+/g, ' ')
      .split(/\s+/)
      .filter(Boolean)
      .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
      .join(' ');
  };
  const runtimeMetaLine = useMemo(
    () => [
      assessment?.organization_name || BRAND.name,
      formatRoleLabel(assessment?.task?.role) || assessment?.task_name || 'Assessment',
      assessment?.candidate_name || null,
    ].filter(Boolean).join(' · '),
    [assessment?.candidate_name, assessment?.organization_name, assessment?.task?.role, assessment?.task_name],
  );
  const reportIssueHref = useMemo(() => {
    const subjectParts = ['Assessment support'];
    if (assessment?.id || assessmentId) {
      subjectParts.push(`#${assessment?.id || assessmentId}`);
    }
    const subject = encodeURIComponent(subjectParts.join(' '));
    return `mailto:support@taali.ai?subject=${subject}`;
  }, [assessment?.id, assessmentId]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    persistAssessmentLightModePreference(assessmentLightMode);
  }, [assessmentLightMode]);

  useEffect(() => {
    if (executing) {
      setOutputPanelOpen(true);
    }
  }, [executing]);

  const handleOpenGuide = useCallback(() => {
    contextWindowRef.current?.scrollIntoView({ behavior: motionSafeScrollBehavior('smooth'), block: 'start' });
  }, []);

  // First-minutes engagement beacons — fire-and-forget, deduped locally and
  // once-per-type server-side; never sent from the demo/deck walkthrough.
  const runtimeEventSentRef = useRef({});
  const sendRuntimeEvent = useCallback(
    (eventType) => {
      const id = assessment?.id ?? assessmentId;
      if (demoMode || !id || !assessmentTokenForApi || !candidateSessionKey) return;
      if (runtimeEventSentRef.current[eventType]) return;
      runtimeEventSentRef.current[eventType] = true;
      assessments.runtimeEvent(
        id,
        eventType,
        assessmentTokenForApi,
        {},
        candidateSessionKey,
      ).catch(() => {});
    },
    [assessment?.id, assessmentId, assessmentTokenForApi, candidateSessionKey, demoMode],
  );

  useEffect(() => {
    if (!loading && assessment) sendRuntimeEvent('runtime_loaded');
  }, [loading, assessment, sendRuntimeEvent]);

  useEffect(() => {
    const id = assessment?.id ?? assessmentId;
    if (
      demoMode
      || loading
      || submitted
      || isTimerPaused
      || !id
      || !assessmentTokenForApi
      || !candidateSessionKey
    ) return undefined;

    const keepWorkspaceAlive = () => {
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
      assessments.keepalive(id, assessmentTokenForApi, candidateSessionKey)
        .then((response) => {
          const authoritativeRemaining = Number(response?.data?.time_remaining);
          if (Number.isFinite(authoritativeRemaining) && authoritativeRemaining >= 0) {
            setTimeLeft(Math.floor(authoritativeRemaining));
          }
        })
        .catch(() => {
          // The next normal workspace request also renews the sandbox. A
          // keepalive failure should not interrupt or alarm the candidate.
        });
    };

    const intervalId = setInterval(keepWorkspaceAlive, KEEPALIVE_INTERVAL_MS);
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') keepWorkspaceAlive();
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      clearInterval(intervalId);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [
    assessment?.id,
    assessmentId,
    assessmentTokenForApi,
    candidateSessionKey,
    demoMode,
    isTimerPaused,
    loading,
    submitted,
  ]);

  useEffect(() => {
    if (!workspaceSecurity.enabled || typeof document === 'undefined') return undefined;
    let wasFullscreen = Boolean(document.fullscreenElement);
    const handleSecurityVisibility = () => {
      if (document.visibilityState === 'hidden') {
        workspaceSecurity.report('visibility_hidden', { source: 'document', length: 0 });
      }
    };
    const handleFullscreenChange = () => {
      const isFullscreen = Boolean(document.fullscreenElement);
      if (wasFullscreen && !isFullscreen) {
        workspaceSecurity.report('fullscreen_exit', { source: 'document', length: 0 });
        workspaceSecurity.announce('Fullscreen was exited. This is recorded only as an advisory activity signal.');
      }
      wasFullscreen = isFullscreen;
    };
    const handleBeforePrint = () => {
      workspaceSecurity.report('print_attempt', { source: 'browser_menu', length: 0 });
      workspaceSecurity.announce('Printing is unavailable in this assessment workspace.');
    };
    document.addEventListener('visibilitychange', handleSecurityVisibility);
    document.addEventListener('fullscreenchange', handleFullscreenChange);
    window.addEventListener('beforeprint', handleBeforePrint);
    return () => {
      document.removeEventListener('visibilitychange', handleSecurityVisibility);
      document.removeEventListener('fullscreenchange', handleFullscreenChange);
      window.removeEventListener('beforeprint', handleBeforePrint);
    };
  }, [workspaceSecurity.announce, workspaceSecurity.enabled, workspaceSecurity.report]);

  const fetchRepoFileOnce = useCallback((path) => {
    const normalizedPath = normalizeRepoPathInput(path);
    const id = assessment?.id || assessmentId;
    if (!normalizedPath || !id || !assessmentTokenForApi || !candidateSessionKey || demoMode) {
      return Promise.reject(new Error('This repository file is unavailable.'));
    }

    const existingRequest = repoFileLoadsRef.current.get(normalizedPath);
    if (existingRequest) return existingRequest;

    const generation = repoLoadGenerationRef.current;
    const request = assessments.getRepoFile(
      id,
      normalizedPath,
      assessmentTokenForApi,
      candidateSessionKey,
    ).then((response) => {
      const responsePath = normalizeRepoPathInput(response?.data?.path);
      if (responsePath !== normalizedPath || typeof response?.data?.content !== 'string') {
        throw new Error('The workspace returned an invalid file response.');
      }
      const content = response.data.content;
      const revision = typeof response.data.revision === 'string' ? response.data.revision : null;
      if (repoLoadGenerationRef.current === generation) {
        setRepoFilesState((currentFiles) => {
          const currentEntry = currentFiles.find((fileEntry) => fileEntry.path === normalizedPath);
          if (!currentEntry || currentEntry.loaded) return currentFiles;
          return hydrateRepoFile(currentFiles, normalizedPath, content, revision);
        });
      }
      return content;
    }).catch((error) => {
      if (repoFileLoadsRef.current.get(normalizedPath) === request) {
        repoFileLoadsRef.current.delete(normalizedPath);
      }
      throw error;
    });

    repoFileLoadsRef.current.set(normalizedPath, request);
    return request;
  }, [assessment?.id, assessmentId, assessmentTokenForApi, candidateSessionKey, demoMode]);

  useEffect(() => {
    selectedRepoFileRef.current = selectedRepoPath;
    if (
      !selectedRepoPath
      || !selectedRepoFileKnown
      || selectedRepoFileLoaded
      || selectedRepoFileLoadError
      || demoMode
    ) {
      return undefined;
    }

    const generation = repoLoadGenerationRef.current;
    fetchRepoFileOnce(selectedRepoPath)
      .then((content) => {
        if (
          repoLoadGenerationRef.current !== generation
          || selectedRepoFileRef.current !== selectedRepoPath
        ) return;
        setEditorContent(content);
        codeRef.current = content;
      })
      .catch((error) => {
        if (
          repoLoadGenerationRef.current !== generation
          || selectedRepoFileRef.current !== selectedRepoPath
        ) return;
        const detail = error?.response?.data?.detail;
        const message = candidateProofErrorMessage(error)
          || detail?.message
          || (typeof detail === 'string' ? detail : "Couldn't load this file. Try again.");
        setRepoFileLoadErrors((current) => ({ ...current, [selectedRepoPath]: message }));
      });

    return undefined;
  }, [
    demoMode,
    fetchRepoFileOnce,
    selectedRepoFileKnown,
    selectedRepoFileLoaded,
    selectedRepoFileLoadError,
    selectedRepoPath,
  ]);

  const handleRetryRepoFile = useCallback(() => {
    if (!selectedRepoPath) return;
    setRepoFileLoadErrors((current) => {
      const next = { ...current };
      delete next[selectedRepoPath];
      return next;
    });
  }, [selectedRepoPath]);

  const toggleRepoDir = useCallback((dir) => {
    if (!dir) return;
    setCollapsedRepoDirs((prev) => ({
      ...prev,
      [dir]: !prev[dir],
    }));
  }, []);

  const handleSelectRepoFile = useCallback(
    (path) => {
      if (path === selectedRepoPath) return;
      const nextRepoFiles = mergeEditorContentIntoRepoFiles(
        repoFilesState,
        selectedRepoPath,
        editorContent,
      );
      const normalizedPath = normalizeRepoPathInput(path);
      const nextEntry = nextRepoFiles.find((fileEntry) => fileEntry.path === normalizedPath);
      const nextContent = nextEntry?.loaded ? (nextEntry.content ?? '') : '';
      setRepoFilesState(nextRepoFiles);
      setSelectedRepoFile(normalizedPath || null);
      selectedRepoFileRef.current = normalizedPath || null;
      setEditorContent(nextContent);
      codeRef.current = nextContent;
      if (normalizedPath) sendRuntimeEvent('file_opened');
    },
    [selectedRepoPath, editorContent, repoFilesState, sendRuntimeEvent],
  );

  const handleEditorChange = useCallback((value) => {
    setEditorContent(value ?? "");
    codeRef.current = value ?? "";
    autosaveFailureKeyRef.current = null;
    if (selectedRepoPath) {
      setRepoFileSaveStates((current) => ({
        ...current,
        [selectedRepoPath]: { status: 'dirty', error: null },
      }));
    }
  }, [selectedRepoPath]);

  const buildRepoSnapshot = useCallback(
    (currentEditorContent = editorContent) => mergeEditorContentIntoRepoFiles(
      repoFilesState,
      selectedRepoPath,
      currentEditorContent,
    ),
    [repoFilesState, selectedRepoPath, editorContent],
  );

  const handleCreateRepoFile = useCallback((requestedPath) => {
    if (!creatingRepoFile) {
      setCreatingRepoFile(true);
      return;
    }

    const normalizedPath = normalizeRepoPathInput(requestedPath);
    if (!normalizedPath) {
      setOutput('Enter a valid relative file path like src/new_file.py.');
      setOutputPanelOpen(true);
      return;
    }

    const nextRepoFiles = buildRepoSnapshot(editorContent);
    if (nextRepoFiles.some((fileEntry) => fileEntry.path === normalizedPath)) {
      setOutput(`File already exists: ${normalizedPath}`);
      setOutputPanelOpen(true);
      return;
    }

    const createdRepoFiles = upsertRepoFile(nextRepoFiles, normalizedPath, '');
    setRepoFilesState(createdRepoFiles);
    setSelectedRepoFile(normalizedPath);
    selectedRepoFileRef.current = normalizedPath;
    setEditorContent('');
    codeRef.current = '';
    setCreatingRepoFile(false);
    setNewRepoFilePath('');
    setOutput(`Created ${normalizedPath}. Add content, then click Save to sync it into the workspace.`);
  }, [buildRepoSnapshot, creatingRepoFile]);

  const handleCancelRepoFileCreate = useCallback(() => {
    setCreatingRepoFile(false);
    setNewRepoFilePath('');
  }, []);

  const handleToggleOutputPanel = useCallback(() => {
    setOutputPanelOpen((prev) => !prev);
  }, []);

  const handleExecute = useCallback(
    async (code) => {
      if (claudePending || refreshingClaudeChanges || savingRepoFile || submitting) return;
      if (isTimerPaused) {
        setOutput("Assessment is paused and your timer is stopped. Running code will be available again when the session resumes.");
        setOutputPanelOpen(true);
        return;
      }
      if (selectedRepoPath && !selectedRepoFileLoaded) {
        setOutput(selectedRepoFileLoadError || 'Wait for the selected file to finish loading before running it.');
        setOutputPanelOpen(true);
        return;
      }
      setOutputPanelOpen(true);
      codeRef.current = code;
      const repoSnapshot = buildRepoSnapshot(code);
      setRepoFilesState(repoSnapshot);
      setExecuting(true);
      if (demoMode) {
        setDemoRunCount((prev) => prev + 1);
        setOutput(demoProfile?.output || "Running demo checks...\n\npytest -q --tb=short\n\n2 failed, 9 passed. The remaining failures are intentional prompts for the walkthrough.");
        setExecuting(false);
        return;
      }
      setOutput("Running...\n");
      try {
        const id = assessment?.id || assessmentId;
        const res = await assessments.execute(
          id,
          {
            code,
            selected_file_path: selectedRepoPath,
            base_revision: selectedRepoEntry?.revision ?? null,
          },
          assessmentTokenForApi,
          candidateSessionKey,
        );
        const result = res.data;
        if (selectedRepoPath) {
          setRepoFilesState((currentFiles) => markRepoFileSynced(
            currentFiles,
            selectedRepoPath,
            code,
            result?.revision,
          ));
          setRepoFileSaveStates((current) => ({
            ...current,
            [selectedRepoPath]: { status: 'saved', error: null },
          }));
          setLastSavedAtIso(new Date().toISOString());
        }
        setOutput(buildExecutionOutput(result));
      } catch (err) {
        const detail = err.response?.data?.detail;
        if (detail?.code === "ASSESSMENT_PAUSED") {
          setIsTimerPaused(true);
          setPauseReason(detail.pause_reason || "claude_outage");
          setPauseMessage(detail.message || "Assessment is paused.");
        }
        setOutput(
          candidateProofErrorMessage(err)
            || detail?.message
            || (typeof detail === 'string' ? detail : 'Something went wrong running your code. Try again — your latest saved work is kept.'),
        );
      } finally {
        setExecuting(false);
      }
    },
    [
      assessment,
      assessmentId,
      assessmentTokenForApi,
      candidateSessionKey,
      isTimerPaused,
      demoMode,
      demoProfile?.output,
      buildRepoSnapshot,
      selectedRepoFileLoaded,
      selectedRepoFileLoadError,
      selectedRepoPath,
      selectedRepoEntry?.revision,
      claudePending,
      refreshingClaudeChanges,
      savingRepoFile,
      submitting,
    ],
  );

  const markSaved = useCallback((path) => {
    if (path) {
      setRepoFileSaveStates((current) => ({
        ...current,
        [path]: { status: 'saved', error: null },
      }));
    }
    setLastSavedAtIso(new Date().toISOString());
  }, []);

  const syncSelectedRepoFileToWorkspace = useCallback(async (code, { announceSuccess = false } = {}) => {
    codeRef.current = code;
    const repoSnapshot = buildRepoSnapshot(code);
    setRepoFilesState(repoSnapshot);

    if (!selectedRepoPath) {
      markSaved(null);
      if (announceSuccess) {
        setOutput("Code saved.");
      }
      return { success: true, repoSnapshot };
    }

    const selectedEntry = repoSnapshot.find((fileEntry) => fileEntry.path === selectedRepoPath);
    if (!selectedEntry?.loaded) {
      const errorMessage = selectedRepoFileLoadError
        || 'Wait for the selected file to finish loading before saving it.';
      if (announceSuccess) {
        setOutput(errorMessage);
        setOutputPanelOpen(true);
      }
      return { success: false, repoSnapshot, errorMessage };
    }

    const id = assessment?.id || assessmentId;
    if (!id || !assessmentTokenForApi) {
      setRepoFilesState((currentFiles) => markRepoFileSynced(currentFiles, selectedRepoPath, code));
      markSaved(selectedRepoPath);
      if (announceSuccess) {
        setOutput(`Saved ${selectedRepoPath} locally.`);
      }
      return { success: true, repoSnapshot };
    }

    setSavingRepoFile(true);
    setRepoFileSaveStates((current) => ({
      ...current,
      [selectedRepoPath]: { status: 'saving', error: null },
    }));
    try {
      const response = await assessments.saveRepoFile(
        id,
        {
          path: selectedRepoPath,
          content: code,
          base_revision: selectedEntry.revision ?? null,
        },
        assessmentTokenForApi,
        candidateSessionKey,
      );
      setRepoFilesState((currentFiles) => markRepoFileSynced(
        currentFiles,
        selectedRepoPath,
        code,
        response?.data?.revision,
      ));
      markSaved(selectedRepoPath);
      if (announceSuccess) {
        setOutput(`Saved ${selectedRepoPath} to the live workspace.`);
      }
      return { success: true, repoSnapshot };
    } catch (err) {
      const detail = err?.response?.data?.detail;
      const errorMessage = candidateProofErrorMessage(err)
        || detail?.message
        || (typeof detail === 'string' ? detail : "Couldn't save your changes. Try again.");
      const conflict = err?.response?.status === 409 && detail?.code === 'FILE_REVISION_CONFLICT';
      setRepoFileSaveStates((current) => ({
        ...current,
        [selectedRepoPath]: {
          status: conflict ? 'conflict' : 'error',
          error: errorMessage,
        },
      }));
      if (announceSuccess) {
        // A failed save must be visible — open the dock so the error isn't
        // buried in a closed panel.
        setOutput(errorMessage);
        setOutputPanelOpen(true);
      }
      return { success: false, repoSnapshot, errorMessage };
    } finally {
      setSavingRepoFile(false);
    }
  }, [
    buildRepoSnapshot,
    selectedRepoPath,
    selectedRepoFileLoadError,
    assessment,
    assessmentId,
    assessmentTokenForApi,
    candidateSessionKey,
    markSaved,
  ]);

  // The sandbox is the repository authority. Before timeout/submission, sync
  // only files that the candidate actually changed, one bounded file request
  // at a time. Unopened manifest entries never travel back through the browser.
  const syncUnsyncedRepoFilesToWorkspace = useCallback(async (code) => {
    codeRef.current = code;
    const repoSnapshot = buildRepoSnapshot(code);
    setRepoFilesState(repoSnapshot);
    const unsyncedFiles = repoSnapshot.filter(isRepoFileUnsynced);

    const id = assessment?.id || assessmentId;
    if (!id || !assessmentTokenForApi || unsyncedFiles.length === 0) {
      return { success: true, repoSnapshot };
    }

    setSavingRepoFile(true);
    let activePath = null;
    try {
      let syncedSnapshot = repoSnapshot;
      for (const fileEntry of unsyncedFiles) {
        activePath = fileEntry.path;
        setRepoFileSaveStates((current) => ({
          ...current,
          [fileEntry.path]: { status: 'saving', error: null },
        }));
        const response = await assessments.saveRepoFile(
          id,
          {
            path: fileEntry.path,
            content: fileEntry.content,
            base_revision: fileEntry.revision ?? null,
          },
          assessmentTokenForApi,
          candidateSessionKey,
        );
        syncedSnapshot = markRepoFileSynced(
          syncedSnapshot,
          fileEntry.path,
          fileEntry.content,
          response?.data?.revision,
        );
        setRepoFilesState((currentFiles) => markRepoFileSynced(
          currentFiles,
          fileEntry.path,
          fileEntry.content,
          response?.data?.revision,
        ));
        setRepoFileSaveStates((current) => ({
          ...current,
          [fileEntry.path]: { status: 'saved', error: null },
        }));
        setLastSavedAtIso(new Date().toISOString());
      }
      return { success: true, repoSnapshot: syncedSnapshot };
    } catch (err) {
      const detail = err?.response?.data?.detail;
      const errorMessage = candidateProofErrorMessage(err)
        || detail?.message
        || (typeof detail === 'string' ? detail : "Couldn't save your changes. Try again.");
      const conflict = err?.response?.status === 409 && detail?.code === 'FILE_REVISION_CONFLICT';
      if (activePath) {
        setRepoFileSaveStates((current) => ({
          ...current,
          [activePath]: {
            status: conflict ? 'conflict' : 'error',
            error: errorMessage,
          },
        }));
      }
      return { success: false, repoSnapshot, errorMessage };
    } finally {
      setSavingRepoFile(false);
    }
  }, [buildRepoSnapshot, assessment, assessmentId, assessmentTokenForApi, candidateSessionKey]);

  const autosaveKey = useMemo(() => unsyncedRepoFiles
    .map((fileEntry) => `${fileEntry.path}\u0000${fileEntry.revision || ''}\u0000${fileEntry.content}`)
    .join('\u0001'), [unsyncedRepoFiles]);

  useEffect(() => {
    if (
      !autosaveKey
      || demoMode
      || loading
      || submitted
      || submitting
      || isTimerPaused
      || claudePending
      || refreshingClaudeChanges
      || executing
      || savingRepoFile
      || autosaveFailureKeyRef.current === autosaveKey
    ) return undefined;

    const timeoutId = setTimeout(() => {
      syncUnsyncedRepoFilesToWorkspace(codeRef.current).then((result) => {
        autosaveFailureKeyRef.current = result.success ? null : autosaveKey;
      }).catch(() => {
        autosaveFailureKeyRef.current = autosaveKey;
      });
    }, AUTOSAVE_DELAY_MS);
    return () => clearTimeout(timeoutId);
  }, [
    autosaveKey,
    claudePending,
    demoMode,
    executing,
    isTimerPaused,
    loading,
    refreshingClaudeChanges,
    savingRepoFile,
    submitted,
    submitting,
    syncUnsyncedRepoFilesToWorkspace,
  ]);

  const handleBeforeClaudeSubmit = useCallback(async () => {
    const result = await syncUnsyncedRepoFilesToWorkspace(codeRef.current);
    if (result.success) return;
    const error = new Error('Workspace save failed');
    error.response = {
      data: {
        detail: {
          message: result.errorMessage || "Couldn't save your edits before asking Claude. Try again.",
        },
      },
    };
    throw error;
  }, [syncUnsyncedRepoFilesToWorkspace]);

  const handleClaudeWorkspaceChanged = useCallback((rawChangedPaths) => {
    const normalized = (Array.isArray(rawChangedPaths) ? rawChangedPaths : [])
      .map((entry) => {
        const path = normalizeRepoPathInput(typeof entry === 'string' ? entry : entry?.path);
        if (!path) return null;
        return {
          path,
          revision: typeof entry?.revision === 'string' ? entry.revision : null,
          deleted: typeof entry !== 'string' && entry?.revision === null,
        };
      })
      .filter(Boolean);
    if (normalized.length === 0) return;
    setRefreshingClaudeChanges(true);
    setPendingClaudeChanges(normalized);
  }, []);

  useEffect(() => {
    if (claudePending || pendingClaudeChanges.length === 0) return undefined;
    let cancelled = false;

    const refreshChangedFiles = async () => {
      const id = assessment?.id || assessmentId;
      if (!id || !assessmentTokenForApi || !candidateSessionKey) {
        setPendingClaudeChanges([]);
        setRefreshingClaudeChanges(false);
        return;
      }

      for (const changedFile of pendingClaudeChanges) {
        const latestEntry = repoFilesRef.current.find((entry) => entry.path === changedFile.path);
        if (latestEntry && isRepoFileUnsynced(latestEntry)) {
          setRepoFileSaveStates((current) => ({
            ...current,
            [changedFile.path]: {
              status: 'conflict',
              error: 'Claude changed this file while it had local edits. Your buffer was kept.',
            },
          }));
          continue;
        }

        if (changedFile.deleted) {
          setRepoFilesState((currentFiles) => currentFiles.filter(
            (entry) => entry.path !== changedFile.path,
          ));
          setRepoFileSaveStates((current) => {
            const next = { ...current };
            delete next[changedFile.path];
            return next;
          });
          setRepoFileLoadErrors((current) => {
            const next = { ...current };
            delete next[changedFile.path];
            return next;
          });
          if (selectedRepoFileRef.current === changedFile.path) {
            selectedRepoFileRef.current = null;
            setSelectedRepoFile(null);
            setEditorContent('');
            codeRef.current = '';
          }
          continue;
        }

        try {
          const response = await assessments.getRepoFile(
            id,
            changedFile.path,
            assessmentTokenForApi,
            candidateSessionKey,
          );
          if (cancelled) return;
          const content = String(response?.data?.content ?? '');
          const revision = response?.data?.revision || changedFile.revision || null;
          setRepoFilesState((currentFiles) => {
            const existing = currentFiles.find((entry) => entry.path === changedFile.path);
            return upsertRepoFile(currentFiles, changedFile.path, content, {
              syncedContent: content,
              revision,
              isNew: existing ? existing.isNew : true,
            });
          });
          setRepoFileSaveStates((current) => ({
            ...current,
            [changedFile.path]: { status: 'saved', error: null },
          }));
          setRepoFileLoadErrors((current) => {
            if (!current[changedFile.path]) return current;
            const next = { ...current };
            delete next[changedFile.path];
            return next;
          });
          if (selectedRepoFileRef.current === changedFile.path) {
            setEditorContent(content);
            codeRef.current = content;
          }
        } catch (error) {
          if (cancelled) return;
          const detail = error?.response?.data?.detail;
          const message = candidateProofErrorMessage(error)
            || detail?.message
            || (typeof detail === 'string' ? detail : "Couldn't refresh a file Claude changed. Try opening it again.");
          setRepoFileLoadErrors((current) => ({ ...current, [changedFile.path]: message }));
          setRepoFileSaveStates((current) => ({
            ...current,
            [changedFile.path]: { status: 'error', error: message },
          }));
        }
      }

      if (!cancelled) {
        setPendingClaudeChanges([]);
        setRefreshingClaudeChanges(false);
      }
    };

    void refreshChangedFiles();
    return () => { cancelled = true; };
  }, [
    assessment?.id,
    assessmentId,
    assessmentTokenForApi,
    candidateSessionKey,
    claudePending,
    pendingClaudeChanges,
  ]);

  const handleSave = useCallback(async (code) => {
    if (claudePending || refreshingClaudeChanges || submitting) return;
    autosaveFailureKeyRef.current = null;
    if (demoMode) {
      setDemoSaveCount((prev) => prev + 1);
    }
    await syncSelectedRepoFileToWorkspace(code, { announceSuccess: true });
  }, [claudePending, demoMode, refreshingClaudeChanges, submitting, syncSelectedRepoFileToWorkspace]);

  const handleProtectedShortcut = useCallback((event) => {
    if (!workspaceSecurity.enabled || (!event.metaKey && !event.ctrlKey) || event.altKey) return;
    const key = String(event.key || '').toLowerCase();
    if (key === 'p') {
      event.preventDefault();
      event.stopPropagation();
      workspaceSecurity.report('print_attempt', { source: 'keyboard', length: 0 });
      workspaceSecurity.announce('Printing is unavailable in this assessment workspace.');
      return;
    }
    if (key === 's') {
      event.preventDefault();
      event.stopPropagation();
      // Keep the familiar shortcut useful: stop the browser's Save Page flow
      // and save the active workspace buffer instead.
      workspaceSecurity.announce('Browser page save was blocked. Saving your current workspace file instead.');
      void handleSave(codeRef.current);
    }
  }, [handleSave, workspaceSecurity]);

  const handleSubmit = useCallback(
    async (autoSubmit = false) => {
      if (submitted || submitting) return;
      // The demo / showcase preview is read-only — a viewer (or the pitch
      // deck) must never be able to submit the walkthrough assessment, which
      // would flip the surface to the "Task submitted" screen. This covers the
      // manual click, the confirm dialog, and the timer auto-submit, which all
      // route through handleSubmit.
      if (demoMode) return;
      if (isTimerPaused) {
        setSubmitError("Assessment is paused and your timer is stopped. You can submit once the session resumes.");
        return;
      }

      if (claudePending || refreshingClaudeChanges || executing || savingRepoFile) {
        setSubmitConfirmOpen(false);
        setSubmitError('Finishing the current workspace action, then your submission will be ready.');
        return;
      }

      if (!autoSubmit) {
        setSubmitConfirmOpen(true);
        return;
      }

      // Enter the in-flight state — the candidate STAYS in the workspace
      // (we do NOT flip to the submitted screen yet). Only a 2xx response
      // flips submitted=true below, so a failed submit never flashes the
      // "Task submitted" screen and then silently reverts.
      setSubmitConfirmOpen(false);
      setSubmitError(null);
      setSubmitting(true);

      try {
        const id = assessment?.id || assessmentId;
        const syncResult = await syncUnsyncedRepoFilesToWorkspace(codeRef.current);
        if (!syncResult.success) {
          setSubmitError(syncResult.errorMessage || "Couldn't sync your latest changes. Try submitting again.");
          return;
        }
        await assessments.submit(
          id,
          {
            final_code: codeRef.current,
            selected_file_path: selectedRepoPath,
          },
          assessmentTokenForApi,
          {
            tab_switch_count: proctoringEnabled ? tabSwitchCount : 0,
          },
          candidateSessionKey,
        );
        // Success — now (and only now) flip to the submitted screen.
        clearCandidateSessionKey(assessmentTokenForApi);
        clearCandidateRuntimeRecovery(assessmentTokenForApi);
        void clearCandidateProofBinding(assessmentTokenForApi);
        setSubmittedAtIso(new Date().toISOString());
        setSubmitted(true);
      } catch (err) {
        const detail = err.response?.data?.detail;
        if (detail?.code === "ASSESSMENT_PAUSED") {
          setIsTimerPaused(true);
          setPauseReason(detail.pause_reason || "claude_outage");
          setPauseMessage(detail.message || "Assessment is paused.");
        }
        // Keep the candidate in the workspace and show the failure in the
        // prominent submit-error banner (near the submit action), not the
        // closed output dock.
        setSubmitError(
          candidateProofErrorMessage(err)
            || detail?.message
            || (typeof detail === 'string' ? detail : "Couldn't submit. Check your connection and try again — your latest saved work is kept."),
        );
      } finally {
        setSubmitting(false);
      }
    },
    [
      assessment,
      assessmentId,
      assessmentTokenForApi,
      candidateSessionKey,
      submitted,
      submitting,
      tabSwitchCount,
      isTimerPaused,
      demoMode,
      claudePending,
      refreshingClaudeChanges,
      executing,
      savingRepoFile,
      proctoringEnabled,
      selectedRepoPath,
      syncUnsyncedRepoFilesToWorkspace,
    ],
  );

  // Mirror the latest handleSubmit into the ref consumed by the timer
  // interval so the timer never invokes a stale closure.
  useEffect(() => {
    handleSubmitRef.current = handleSubmit;
  }, [handleSubmit]);

  useEffect(() => {
    if (
      loading
      || submitted
      || submitting
      || isTimerPaused
      || timeLeft > 0
      || claudePending
      || refreshingClaudeChanges
      || executing
      || savingRepoFile
      || autoSubmitAttemptedRef.current
    ) return;
    autoSubmitAttemptedRef.current = true;
    handleSubmitRef.current?.(true);
  }, [
    claudePending,
    executing,
    isTimerPaused,
    loading,
    refreshingClaudeChanges,
    savingRepoFile,
    submitted,
    submitting,
    timeLeft,
  ]);

  // Same idea for the pre-timeout snapshot pusher — fire-and-forget so the
  // timer never blocks on the network.
  useEffect(() => {
    preTimeoutSnapshotRef.current = () => {
      // Best-effort; surface errors to the candidate as a one-off output line
      // but never throw out of the timer.
      syncUnsyncedRepoFilesToWorkspace(codeRef.current).catch(() => undefined);
    };
  }, [syncUnsyncedRepoFilesToWorkspace]);

  // If the assessment changes (or the candidate starts a new one), allow the
  // pre-timeout snapshot to fire again on the new run.
  useEffect(() => {
    preTimeoutSnapshotFlushedRef.current = false;
    autoSubmitAttemptedRef.current = false;
  }, [assessment?.id, assessmentId]);

  const totalDurationSeconds = Math.max(1, Number((assessment?.duration_minutes || 30) * 60));
  const remainingRatio = Math.max(0, Math.min(1, timeLeft / totalDurationSeconds));
  const progressPercent = Math.max(0, Math.min(100, Math.round((1 - remainingRatio) * 100)));
  const isTimeLow = timeLeft > 0 && timeLeft < 300; // under 5 minutes
  const timeUrgencyLevel = remainingRatio <= 0.1 ? 'danger' : (remainingRatio <= 0.2 ? 'warning' : 'normal');
  const isClaudeBudgetExhausted = Boolean(claudeBudget?.enabled && claudeBudget?.is_exhausted);
  const workspaceLocked = claudePending || refreshingClaudeChanges;
  const workspaceActionsDisabled = workspaceLocked || executing || savingRepoFile || submitting;
  const saveStates = Object.values(repoFileSaveStates);
  const hasSaveConflict = saveStates.some((entry) => entry?.status === 'conflict');
  const hasSaveError = saveStates.some((entry) => entry?.status === 'error');
  const saveStateTone = hasSaveConflict || hasSaveError
    ? 'danger'
    : (savingRepoFile || hasUnsavedEdits || workspaceLocked ? 'pending' : 'saved');
  const saveStateLabel = useMemo(() => {
    if (claudePending) return 'Claude is updating the workspace';
    if (refreshingClaudeChanges) return 'Refreshing Claude changes…';
    if (hasSaveConflict) return 'File changed in workspace — review needed';
    if (hasSaveError) return 'Autosave failed — use Save to retry';
    if (savingRepoFile) return 'Saving…';
    if (hasUnsavedEdits) return 'Autosave pending…';
    if (lastSavedAtIso) {
      const savedDate = new Date(lastSavedAtIso);
      const hhmm = Number.isNaN(savedDate.getTime())
        ? null
        : savedDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      return hhmm ? `Saved · ${hhmm}` : 'Saved';
    }
    return 'All changes saved';
  }, [
    claudePending,
    hasSaveConflict,
    hasSaveError,
    hasUnsavedEdits,
    lastSavedAtIso,
    refreshingClaudeChanges,
    savingRepoFile,
  ]);

  useEffect(() => {
    if (!hasUnsavedEdits && !savingRepoFile && !submitting && !claudePending) return undefined;
    const warnBeforeUnload = (event) => {
      event.preventDefault();
      event.returnValue = '';
      return '';
    };
    window.addEventListener('beforeunload', warnBeforeUnload);
    return () => window.removeEventListener('beforeunload', warnBeforeUnload);
  }, [claudePending, hasUnsavedEdits, savingRepoFile, submitting]);
  const privacyFlags = useMemo(() => {
    const flags = [];
    flags.push(proctoringEnabled ? 'Activity signals enabled' : 'Session transcript only');
    if (isTimerPaused) {
      flags.push('Session paused');
    } else if (isClaudeBudgetExhausted) {
      flags.push('Claude budget used up');
    } else {
      flags.push('Claude budget OK');
    }
    return flags;
  }, [isClaudeBudgetExhausted, isTimerPaused, proctoringEnabled]);
  const progressLabel = useMemo(() => {
    if (executing) return 'Running the latest check';
    if (output) return 'Latest output captured in the workspace dock';
    return `${Math.max(0, totalDurationSeconds - timeLeft)}s spent in the live workspace`;
  }, [executing, output, totalDurationSeconds, timeLeft]);

  if (loading) {
    return <AssessmentStatusScreen mode="loading" lightMode={assessmentLightMode} />;
  }

  if (startError) {
    return (
      <div className={`taali-runtime ${assessmentLightMode ? 'taali-runtime-light' : 'taali-runtime-dark'} flex h-screen items-center justify-center bg-[var(--taali-runtime-bg)] px-6`}>
        <div className="w-full max-w-lg rounded-[var(--taali-radius-panel)] border border-[var(--taali-danger-border)] bg-[var(--taali-runtime-panel)] p-7 text-center shadow-[var(--taali-shadow-strong)]" role="alert">
          <h1 className="font-display text-2xl font-semibold text-[var(--taali-runtime-text)]">Assessment couldn&rsquo;t open</h1>
          <p className="mt-3 text-sm leading-6 text-[var(--taali-runtime-muted)]">{startError}</p>
          <div className="mt-6 flex flex-wrap justify-center gap-3">
            <button type="button" className="taali-btn taali-btn-primary taali-btn-md" onClick={() => setStartAttempt((current) => current + 1)}>
              Try again
            </button>
            <a className="taali-btn taali-btn-secondary taali-btn-md" href="mailto:support@taali.ai?subject=Assessment%20support">
              Contact support
            </a>
          </div>
        </div>
      </div>
    );
  }

  if (submitted) {
    return <AssessmentStatusScreen mode="submitted" submittedAt={submittedAtIso} lightMode={assessmentLightMode} />;
  }

  return (
    <AssessmentWorkspaceSecurityProvider value={workspaceSecurity}>
    <div
      className={`taali-runtime ${workspaceSecurity.enabled ? 'taali-runtime-protected' : ''} ${assessmentLightMode ? 'taali-runtime-light' : 'taali-runtime-dark'} flex h-screen flex-col bg-[var(--taali-runtime-bg)] text-[var(--taali-runtime-text)]`}
      onCopy={protectedRootHandlers.onCopy}
      onCut={protectedRootHandlers.onCut}
      onPaste={protectedRootHandlers.onPaste}
      onDragOver={protectedRootHandlers.onDragOver}
      onDragStart={protectedRootHandlers.onDragStart}
      onDrop={protectedRootHandlers.onDrop}
      onContextMenu={protectedRootHandlers.onContextMenu}
      onKeyDownCapture={handleProtectedShortcut}
    >
      <WorkspacePrintBlocker />
      <WorkspaceSecurityWatermark />
      <AssessmentRuntimeAlerts
        showTabWarning={showTabWarning}
        proctoringEnabled={proctoringEnabled}
        isTimerPaused={isTimerPaused}
        pauseReason={pauseReason}
        pauseMessage={pauseMessage}
        onRetryClaude={null}
        retryingClaude={false}
        isClaudeBudgetExhausted={isClaudeBudgetExhausted}
        claudeBudget={claudeBudget}
        formatUsd={formatUsd}
        timeMilestoneNotice={timeMilestoneNotice}
        lightMode={assessmentLightMode}
      />

      <AssessmentTopBar
        taskName={assessment?.task_name || 'Assessment'}
        metaLine={runtimeMetaLine}
        claudeBudget={claudeBudget}
        formatUsd={formatUsd}
        formatBudgetUsd={formatBudgetUsd}
        isTimeLow={isTimeLow}
        timeUrgencyLevel={timeUrgencyLevel}
        timeLeft={timeLeft}
        formatTime={formatTime}
        isTimerPaused={isTimerPaused}
        onOpenGuide={handleOpenGuide}
        reportIssueHref={reportIssueHref}
        onSubmit={() => handleSubmit(false)}
        submitDisabled={demoMode || submitting || workspaceLocked || executing || savingRepoFile}
        submitDisabledReason={
          demoMode
            ? 'Preview — submission is disabled in the demo'
            : workspaceLocked
              ? 'Claude is updating the workspace'
              : 'Finishing the current workspace action'
        }
      />

      <WorkspaceSecurityBanner supportHref={reportIssueHref} />

      <div className="flex-1 overflow-y-auto">
        <div className={`${demoMode ? 'w-full' : 'mx-auto max-w-[90rem]'} px-4 py-4 lg:px-8 lg:py-5`}>
          <AssessmentStagePanel twoStage={assessment?.task?.two_stage || DEFAULT_ORIENTATION_STAGES} />
          <AssessmentContextWindow
            ref={contextWindowRef}
            taskName={assessment?.task_name || 'Assessment brief'}
            taskRole={runtimeMetaLine}
            taskContext={taskContext}
            repoFiles={repoFiles}
            cloneCommand={assessment?.clone_command}
            // Demo showcase opens with the brief collapsed so the workspace
            // (locked chat + seeded transcript + clickable repo) is the first
            // thing a visitor sees in the iframe; live candidates read it first.
            defaultExpanded={!demoMode}
          />

          <AssessmentWorkspace
            className="mt-4"
            staticAssistantPanelWidth={demoMode ? 620 : undefined}
            hasRepoStructure={hasRepoStructure}
            modifiedRepoPaths={modifiedRepoPaths}
            repoFileTree={repoFileTree}
            repoPanelCollapsed={repoPanelCollapsed}
            onToggleRepoPanel={() => setRepoPanelCollapsed((current) => !current)}
            collapsedRepoDirs={collapsedRepoDirs}
            toggleRepoDir={toggleRepoDir}
            selectedRepoPath={selectedRepoPath}
            onSelectRepoFile={handleSelectRepoFile}
            onCreateRepoFile={handleCreateRepoFile}
            creatingRepoFile={creatingRepoFile}
            newRepoFilePath={newRepoFilePath}
            onNewRepoFilePathChange={setNewRepoFilePath}
            onCancelRepoFileCreate={handleCancelRepoFileCreate}
            assessmentStarterCode={assessment?.starter_code || ''}
            editorContent={editorContent}
            onEditorChange={handleEditorChange}
            onExecute={handleExecute}
            onSave={handleSave}
            savingRepoFile={savingRepoFile}
            workspaceLocked={workspaceLocked || submitting}
            workspaceActionsDisabled={workspaceActionsDisabled}
            repoFileLoading={selectedRepoFileLoading}
            repoFileLoadError={selectedRepoFileLoadError}
            onRetryRepoFile={handleRetryRepoFile}
            editorLanguage={hasRepoStructure ? languageFromPath(selectedRepoPath) : (assessment?.language || 'python')}
            editorFilename={selectedRepoPath || assessment?.filename || 'main'}
            isTimerPaused={isTimerPaused}
            assistantPanelCollapsed={assistantPanelCollapsed}
            onToggleAssistantPanel={() => setAssistantPanelCollapsed((current) => !current)}
            outputPanelOpen={outputPanelOpen}
            onToggleOutput={handleToggleOutputPanel}
            output={output}
            executing={executing}
            claudePromptDisabled={isTimerPaused || submitted || executing || savingRepoFile || submitting || refreshingClaudeChanges}
            assessmentId={assessment?.id || assessmentId}
            assessmentToken={assessmentTokenForApi}
            candidateSessionKey={candidateSessionKey}
            claudeBudget={claudeBudget}
            onClaudeBudgetUpdate={setClaudeBudget}
            onBeforeClaudeSubmit={handleBeforeClaudeSubmit}
            onClaudePendingChange={setClaudePending}
            onClaudeWorkspaceChanged={handleClaudeWorkspaceChanged}
            selectedFilePath={selectedRepoPath}
            codeContext={editorContent}
            lightMode={assessmentLightMode}
            branchName={assessment?.branch_name}
            initialAiPrompts={demoMode ? demoInitialAiPrompts : (assessment?.ai_prompts || null)}
            chatLocked={demoMode}
          />

          {submitError ? (
            <div
              className="mt-4 flex flex-col gap-3 rounded-[var(--radius-lg)] border border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] px-5 py-4 shadow-[var(--shadow-sm)] md:flex-row md:items-center md:justify-between lg:px-6"
              role="alert"
              data-testid="assessment-submit-error"
            >
              <div className="min-w-0">
                <div className="font-mono text-[0.65625rem] uppercase tracking-[0.1em] text-[var(--taali-danger)]">
                  Submit didn&rsquo;t go through
                </div>
                <p className="mt-1 text-[0.8125rem] leading-5 text-[var(--taali-danger)]">
                  {submitError}
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => handleSubmit(true)}
                  disabled={isTimerPaused || submitting || workspaceLocked || executing || savingRepoFile}
                  className="taali-btn taali-btn-primary taali-btn-sm"
                >
                  {submitting ? 'Submitting...' : 'Retry submit'}
                </button>
                <button
                  type="button"
                  onClick={() => setSubmitError(null)}
                  className="taali-btn taali-btn-danger taali-btn-sm"
                >
                  Dismiss
                </button>
              </div>
            </div>
          ) : null}

          <section className="mt-4 grid gap-4 rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] px-5 py-5 shadow-[var(--shadow-sm)] lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center lg:px-6">
            <div className="flex flex-wrap items-center gap-4">
              <span className="font-mono text-[0.6875rem] uppercase tracking-[0.1em] text-[var(--mute)]">Progress</span>
              <div className="min-w-[13.75rem] flex-1 overflow-hidden rounded-full bg-[var(--bg-3)]">
                <div
                  className="h-2 rounded-full bg-[linear-gradient(90deg,var(--purple),var(--purple-2))]"
                  style={{ width: `${Math.max(4, progressPercent)}%` }}
                />
              </div>
              <span className="min-w-[2.625rem] font-mono text-[0.75rem] text-[var(--ink-2)]">{progressPercent}%</span>
              <span className="text-[0.75rem] leading-5 text-[var(--mute)]">{progressLabel}</span>
            </div>

            <div className="flex flex-wrap items-center gap-3 lg:justify-end">
              <span className="max-w-[17.5rem] text-[0.75rem] leading-5 text-[var(--mute)]">
                Your edits save automatically. Submit when you are happy with the workspace.
              </span>
              <button
                type="button"
                onClick={() => handleSave(codeRef.current)}
                disabled={isTimerPaused || savingRepoFile || workspaceLocked || selectedRepoFileLoading || Boolean(selectedRepoFileLoadError)}
                className="taali-btn taali-btn-secondary taali-btn-sm"
              >
                {savingRepoFile ? 'Saving...' : 'Save now'}
              </button>
              <button
                type="button"
                onClick={() => handleSubmit(false)}
                disabled={isTimerPaused || submitting || workspaceLocked || executing || savingRepoFile}
                className="taali-btn taali-btn-primary taali-btn-sm"
              >
                {submitting ? 'Submitting...' : 'Submit'}
              </button>
            </div>
          </section>

          <footer className="mt-4 mb-6 flex flex-col gap-3 px-1 text-[0.71875rem] text-[var(--mute)] md:flex-row md:items-center md:justify-between">
            <div>
              We record workspace actions and Claude chat for this assessment; we do not record your screen, camera, or microphone. <a href={reportIssueHref} className="text-[var(--purple)]">Need help?</a>
            </div>
            <div className="flex flex-wrap items-center gap-4">
              <span className="inline-flex items-center gap-2 font-mono" data-testid="assessment-save-state">
                <span
                  className={`h-[0.375rem] w-[0.375rem] rounded-full ${
                    saveStateTone === 'danger'
                      ? 'bg-[var(--taali-danger)]'
                      : saveStateTone === 'pending'
                        ? 'bg-[var(--amber)]'
                        : 'bg-[var(--green)]'
                  }`}
                />
                {saveStateLabel}
              </span>
              {privacyFlags.map((flag) => (
                <span key={flag} className="inline-flex items-center gap-2 font-mono">
                  <span className="h-[0.375rem] w-[0.375rem] rounded-full bg-[var(--green)]" />
                  {flag}
                </span>
              ))}
            </div>
          </footer>
        </div>
      </div>

      {submitConfirmOpen ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--taali-runtime-overlay)] p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="assessment-submit-confirm-title"
          aria-describedby="assessment-submit-confirm-description"
        >
          <div className="w-full max-w-xl rounded-[var(--taali-radius-panel)] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel)] p-7 text-[var(--taali-runtime-text)] shadow-[var(--taali-shadow-strong)] backdrop-blur-sm">
            <h2
              id="assessment-submit-confirm-title"
              className="font-display text-[1.5rem] font-semibold tracking-[-0.02em] text-[var(--taali-runtime-text)]"
            >
              Submit assessment<span className="text-[var(--taali-purple)]">?</span>
            </h2>
            <p
              id="assessment-submit-confirm-description"
              className="mt-3 text-[0.875rem] leading-[1.6] text-[var(--taali-runtime-muted)]"
            >
              Your work will be locked in and the hiring team will start their review. You won&rsquo;t be able to make further changes.
            </p>
            <div className="mt-6 flex items-center justify-end gap-3">
              <button
                ref={submitCancelButtonRef}
                type="button"
                className="taali-btn taali-btn-secondary taali-btn-md"
                onClick={() => setSubmitConfirmOpen(false)}
              >
                Cancel
              </button>
              <button
                ref={submitConfirmButtonRef}
                type="button"
                className="taali-btn taali-btn-primary taali-btn-md"
                onClick={() => handleSubmit(true)}
                disabled={submitting || workspaceLocked || executing || savingRepoFile}
              >
                {submitting ? 'Submitting…' : 'Submit'}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
    </AssessmentWorkspaceSecurityProvider>
  );
}
