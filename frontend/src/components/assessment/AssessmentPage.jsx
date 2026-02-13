import { useState, useEffect, useRef, useCallback } from 'react';
import { Code, Clock } from 'lucide-react';
import CodeEditor from './CodeEditor';
import ClaudeChat from './ClaudeChat';
import { BRAND } from '../../config/brand';
import { assessments } from '../../lib/api';

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
  const [selectedRepoFile, setSelectedRepoFile] = useState(null);
  const codeRef = useRef("");
  const timerRef = useRef(null);

  // Use startData from welcome page (no double-start), or taskData for demo, or call start() only if token and no startData
  useEffect(() => {
    if (startData) {
      const normalized = normalizeStartData(startData);
      setAssessment(normalized);
      codeRef.current = normalized.starter_code || "";
      setTimeLeft(normalized.time_remaining);
      setProctoringEnabled(startData.task?.proctoring_enabled || false);
      setLoading(false);
      return;
    }
    if (taskData) {
      setAssessment(taskData);
      codeRef.current = taskData.starter_code || "";
      setTimeLeft((taskData.duration_minutes || 30) * 60);
      setProctoringEnabled(taskData.proctoring_enabled || false);
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
        codeRef.current = normalized.starter_code || "";
        setTimeLeft(normalized.time_remaining);
        setProctoringEnabled(data.task?.proctoring_enabled || false);
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
    if (loading || submitted || timeLeft <= 0) return;

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
  }, [loading, submitted]);

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

  const assessmentTokenForApi = assessment?.token ?? token;
  const taskContext = assessment?.scenario || assessment?.description || "";
  const repoFiles = extractRepoFiles(assessment?.repo_structure);
  const activeRepoFile =
    selectedRepoFile && repoFiles.some((file) => file.path === selectedRepoFile)
      ? selectedRepoFile
      : repoFiles[0]?.path || null;
  const activeRepoContent = repoFiles.find(
    (file) => file.path === activeRepoFile,
  )?.content;

  // Execute code
  const handleExecute = useCallback(
    async (code) => {
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
        setOutput(
          `Execution error: ${err.response?.data?.detail || err.message}`,
        );
      } finally {
        setExecuting(false);
      }
    },
    [assessment, assessmentId, assessmentTokenForApi],
  );

  // Save code (just updates ref, could persist)
  const handleSave = useCallback((code) => {
    codeRef.current = code;
    setOutput("Code saved.");
  }, []);

  // Claude chat
  const handleClaudeMessage = useCallback(
    async (message, history) => {
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
      return (
        res.data.response || res.data.message || "No response from Claude."
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
    ],
  );

  // Submit assessment
  const handleSubmit = useCallback(
    async (autoSubmit = false) => {
      if (submitted) return;

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
        setOutput(`Submit error: ${err.response?.data?.detail || err.message}`);
        setSubmitted(false);
      }
    },
    [
      assessment,
      assessmentId,
      assessmentTokenForApi,
      submitted,
      tabSwitchCount,
    ],
  );

  const isTimeLow = timeLeft > 0 && timeLeft < 300; // under 5 minutes

  // Loading state
  if (loading) {
    return (
      <div className="h-screen flex items-center justify-center bg-white">
        <div className="text-center">
          <div
            className="w-16 h-16 border-2 border-black flex items-center justify-center mx-auto mb-4 animate-pulse"
            style={{ backgroundColor: "#9D00FF" }}
          >
            <Code size={28} className="text-white" />
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
          <div
            className="w-16 h-16 border-2 border-black flex items-center justify-center mx-auto mb-6"
            style={{ backgroundColor: "#9D00FF" }}
          >
            <Code size={28} className="text-white" />
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

      {/* Top bar */}
      <div className="border-b-2 border-black bg-white px-4 py-3 flex items-center justify-between">
        <div className="flex items-center gap-4">
          {/* Logo */}
          <div className="flex items-center gap-2">
            <div
              className="w-8 h-8 border-2 border-black flex items-center justify-center"
              style={{ backgroundColor: "#9D00FF" }}
            >
              <Code size={16} className="text-white" />
            </div>
            <span className="text-lg font-bold tracking-tight">{BRAND.name}</span>
          </div>
          {/* Task name */}
          <span className="font-mono text-sm text-gray-500">|</span>
          <span className="font-mono text-sm font-bold">
            {assessment?.task_name || "Assessment"}
          </span>
        </div>
        <div className="flex items-center gap-4">
          {/* Timer */}
          <div
            className={`flex items-center gap-2 border-2 border-black px-4 py-1.5 font-mono text-sm font-bold ${
              isTimeLow ? "bg-red-500 text-white border-red-600" : "bg-white"
            }`}
          >
            <Clock size={16} />
            <span>{formatTime(timeLeft)}</span>
          </div>
          {/* Submit */}
          <button
            onClick={() => handleSubmit(false)}
            className="border-2 border-black px-6 py-1.5 font-mono text-sm font-bold text-white hover:bg-black transition-colors"
            style={{ backgroundColor: "#9D00FF" }}
          >
            Submit
          </button>
        </div>
      </div>

      <div className="border-b-2 border-black bg-gray-50 p-4">
        <div className="grid md:grid-cols-2 gap-4">
          <div>
            <div className="font-mono text-xs text-gray-500 mb-1">
              Task Context
            </div>
            <p className="font-mono text-sm text-gray-700 whitespace-pre-wrap mb-3">
              {taskContext || "Task context has not been provided yet."}
            </p>
            <div>
              <div className="font-mono text-xs text-gray-500 mb-1">Repository Context</div>
              {repoFiles.length === 0 ? (
                <p className="font-mono text-xs text-gray-600">No repository files provided for this assessment.</p>
              ) : (
                <>
                  <div className="flex flex-wrap gap-2 mb-2 max-h-16 overflow-auto">
                    {repoFiles.map((file) => (
                      <button
                        key={file.path}
                        type="button"
                        className={`border px-2 py-1 font-mono text-xs ${activeRepoFile === file.path ? 'border-black bg-black text-white' : 'border-gray-400 bg-white'}`}
                        onClick={() => setSelectedRepoFile(file.path)}
                      >
                        {file.path}
                      </button>
                    ))}
                  </div>
                  <pre className="bg-black text-gray-200 p-2 text-xs overflow-auto max-h-36 border-2 border-black">{activeRepoContent || 'No file content available.'}</pre>
                </>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Code editor - 65% */}
        <div className="w-[65%] border-r-2 border-black">
          <CodeEditor
            initialCode={assessment?.starter_code || ""}
            onExecute={handleExecute}
            onSave={handleSave}
            language={assessment?.language || "python"}
            filename={assessment?.filename || "pipeline.py"}
          />
        </div>

        {/* Right panel - 35% */}
        <div className="w-[35%] flex flex-col">
          {/* Claude chat - 60% */}
          <div className="h-[60%] border-b-2 border-black">
            <ClaudeChat
              onSendMessage={handleClaudeMessage}
              onPaste={() => setPasteDetected(true)}
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
