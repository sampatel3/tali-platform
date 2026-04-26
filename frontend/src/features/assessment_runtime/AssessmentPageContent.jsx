import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { BRAND } from '../../config/brand';
import { assessments } from '../../shared/api';
import { AssessmentContextWindow } from './AssessmentContextWindow';
import { AssessmentRuntimeAlerts } from './AssessmentRuntimeAlerts';
import { AssessmentStatusScreen } from './AssessmentStatusScreen';
import { AssessmentTopBar } from './AssessmentTopBar';
import { AssessmentWorkspace } from './AssessmentWorkspace';
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
const CLAUDE_PROMPT_SLOW_MS = 10000;
const CLAUDE_PROMPT_STALL_MS = 45000;

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
  if (files.length > 0) {
    return {
      repoFiles: files,
      selectedRepoFile: files[0].path,
      editorContent: files[0].content ?? '',
    };
  }

  return {
    repoFiles: [],
    selectedRepoFile: null,
    editorContent: starter,
  };
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
  const [demoPromptMessages, setDemoPromptMessages] = useState([]);
  const [terminalEvents, setTerminalEvents] = useState([]);
  const [terminalConnected, setTerminalConnected] = useState(false);
  const [terminalRestarting, setTerminalRestarting] = useState(false);
  const [terminalPanelOpen, setTerminalPanelOpen] = useState(false);
  const [outputPanelOpen, setOutputPanelOpen] = useState(false);
  const [terminalSessionNonce, setTerminalSessionNonce] = useState(0);
  const [assessmentLightMode, setAssessmentLightMode] = useState(readAssessmentLightModePreference);
  const [claudePrompt, setClaudePrompt] = useState('');
  const [claudePromptPasted, setClaudePromptPasted] = useState(false);
  const [claudePromptSending, setClaudePromptSending] = useState(false);
  const [claudePromptSlow, setClaudePromptSlow] = useState(false);
  const [claudeConversation, setClaudeConversation] = useState([]);
  const [repoPanelCollapsed, setRepoPanelCollapsed] = useState(false);
  const [assistantPanelCollapsed, setAssistantPanelCollapsed] = useState(false);
  const [collapsedRepoDirs, setCollapsedRepoDirs] = useState({});
  const codeRef = useRef("");
  const contextWindowRef = useRef(null);
  const timerRef = useRef(null);
  const terminalWsRef = useRef(null);
  const terminalReconnectTimerRef = useRef(null);
  const terminalEventSeqRef = useRef(0);
  const terminalManualCloseRef = useRef(false);
  const assessmentStartedAtRef = useRef(null);
  const lastPromptSentAtRef = useRef(null);
  const milestoneFlagsRef = useRef({ halfway: false, warning80: false, warning90: false });
  const milestoneTimerRef = useRef(null);
  const pendingClaudeRequestIdRef = useRef(null);
  const ignoredClaudeRequestIdsRef = useRef(new Set());
  const claudePromptSlowTimerRef = useRef(null);
  const claudePromptStallTimerRef = useRef(null);

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

  const clearClaudePromptTimers = useCallback(() => {
    if (claudePromptSlowTimerRef.current) {
      clearTimeout(claudePromptSlowTimerRef.current);
      claudePromptSlowTimerRef.current = null;
    }
    if (claudePromptStallTimerRef.current) {
      clearTimeout(claudePromptStallTimerRef.current);
      claudePromptStallTimerRef.current = null;
    }
    setClaudePromptSlow(false);
  }, []);

  const startClaudePromptTimers = useCallback((requestId) => {
    clearClaudePromptTimers();
    claudePromptSlowTimerRef.current = setTimeout(() => {
      if (pendingClaudeRequestIdRef.current === requestId) {
        setClaudePromptSlow(true);
      }
    }, CLAUDE_PROMPT_SLOW_MS);
    claudePromptStallTimerRef.current = setTimeout(() => {
      if (pendingClaudeRequestIdRef.current !== requestId) {
        return;
      }
      ignoredClaudeRequestIdsRef.current.add(requestId);
      pendingClaudeRequestIdRef.current = null;
      setClaudePromptSending(false);
      setClaudePromptSlow(false);
      setClaudeConversation((prev) => {
        const next = [
          ...prev,
          {
            role: 'assistant',
            content: '[Error] Claude is taking longer than expected in the live repo session. Open the terminal dock to inspect progress, or click Restart terminal and try again.',
          },
        ];
        return next.slice(-30);
      });
    }, CLAUDE_PROMPT_STALL_MS);
  }, [clearClaudePromptTimers]);

  useEffect(() => {
    setSubmitted(false);
    setDemoRunCount(0);
    setDemoSaveCount(0);
    setDemoPromptMessages([]);
    setTerminalEvents([]);
    setTerminalConnected(false);
    setTerminalRestarting(false);
    setTerminalPanelOpen(false);
    setOutputPanelOpen(false);
    setTerminalSessionNonce(0);
    setClaudePrompt('');
    setClaudePromptPasted(false);
    setClaudePromptSending(false);
    setClaudePromptSlow(false);
    setClaudeConversation([]);
    setRepoPanelCollapsed(false);
    setAssistantPanelCollapsed(false);
    pendingClaudeRequestIdRef.current = null;
    ignoredClaudeRequestIdsRef.current.clear();
    clearClaudePromptTimers();
    lastPromptSentAtRef.current = null;
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
      assessmentStartedAtRef.current = Date.now() - Math.max(0, (((normalized.duration_minutes || 30) * 60) - (normalized.time_remaining || 0)) * 1000);
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
      assessmentStartedAtRef.current = Date.now();
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
        assessmentStartedAtRef.current = Date.now() - Math.max(0, (((normalized.duration_minutes || 30) * 60) - (normalized.time_remaining || 0)) * 1000);
      } catch (err) {
        setOutput(`Error starting assessment: ${err.message}`);
      } finally {
        setLoading(false);
      }
    };
    startAssessment();
  }, [token, taskData, startData, clearClaudePromptTimers]);

  useEffect(() => {
    return () => {
      if (milestoneTimerRef.current) {
        clearTimeout(milestoneTimerRef.current);
      }
      clearClaudePromptTimers();
    };
  }, [clearClaudePromptTimers]);

  useEffect(() => {
    if (loading || submitted || timeLeft <= 0 || isTimerPaused) return;

    timerRef.current = setInterval(() => {
      setTimeLeft((prev) => {
        if (prev <= 1) {
          clearInterval(timerRef.current);
          handleSubmit(true);
          return 0;
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
  const selectedRepoPath =
    selectedRepoFile && repoFiles.some((file) => file.path === selectedRepoFile)
      ? selectedRepoFile
      : repoFiles[0]?.path || null;
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
  const aiMode = assessment?.ai_mode || 'claude_cli_terminal';
  const showTerminal = aiMode === 'claude_cli_terminal';
  const runtimeMetaLine = useMemo(
    () => [
      assessment?.organization_name || BRAND.name,
      assessment?.task?.role || assessment?.task_name || 'Assessment',
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
    setTerminalPanelOpen(false);
  }, [showTerminal]);

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

  const appendTerminalEvent = useCallback((event) => {
    terminalEventSeqRef.current += 1;
    setTerminalEvents((prev) => [
      ...prev,
      { id: terminalEventSeqRef.current, ...event },
    ]);
  }, []);

  const sendTerminalPayload = useCallback((payload) => {
    const ws = terminalWsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    try {
      ws.send(JSON.stringify(payload));
      return true;
    } catch {
      return false;
    }
  }, []);

  const handleTerminalInput = useCallback((data) => {
    sendTerminalPayload({ type: 'input', data });
  }, [sendTerminalPayload]);

  const handleTerminalResize = useCallback((rows, cols) => {
    sendTerminalPayload({ type: 'resize', rows, cols });
  }, [sendTerminalPayload]);

  const handleRestartTerminal = useCallback(async () => {
    const id = assessment?.id || assessmentId;
    if (!id || terminalRestarting) return;
    setTerminalRestarting(true);
    setTerminalConnected(false);
    setTerminalEvents([]);
    if (pendingClaudeRequestIdRef.current) {
      const staleRequestId = pendingClaudeRequestIdRef.current;
      pendingClaudeRequestIdRef.current = null;
      ignoredClaudeRequestIdsRef.current.add(staleRequestId);
      clearClaudePromptTimers();
      setClaudePromptSending(false);
      setClaudeConversation((prev) => {
        const next = [...prev, { role: 'assistant', content: `[Error] Claude request ${staleRequestId} was interrupted by a terminal restart.` }];
        return next.slice(-30);
      });
    }
    try {
      await assessments.terminalStop(id, assessmentTokenForApi);
    } catch (err) {
      const detail = err?.response?.data?.detail || err?.message || 'Failed to stop terminal.';
      appendTerminalEvent({ type: 'error', message: String(detail) });
    } finally {
      setTerminalSessionNonce((prev) => prev + 1);
      setTerminalRestarting(false);
    }
  }, [assessment, assessmentId, assessmentTokenForApi, terminalRestarting, appendTerminalEvent, clearClaudePromptTimers]);

  const handleToggleTerminalPanel = useCallback(() => {
    setTerminalPanelOpen((prev) => !prev);
  }, []);

  const handleToggleOutputPanel = useCallback(() => {
    setOutputPanelOpen((prev) => !prev);
  }, []);

  useEffect(() => {
    // Demo sessions use the same terminal transport; do not skip websocket init in demo mode.
    if (!showTerminal || loading || submitted || isTimerPaused) return undefined;
    const id = assessment?.id || assessmentId;
    if (!id || !assessmentTokenForApi) return undefined;

    let disposed = false;
    let reconnectAttempts = 0;
    let heartbeatInterval = null;
    terminalManualCloseRef.current = false;

    const scheduleReconnect = () => {
      if (disposed || terminalManualCloseRef.current || submitted) return;
      reconnectAttempts += 1;
      const waitMs = Math.min(1000 * (2 ** reconnectAttempts), 8000);
      terminalReconnectTimerRef.current = setTimeout(() => {
        connect();
      }, waitMs);
    };

    const clearHeartbeat = () => {
      if (heartbeatInterval) {
        clearInterval(heartbeatInterval);
        heartbeatInterval = null;
      }
    };

    const connect = () => {
      if (disposed) return;
      const wsUrl = assessments.terminalWsUrl(id, assessmentTokenForApi);
      const ws = new WebSocket(wsUrl);
      terminalWsRef.current = ws;

      ws.onopen = () => {
        if (disposed) return;
        reconnectAttempts = 0;
        setTerminalConnected(true);
        ws.send(JSON.stringify({ type: 'init' }));
        clearHeartbeat();
        heartbeatInterval = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'heartbeat' }));
          }
        }, 15000);
      };

      ws.onmessage = (event) => {
        if (disposed) return;
        let payload = null;
        try {
          payload = JSON.parse(event.data);
        } catch {
          return;
        }
        if (!payload || typeof payload !== 'object') return;

        if (payload.type === 'ready') {
          if (payload?.claude_budget && typeof payload.claude_budget === 'object') {
            setClaudeBudget(payload.claude_budget);
          }
          return;
        }

        if (payload.type === 'status') {
          // Intentionally not rendered to candidates to keep terminal output clean.
          return;
        }

        if (payload.type === 'output') {
          appendTerminalEvent({
            type: 'output',
            data: String(payload.data || ''),
            stream: payload.stream || 'pty',
          });
          return;
        }

        if (payload.type === 'claude_chat_started') {
          return;
        }

        if (payload.type === 'claude_chat_done') {
          const requestId = String(payload.request_id || '');
          if (ignoredClaudeRequestIdsRef.current.has(requestId)) {
            ignoredClaudeRequestIdsRef.current.delete(requestId);
            if (payload?.claude_budget && typeof payload.claude_budget === 'object') {
              setClaudeBudget(payload.claude_budget);
            }
            return;
          }
          if (!pendingClaudeRequestIdRef.current || pendingClaudeRequestIdRef.current === requestId) {
            pendingClaudeRequestIdRef.current = null;
            clearClaudePromptTimers();
            setClaudePromptSending(false);
          }
          if (payload?.claude_budget && typeof payload.claude_budget === 'object') {
            setClaudeBudget(payload.claude_budget);
          }
          const reply = String(payload.content || '').trim() || 'Claude completed without returning a visible response.';
          setClaudeConversation((prev) => {
            const next = [...prev, { role: 'assistant', content: reply }];
            return next.slice(-30);
          });
          return;
        }

        if (payload.type === 'claude_chat_error') {
          const requestId = String(payload.request_id || '');
          if (ignoredClaudeRequestIdsRef.current.has(requestId)) {
            ignoredClaudeRequestIdsRef.current.delete(requestId);
            return;
          }
          if (!pendingClaudeRequestIdRef.current || pendingClaudeRequestIdRef.current === requestId) {
            pendingClaudeRequestIdRef.current = null;
            clearClaudePromptTimers();
            setClaudePromptSending(false);
          }
          const message = String(payload.message || 'Claude prompt failed.');
          setClaudeConversation((prev) => {
            const next = [...prev, { role: 'assistant', content: `[Error] ${message}` }];
            return next.slice(-30);
          });
          return;
        }

        if (payload.type === 'error') {
          const message = String(payload.message || 'Terminal error');
          if (payload?.claude_budget && typeof payload.claude_budget === 'object') {
            setClaudeBudget(payload.claude_budget);
          }
          appendTerminalEvent({ type: 'error', message });
          return;
        }

        if (payload.type === 'exit') {
          appendTerminalEvent({ type: 'exit', message: 'Terminal exited.' });
        }
      };

      ws.onerror = () => {};

      ws.onclose = () => {
        if (disposed) return;
        setTerminalConnected(false);
        clearHeartbeat();
        scheduleReconnect();
      };
    };

    connect();

    return () => {
      disposed = true;
      clearHeartbeat();
      clearTimeout(terminalReconnectTimerRef.current);
      terminalReconnectTimerRef.current = null;
      terminalManualCloseRef.current = true;
      clearClaudePromptTimers();
      if (terminalWsRef.current) {
        try {
          terminalWsRef.current.close();
        } catch {
          // noop
        }
      }
      terminalWsRef.current = null;
    };
  }, [
    showTerminal,
    demoMode,
    loading,
    submitted,
    isTimerPaused,
    assessment,
    assessmentId,
    assessmentTokenForApi,
    appendTerminalEvent,
    terminalSessionNonce,
    clearClaudePromptTimers,
  ]);

  const handleExecute = useCallback(
    async (code) => {
      if (isTimerPaused) {
        setOutput("Assessment is paused. Retry Claude to resume execution.");
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
          `Execution error: ${detail?.message || detail || err.message}`,
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
      const errorMessage = `Save error: ${detail?.message || detail || err.message}`;
      if (announceSuccess) {
        setOutput(errorMessage);
      }
      return { success: false, repoSnapshot, errorMessage };
    } finally {
      setSavingRepoFile(false);
    }
  }, [buildRepoSnapshot, selectedRepoPath, assessment, assessmentId, assessmentTokenForApi]);

  const handleSave = useCallback(async (code) => {
    if (demoMode) {
      setDemoSaveCount((prev) => prev + 1);
    }
    await syncSelectedRepoFileToWorkspace(code, { announceSuccess: true });
  }, [demoMode, syncSelectedRepoFileToWorkspace]);

  const handleClaudePromptSubmit = useCallback(async () => {
    const message = String(claudePrompt || '').trim();
    if (!message || claudePromptSending) return;
    if (isTimerPaused) {
      setOutput("Assessment is paused. Retry Claude before sending prompts.");
      return;
    }
    const id = assessment?.id || assessmentId;
    if (!id || !assessmentTokenForApi) return;

    setClaudePromptSending(true);
    setClaudePrompt('');
    setClaudePromptPasted(false);
    const nowMs = Date.now();
    const timeSinceAssessmentStartMs = assessmentStartedAtRef.current != null
      ? Math.max(0, nowMs - assessmentStartedAtRef.current)
      : null;
    const timeSinceLastPromptMs = lastPromptSentAtRef.current != null
      ? Math.max(0, nowMs - lastPromptSentAtRef.current)
      : null;
    lastPromptSentAtRef.current = nowMs;
    setClaudeConversation((prev) => {
      const next = [...prev, { role: 'user', content: message }];
      return next.slice(-30);
    });
    if (demoMode) {
      setDemoPromptMessages((prev) => [...prev, message].slice(-100));
    }
    let awaitingTerminalReply = false;
    try {
      if (showTerminal) {
        const syncResult = await syncSelectedRepoFileToWorkspace(codeRef.current, { announceSuccess: false });
        if (!syncResult?.success) {
          throw new Error(syncResult?.errorMessage || 'Claude could not sync the latest file state into the workspace.');
        }

        const requestId = `claude-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
        pendingClaudeRequestIdRef.current = requestId;
        const sent = sendTerminalPayload({
          type: 'claude_prompt',
          request_id: requestId,
          message,
          code_context: codeRef.current,
          selected_file_path: selectedRepoPath,
          paste_detected: claudePromptPasted,
          browser_focused: typeof document !== 'undefined' ? document.visibilityState === 'visible' : true,
          time_since_assessment_start_ms: timeSinceAssessmentStartMs,
          time_since_last_prompt_ms: timeSinceLastPromptMs,
        });
        if (!sent) {
          pendingClaudeRequestIdRef.current = null;
          throw new Error('Claude terminal is not connected yet. Restart the terminal and try again.');
        }
        startClaudePromptTimers(requestId);
        awaitingTerminalReply = true;
        return;
      }

      const repoSnapshot = buildRepoSnapshot(codeRef.current);
      setRepoFilesState(repoSnapshot);
      const res = await assessments.claude(
        id,
        {
          message,
          conversation_history: claudeConversation,
          code_context: codeRef.current,
          selected_file_path: selectedRepoPath,
          repo_files: repoSnapshot,
          paste_detected: claudePromptPasted,
          browser_focused: typeof document !== 'undefined' ? document.visibilityState === 'visible' : true,
          time_since_assessment_start_ms: timeSinceAssessmentStartMs,
          time_since_last_prompt_ms: timeSinceLastPromptMs,
        },
        assessmentTokenForApi,
      );
      const payload = res?.data || {};
      const reply = String(payload.content || payload.response || '').trim() || 'No response from Claude.';
      if (payload?.claude_budget && typeof payload.claude_budget === 'object') {
        setClaudeBudget(payload.claude_budget);
      }

      setClaudeConversation((prev) => {
        const next = [...prev, { role: 'assistant', content: reply }];
        return next.slice(-30);
      });
    } catch (err) {
      pendingClaudeRequestIdRef.current = null;
      clearClaudePromptTimers();
      const detail = err?.response?.data?.detail;
      const errorMessage = typeof detail === 'string'
        ? detail
        : detail?.message || err?.message || 'Claude prompt failed.';
      setClaudeConversation((prev) => {
        const next = [...prev, { role: 'assistant', content: `[Error] ${errorMessage}` }];
        return next.slice(-30);
      });
    } finally {
      if (!awaitingTerminalReply) {
        setClaudePromptSending(false);
      }
    }
  }, [
    claudePrompt,
    claudePromptSending,
    isTimerPaused,
    assessment,
    assessmentId,
    assessmentTokenForApi,
    claudeConversation,
    buildRepoSnapshot,
    selectedRepoPath,
    showTerminal,
    claudePromptPasted,
    sendTerminalPayload,
    syncSelectedRepoFileToWorkspace,
    startClaudePromptTimers,
    clearClaudePromptTimers,
  ]);

  const handleSubmit = useCallback(
    async (autoSubmit = false) => {
      if (submitted) return;
      if (isTimerPaused) {
        setOutput("Assessment is paused. Retry Claude before submitting.");
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
        if (showTerminal) {
          try {
            await assessments.terminalStop(id, assessmentTokenForApi);
          } catch {
            // Best effort: submission can continue even if terminal stop fails.
          }
        }
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
        setOutput(`Submit error: ${detail?.message || detail || err.message}`);
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
      showTerminal,
      buildRepoSnapshot,
      selectedRepoPath,
    ],
  );

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
      flags.push('Claude credit exhausted');
    } else {
      flags.push('Claude credit OK');
    }
    return flags;
  }, [isClaudeBudgetExhausted, isTimerPaused, proctoringEnabled]);
  const progressLabel = useMemo(() => {
    if (executing) return 'Running the latest check';
    if (output) return 'Latest output captured in the workspace dock';
    if (showTerminal && terminalConnected) return 'Terminal session connected';
    return `${Math.max(0, totalDurationSeconds - timeLeft)}s spent in the live workspace`;
  }, [executing, output, showTerminal, terminalConnected, totalDurationSeconds, timeLeft]);

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
      />

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-[1440px] px-4 py-4 lg:px-8 lg:py-5">
          <AssessmentContextWindow
            ref={contextWindowRef}
            taskName={assessment?.task_name || 'Assessment brief'}
            taskRole={runtimeMetaLine}
            taskContext={taskContext}
            repoFiles={repoFiles}
            cloneCommand={assessment?.clone_command}
          />

          <AssessmentWorkspace
            className="mt-4"
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
            showTerminal={showTerminal}
            assistantPanelCollapsed={assistantPanelCollapsed}
            onToggleAssistantPanel={() => setAssistantPanelCollapsed((current) => !current)}
            terminalPanelOpen={terminalPanelOpen}
            onToggleTerminal={handleToggleTerminalPanel}
            outputPanelOpen={outputPanelOpen}
            onToggleOutput={handleToggleOutputPanel}
            terminalConnected={terminalConnected}
            terminalEvents={terminalEvents}
            onTerminalInput={handleTerminalInput}
            onTerminalResize={handleTerminalResize}
            onRestartTerminal={handleRestartTerminal}
            terminalRestarting={terminalRestarting}
            output={output}
            executing={executing}
            claudeConversation={claudeConversation}
            claudePrompt={claudePrompt}
            onClaudePromptChange={(value) => {
              setClaudePrompt(value);
              if (!String(value || '').trim()) {
                setClaudePromptPasted(false);
              }
            }}
            onClaudePromptPaste={() => setClaudePromptPasted(true)}
            onClaudePromptSubmit={handleClaudePromptSubmit}
            claudePromptSending={claudePromptSending}
            claudePromptSlow={claudePromptSlow}
            claudePromptDisabled={isTimerPaused || submitted}
            lightMode={assessmentLightMode}
            branchName={assessment?.branch_name}
          />

          <section className="mt-4 grid gap-4 rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] px-5 py-5 shadow-[var(--shadow-sm)] lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center lg:px-6">
            <div className="flex flex-wrap items-center gap-4">
              <span className="font-mono text-[11px] uppercase tracking-[0.1em] text-[var(--mute)]">Progress</span>
              <div className="min-w-[220px] flex-1 overflow-hidden rounded-full bg-[var(--bg-3)]">
                <div
                  className="h-2 rounded-full bg-[linear-gradient(90deg,var(--purple),var(--purple-2))]"
                  style={{ width: `${Math.max(4, progressPercent)}%` }}
                />
              </div>
              <span className="min-w-[42px] font-mono text-[12px] text-[var(--ink-2)]">{progressPercent}%</span>
              <span className="text-[12px] leading-5 text-[var(--mute)]">{progressLabel}</span>
            </div>

            <div className="flex flex-wrap items-center gap-3 lg:justify-end">
              <span className="max-w-[280px] text-[12px] leading-5 text-[var(--mute)]">
                You can submit any time. Save keeps your current file synced into the live workspace before you finalize.
              </span>
              <button
                type="button"
                onClick={() => handleSave(codeRef.current)}
                disabled={isTimerPaused || savingRepoFile}
                className="rounded-full border border-[var(--line)] bg-[var(--bg-2)] px-4 py-2 text-[12px] font-medium text-[var(--ink-2)] transition-colors hover:border-[var(--ink)] hover:text-[var(--ink)] disabled:opacity-50"
              >
                {savingRepoFile ? 'Saving...' : 'Save draft'}
              </button>
              <button
                type="button"
                onClick={() => handleSubmit(false)}
                disabled={isTimerPaused}
                className="rounded-full bg-[var(--purple)] px-4 py-2 text-[12px] font-medium text-white transition-colors hover:bg-[var(--purple-2)] disabled:opacity-50"
              >
                Submit
              </button>
            </div>
          </section>

          <footer className="mt-4 mb-6 flex flex-col gap-3 px-1 text-[11.5px] text-[var(--mute)] md:flex-row md:items-center md:justify-between">
            <div>
              We record your editor, terminal, and Claude chat for this session only. <a href={reportIssueHref} className="text-[var(--purple)]">Need help?</a>
            </div>
            <div className="flex flex-wrap items-center gap-4">
              {privacyFlags.map((flag) => (
                <span key={flag} className="inline-flex items-center gap-2 font-mono">
                  <span className="h-[6px] w-[6px] rounded-full bg-[var(--green)]" />
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
          <div className="w-full max-w-xl rounded-[var(--taali-radius-panel)] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel)] p-6 text-[var(--taali-runtime-text)] shadow-[var(--taali-shadow-strong)] backdrop-blur-sm">
            <h2 id="assessment-submit-confirm-title" className="font-mono text-sm font-bold uppercase tracking-wide text-[var(--taali-purple)]">
              Confirm Submission
            </h2>
            <p className="mt-3 font-mono text-sm text-[var(--taali-runtime-muted)]">
              Are you sure you want to submit? You cannot make changes after submitting.
            </p>
            <div className="mt-5 flex items-center justify-end gap-3">
              <button
                type="button"
                className="rounded-[var(--taali-radius-control)] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel-alt)] px-4 py-2 font-mono text-xs font-bold uppercase tracking-wide text-[var(--taali-runtime-text)] transition-colors hover:border-[var(--taali-purple)] hover:text-[var(--taali-purple)]"
                onClick={() => setSubmitConfirmOpen(false)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="rounded-[var(--taali-radius-control)] border border-[var(--taali-purple)] bg-[var(--taali-purple)] px-4 py-2 font-mono text-xs font-bold uppercase tracking-wide text-white transition-colors hover:bg-[var(--taali-purple-hover)]"
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
