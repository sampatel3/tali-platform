import React, { useEffect, useState } from 'react';
import {
  Brain,
  Check,
  ChevronRight,
  Loader2,
  Shield,
  Terminal,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';

import { assessments as assessmentsApi } from '../../shared/api';
import { Logo } from '../../shared/ui/Branding';

export const CandidateWelcomePage = ({ token, assessmentId, onNavigate, onStarted }) => {
  const [loadingStart, setLoadingStart] = useState(false);
  const [startError, setStartError] = useState('');
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewData, setPreviewData] = useState(null);
  const [previewError, setPreviewError] = useState('');

  useEffect(() => {
    let cancelled = false;
    const loadPreview = async () => {
      if (!token) return;
      setPreviewLoading(true);
      setPreviewError('');
      try {
        const res = await assessmentsApi.preview(token);
        if (cancelled) return;
        setPreviewData(res.data || null);
      } catch (err) {
        if (cancelled) return;
        setPreviewData(null);
        setPreviewError(err?.response?.data?.detail || 'Task preview is not available yet.');
      } finally {
        if (!cancelled) setPreviewLoading(false);
      }
    };
    loadPreview();
    return () => {
      cancelled = true;
    };
  }, [token]);

  const handleStart = async () => {
    if (!token) {
      setStartError('Assessment token is missing from the link.');
      return;
    }
    setLoadingStart(true);
    setStartError('');
    try {
      const res = await assessmentsApi.start(token);
      const data = res.data;
      if (onStarted) onStarted(data);
      onNavigate('assessment');
    } catch (err) {
      const msg = err.response?.data?.detail || 'Failed to start assessment';
      setStartError(msg);
    } finally {
      setLoadingStart(false);
    }
  };

  const taskPreview = previewData?.task || {};
  const scenarioMarkdown = String(taskPreview?.scenario || '').trim();
  const expectedJourney = taskPreview?.expected_candidate_journey && typeof taskPreview.expected_candidate_journey === 'object'
    ? taskPreview.expected_candidate_journey
    : null;
  const durationMinutes = Number(taskPreview?.duration_minutes ?? previewData?.duration_minutes ?? 30);

  return (
    <div className="min-h-screen bg-white">
      <nav className="border-b-2 border-black bg-white">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center gap-4">
          <Logo onClick={() => {}} />
          <span className="font-mono text-sm text-gray-500">|</span>
          <span className="font-mono text-sm">Technical Assessment</span>
        </div>
      </nav>
      <div className="max-w-3xl mx-auto px-6 py-16">
        <div className="text-center mb-8">
          <div
            className="inline-block px-4 py-2 text-xs font-mono font-bold text-[var(--taali-surface)] border-2 border-[var(--taali-border)] mb-4 bg-[var(--taali-purple)]"
          >
            TAALI Assessment
          </div>
          <h1 className="text-4xl font-bold mb-2">Technical Assessment</h1>
          <p className="text-[var(--taali-muted)]">You&apos;ve been invited to complete a coding challenge</p>
        </div>

        <div className="border-2 border-[var(--taali-border)] p-5 mb-6 bg-[var(--taali-bg)]">
          <div className="flex flex-wrap items-center gap-2 font-mono text-xs text-[var(--taali-muted)]">
            <span>Role: {taskPreview?.role || 'Engineering'}</span>
            <span>•</span>
            <span>Duration: {durationMinutes} minutes</span>
            {taskPreview?.name ? (
              <>
                <span>•</span>
                <span>Task: {taskPreview.name}</span>
              </>
            ) : null}
          </div>
          {previewLoading ? (
            <p className="mt-2 text-xs text-[var(--taali-muted)]">Loading task context…</p>
          ) : previewError ? (
            <p className="mt-2 text-xs text-[var(--taali-warning)]">{previewError}</p>
          ) : null}
        </div>

        <div className="border-2 border-[var(--taali-border)] p-8 mb-8">
          <p className="text-lg mb-4">Welcome,</p>
          <p className="text-sm text-[var(--taali-text)] mb-4 leading-relaxed">
            You&apos;ve been invited to complete a technical assessment. This is a real coding environment where you can write, run, and test code with AI assistance.
          </p>
          <p className="text-sm text-[var(--taali-text)] mb-4">You&apos;ll have access to:</p>
          <ul className="space-y-2 mb-6">
            {['Full Python environment (sandboxed)', 'Claude AI assistant for help', 'All the tools you\'d use on the job'].map((item) => (
              <li key={item} className="flex items-center gap-2 font-mono text-sm">
                <Check size={16} className="text-[var(--taali-purple)]" /> {item}
              </li>
            ))}
          </ul>
          <p className="text-sm text-[var(--taali-text)] italic">
            This isn&apos;t a trick. We want to see how you actually work.
          </p>
          <p className="text-sm text-[var(--taali-muted)] mt-4">Ready when you are.</p>
        </div>

        <div className="border-2 border-[var(--taali-border)] p-6 mb-8 bg-[var(--taali-purple-soft)]">
          <h2 className="text-xl font-bold mb-3">How you&apos;ll be evaluated</h2>
          <ul className="space-y-2 font-mono text-sm text-[var(--taali-text)]">
            <li>• Ask clear, structured questions to Claude.</li>
            <li>• Provide concrete context (code, files, errors) when you&apos;re stuck.</li>
            <li>• Work independently before escalating to AI.</li>
            <li>• Apply AI responses effectively and iterate with evidence.</li>
            <li>• Communicate reasoning, tradeoffs, and next-step judgment.</li>
          </ul>
          <p className="mt-3 text-xs text-[var(--taali-muted)]">
            TAALI evaluates your collaboration process with AI, not just the final output.
          </p>
        </div>

        {scenarioMarkdown ? (
          <div className="border-2 border-[var(--taali-border)] p-6 mb-8 bg-white">
            <h2 className="text-xl font-bold mb-3">Task scenario</h2>
            <div className="prose prose-sm max-w-none text-[var(--taali-text)] prose-headings:font-bold prose-code:font-mono">
              <ReactMarkdown>{scenarioMarkdown}</ReactMarkdown>
            </div>
          </div>
        ) : null}

        {expectedJourney ? (
          <div className="border-2 border-[var(--taali-border)] p-6 mb-8">
            <h2 className="text-xl font-bold mb-3">Expected working flow</h2>
            <div className="space-y-3">
              {Object.entries(expectedJourney).map(([phase, bullets]) => (
                <div key={phase}>
                  <div className="font-mono text-xs font-bold uppercase text-[var(--taali-muted)] mb-1">{phase.replace(/_/g, ' ')}</div>
                  <ul className="space-y-1">
                    {(Array.isArray(bullets) ? bullets : []).map((item) => (
                      <li key={`${phase}-${item}`} className="font-mono text-xs text-[var(--taali-text)]">• {item}</li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        <div className="grid md:grid-cols-3 gap-4 mb-8">
          <div className="border-2 border-[var(--taali-border)] p-6">
            <Terminal size={24} className="mb-3" />
            <h3 className="font-bold mb-2">What You&apos;ll Do</h3>
            <ul className="font-mono text-xs text-[var(--taali-muted)] space-y-1">
              <li>Complete a coding challenge</li>
              <li>Use AI tools as you normally would</li>
              <li>Write and run your solution</li>
            </ul>
          </div>
          <div className="border-2 border-[var(--taali-border)] p-6">
            <Brain size={24} className="mb-3" />
            <h3 className="font-bold mb-2">What We&apos;re Testing</h3>
            <ul className="font-mono text-xs text-[var(--taali-muted)] space-y-1">
              <li>Problem-solving approach</li>
              <li>AI collaboration skills</li>
              <li>Code quality & testing</li>
            </ul>
          </div>
          <div className="border-2 border-[var(--taali-border)] p-6">
            <Shield size={24} className="mb-3" />
            <h3 className="font-bold mb-2">What You&apos;ll Need</h3>
            <ul className="font-mono text-xs text-[var(--taali-muted)] space-y-1">
              <li>Desktop browser (Chrome/Firefox)</li>
              <li>Uninterrupted time</li>
              <li>Stable internet connection</li>
              <li>Read the task context before coding</li>
            </ul>
          </div>
        </div>

        {startError && (
          <div className="border-2 border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-4 mb-4 text-sm text-[var(--taali-danger)]">
            {startError}
          </div>
        )}

        <button
          className="w-full border-2 border-[var(--taali-border)] py-4 font-bold text-lg text-[var(--taali-surface)] bg-[var(--taali-purple)] hover:opacity-90 transition-colors flex items-center justify-center gap-2"
          onClick={handleStart}
          disabled={loadingStart}
        >
          {loadingStart ? (
            <><Loader2 size={20} className="animate-spin" /> Starting Assessment...</>
          ) : (
            <>Start Assessment <ChevronRight size={20} /></>
          )}
        </button>
      </div>
    </div>
  );
};
