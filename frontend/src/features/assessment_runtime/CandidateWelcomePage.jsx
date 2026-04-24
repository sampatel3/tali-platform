import React, { useEffect, useMemo, useState } from 'react';
import { Check, Loader2 } from 'lucide-react';
import ReactMarkdown from 'react-markdown';

import { assessments as assessmentsApi } from '../../shared/api';
import { CandidateMiniNav } from '../../shared/layout/TaaliLayout';

const CANDIDATE_START_BLOCKED_MESSAGE = 'This assessment is not available yet. Please contact the hiring team to continue.';

const safeBrowserLabel = () => {
  if (typeof navigator === 'undefined') return 'Browser ready';
  if (navigator.userAgent.includes('Chrome')) return 'Chrome';
  if (navigator.userAgent.includes('Safari')) return 'Safari';
  if (navigator.userAgent.includes('Firefox')) return 'Firefox';
  return 'Supported browser';
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
    void loadPreview();
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
      setStartError('Complete the 2-minute warmup prompt before starting.');
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
      await assessmentsApi.uploadCv(assessmentId || null, token, file);
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
  const visibleStartMessage = startError || startBlockedMessage || previewError;
  const startButtonDisabled = loadingStart || previewLoading || isStartBlocked;
  const roleLabel = taskPreview?.role || previewData?.role || 'Engineering';
  const dueLabel = useMemo(() => {
    const dueAt = previewData?.due_at || taskPreview?.due_at;
    if (!dueAt) return 'Open link deadline applies';
    const parsed = new Date(dueAt);
    if (Number.isNaN(parsed.getTime())) return 'Open link deadline applies';
    return parsed.toLocaleString();
  }, [previewData?.due_at, taskPreview?.due_at]);

  return (
    <div className="min-h-screen bg-[var(--bg)] text-[var(--ink)]">
      <CandidateMiniNav label="Candidate assessment · secure session" />

      <div className="mx-auto grid max-w-[1040px] gap-7 px-6 py-10 md:px-8 lg:grid-cols-[1.2fr_.8fr]">
        <div className="rounded-[var(--radius-xl)] border border-[var(--line)] bg-[var(--bg-2)] px-8 py-9 shadow-[var(--shadow-sm)]">
          <div className="kicker">INVITED BY THE HIRING TEAM</div>
          <h1 className="mt-4 font-[var(--font-display)] text-[46px] font-semibold leading-[1.02] tracking-[-0.035em]">
            Ready to <em>show your work</em>?
          </h1>
          <p className="mt-4 max-w-[560px] text-[17px] leading-[1.55] text-[var(--ink-2)]">
            This is a real engineering task, not a puzzle. You’ll work with Claude for up to {durationMinutes} minutes, and we care <em>how</em> you work with the AI, not just what you ship.
          </p>

          <div className="my-6 grid gap-4 border-y border-[var(--line-2)] py-4 md:grid-cols-3">
            <div>
              <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.1em] text-[var(--mute)]">Duration</div>
              <div className="mt-1 text-[17px] font-semibold">{durationMinutes} min</div>
            </div>
            <div>
              <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.1em] text-[var(--mute)]">Tools</div>
              <div className="mt-1 text-[17px] font-semibold">Claude · IDE · Docs</div>
            </div>
            <div>
              <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.1em] text-[var(--mute)]">Submit by</div>
              <div className="mt-1 text-[17px] font-semibold">{dueLabel}</div>
            </div>
          </div>

          <div>
            <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.12em] text-[var(--purple)]">What to expect</div>
            <div className="mt-4 space-y-4">
              {[
                ['A real prompt, not a riddle', 'You’ll get a repo, a scenario, and a task brief grounded in real engineering work.'],
                ['Work the way you normally do', 'Use Claude when it helps. Accept, reject, push back, and iterate as you would on the job.'],
                ['One session, one sitting', 'You can pause briefly if needed. We record the transcript, not your screen, camera, or microphone.'],
                ['Optional feedback at the end', 'A short feedback step helps improve the assessment and can unlock your candidate-facing summary.'],
              ].map(([title, body]) => (
                <div key={title} className="grid grid-cols-[22px_1fr] gap-4 border-b border-[var(--line-2)] pb-4 last:border-b-0 last:pb-0">
                  <div className="grid h-[22px] w-[22px] place-items-center rounded-full bg-[var(--purple-soft)] text-[var(--purple)]">
                    <Check size={12} strokeWidth={2.5} />
                  </div>
                  <div>
                    <div className="text-[14.5px] font-medium">{title}</div>
                    <div className="mt-1 text-[13px] leading-6 text-[var(--mute)]">{body}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {visibleStartMessage ? (
            <div className="mt-5 rounded-[14px] border border-[var(--taali-warning-border)] bg-[var(--taali-warning-soft)] p-4 text-sm text-[var(--ink-2)]">
              {visibleStartMessage}
            </div>
          ) : null}

          <div className="mt-6 flex flex-col gap-3">
            <button type="button" className="btn btn-purple btn-lg w-full justify-center" onClick={handleStart} disabled={startButtonDisabled}>
              {loadingStart ? (
                <>
                  <Loader2 size={16} className="animate-spin" /> Starting assessment…
                </>
              ) : isStartBlocked ? (
                'Assessment unavailable'
              ) : (
                <>
                  Start assessment <span className="arrow">→</span>
                </>
              )}
            </button>
            <button
              type="button"
              className="btn btn-outline btn-lg w-full justify-center"
              onClick={() => document.getElementById('task-brief')?.scrollIntoView({ behavior: 'smooth' })}
            >
              Review the task brief first
            </button>
          </div>
        </div>

        <div className="space-y-4">
          <div className="rounded-[var(--radius-lg)] bg-[var(--ink)] p-6 text-[var(--bg)] shadow-[var(--shadow-sm)]">
            <div className="kicker text-[var(--purple-2)]">APPLYING FOR</div>
            <h3 className="mt-3 font-[var(--font-display)] text-[22px] font-semibold tracking-[-0.02em]">{roleLabel}</h3>
            <p className="mt-2 text-[13px] leading-6 text-white/70">
              {taskPreview?.company_name || 'Taali hiring team'} · {taskPreview?.location || 'Remote friendly'} · {taskPreview?.seniority || 'Technical assessment'}
            </p>
            <div className="mt-4 flex gap-2">
              <span className="chip purple">Assessment {assessmentId ? `#${assessmentId}` : '1 of 1'}</span>
            </div>
          </div>

          <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)]">
            <h3 className="font-[var(--font-display)] text-[18px] font-semibold tracking-[-0.02em]">System <em>check</em></h3>
            <p className="mt-1 text-[12.5px] text-[var(--mute)]">We ran a quick check. You’re good to go.</p>
            <div className="mt-4 space-y-3 text-[13px]">
              {[
                ['Browser', safeBrowserLabel()],
                ['Connection', 'Ready'],
                ['Screen', typeof window !== 'undefined' ? `${window.innerWidth} × ${window.innerHeight}` : 'Ready'],
                ['Claude access', previewLoading ? 'Checking…' : 'Ready'],
              ].map(([label, value]) => (
                <div key={label} className="flex items-center justify-between border-b border-[var(--line-2)] pb-3 last:border-b-0 last:pb-0">
                  <span className="font-[var(--font-mono)] text-[11.5px] text-[var(--mute)]">{label}</span>
                  <span className="flex items-center gap-2 text-[var(--green)]"><Check size={12} strokeWidth={2.5} /> {value}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)]">
            <h3 className="font-[var(--font-display)] text-[18px] font-semibold tracking-[-0.02em]">Your <em>rights</em></h3>
            <div className="mt-4 rounded-[var(--radius)] bg-[var(--bg-3)] p-4 text-[12.5px] leading-6 text-[var(--ink-2)]">
              We record what you prompted, what Claude said, and what you accepted or edited. We do not record your screen, microphone, or camera.
              <br />
              <br />
              Your session transcript is visible only to the hiring team. You can request deletion of your data at any time.
            </div>
          </div>
        </div>
      </div>

      <div className="mx-auto max-w-[1040px] space-y-6 px-6 pb-16 md:px-8">
        {!hasCvOnFile ? (
          <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)]">
            <h2 className="font-[var(--font-display)] text-[22px] font-semibold tracking-[-0.02em]">Optional: upload your <em>CV</em>.</h2>
            <p className="mt-2 text-[14px] leading-7 text-[var(--mute)]">Uploading a CV helps role-fit analysis. You can still continue without it.</p>
            <input type="file" accept=".pdf,.doc,.docx" onChange={handleCvUpload} disabled={cvUploading} className="mt-4 block w-full text-sm" />
            {cvUploading ? <p className="mt-2 text-xs text-[var(--mute)]">Uploading…</p> : null}
            {cvUploadSuccess ? <p className="mt-2 text-xs text-[var(--green)]">{cvUploadSuccess}</p> : null}
            {cvUploadError ? <p className="mt-2 text-xs text-[var(--red)]">{cvUploadError}</p> : null}
          </div>
        ) : null}

        {taskPreview?.calibration_enabled && calibrationPromptText ? (
          <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)]">
            <h2 className="font-[var(--font-display)] text-[22px] font-semibold tracking-[-0.02em]">2-minute <em>warmup</em>.</h2>
            <p className="mt-2 text-[14px] leading-7 text-[var(--mute)]">This captures your baseline AI-collaboration style before the main task.</p>
            <div className="mt-4 rounded-[14px] border border-[var(--line)] bg-[var(--bg)] p-4 font-[var(--font-mono)] text-xs leading-6 text-[var(--ink-2)]">
              {calibrationPromptText}
            </div>
            <label className="field mt-4">
              <span className="k">Your prompt to Claude</span>
              <textarea
                className="min-h-[120px]"
                value={warmupPrompt}
                onChange={(event) => setWarmupPrompt(event.target.value)}
                placeholder="Write the prompt you would send to Claude for this warmup..."
              />
            </label>
          </div>
        ) : null}

        {scenarioMarkdown ? (
          <div id="task-brief" className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)]">
            <h2 className="font-[var(--font-display)] text-[22px] font-semibold tracking-[-0.02em]">Task <em>brief</em>.</h2>
            <div className="prose prose-sm mt-4 max-w-none text-[var(--ink)] prose-headings:font-semibold prose-p:text-[var(--ink-2)] prose-li:text-[var(--ink-2)] prose-strong:text-[var(--ink)] prose-code:font-mono">
              <ReactMarkdown>{scenarioMarkdown}</ReactMarkdown>
            </div>
          </div>
        ) : null}

        {expectedJourney ? (
          <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)]">
            <h2 className="font-[var(--font-display)] text-[22px] font-semibold tracking-[-0.02em]">Expected working <em>flow</em>.</h2>
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              {Object.entries(expectedJourney).map(([phase, bullets]) => (
                <div key={phase} className="rounded-[14px] border border-[var(--line)] bg-[var(--bg)] p-4">
                  <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.1em] text-[var(--purple)]">{phase.replace(/_/g, ' ')}</div>
                  <ul className="mt-3 space-y-2 text-[13px] leading-6 text-[var(--ink-2)]">
                    {(Array.isArray(bullets) ? bullets : []).map((bullet) => <li key={bullet}>• {bullet}</li>)}
                  </ul>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
};

export default CandidateWelcomePage;
