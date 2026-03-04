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
import { BrandLabel, Logo } from '../../shared/ui/Branding';

const CANDIDATE_START_BLOCKED_MESSAGE = 'This assessment is not available yet. Please contact the hiring team to continue.';

const SCENARIO_MARKDOWN_COMPONENTS = {
  h1: ({ children }) => <h3 className="mt-6 text-xl font-semibold text-[var(--taali-text)] first:mt-0">{children}</h3>,
  h2: ({ children }) => <h3 className="mt-6 text-xl font-semibold text-[var(--taali-text)] first:mt-0">{children}</h3>,
  h3: ({ children }) => <h4 className="mt-5 text-lg font-semibold text-[var(--taali-text)] first:mt-0">{children}</h4>,
  p: ({ children }) => <p className="whitespace-pre-line text-base leading-8 text-[var(--taali-text)] [&:not(:first-child)]:mt-4">{children}</p>,
  ul: ({ children }) => <ul className="mt-4 list-disc space-y-3 rounded-[var(--taali-radius-card)] bg-[var(--taali-surface-subtle)] p-4 pl-9">{children}</ul>,
  ol: ({ children }) => <ol className="mt-4 list-decimal space-y-3 rounded-[var(--taali-radius-card)] bg-[var(--taali-surface-subtle)] p-4 pl-9">{children}</ol>,
  li: ({ children }) => <li className="whitespace-pre-line pl-1 text-base leading-7 text-[var(--taali-text)] marker:text-[var(--taali-purple)]">{children}</li>,
  blockquote: ({ children }) => (
    <blockquote className="mt-4 rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface-subtle)] px-4 py-4 text-base leading-8 text-[var(--taali-text)]">
      {children}
    </blockquote>
  ),
  strong: ({ children }) => <strong className="font-semibold text-[var(--taali-text)]">{children}</strong>,
  em: ({ children }) => <em className="italic text-[var(--taali-text)]">{children}</em>,
  code: ({ children }) => (
    <code className="rounded-md bg-[var(--taali-surface-subtle)] px-1.5 py-0.5 font-mono text-[0.9em] text-[var(--taali-text)]">
      {children}
    </code>
  ),
};

export const CandidateWelcomePage = ({ token, assessmentId, onNavigate, onStarted }) => {
  const [loadingStart, setLoadingStart] = useState(false);
  const [startError, setStartError] = useState('');
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewData, setPreviewData] = useState(null);
  const [previewError, setPreviewError] = useState('');
  const [warmupPrompt, setWarmupPrompt] = useState('');
  const [cvUploading, setCvUploading] = useState(false);
  const [cvUploadError, setCvUploadError] = useState('');
  const [cvUploadSuccess, setCvUploadSuccess] = useState('');
  const [hasCvOnFile, setHasCvOnFile] = useState(false);

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
        setHasCvOnFile(Boolean(res?.data?.task?.has_cv_on_file));
      } catch (err) {
        if (cancelled) return;
        setPreviewData(null);
        setPreviewError(err?.response?.data?.detail || 'Task preview is not available yet.');
        setHasCvOnFile(false);
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
    if (isStartBlocked) {
      setStartError(startBlockedMessage || CANDIDATE_START_BLOCKED_MESSAGE);
      return;
    }
    const requiresWarmup = Boolean(taskPreview?.calibration_enabled);
    const warmupText = String(warmupPrompt || '').trim();
    if (requiresWarmup && !warmupText) {
      setStartError('Write the short baseline Claude prompt before starting.');
      return;
    }
    setLoadingStart(true);
    setStartError('');
    try {
      const res = await assessmentsApi.start(token, {
        calibration_warmup_prompt: warmupText || undefined,
      });
      const data = res.data;
      if (onStarted) onStarted(data);
      onNavigate('assessment');
    } catch (err) {
      const msg = err?.response?.status === 402
        ? (err?.response?.data?.detail || CANDIDATE_START_BLOCKED_MESSAGE)
        : (err?.response?.data?.detail || 'Failed to start assessment');
      setStartError(msg);
    } finally {
      setLoadingStart(false);
    }
  };

  const handleCvUpload = async (event) => {
    const file = event?.target?.files?.[0];
    if (!file || !token) return;
    setCvUploading(true);
    setCvUploadError('');
    setCvUploadSuccess('');
    try {
      await assessmentsApi.uploadCv(null, token, file);
      setHasCvOnFile(true);
      setCvUploadSuccess(`Uploaded ${file.name}.`);
    } catch (err) {
      setCvUploadError(err?.response?.data?.detail || 'Failed to upload CV.');
    } finally {
      setCvUploading(false);
      if (event?.target) event.target.value = '';
    }
  };

  const taskPreview = previewData?.task || {};
  const scenarioMarkdown = String(taskPreview?.scenario || '').trim();
  const calibrationPromptText = String(taskPreview?.calibration_prompt || '').trim();
  const expectedJourney = taskPreview?.expected_candidate_journey && typeof taskPreview.expected_candidate_journey === 'object'
    ? taskPreview.expected_candidate_journey
    : null;
  const durationMinutes = Number(taskPreview?.duration_minutes ?? previewData?.duration_minutes ?? 30);
  const startGate = previewData?.start_gate && typeof previewData.start_gate === 'object'
    ? previewData.start_gate
    : null;
  const startBlockedMessage = String(startGate?.message || '').trim();
  const isStartBlocked = startGate?.can_start === false;
  const visibleStartMessage = startError || startBlockedMessage;

  return (
    <div className="min-h-screen bg-[var(--taali-bg)] text-[var(--taali-text)]">
      <nav className="border-b border-[var(--taali-border-soft)] bg-[var(--taali-surface)] backdrop-blur-sm">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center gap-4">
          <Logo onClick={() => {}} />
          <span className="font-mono text-sm text-[var(--taali-muted)]">|</span>
          <span className="font-mono text-sm">Technical Assessment</span>
        </div>
      </nav>
      <div className="max-w-3xl mx-auto px-6 py-16">
        <div className="text-center mb-8">
          <BrandLabel className="mb-4" toneClassName="text-[var(--taali-purple)]">TAALI Assessment</BrandLabel>
          <h1 className="text-4xl font-bold mb-2">Technical Assessment</h1>
          <p className="text-[var(--taali-muted)]">You&apos;ve been invited to complete a coding challenge</p>
        </div>

        <div className="mb-6 rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-5 shadow-[var(--taali-shadow-soft)]">
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
          {hasCvOnFile ? (
            <p className="mt-2 text-xs text-[var(--taali-success)]">CV on file: yes</p>
          ) : null}
        </div>

        <div className="mb-8 rounded-[var(--taali-radius-panel)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-8 shadow-[var(--taali-shadow-soft)]">
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

        <div className="mb-8 rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-purple-soft)] p-6 shadow-[var(--taali-shadow-soft)]">
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

        {!hasCvOnFile ? (
          <div className="mb-8 rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-6 shadow-[var(--taali-shadow-soft)]">
            <h2 className="text-xl font-bold mb-2">Optional: Upload your CV</h2>
            <p className="text-sm text-[var(--taali-muted)] mb-3">
              Uploading a CV helps role-fit analysis. You can still continue without it.
            </p>
            <input
              type="file"
              accept=".pdf,.doc,.docx"
              onChange={handleCvUpload}
              disabled={cvUploading}
              className="block w-full text-sm text-[var(--taali-text)]"
            />
            {cvUploading ? <p className="mt-2 font-mono text-xs text-[var(--taali-muted)]">Uploading…</p> : null}
            {cvUploadSuccess ? <p className="mt-2 font-mono text-xs text-[var(--taali-success)]">{cvUploadSuccess}</p> : null}
            {cvUploadError ? <p className="mt-2 font-mono text-xs text-[var(--taali-danger)]">{cvUploadError}</p> : null}
          </div>
        ) : null}

        {taskPreview?.calibration_enabled && calibrationPromptText ? (
          <div className="mb-8 rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface-muted)] p-6 shadow-[var(--taali-shadow-soft)]">
            <h2 className="mb-2 text-xl font-bold">Quick baseline prompt (about 2 minutes)</h2>
            <p className="mb-4 text-sm leading-6 text-[var(--taali-muted)]">
              Before the repo opens, we ask for one short Claude prompt so TAALI can compare your initial prompting style with how you work once the real task context is available.
            </p>
            <div className="mb-4 grid gap-3 md:grid-cols-3">
              {[
                ['What it is', 'A short prompt-only warmup, not a separate coding exercise.'],
                ['Why it exists', 'It gives us a baseline for how you frame a problem before you have full repo context.'],
                ['How to treat it', 'Keep it concise. The main assessment still carries the weight.'],
              ].map(([title, text]) => (
                <div key={title} className="rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] px-4 py-3">
                  <div className="font-mono text-[11px] font-bold uppercase tracking-[0.12em] text-[var(--taali-muted)]">{title}</div>
                  <p className="mt-2 text-sm leading-6 text-[var(--taali-text)]">{text}</p>
                </div>
              ))}
            </div>
            <div className="mb-3 rounded-[var(--taali-radius-control)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-3 font-mono text-xs text-[var(--taali-text)]">
              {calibrationPromptText}
            </div>
            <label className="block">
              <span className="mb-1 block font-mono text-xs text-[var(--taali-muted)]">Draft the first prompt you would send Claude</span>
              <textarea
                className="taali-textarea min-h-[110px] w-full bg-[var(--taali-surface)] font-mono text-sm text-[var(--taali-text)] outline-none"
                value={warmupPrompt}
                onChange={(event) => setWarmupPrompt(event.target.value)}
                placeholder="Write the short prompt you would send Claude before the main task opens..."
              />
            </label>
          </div>
        ) : null}

        {scenarioMarkdown ? (
          <div className="mb-8 rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-6 shadow-[var(--taali-shadow-soft)]">
            <h2 className="text-xl font-bold mb-3">Task scenario</h2>
            <p className="mb-4 text-sm leading-6 text-[var(--taali-muted)]">
              This is the operating context waiting for you when the timer starts. Read it like a real handoff from the team you just joined.
            </p>
            <div className="max-w-none">
              <ReactMarkdown components={SCENARIO_MARKDOWN_COMPONENTS}>{scenarioMarkdown}</ReactMarkdown>
            </div>
          </div>
        ) : null}

        {expectedJourney ? (
          <div className="mb-8 rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-6 shadow-[var(--taali-shadow-soft)]">
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
          <div className="rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-6 shadow-[var(--taali-shadow-soft)]">
            <Terminal size={24} className="mb-3" />
            <h3 className="font-bold mb-2">What You&apos;ll Do</h3>
            <ul className="font-mono text-xs text-[var(--taali-muted)] space-y-1">
              <li>Complete a coding challenge</li>
              <li>Use AI tools as you normally would</li>
              <li>Write and run your solution</li>
            </ul>
          </div>
          <div className="rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-6 shadow-[var(--taali-shadow-soft)]">
            <Brain size={24} className="mb-3" />
            <h3 className="font-bold mb-2">What We&apos;re Testing</h3>
            <ul className="font-mono text-xs text-[var(--taali-muted)] space-y-1">
              <li>Problem-solving approach</li>
              <li>AI collaboration skills</li>
              <li>Code quality & testing</li>
            </ul>
          </div>
          <div className="rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-6 shadow-[var(--taali-shadow-soft)]">
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

        {visibleStartMessage && (
          <div className="mb-4 rounded-[var(--taali-radius-card)] border border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-4 text-sm text-[var(--taali-danger)]">
            {visibleStartMessage}
          </div>
        )}

        <button
          className="flex w-full items-center justify-center gap-2 rounded-[var(--taali-radius-control)] border border-[var(--taali-purple)] bg-[linear-gradient(135deg,var(--taali-purple),var(--taali-purple-hover))] py-4 text-lg font-bold text-white shadow-[var(--taali-shadow-soft)] transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
          onClick={handleStart}
          disabled={loadingStart || isStartBlocked}
        >
          {loadingStart ? (
            <><Loader2 size={20} className="animate-spin" /> Starting Assessment...</>
          ) : isStartBlocked ? (
            'Assessment unavailable'
          ) : (
            <>Start Assessment <ChevronRight size={20} /></>
          )}
        </button>
      </div>
    </div>
  );
};
