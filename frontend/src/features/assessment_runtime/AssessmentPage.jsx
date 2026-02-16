import { useState, useEffect, useRef, useCallback } from 'react';
import { BRAND } from '../../config/brand';
import { assessments } from '../../shared/api';
import { AssessmentContextWindow } from './AssessmentContextWindow';
import { AssessmentRuntimeAlerts } from './AssessmentRuntimeAlerts';
import { AssessmentStatusScreen } from './AssessmentStatusScreen';
import { AssessmentTopBar } from './AssessmentTopBar';
import { AssessmentWorkspace } from './AssessmentWorkspace';
import { DemoAssessmentSummary } from './DemoAssessmentSummary';
import { buildDemoSummary } from '../demo/demoSummary';
import {
  buildRepoFileTree,
  extractRepoFiles,
  formatTime,
  formatUsd,
  languageFromPath,
  normalizeStartData,
} from './assessmentRuntimeHelpers';

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
  const [pasteDetected, setPasteDetected] = useState(false);
  const [browserFocused, setBrowserFocused] = useState(true);
  const [tabSwitchCount, setTabSwitchCount] = useState(0);
  const [lastPromptTime, setLastPromptTime] = useState(null);
  const [proctoringEnabled, setProctoringEnabled] = useState(false);
  const [showTabWarning, setShowTabWarning] = useState(false);
  const [isTimerPaused, setIsTimerPaused] = useState(false);
  const [pauseReason, setPauseReason] = useState(null);
  const [pauseMessage, setPauseMessage] = useState("");
  const [retryingClaude, setRetryingClaude] = useState(false);
  const [claudeBudget, setClaudeBudget] = useState(null);
  const [selectedRepoFile, setSelectedRepoFile] = useState(null);
  const [repoFileEdits, setRepoFileEdits] = useState({});
  const [editorContent, setEditorContent] = useState("");
  const [demoRunCount, setDemoRunCount] = useState(0);
  const [demoSaveCount, setDemoSaveCount] = useState(0);
  const [demoPromptMessages, setDemoPromptMessages] = useState([]);
  const [demoSummary, setDemoSummary] = useState(null);
  const [terminalEvents, setTerminalEvents] = useState([]);
  const [terminalConnected, setTerminalConnected] = useState(false);
  const [terminalStopping, setTerminalStopping] = useState(false);
  const [terminalFallbackChat, setTerminalFallbackChat] = useState(false);
  const [collapsedSections, setCollapsedSections] = useState({
    contextWindow: false,
    taskContext: false,
    rubric: false,
    repoTree: false,
  });
  const [collapsedRepoDirs, setCollapsedRepoDirs] = useState({});
  const codeRef = useRef("");
  const timerRef = useRef(null);
  const terminalWsRef = useRef(null);
  const terminalReconnectTimerRef = useRef(null);
  const terminalEventSeqRef = useRef(0);
  const terminalManualCloseRef = useRef(false);

  useEffect(() => {
    setSubmitted(false);
    setDemoRunCount(0);
    setDemoSaveCount(0);
    setDemoPromptMessages([]);
    setDemoSummary(null);
    setTerminalEvents([]);
    setTerminalConnected(false);
    setTerminalStopping(false);
    setTerminalFallbackChat(false);

    if (startData) {
      const normalized = normalizeStartData(startData);
      setAssessment(normalized);
      const files = extractRepoFiles(normalized.repo_structure);
      const starter = normalized.starter_code || "";
      if (files.length > 0) {
        const firstPath = files[0].path;
        setSelectedRepoFile(firstPath);
        setEditorContent(files[0].content ?? "");
        codeRef.current = files[0].content ?? "";
      } else {
        setEditorContent(starter);
        codeRef.current = starter;
      }
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
      const files = extractRepoFiles(taskData.repo_structure);
      const starter = taskData.starter_code || "";
      if (files.length > 0) {
        const firstPath = files[0].path;
        setSelectedRepoFile(firstPath);
        setEditorContent(files[0].content ?? "");
        codeRef.current = files[0].content ?? "";
      } else {
        setEditorContent(starter);
        codeRef.current = starter;
      }
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
        const files = extractRepoFiles(normalized.repo_structure);
        const starter = normalized.starter_code || "";
        if (files.length > 0) {
          const firstPath = files[0].path;
          setSelectedRepoFile(firstPath);
          setEditorContent(files[0].content ?? "");
          codeRef.current = files[0].content ?? "";
        } else {
          setEditorContent(starter);
          codeRef.current = starter;
        }
        setTimeLeft(normalized.time_remaining);
        setProctoringEnabled(data.task?.proctoring_enabled || false);
        setIsTimerPaused(Boolean(normalized.is_timer_paused));
        setPauseReason(normalized.pause_reason || null);
        setClaudeBudget(normalized.claude_budget || null);
      } catch (err) {
        setOutput(`Error starting assessment: ${err.message}`);
      } finally {
        setLoading(false);
      }
    };
    startAssessment();
  }, [token, taskData, startData]);

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
    if (!proctoringEnabled) return undefined;

    const handleFocus = () => setBrowserFocused(true);
    const handleBlur = () => setBrowserFocused(false);
    const handleVisibilityChange = () => {
      if (
        typeof document !== "undefined" &&
        document.visibilityState === "hidden"
      ) {
        setTabSwitchCount((prev) => prev + 1);
        setBrowserFocused(false);
        if (proctoringEnabled) {
          setShowTabWarning(true);
          setTimeout(() => setShowTabWarning(false), 3000);
        }
      } else {
        setBrowserFocused(true);
      }
    };

    window.addEventListener("focus", handleFocus);
    window.addEventListener("blur", handleBlur);
    if (typeof document !== "undefined" && "visibilityState" in document) {
      document.addEventListener("visibilitychange", handleVisibilityChange);
    }

    return () => {
      window.removeEventListener("focus", handleFocus);
      window.removeEventListener("blur", handleBlur);
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
  const repoFiles = extractRepoFiles(assessment?.repo_structure);
  const rubricCategories = assessment?.rubric_categories || assessment?.task?.rubric_categories || [];
  const selectedRepoPath =
    selectedRepoFile && repoFiles.some((file) => file.path === selectedRepoFile)
      ? selectedRepoFile
      : repoFiles[0]?.path || null;
  const repoFileTree = buildRepoFileTree(repoFiles);
  const hasRepoStructure = repoFiles.length > 0;
  const aiMode = assessment?.ai_mode || (assessment?.terminal_mode ? 'claude_cli_terminal' : 'legacy_chat');
  const terminalCapabilities = assessment?.terminal_capabilities || {};
  const showTerminal = aiMode === 'claude_cli_terminal' && !terminalFallbackChat;

  const toggleSection = useCallback((sectionKey) => {
    setCollapsedSections((prev) => ({
      ...prev,
      [sectionKey]: !prev[sectionKey],
    }));
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
      setRepoFileEdits((prev) => ({
        ...prev,
        ...(selectedRepoPath ? { [selectedRepoPath]: editorContent } : {}),
      }));
      setSelectedRepoFile(path);
      const nextContent =
        repoFileEdits[path] !== undefined
          ? repoFileEdits[path]
          : repoFiles.find((f) => f.path === path)?.content ?? "";
      setEditorContent(nextContent);
      codeRef.current = nextContent;
    },
    [selectedRepoPath, editorContent, repoFileEdits, repoFiles],
  );

  const handleEditorChange = useCallback((value) => {
    setEditorContent(value ?? "");
    codeRef.current = value ?? "";
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

  const handleTerminalStop = useCallback(async () => {
    const id = assessment?.id || assessmentId;
    if (!id || terminalStopping) return;
    setTerminalStopping(true);
    try {
      await assessments.terminalStop(id, assessmentTokenForApi);
      sendTerminalPayload({ type: 'stop' });
    } catch (err) {
      const detail = err?.response?.data?.detail || err?.message || 'Failed to stop terminal.';
      appendTerminalEvent({ type: 'error', message: String(detail) });
    } finally {
      setTerminalStopping(false);
    }
  }, [assessment, assessmentId, assessmentTokenForApi, terminalStopping, appendTerminalEvent, sendTerminalPayload]);

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

        if (payload.type === 'error') {
          const message = String(payload.message || 'Terminal error');
          appendTerminalEvent({ type: 'error', message });
          if (payload.fallback_chat) {
            setTerminalFallbackChat(true);
          }
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
  ]);

  const handleExecute = useCallback(
    async (code) => {
      if (isTimerPaused) {
        setOutput("Assessment is paused. Retry Claude to resume execution.");
        return;
      }
      codeRef.current = code;
      setExecuting(true);
      if (demoMode) {
        setDemoRunCount((prev) => prev + 1);
      }
      setOutput("Running...\n");
      try {
        const id = assessment?.id || assessmentId;
        const res = await assessments.execute(id, code, assessmentTokenForApi);
        const result = res.data;
        setOutput(result.stdout || result.output || "No output.");
        if (result.stderr) {
          setOutput((prev) => prev + "\n--- stderr ---\n" + result.stderr);
        }
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
    [assessment, assessmentId, assessmentTokenForApi, isTimerPaused, demoMode],
  );

  const handleSave = useCallback((code) => {
    codeRef.current = code;
    if (demoMode) {
      setDemoSaveCount((prev) => prev + 1);
    }
    setOutput("Code saved.");
  }, [demoMode]);

  const handleClaudeMessage = useCallback(
    async (message, history) => {
      if (isTimerPaused) {
        return "Assessment is paused while Claude is unavailable. Use Retry to resume.";
      }

      if (demoMode) {
        setDemoPromptMessages((prev) => [...prev, message]);
      }

      const id = assessment?.id || assessmentId;

      const now = Date.now();
      const timeSinceLastMs = lastPromptTime ? now - lastPromptTime : null;
      setLastPromptTime(now);

      const wasPasted = pasteDetected;
      setPasteDetected(false);

      const res = await assessments.claude(
        id,
        message,
        history,
        assessmentTokenForApi,
        {
          code_context: codeRef.current,
          selected_file_path: selectedRepoPath,
          paste_detected: wasPasted,
          browser_focused: proctoringEnabled ? browserFocused : true,
          time_since_last_prompt_ms: timeSinceLastMs,
        },
      );
      const payload = res.data || {};
      if (payload.claude_budget) {
        setClaudeBudget(payload.claude_budget);
      }
      if (typeof payload.time_remaining_seconds === "number") {
        setTimeLeft(payload.time_remaining_seconds);
      }
      if (payload.is_timer_paused) {
        setIsTimerPaused(true);
        setPauseReason(payload.pause_reason || "claude_outage");
        setPauseMessage(payload.response || payload.message || "Claude is unavailable and your timer is paused.");
      } else {
        setIsTimerPaused(false);
        setPauseReason(null);
      }
      if (payload.requires_budget_top_up) {
        setOutput(payload.response || payload.message || "Claude budget limit reached for this task.");
      }
      if (payload.requires_terminal) {
        setTerminalFallbackChat(true);
      }
      return (
        payload.response || payload.content || payload.message || "No response from Claude."
      );
    },
    [
      assessment,
      assessmentId,
      assessmentTokenForApi,
      lastPromptTime,
      pasteDetected,
      browserFocused,
      proctoringEnabled,
      isTimerPaused,
      demoMode,
      selectedRepoPath,
    ],
  );

  const handleRetryClaude = useCallback(async () => {
    if (demoMode) {
      setIsTimerPaused(false);
      setPauseReason(null);
      setPauseMessage("");
      return;
    }
    const id = assessment?.id || assessmentId;
    if (!id) return;
    setRetryingClaude(true);
    try {
      const res = await assessments.claudeRetry(id, assessmentTokenForApi);
      const payload = res.data || {};
      if (payload.success && !payload.is_timer_paused) {
        setIsTimerPaused(false);
        setPauseReason(null);
        setPauseMessage("");
        if (typeof payload.time_remaining_seconds === "number") {
          setTimeLeft(payload.time_remaining_seconds);
        }
        setOutput("Claude recovered. Assessment resumed.");
      } else {
        setIsTimerPaused(true);
        setPauseReason(payload.pause_reason || "claude_outage");
        setPauseMessage(payload.message || "Claude is still unavailable.");
      }
    } catch (err) {
      setIsTimerPaused(true);
      setPauseMessage(err?.response?.data?.detail?.message || "Claude is still unavailable.");
    } finally {
      setRetryingClaude(false);
    }
  }, [assessment, assessmentId, assessmentTokenForApi, demoMode]);

  const handleSubmit = useCallback(
    async (autoSubmit = false) => {
      if (submitted) return;
      if (isTimerPaused) {
        setOutput("Assessment is paused. Retry Claude before submitting.");
        return;
      }

      if (!autoSubmit) {
        const confirmed = window.confirm(
          "Are you sure you want to submit? You cannot make changes after submitting.",
        );
        if (!confirmed) return;
      }

      setSubmitted(true);
      clearInterval(timerRef.current);

      try {
        const id = assessment?.id || assessmentId;
        if (showTerminal) {
          try {
            await assessments.terminalStop(id, assessmentTokenForApi);
          } catch {
            // Best effort: submission can continue even if terminal stop fails.
          }
        }
        const res = await assessments.submit(id, codeRef.current, assessmentTokenForApi, {
          tab_switch_count: proctoringEnabled ? tabSwitchCount : 0,
        });
        if (demoMode) {
          const spentSeconds = Math.max(0, ((assessment?.duration_minutes || 30) * 60) - timeLeft);
          const summary = buildDemoSummary({
            runCount: demoRunCount,
            promptMessages: demoPromptMessages,
            saveCount: demoSaveCount,
            finalCode: codeRef.current,
            timeSpentSeconds: spentSeconds,
            tabSwitchCount: proctoringEnabled ? tabSwitchCount : 0,
            taskKey: assessment?.task?.task_key || null,
            submissionResult: res?.data || null,
          });
          setDemoSummary(summary);
          setOutput("Demo submitted successfully.");
          return;
        }
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
      demoRunCount,
      demoPromptMessages,
      demoSaveCount,
      timeLeft,
      proctoringEnabled,
      assessment?.duration_minutes,
      showTerminal,
    ],
  );

  const isTimeLow = timeLeft > 0 && timeLeft < 300; // under 5 minutes
  const isClaudeBudgetExhausted = Boolean(claudeBudget?.enabled && claudeBudget?.is_exhausted);

  if (loading) {
    return <AssessmentStatusScreen mode="loading" />;
  }

  if (submitted) {
    if (demoMode) {
      return (
        <DemoAssessmentSummary
          assessmentName={assessment?.task_name || 'Demo assessment'}
          profile={demoProfile}
          summary={demoSummary}
          onRestart={onDemoRestart || (() => {})}
          onJoinTaali={onJoinTaali || (() => {})}
        />
      );
    }
    return <AssessmentStatusScreen mode="submitted" />;
  }

  return (
    <div className="h-screen flex flex-col bg-white">
      <AssessmentRuntimeAlerts
        showTabWarning={showTabWarning}
        proctoringEnabled={proctoringEnabled}
        isTimerPaused={isTimerPaused}
        pauseReason={pauseReason}
        pauseMessage={pauseMessage}
        onRetryClaude={handleRetryClaude}
        retryingClaude={retryingClaude}
        isClaudeBudgetExhausted={isClaudeBudgetExhausted}
        claudeBudget={claudeBudget}
        formatUsd={formatUsd}
      />

      <AssessmentTopBar
        brandName={BRAND.name}
        taskName={assessment?.task_name || 'Assessment'}
        claudeBudget={claudeBudget}
        aiMode={aiMode}
        terminalCapabilities={terminalCapabilities}
        formatUsd={formatUsd}
        isTimeLow={isTimeLow}
        timeLeft={timeLeft}
        formatTime={formatTime}
        isTimerPaused={isTimerPaused}
        onSubmit={() => handleSubmit(false)}
      />

      <AssessmentContextWindow
        collapsedSections={collapsedSections}
        toggleSection={toggleSection}
        taskContext={taskContext}
        rubricCategories={rubricCategories}
        cloneCommand={assessment?.clone_command}
      />

      <AssessmentWorkspace
        hasRepoStructure={hasRepoStructure}
        collapsedSections={collapsedSections}
        toggleSection={toggleSection}
        repoFileTree={repoFileTree}
        collapsedRepoDirs={collapsedRepoDirs}
        toggleRepoDir={toggleRepoDir}
        selectedRepoPath={selectedRepoPath}
        onSelectRepoFile={handleSelectRepoFile}
        assessmentStarterCode={assessment?.starter_code || ''}
        editorContent={editorContent}
        onEditorChange={handleEditorChange}
        onExecute={handleExecute}
        onSave={handleSave}
        editorLanguage={hasRepoStructure ? languageFromPath(selectedRepoPath) : (assessment?.language || 'python')}
        editorFilename={selectedRepoPath || assessment?.filename || 'main'}
        isTimerPaused={isTimerPaused}
        onSendClaudeMessage={handleClaudeMessage}
        onPasteDetected={() => setPasteDetected(true)}
        aiMode={aiMode}
        showTerminal={showTerminal}
        terminalConnected={terminalConnected}
        terminalEvents={terminalEvents}
        onTerminalInput={handleTerminalInput}
        onTerminalResize={handleTerminalResize}
        onTerminalStop={handleTerminalStop}
        terminalStopping={terminalStopping}
        claudeBudget={claudeBudget}
        isClaudeBudgetExhausted={isClaudeBudgetExhausted}
        output={output}
        executing={executing}
      />
    </div>
  );
}
