import { useState, useEffect, useRef, useCallback } from 'react';
import { Clock, ChevronRight, ChevronDown, FileText, Folder } from 'lucide-react';
import CodeEditor from '../../components/assessment/CodeEditor';
import ClaudeChat from '../../components/assessment/ClaudeChat';
import { BRAND } from '../../config/brand';
import { assessments } from '../../shared/api';

/** Normalize API start response to assessment shape used by this component */
function normalizeStartData(startData) {
  const task = startData.task || {};
  return {
    id: startData.assessment_id,
    token: startData.token,
    starter_code: task.starter_code || "",
    duration_minutes: task.duration_minutes ?? 30,
    time_remaining:
      startData.time_remaining ?? (task.duration_minutes ?? 30) * 60,
    task_name: task.name || "Assessment",
    description: task.description || startData.description || "",
    scenario: task.scenario || startData.scenario || "",
    repo_structure: task.repo_structure || startData.repo_structure || null,
    task,
    rubric_categories: task.rubric_categories || startData.rubric_categories || [],
    clone_command: startData.clone_command || task.clone_command || null,
    claude_budget: startData.claude_budget || null,
    claude_budget_limit_usd: task.claude_budget_limit_usd ?? null,
    is_timer_paused: Boolean(startData.is_timer_paused),
    pause_reason: startData.pause_reason || null,
  };
}

function extractRepoFiles(repoStructure) {
  if (!repoStructure) return [];
  if (Array.isArray(repoStructure?.files)) {
    return repoStructure.files
      .map((f) => ({
        path: f.path || f.name || "file",
        content: f.content || "",
      }))
      .filter((f) => f.path);
  }
  if (repoStructure?.files && typeof repoStructure.files === "object") {
    return Object.entries(repoStructure.files).map(([path, content]) => ({
      path,
      content:
        typeof content === "string"
          ? content
          : JSON.stringify(content, null, 2),
    }));
  }
  return [];
}

/** Build a tree { dirPath: [filePaths] } for repo file list */
function buildRepoFileTree(repoFiles) {
  const tree = { "": [] };
  for (const { path } of repoFiles) {
    const i = path.lastIndexOf("/");
    const dir = i >= 0 ? path.slice(0, i) : "";
    if (!tree[dir]) tree[dir] = [];
    tree[dir].push(path);
  }
  for (const dir of Object.keys(tree)) {
    tree[dir].sort();
  }
  return tree;
}

/** Infer language from filename for Monaco */
function languageFromPath(path) {
  if (!path) return "python";
  if (/\.(py|pyw)$/i.test(path)) return "python";
  if (/\.(js|jsx|ts|tsx|mjs|cjs)$/i.test(path)) return "javascript";
  if (/\.(md|mdx)$/i.test(path)) return "markdown";
  if (/\.(json)$/i.test(path)) return "json";
  if (/\.(yaml|yml)$/i.test(path)) return "yaml";
  if (/\.(sh|bash)$/i.test(path)) return "shell";
  return "plaintext";
}

const AssessmentBrandGlyph = ({ sizeClass = 'w-8 h-8', markSizeClass = 'w-6 h-6' }) => (
  <div
    className={`${sizeClass} border-2 border-black flex items-center justify-center`}
    style={{ backgroundColor: '#9D00FF' }}
    aria-hidden="true"
  >
    <svg viewBox="0 0 24 24" className={markSizeClass} fill="none">
      <path
        d="M6 4.5v15M10 4.5v15M14 4.5v15M18 4.5v15M4 18.5L20 5.5"
        stroke="#FFFFFF"
        strokeWidth="2.5"
        strokeLinecap="round"
      />
    </svg>
  </div>
);

export default function AssessmentPage({
  assessmentId,
  token,
  taskData,
  startData,
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
  const [collapsedSections, setCollapsedSections] = useState({
    contextWindow: false,
    taskContext: false,
    rubric: false,
    repoContext: false,
    repoTree: false,
  });
  const [collapsedRepoDirs, setCollapsedRepoDirs] = useState({});
  const codeRef = useRef("");
  const timerRef = useRef(null);

  // Use startData from welcome page (no double-start), or taskData for demo, or call start() only if token and no startData
  useEffect(() => {
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

  // Countdown timer
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

  // Browser focus and tab visibility tracking
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

  const formatTime = (seconds) => {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  };

  const formatUsd = (value) => (
    typeof value === "number" && !Number.isNaN(value) ? `$${value.toFixed(2)}` : "N/A"
  );

  const assessmentTokenForApi = assessment?.token ?? token;
  const taskContext = assessment?.scenario || assessment?.description || "";
  const repoFiles = extractRepoFiles(assessment?.repo_structure);
  const rubricCategories = assessment?.rubric_categories || assessment?.task?.rubric_categories || [];
  const selectedRepoPath =
    selectedRepoFile && repoFiles.some((file) => file.path === selectedRepoFile)
      ? selectedRepoFile
      : repoFiles[0]?.path || null;
  const selectedRepoContent = repoFiles.find(
    (file) => file.path === selectedRepoPath,
  )?.content;
  const repoFileTree = buildRepoFileTree(repoFiles);
  const hasRepoStructure = repoFiles.length > 0;

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

  // Execute code
  const handleExecute = useCallback(
    async (code) => {
      if (isTimerPaused) {
        setOutput("Assessment is paused. Retry Claude to resume execution.");
        return;
      }
      codeRef.current = code;
      setExecuting(true);
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
    [assessment, assessmentId, assessmentTokenForApi, isTimerPaused],
  );

  // Save code (just updates ref, could persist)
  const handleSave = useCallback((code) => {
    codeRef.current = code;
    setOutput("Code saved.");
  }, []);

  // Claude chat
  const handleClaudeMessage = useCallback(
    async (message, history) => {
      if (isTimerPaused) {
        return "Assessment is paused while Claude is unavailable. Use Retry to resume.";
      }
      const id = assessment?.id || assessmentId;

      // Compute time since last prompt
      const now = Date.now();
      const timeSinceLastMs = lastPromptTime ? now - lastPromptTime : null;
      setLastPromptTime(now);

      // Capture and reset paste detected
      const wasPasted = pasteDetected;
      setPasteDetected(false);

      const res = await assessments.claude(
        id,
        message,
        history,
        assessmentTokenForApi,
        {
          code_context: codeRef.current,
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
    ],
  );

  const handleRetryClaude = useCallback(async () => {
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
  }, [assessment, assessmentId, assessmentTokenForApi]);

  // Submit assessment
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
        await assessments.submit(id, codeRef.current, assessmentTokenForApi, {
          tab_switch_count: proctoringEnabled ? tabSwitchCount : 0,
        });
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
    ],
  );

  const isTimeLow = timeLeft > 0 && timeLeft < 300; // under 5 minutes
  const isClaudeBudgetExhausted = Boolean(claudeBudget?.enabled && claudeBudget?.is_exhausted);

  // Loading state
  if (loading) {
    return (
      <div className="h-screen flex items-center justify-center bg-white">
        <div className="text-center">
          <div className="mx-auto mb-4 animate-pulse w-fit">
            <AssessmentBrandGlyph sizeClass="w-16 h-16" markSizeClass="w-[2.7rem] h-[2.7rem]" />
          </div>
          <p className="font-mono text-sm text-gray-600">
            Loading assessment...
          </p>
        </div>
      </div>
    );
  }

  // Submitted state
  if (submitted) {
    return (
      <div className="h-screen flex items-center justify-center bg-white">
        <div className="text-center border-2 border-black p-12 max-w-md">
          <div className="mx-auto mb-6 w-fit">
            <AssessmentBrandGlyph sizeClass="w-16 h-16" markSizeClass="w-[2.7rem] h-[2.7rem]" />
          </div>
          <h1 className="text-3xl font-bold mb-4">Assessment Submitted</h1>
          <p className="font-mono text-sm text-gray-600 mb-2">
            Thank you for completing the assessment.
          </p>
          <p className="font-mono text-sm text-gray-600">
            Your results will be reviewed and you&apos;ll hear back soon.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col bg-white">
      {/* Tab switch warning toast */}
      {showTabWarning && (
        <div className="fixed top-4 right-4 z-50 border-2 border-red-500 bg-red-50 p-4 shadow-lg">
          <div className="font-mono text-sm text-red-700 font-bold">
            You have left the assessment tab.
          </div>
          <div className="font-mono text-xs text-red-600">
            This has been recorded.
          </div>
        </div>
      )}

      {/* Proctoring notice banner */}
      {proctoringEnabled && (
        <div className="border-b-2 border-black bg-yellow-50 p-2 text-center">
          <span className="font-mono text-xs text-yellow-800 font-bold">
            ⚠ This assessment is proctored — tab switches and browser focus are
            being recorded
          </span>
        </div>
      )}

      {isTimerPaused && (
        <div className="border-b-2 border-black bg-red-50 px-4 py-2 flex items-center justify-between gap-3">
          <div className="font-mono text-xs text-red-700">
            Assessment paused: Claude is currently unavailable{pauseReason ? ` (${pauseReason})` : ""}.
            {pauseMessage ? ` ${pauseMessage}` : ""}
          </div>
          <button
            type="button"
            className="border-2 border-black px-3 py-1 font-mono text-xs font-bold bg-white hover:bg-black hover:text-white disabled:opacity-60"
            onClick={handleRetryClaude}
            disabled={retryingClaude}
          >
            {retryingClaude ? "Retrying..." : "Retry Claude"}
          </button>
        </div>
      )}

      {isClaudeBudgetExhausted && (
        <div className="border-b-2 border-black bg-amber-50 px-4 py-2">
          <div className="font-mono text-xs text-amber-800">
            Claude budget exhausted for this task
            {claudeBudget?.limit_usd ? ` (${formatUsd(claudeBudget.limit_usd)} cap reached)` : ""}.
            Continue coding and submit when ready.
          </div>
        </div>
      )}

      {/* Top bar */}
      <div className="border-b-2 border-black bg-white px-4 py-3 flex items-center justify-between">
        <div className="flex items-center gap-4">
          {/* Logo */}
          <div className="flex items-center gap-2">
            <AssessmentBrandGlyph />
            <span className="text-lg font-bold tracking-tight">{BRAND.name}</span>
          </div>
          {/* Task name */}
          <span className="font-mono text-sm text-gray-500">|</span>
          <span className="font-mono text-sm font-bold">
            {assessment?.task_name || "Assessment"}
          </span>
        </div>
        <div className="flex items-center gap-4">
          {claudeBudget?.enabled && (
            <div className="border-2 border-black px-3 py-1.5 font-mono text-xs bg-amber-50">
              Claude left: {formatUsd(claudeBudget.remaining_usd)} / {formatUsd(claudeBudget.limit_usd)}
            </div>
          )}
          {/* Timer */}
          <div
            className={`flex items-center gap-2 border-2 border-black px-4 py-1.5 font-mono text-sm font-bold ${
              isTimeLow ? "bg-red-500 text-white border-red-600" : "bg-white"
            }`}
          >
            <Clock size={16} />
            <span>{formatTime(timeLeft)}</span>
            {isTimerPaused && <span className="text-xs uppercase">Paused</span>}
          </div>
          {/* Submit */}
          <button
            onClick={() => handleSubmit(false)}
            disabled={isTimerPaused}
            className="border-2 border-black px-6 py-1.5 font-mono text-sm font-bold text-white hover:bg-black transition-colors"
            style={{ backgroundColor: "#9D00FF" }}
          >
            Submit
          </button>
        </div>
      </div>

      <div className="border-b-2 border-black bg-gray-50">
        <button
          type="button"
          className="w-full px-4 py-2.5 flex items-center justify-between font-mono text-xs font-bold text-gray-700 hover:bg-gray-100"
          onClick={() => toggleSection("contextWindow")}
        >
          <span>Context Window</span>
          {collapsedSections.contextWindow ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
        </button>

        {!collapsedSections.contextWindow && (
          <div className="p-4 border-t border-gray-200 max-h-[34vh] overflow-y-auto">
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              <div className="border border-black bg-white">
                <button
                  type="button"
                  className="w-full px-3 py-2 flex items-center justify-between font-mono text-xs font-bold text-gray-700 hover:bg-gray-100"
                  onClick={() => toggleSection("taskContext")}
                >
                  <span>Task Context</span>
                  {collapsedSections.taskContext ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
                </button>
                {!collapsedSections.taskContext && (
                  <div className="border-t border-gray-200 px-3 py-2">
                    <div className="max-h-32 overflow-y-auto pr-1">
                      <p className="font-mono text-sm text-gray-700 whitespace-pre-wrap">
                        {taskContext || "Task context has not been provided yet."}
                      </p>
                    </div>
                  </div>
                )}
              </div>

              <div className="border border-black bg-white">
                <button
                  type="button"
                  className="w-full px-3 py-2 flex items-center justify-between font-mono text-xs font-bold text-gray-700 hover:bg-gray-100"
                  onClick={() => toggleSection("rubric")}
                >
                  <span>How you'll be assessed</span>
                  {collapsedSections.rubric ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
                </button>
                {!collapsedSections.rubric && (
                  <div className="border-t border-gray-200 px-3 py-2">
                    <div className="max-h-32 overflow-y-auto pr-1">
                      {rubricCategories.length === 0 ? (
                        <p className="font-mono text-xs text-gray-600">Rubric categories will be shown when available.</p>
                      ) : (
                        <ul className="font-mono text-xs text-gray-700 space-y-1">
                          {rubricCategories.map((item) => (
                            <li key={item.category} className="flex justify-between gap-3">
                              <span className="truncate">{String(item.category || "").replace(/_/g, " ")}</span>
                              <span>{Math.round((Number(item.weight || 0) * 100))}%</span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                    {assessment?.clone_command && (
                      <div className="font-mono text-[11px] text-gray-600 mt-2 break-all">
                        Workspace clone command: <code>{assessment.clone_command}</code>
                      </div>
                    )}
                  </div>
                )}
              </div>

              <div className="border border-black bg-white">
                <button
                  type="button"
                  className="w-full px-3 py-2 flex items-center justify-between font-mono text-xs font-bold text-gray-700 hover:bg-gray-100"
                  onClick={() => toggleSection("repoContext")}
                >
                  <span>Repository Context</span>
                  {collapsedSections.repoContext ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
                </button>
                {!collapsedSections.repoContext && (
                  <div className="border-t border-gray-200 px-3 py-2">
                    {repoFiles.length === 0 ? (
                      <p className="font-mono text-xs text-gray-600">No repository files provided for this assessment.</p>
                    ) : (
                      <>
                        <div className="flex flex-wrap gap-2 mb-2 max-h-16 overflow-auto pr-1">
                          {repoFiles.map((file) => (
                            <button
                              key={file.path}
                              type="button"
                              className={`border px-2 py-1 font-mono text-xs ${selectedRepoPath === file.path ? "border-black bg-black text-white" : "border-gray-400 bg-white"}`}
                              onClick={() => handleSelectRepoFile(file.path)}
                            >
                              {file.path}
                            </button>
                          ))}
                        </div>
                        <pre className="bg-black text-gray-200 p-2 text-xs overflow-auto max-h-32 border-2 border-black">
                          {selectedRepoContent || "No file content available."}
                        </pre>
                      </>
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Main content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Code editor + optional file tree - 65% */}
        <div className="w-[65%] border-r-2 border-black flex flex-col">
          <div className="flex-1 flex overflow-hidden">
            {hasRepoStructure && (
              <div className={`${collapsedSections.repoTree ? "w-10" : "w-52"} border-r-2 border-black bg-gray-50 flex flex-col overflow-hidden transition-all duration-150`}>
                <button
                  type="button"
                  className="px-2 py-2 border-b border-gray-200 font-mono text-xs font-bold text-gray-600 flex items-center gap-1.5 hover:bg-gray-100"
                  onClick={() => toggleSection("repoTree")}
                >
                  {collapsedSections.repoTree ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
                  {!collapsedSections.repoTree && <span>Repository</span>}
                </button>
                {!collapsedSections.repoTree && (
                  <div className="flex-1 overflow-y-auto py-1">
                    {Object.entries(repoFileTree)
                      .sort(([a], [b]) => (a || "").localeCompare(b || ""))
                      .map(([dir, paths]) => (
                        <div key={dir || "(root)"} className="mb-1">
                          {dir ? (
                            <button
                              type="button"
                              className="w-full px-2 py-0.5 font-mono text-xs text-gray-500 flex items-center gap-0.5 hover:bg-gray-100 text-left"
                              onClick={() => toggleRepoDir(dir)}
                            >
                              {collapsedRepoDirs[dir] ? <ChevronRight size={10} /> : <ChevronDown size={10} />}
                              <Folder size={10} />
                              <span>{dir}/</span>
                            </button>
                          ) : null}
                          <div className={dir ? "pl-3" : ""} hidden={Boolean(dir && collapsedRepoDirs[dir])}>
                            {paths.map((path) => {
                              const name = path.includes("/") ? path.slice(path.lastIndexOf("/") + 1) : path;
                              const isSelected = path === selectedRepoPath;
                              return (
                                <button
                                  key={path}
                                  type="button"
                                  className={`w-full text-left px-2 py-1 font-mono text-xs flex items-center gap-1.5 hover:bg-gray-200 ${
                                    isSelected ? "bg-black text-white hover:bg-gray-800" : "text-gray-800"
                                  }`}
                                  onClick={() => handleSelectRepoFile(path)}
                                >
                                  <FileText size={10} />
                                  <span className="truncate">{name}</span>
                                </button>
                              );
                            })}
                          </div>
                        </div>
                      ))}
                  </div>
                )}
              </div>
            )}
            <div className="flex-1 min-w-0">
              <CodeEditor
                initialCode={assessment?.starter_code || ""}
                value={editorContent}
                onChange={handleEditorChange}
                onExecute={handleExecute}
                onSave={handleSave}
                language={hasRepoStructure ? languageFromPath(selectedRepoPath) : (assessment?.language || "python")}
                filename={selectedRepoPath || assessment?.filename || "main"}
                disabled={isTimerPaused}
              />
            </div>
          </div>
        </div>

        {/* Right panel - 35% */}
        <div className="w-[35%] flex flex-col">
          {/* Claude chat - 60% */}
          <div className="h-[60%] border-b-2 border-black">
            <ClaudeChat
              onSendMessage={handleClaudeMessage}
              onPaste={() => setPasteDetected(true)}
              budget={claudeBudget}
              disabled={isTimerPaused || isClaudeBudgetExhausted}
              disabledReason={isTimerPaused ? "timer_paused" : (isClaudeBudgetExhausted ? "budget_exhausted" : null)}
            />
          </div>

          {/* Output console - 40% */}
          <div className="h-[40%] bg-black text-white p-4 font-mono text-sm overflow-y-auto">
            <div className="flex items-center gap-2 mb-3">
              <span className="font-bold" style={{ color: "#9D00FF" }}>
                Output:
              </span>
              {executing && (
                <span className="text-yellow-400 animate-pulse text-xs">
                  executing...
                </span>
              )}
            </div>
            <pre className="whitespace-pre-wrap text-gray-300">
              {output || "Run your code to see output here."}
            </pre>
          </div>
        </div>
      </div>
    </div>
  );
}
