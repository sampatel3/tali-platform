import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { BRAND } from '../../config/brand';
import { assessments } from '../../shared/api';
import { AssessmentContextWindow } from './AssessmentContextWindow';
import { AssessmentRuntimeAlerts } from './AssessmentRuntimeAlerts';
import { AssessmentStatusScreen } from './AssessmentStatusScreen';
import { AssessmentTopBar } from './AssessmentTopBar';
import { AssessmentWorkspace } from './AssessmentWorkspace';
import { AssessmentStagePanel } from './AssessmentStagePanel';
import {
  buildRepoFileTree,
  extractRepoFiles,
  formatBudgetUsd,
  formatTime,
  formatUsd,
  languageFromPath,
  mergeEditorContentIntoRepoFiles,
  normalizeStartData,
  normalizeRepoPathInput,
  upsertRepoFile,
} from './assessmentRuntimeHelpers';

const ASSESSMENT_THEME_STORAGE_KEY = 'taali_assessment_theme';

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

const initializeRepoEditorState = (runtimeData) => {
  const files = extractRepoFiles(runtimeData?.repo_structure);
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
      editorContent: explicitFile.content ?? '',
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
      editorContent: primaryFile.content ?? '',
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
  const [loading, setLoading] = useState(true);
  const [output, setOutput] = useState("");
  const [executing, setExecuting] = useState(false);
  const [timeLeft, setTimeLeft] = useState(0);
  const [submitted, setSubmitted] = useState(false);
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
  const [savingRepoFile, setSavingRepoFile] = useState(false);
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
    setSubmitted(false);
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
    setEditorContent('');
    setCollapsedRepoDirs({});
    setSavingRepoFile(false);
    setCreatingRepoFile(false);
    setNewRepoFilePath('');

    if (startData) {
      const normalized = normalizeStartData(startData);
      setAssessment(normalized);
      const repoState = initializeRepoEditorState(normalized);
      setRepoFilesState(repoState.repoFiles);
      setSelectedRepoFile(repoState.selectedRepoFile);
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
      const repoState = initializeRepoEditorState(taskData);
      setRepoFilesState(repoState.repoFiles);
      setSelectedRepoFile(repoState.selectedRepoFile);
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
      setOutput("Error: No assessment token provided.");
      return;
    }
    const startAssessment = async () => {
      try {
        const res = await assessments.start(token);
        const data = res.data;
        const normalized = normalizeStartData(data);
        setAssessment(normalized);
        const repoState = initializeRepoEditorState(normalized);
        setRepoFilesState(repoState.repoFiles);
        setSelectedRepoFile(repoState.selectedRepoFile);
        setEditorContent(repoState.editorContent);
        codeRef.current = repoState.editorContent;
        setTimeLeft(normalized.time_remaining);
        setProctoringEnabled(data.task?.proctoring_enabled || false);
        setIsTimerPaused(Boolean(normalized.is_timer_paused));
        setPauseReason(normalized.pause_reason || null);
        setClaudeBudget(normalized.claude_budget || null);
      } catch {
        setOutput("Couldn't load the assessment. Refresh the page to try again.");
      } finally {
        setLoading(false);
      }
    };
    startAssessment();
  }, [token, taskData, startData]);

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
          // Read from the ref so we always invoke the latest handleSubmit
          // (its deps include things that change during the assessment, like
          // tabSwitchCount and the repo snapshot helpers).
          handleSubmitRef.current?.(true);
          return 0;
        }
        // 30s before zero, push the full in-browser snapshot to the sandbox
        // so even if the server-side timeout finalizer fires first, the
        // captured git diff reflects the candidate's latest unsaved edits.
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
  const initialRepoFiles = useMemo(
    () => extractRepoFiles(assessment?.repo_structure),
    [assessment?.repo_structure],
  );
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
  const repoFileTree = buildRepoFileTree(repoFiles);
  const modifiedRepoPaths = useMemo(() => {
    const initialFileMap = new Map(
      initialRepoFiles.map((fileEntry) => [fileEntry.path, String(fileEntry.content || '')]),
    );
    return repoFiles
      .filter((fileEntry) => initialFileMap.get(fileEntry.path) !== String(fileEntry.content || ''))
      .map((fileEntry) => fileEntry.path);
  }, [initialRepoFiles, repoFiles]);
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
    contextWindowRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, []);

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
      const nextContent =
        nextRepoFiles.find((fileEntry) => fileEntry.path === normalizedPath)?.content ?? "";
      setRepoFilesState(nextRepoFiles);
      setSelectedRepoFile(normalizedPath || null);
      setEditorContent(nextContent);
      codeRef.current = nextContent;
    },
    [selectedRepoPath, editorContent, repoFilesState],
  );

  const handleEditorChange = useCallback((value) => {
    setEditorContent(value ?? "");
    codeRef.current = value ?? "";
  }, []);

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
      return;
    }

    const nextRepoFiles = buildRepoSnapshot(editorContent);
    if (nextRepoFiles.some((fileEntry) => fileEntry.path === normalizedPath)) {
      setOutput(`File already exists: ${normalizedPath}`);
      return;
    }

    const createdRepoFiles = upsertRepoFile(nextRepoFiles, normalizedPath, '');
    setRepoFilesState(createdRepoFiles);
    setSelectedRepoFile(normalizedPath);
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
      if (isTimerPaused) {
        setOutput("Assessment is paused and your timer is stopped. Running code will be available again when the session resumes.");
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
            repo_files: repoSnapshot,
          },
          assessmentTokenForApi,
        );
        const result = res.data;
        setOutput(buildExecutionOutput(result));
      } catch (err) {
        const detail = err.response?.data?.detail;
        if (detail?.code === "ASSESSMENT_PAUSED") {
          setIsTimerPaused(true);
          setPauseReason(detail.pause_reason || "claude_outage");
          setPauseMessage(detail.message || "Assessment is paused.");
        }
        setOutput(
          detail?.message
            || (typeof detail === 'string' ? detail : 'Something went wrong running your code. Try again — your work is saved.'),
        );
      } finally {
        setExecuting(false);
      }
    },
    [assessment, assessmentId, assessmentTokenForApi, isTimerPaused, demoMode, demoProfile?.output, buildRepoSnapshot, selectedRepoPath],
  );

  const syncSelectedRepoFileToWorkspace = useCallback(async (code, { announceSuccess = false } = {}) => {
    codeRef.current = code;
    const repoSnapshot = buildRepoSnapshot(code);
    setRepoFilesState(repoSnapshot);

    if (!selectedRepoPath) {
      if (announceSuccess) {
        setOutput("Code saved.");
      }
      return { success: true, repoSnapshot };
    }

    const id = assessment?.id || assessmentId;
    if (!id || !assessmentTokenForApi) {
      if (announceSuccess) {
        setOutput(`Saved ${selectedRepoPath} locally.`);
      }
      return { success: true, repoSnapshot };
    }

    setSavingRepoFile(true);
    try {
      await assessments.saveRepoFile(
        id,
        {
          path: selectedRepoPath,
          content: code,
        },
        assessmentTokenForApi,
      );
      if (announceSuccess) {
        setOutput(`Saved ${selectedRepoPath} to the live workspace.`);
      }
      return { success: true, repoSnapshot };
    } catch (err) {
      const detail = err?.response?.data?.detail;
      const errorMessage = detail?.message
        || (typeof detail === 'string' ? detail : "Couldn't save your changes. Try again.");
      if (announceSuccess) {
        setOutput(errorMessage);
      }
      return { success: false, repoSnapshot, errorMessage };
    } finally {
      setSavingRepoFile(false);
    }
  }, [buildRepoSnapshot, selectedRepoPath, assessment, assessmentId, assessmentTokenForApi]);

  // Sync the entire in-browser repo snapshot (every modified file) to the
  // sandbox. Used by the pre-timeout snapshot push so the captured git diff
  // reflects the candidate's latest edits to every file, not just the open one.
  const syncAllRepoFilesToWorkspace = useCallback(async (code) => {
    codeRef.current = code;
    const repoSnapshot = buildRepoSnapshot(code);
    setRepoFilesState(repoSnapshot);

    const id = assessment?.id || assessmentId;
    if (!id || !assessmentTokenForApi || repoSnapshot.length === 0) {
      return { success: true, repoSnapshot };
    }

    setSavingRepoFile(true);
    try {
      await assessments.saveRepoFile(
        id,
        { files: repoSnapshot },
        assessmentTokenForApi,
      );
      return { success: true, repoSnapshot };
    } catch (err) {
      const detail = err?.response?.data?.detail;
      const errorMessage = detail?.message
        || (typeof detail === 'string' ? detail : "Couldn't save your changes. Try again.");
      return { success: false, repoSnapshot, errorMessage };
    } finally {
      setSavingRepoFile(false);
    }
  }, [buildRepoSnapshot, assessment, assessmentId, assessmentTokenForApi]);

  const handleSave = useCallback(async (code) => {
    if (demoMode) {
      setDemoSaveCount((prev) => prev + 1);
    }
    await syncSelectedRepoFileToWorkspace(code, { announceSuccess: true });
  }, [demoMode, syncSelectedRepoFileToWorkspace]);

  const handleSubmit = useCallback(
    async (autoSubmit = false) => {
      if (submitted) return;
      // The demo / showcase preview is read-only — a viewer (or the pitch
      // deck) must never be able to submit the walkthrough assessment, which
      // would flip the surface to the "Task submitted" screen. This covers the
      // manual click, the confirm dialog, and the timer auto-submit, which all
      // route through handleSubmit.
      if (demoMode) return;
      if (isTimerPaused) {
        setOutput("Assessment is paused and your timer is stopped. You can submit once the session resumes.");
        return;
      }

      if (!autoSubmit) {
        setSubmitConfirmOpen(true);
        return;
      }

      setSubmitConfirmOpen(false);
      setSubmitted(true);
      setSubmittedAtIso(new Date().toISOString());
      clearInterval(timerRef.current);

      try {
        const id = assessment?.id || assessmentId;
        const repoSnapshot = buildRepoSnapshot(codeRef.current);
        setRepoFilesState(repoSnapshot);
        if (demoMode) {
          setOutput("Task submitted successfully. You may close this tab.");
          return;
        }
        await assessments.submit(
          id,
          {
            final_code: codeRef.current,
            selected_file_path: selectedRepoPath,
            repo_files: repoSnapshot,
          },
          assessmentTokenForApi,
          {
            tab_switch_count: proctoringEnabled ? tabSwitchCount : 0,
          },
        );
        setOutput(
          "Assessment submitted successfully! You may close this window.",
        );
      } catch (err) {
        const detail = err.response?.data?.detail;
        if (detail?.code === "ASSESSMENT_PAUSED") {
          setIsTimerPaused(true);
          setPauseReason(detail.pause_reason || "claude_outage");
          setPauseMessage(detail.message || "Assessment is paused.");
        }
        setOutput(
          detail?.message
            || (typeof detail === 'string' ? detail : "Couldn't submit. Check your connection and try again — your work is saved."),
        );
        setSubmitted(false);
        setSubmittedAtIso(null);
      }
    },
    [
      assessment,
      assessmentId,
      assessmentTokenForApi,
      submitted,
      tabSwitchCount,
      isTimerPaused,
      demoMode,
      proctoringEnabled,
      buildRepoSnapshot,
      selectedRepoPath,
    ],
  );

  // Mirror the latest handleSubmit into the ref consumed by the timer
  // interval so the timer never invokes a stale closure.
  useEffect(() => {
    handleSubmitRef.current = handleSubmit;
  }, [handleSubmit]);

  // Same idea for the pre-timeout snapshot pusher — fire-and-forget so the
  // timer never blocks on the network.
  useEffect(() => {
    preTimeoutSnapshotRef.current = () => {
      // Best-effort; surface errors to the candidate as a one-off output line
      // but never throw out of the timer.
      syncAllRepoFilesToWorkspace(codeRef.current).catch(() => undefined);
    };
  }, [syncAllRepoFilesToWorkspace]);

  // If the assessment changes (or the candidate starts a new one), allow the
  // pre-timeout snapshot to fire again on the new run.
  useEffect(() => {
    preTimeoutSnapshotFlushedRef.current = false;
  }, [assessment?.id, assessmentId]);

  const totalDurationSeconds = Math.max(1, Number((assessment?.duration_minutes || 30) * 60));
  const remainingRatio = Math.max(0, Math.min(1, timeLeft / totalDurationSeconds));
  const progressPercent = Math.max(0, Math.min(100, Math.round((1 - remainingRatio) * 100)));
  const isTimeLow = timeLeft > 0 && timeLeft < 300; // under 5 minutes
  const timeUrgencyLevel = remainingRatio <= 0.1 ? 'danger' : (remainingRatio <= 0.2 ? 'warning' : 'normal');
  const isClaudeBudgetExhausted = Boolean(claudeBudget?.enabled && claudeBudget?.is_exhausted);
  const privacyFlags = useMemo(() => {
    const flags = ['Autosave active'];
    flags.push(proctoringEnabled ? 'Activity signals enabled' : 'Session transcript only');
    if (isTimerPaused) {
      flags.push('Session paused');
    } else if (isClaudeBudgetExhausted) {
      flags.push('AI budget used up');
    } else {
      flags.push('AI budget OK');
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

  if (submitted) {
    return <AssessmentStatusScreen mode="submitted" submittedAt={submittedAtIso} lightMode={assessmentLightMode} />;
  }

  return (
    <div className={`taali-runtime ${assessmentLightMode ? 'taali-runtime-light' : 'taali-runtime-dark'} flex h-screen flex-col bg-[var(--taali-runtime-bg)] text-[var(--taali-runtime-text)]`}>
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
        submitDisabled={demoMode}
      />

      <div className="flex-1 overflow-y-auto">
        <div className={`${demoMode ? 'w-full' : 'mx-auto max-w-[90rem]'} px-4 py-4 lg:px-8 lg:py-5`}>
          <AssessmentStagePanel twoStage={assessment?.task?.two_stage} />
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
            editorLanguage={hasRepoStructure ? languageFromPath(selectedRepoPath) : (assessment?.language || 'python')}
            editorFilename={selectedRepoPath || assessment?.filename || 'main'}
            isTimerPaused={isTimerPaused}
            assistantPanelCollapsed={assistantPanelCollapsed}
            onToggleAssistantPanel={() => setAssistantPanelCollapsed((current) => !current)}
            outputPanelOpen={outputPanelOpen}
            onToggleOutput={handleToggleOutputPanel}
            output={output}
            executing={executing}
            claudePromptDisabled={isTimerPaused || submitted}
            assessmentId={assessment?.id || assessmentId}
            assessmentToken={assessmentTokenForApi}
            claudeBudget={claudeBudget}
            onClaudeBudgetUpdate={setClaudeBudget}
            selectedFilePath={selectedRepoPath}
            codeContext={editorContent}
            lightMode={assessmentLightMode}
            branchName={assessment?.branch_name}
            initialAiPrompts={demoMode ? demoInitialAiPrompts : (assessment?.ai_prompts || null)}
            chatLocked={demoMode}
          />

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
                You can submit any time. Save keeps your current file synced into the live workspace before you finalize.
              </span>
              <button
                type="button"
                onClick={() => handleSave(codeRef.current)}
                disabled={isTimerPaused || savingRepoFile}
                className="rounded-full border border-[var(--line)] bg-[var(--bg-2)] px-4 py-2 text-[0.75rem] font-medium text-[var(--ink-2)] transition-colors hover:border-[var(--ink)] hover:text-[var(--ink)] disabled:opacity-50"
              >
                {savingRepoFile ? 'Saving...' : 'Save draft'}
              </button>
              <button
                type="button"
                onClick={() => handleSubmit(false)}
                disabled={isTimerPaused}
                className="rounded-full bg-[var(--purple)] px-4 py-2 text-[0.75rem] font-medium text-white transition-colors hover:bg-[var(--purple-2)] disabled:opacity-50"
              >
                Submit
              </button>
            </div>
          </section>

          <footer className="mt-4 mb-6 flex flex-col gap-3 px-1 text-[0.71875rem] text-[var(--mute)] md:flex-row md:items-center md:justify-between">
            <div>
              We record your editor and AI chat for this session only. <a href={reportIssueHref} className="text-[var(--purple)]">Need help?</a>
            </div>
            <div className="flex flex-wrap items-center gap-4">
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
        >
          <div className="w-full max-w-xl rounded-[var(--taali-radius-panel)] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel)] p-7 text-[var(--taali-runtime-text)] shadow-[var(--taali-shadow-strong)] backdrop-blur-sm">
            <h2
              id="assessment-submit-confirm-title"
              className="font-display text-[1.5rem] font-semibold tracking-[-0.02em] text-[var(--taali-runtime-text)]"
            >
              Submit assessment<span className="text-[var(--taali-purple)]">?</span>
            </h2>
            <p className="mt-3 text-[0.875rem] leading-[1.6] text-[var(--taali-runtime-muted)]">
              Your work will be locked in and the hiring team will start their review. You won&rsquo;t be able to make further changes.
            </p>
            <div className="mt-6 flex items-center justify-end gap-3">
              <button
                type="button"
                className="inline-flex items-center justify-center rounded-full border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel-alt)] px-5 py-2.5 text-sm font-medium text-[var(--taali-runtime-text)] transition-colors hover:border-[var(--taali-purple)] hover:text-[var(--taali-purple)]"
                onClick={() => setSubmitConfirmOpen(false)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="inline-flex items-center justify-center rounded-full bg-[var(--taali-purple)] px-6 py-2.5 text-sm font-medium text-white transition-colors hover:bg-[var(--taali-purple-hover)]"
                onClick={() => handleSubmit(true)}
              >
                Submit
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
