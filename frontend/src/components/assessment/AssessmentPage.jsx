import { useState, useEffect, useRef, useCallback } from 'react';
import { Code, Clock } from 'lucide-react';
import CodeEditor from './CodeEditor';
import ClaudeChat from './ClaudeChat';
import { assessments } from '../../lib/api';

/** Normalize API start response to assessment shape used by this component */
function normalizeStartData(startData) {
  const task = startData.task || {};
  return {
    id: startData.assessment_id,
    token: startData.token,
    starter_code: task.starter_code || '',
    duration_minutes: task.duration_minutes ?? 30,
    time_remaining: startData.time_remaining ?? (task.duration_minutes ?? 30) * 60,
    task_name: task.name || 'Assessment',
    task,
  };
}

export default function AssessmentPage({ assessmentId, token, taskData, startData }) {
  const [assessment, setAssessment] = useState(null);
  const [loading, setLoading] = useState(true);
  const [output, setOutput] = useState('');
  const [executing, setExecuting] = useState(false);
  const [timeLeft, setTimeLeft] = useState(0);
  const [submitted, setSubmitted] = useState(false);
  const codeRef = useRef('');
  const timerRef = useRef(null);

  // Use startData from welcome page (no double-start), or taskData for demo, or call start() only if token and no startData
  useEffect(() => {
    if (startData) {
      const normalized = normalizeStartData(startData);
      setAssessment(normalized);
      codeRef.current = normalized.starter_code || '';
      setTimeLeft(normalized.time_remaining);
      setLoading(false);
      return;
    }
    if (taskData) {
      setAssessment(taskData);
      codeRef.current = taskData.starter_code || '';
      setTimeLeft((taskData.duration_minutes || 30) * 60);
      setLoading(false);
      return;
    }
    if (!token) {
      setLoading(false);
      setOutput('Error: No assessment token provided.');
      return;
    }
    const startAssessment = async () => {
      try {
        const res = await assessments.start(token);
        const data = res.data;
        const normalized = normalizeStartData(data);
        setAssessment(normalized);
        codeRef.current = normalized.starter_code || '';
        setTimeLeft(normalized.time_remaining);
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

  const formatTime = (seconds) => {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  };

  const assessmentTokenForApi = assessment?.token ?? token;

  // Execute code
  const handleExecute = useCallback(
    async (code) => {
      codeRef.current = code;
      setExecuting(true);
      setOutput('Running...\n');
      try {
        const id = assessment?.id || assessmentId;
        const res = await assessments.execute(id, code, assessmentTokenForApi);
        const result = res.data;
        setOutput(result.stdout || result.output || 'No output.');
        if (result.stderr) {
          setOutput((prev) => prev + '\n--- stderr ---\n' + result.stderr);
        }
      } catch (err) {
        setOutput(`Execution error: ${err.response?.data?.detail || err.message}`);
      } finally {
        setExecuting(false);
      }
    },
    [assessment, assessmentId, assessmentTokenForApi]
  );

  // Save code (just updates ref, could persist)
  const handleSave = useCallback((code) => {
    codeRef.current = code;
    setOutput('Code saved.');
  }, []);

  // Claude chat
  const handleClaudeMessage = useCallback(
    async (message, history) => {
      const id = assessment?.id || assessmentId;
      const res = await assessments.claude(id, message, history, assessmentTokenForApi);
      return res.data.response || res.data.message || 'No response from Claude.';
    },
    [assessment, assessmentId, assessmentTokenForApi]
  );

  // Submit assessment
  const handleSubmit = useCallback(
    async (autoSubmit = false) => {
      if (submitted) return;

      if (!autoSubmit) {
        const confirmed = window.confirm(
          'Are you sure you want to submit? You cannot make changes after submitting.'
        );
        if (!confirmed) return;
      }

      setSubmitted(true);
      clearInterval(timerRef.current);

      try {
        const id = assessment?.id || assessmentId;
        await assessments.submit(id, codeRef.current, assessmentTokenForApi);
        setOutput('Assessment submitted successfully! You may close this window.');
      } catch (err) {
        setOutput(`Submit error: ${err.response?.data?.detail || err.message}`);
        setSubmitted(false);
      }
    },
    [assessment, assessmentId, assessmentTokenForApi, submitted]
  );

  const isTimeLow = timeLeft > 0 && timeLeft < 300; // under 5 minutes

  // Loading state
  if (loading) {
    return (
      <div className="h-screen flex items-center justify-center bg-white">
        <div className="text-center">
          <div
            className="w-16 h-16 border-2 border-black flex items-center justify-center mx-auto mb-4 animate-pulse"
            style={{ backgroundColor: '#9D00FF' }}
          >
            <Code size={28} className="text-white" />
          </div>
          <p className="font-mono text-sm text-gray-600">Loading assessment...</p>
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
            style={{ backgroundColor: '#9D00FF' }}
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
      {/* Top bar */}
      <div className="border-b-2 border-black bg-white px-4 py-3 flex items-center justify-between">
        <div className="flex items-center gap-4">
          {/* Logo */}
          <div className="flex items-center gap-2">
            <div
              className="w-8 h-8 border-2 border-black flex items-center justify-center"
              style={{ backgroundColor: '#9D00FF' }}
            >
              <Code size={16} className="text-white" />
            </div>
            <span className="text-lg font-bold tracking-tight">TALI</span>
          </div>
          {/* Task name */}
          <span className="font-mono text-sm text-gray-500">|</span>
          <span className="font-mono text-sm font-bold">
            {assessment?.task_name || 'Assessment'}
          </span>
        </div>
        <div className="flex items-center gap-4">
          {/* Timer */}
          <div
            className={`flex items-center gap-2 border-2 border-black px-4 py-1.5 font-mono text-sm font-bold ${
              isTimeLow ? 'bg-red-500 text-white border-red-600' : 'bg-white'
            }`}
          >
            <Clock size={16} />
            <span>{formatTime(timeLeft)}</span>
          </div>
          {/* Submit */}
          <button
            onClick={() => handleSubmit(false)}
            className="border-2 border-black px-6 py-1.5 font-mono text-sm font-bold text-white hover:bg-black transition-colors"
            style={{ backgroundColor: '#9D00FF' }}
          >
            Submit
          </button>
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Code editor - 65% */}
        <div className="w-[65%] border-r-2 border-black">
          <CodeEditor
            initialCode={assessment?.starter_code || ''}
            onExecute={handleExecute}
            onSave={handleSave}
            language={assessment?.language || 'python'}
            filename={assessment?.filename || 'pipeline.py'}
          />
        </div>

        {/* Right panel - 35% */}
        <div className="w-[35%] flex flex-col">
          {/* Claude chat - 60% */}
          <div className="h-[60%] border-b-2 border-black">
            <ClaudeChat onSendMessage={handleClaudeMessage} />
          </div>

          {/* Output console - 40% */}
          <div className="h-[40%] bg-black text-white p-4 font-mono text-sm overflow-y-auto">
            <div className="flex items-center gap-2 mb-3">
              <span className="font-bold" style={{ color: '#9D00FF' }}>
                Output:
              </span>
              {executing && (
                <span className="text-yellow-400 animate-pulse text-xs">
                  executing...
                </span>
              )}
            </div>
            <pre className="whitespace-pre-wrap text-gray-300">
              {output || 'Run your code to see output here.'}
            </pre>
          </div>
        </div>
      </div>
    </div>
  );
}
