import React, { useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Loader2,
  Monitor,
  Shield,
  Sparkles,
  UploadCloud,
  Wifi,
} from 'lucide-react';

import { assessments as assessmentsApi } from '../../shared/api';
import { CandidateMiniNav } from '../../shared/layout/TaaliLayout';

const CANDIDATE_START_BLOCKED_MESSAGE = 'This assessment is not available yet. Please contact the hiring team to continue.';

const InfoRow = ({ label, value }) => (
  <div className="rounded-[16px] border border-[var(--line)] bg-[var(--bg)] p-4">
    <div className="font-mono text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)]">{label}</div>
    <div className="mt-2 text-[14px] font-medium text-[var(--ink-2)]">{value}</div>
  </div>
);

const getFirstName = (fullName) => {
  const first = String(fullName || '').trim().split(/\s+/)[0];
  return first || 'there';
};

const formatDeadline = (value) => {
  if (!value) return 'No hard deadline listed';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'No hard deadline listed';
  return new Intl.DateTimeFormat(undefined, {
    weekday: 'short',
    month: 'short',
    day: '2-digit',
    hour: 'numeric',
    minute: '2-digit',
  }).format(date);
};

const detectBrowser = (userAgent) => {
  const ua = String(userAgent || '');
  if (/Edg\//i.test(ua)) return 'Microsoft Edge';
  if (/Chrome\//i.test(ua) && !/Edg\//i.test(ua)) return 'Google Chrome';
  if (/Firefox\//i.test(ua)) return 'Mozilla Firefox';
  if (/Safari\//i.test(ua) && !/Chrome\//i.test(ua)) return 'Safari';
  return 'Compatible browser';
};

export const CandidateWelcomePage = ({ token, assessmentId, onNavigate, onStarted }) => {
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewData, setPreviewData] = useState(null);
  const [previewError, setPreviewError] = useState('');
  const [loadingStart, setLoadingStart] = useState(false);
  const [startError, setStartError] = useState('');
  const [cvUploading, setCvUploading] = useState(false);
  const [cvUploadError, setCvUploadError] = useState('');
  const [cvUploadSuccess, setCvUploadSuccess] = useState('');
  const [hasCvOnFile, setHasCvOnFile] = useState(false);
  const [systemCheck, setSystemCheck] = useState({
    browser: 'Checking...',
    connection: 'Checking...',
    screen: 'Checking...',
  });

  useEffect(() => {
    let cancelled = false;

    const loadPreview = async () => {
      if (!token) return;
      setPreviewLoading(true);
      setPreviewError('');
      try {
        const res = await assessmentsApi.preview(token);
        if (!cancelled) {
          setPreviewData(res?.data || null);
          setHasCvOnFile(Boolean(res?.data?.task?.has_cv_on_file));
        }
      } catch (err) {
        if (!cancelled) {
          setPreviewData(null);
          setHasCvOnFile(false);
          setPreviewError(err?.response?.data?.detail || 'Task preview is not available yet.');
        }
      } finally {
        if (!cancelled) setPreviewLoading(false);
      }
    };

    void loadPreview();
    return () => {
      cancelled = true;
    };
  }, [token]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    setSystemCheck({
      browser: detectBrowser(window.navigator.userAgent),
      connection: connection?.downlink
        ? `${connection.effectiveType ? `${String(connection.effectiveType).toUpperCase()} · ` : ''}${connection.downlink.toFixed(0)} Mbps`
        : 'Stable connection detected',
      screen: `${window.screen.width} × ${window.screen.height}`,
    });
  }, []);

  const taskPreview = previewData?.task || {};
  const startGate = previewData?.start_gate && typeof previewData.start_gate === 'object'
    ? previewData.start_gate
    : null;
  const isStartBlocked = startGate?.can_start === false;
  const startBlockedMessage = String(startGate?.message || '').trim() || CANDIDATE_START_BLOCKED_MESSAGE;
  const durationMinutes = Number(taskPreview?.duration_minutes ?? previewData?.duration_minutes ?? 30);
  const candidateName = String(previewData?.candidate_name || '').trim();
  const organizationName = String(previewData?.organization_name || '').trim();

  const startButtonLabel = useMemo(() => {
    if (loadingStart) return 'Starting assessment...';
    if (isStartBlocked) return 'Assessment unavailable';
    return 'Start assessment';
  }, [isStartBlocked, loadingStart]);

  const handleStart = async () => {
    if (!token) {
      setStartError('Assessment token is missing from the link.');
      return;
    }
    if (isStartBlocked) {
      setStartError(startBlockedMessage);
      return;
    }

    setLoadingStart(true);
    setStartError('');
    try {
      const res = await assessmentsApi.start(token);
      const payload = res?.data || {};
      onStarted?.(payload);
      onNavigate?.('assessment');
    } catch (err) {
      setStartError(err?.response?.data?.detail || 'Failed to start assessment.');
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
      await assessmentsApi.uploadCv(assessmentId, token, file);
      setHasCvOnFile(true);
      setCvUploadSuccess(`Uploaded ${file.name}.`);
    } catch (err) {
      setCvUploadError(err?.response?.data?.detail || 'Failed to upload CV.');
    } finally {
      setCvUploading(false);
      if (event?.target) event.target.value = '';
    }
  };

  const visibleError = startError || (isStartBlocked ? startBlockedMessage : previewError);
  const metaTitle = [
    organizationName || 'TAALI',
    taskPreview?.role || 'Candidate assessment',
    candidateName || null,
  ].filter(Boolean).join(' · ');

  return (
    <div className="min-h-screen bg-[var(--bg)] text-[var(--ink)]">
      <CandidateMiniNav />

      <div className="mx-auto max-w-[1120px] px-6 py-10 md:px-10 md:py-14">
        <div className="grid gap-6 lg:grid-cols-[1.08fr_.92fr]">
          <div className="relative overflow-hidden rounded-[var(--radius-xl)] border border-[var(--line)] bg-[var(--bg-2)] p-8 shadow-[var(--shadow-lg)]">
            <div className="absolute right-[-60px] top-[-60px] h-56 w-56 rounded-full bg-[radial-gradient(circle,var(--purple-soft),transparent_68%)] opacity-80" />
            <div className="relative">
              <div className="kicker">{organizationName ? `Invited by ${organizationName}` : 'Candidate assessment'}</div>
              <h1 className="mt-4 font-[var(--font-display)] text-[clamp(42px,5vw,64px)] leading-[0.96] tracking-[-0.04em]">
                Hi {getFirstName(candidateName)} - ready to show your <em>work</em>?
              </h1>
              <p className="mt-4 max-w-[620px] text-[15px] leading-7 text-[var(--mute)]">
                This is a real engineering task, not a puzzle. You’ll work in a browser-based IDE with the same repo, runtime, and AI tooling your hiring team wants to evaluate. The brief opens when you click start.
              </p>

              <div className="mt-6 grid gap-4 md:grid-cols-3">
                <InfoRow label="Role" value={taskPreview?.role || 'Engineering'} />
                <InfoRow label="Duration" value={`${durationMinutes} min`} />
                <InfoRow label="Submit by" value={formatDeadline(previewData?.expires_at)} />
              </div>

              <div className="mt-6 space-y-3">
                {[
                  'A real prompt, not a riddle.',
                  'Work the way you normally do with Claude and the live repo.',
                  'We evaluate how you collaborate with AI, not just the final answer.',
                  'The session transcript is reviewed - not your screen, mic, or camera.',
                ].map((item) => (
                  <div key={item} className="flex items-start gap-3 rounded-[14px] border border-[var(--line)] bg-[var(--bg)] px-4 py-3">
                    <CheckCircle2 size={18} className="mt-0.5 shrink-0 text-[var(--purple)]" />
                    <div className="text-[13px] leading-6 text-[var(--ink-2)]">{item}</div>
                  </div>
                ))}
              </div>

              {visibleError ? (
                <div className="mt-6 rounded-[14px] border border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-4 text-sm text-[var(--taali-danger)]">
                  <div className="flex items-start gap-3">
                    <AlertTriangle size={18} className="mt-0.5 shrink-0" />
                    <div>{visibleError}</div>
                  </div>
                </div>
              ) : null}

              <div className="mt-6">
                <button
                  type="button"
                  className="btn btn-primary btn-lg w-full justify-center disabled:cursor-not-allowed disabled:opacity-60"
                  onClick={handleStart}
                  disabled={loadingStart || isStartBlocked}
                >
                  {loadingStart ? (
                    <>
                      <Loader2 size={18} className="animate-spin" />
                      {startButtonLabel}
                    </>
                  ) : (
                    <>
                      {startButtonLabel} {!isStartBlocked ? <ChevronRight size={18} /> : null}
                    </>
                  )}
                </button>
              </div>
            </div>
          </div>

          <div className="space-y-4">
            <div className="rounded-[var(--radius-xl)] bg-[var(--ink)] p-6 text-[var(--bg)] shadow-[var(--shadow-lg)]">
              <div className="font-mono text-[11px] uppercase tracking-[0.08em] text-[var(--purple-2)]">What to expect</div>
              <h2 className="mt-4 font-[var(--font-display)] text-[30px] leading-[1] tracking-[-0.03em]">
                Repo, editor, and Claude - all in one workspace.
              </h2>
              <p className="mt-4 text-[14px] leading-7 text-white/72">
                We record prompts, accept/reject decisions, and validation runs so the hiring team can review your process with context.
              </p>
              <div className="mt-5 rounded-[14px] border border-white/10 bg-white/10 px-4 py-3 font-mono text-[11px] uppercase tracking-[0.08em] text-white/80">
                {metaTitle || 'Candidate workspace'}
              </div>
            </div>

            {!hasCvOnFile ? (
              <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)]">
                <div className="text-[20px] font-semibold tracking-[-0.02em]">Optional CV upload</div>
                <p className="mt-2 text-[13px] leading-6 text-[var(--mute)]">
                  Uploading your CV helps the role-fit analysis. You can still continue without it.
                </p>
                <label className="mt-4 flex cursor-pointer items-center justify-center gap-2 rounded-[14px] border border-dashed border-[var(--line)] bg-[var(--bg)] px-4 py-4 text-[13px] font-medium text-[var(--ink-2)] transition-colors hover:border-[var(--purple)] hover:text-[var(--purple)]">
                  <UploadCloud size={16} />
                  <span>{cvUploading ? 'Uploading...' : 'Choose PDF, DOC, or DOCX'}</span>
                  <input type="file" accept=".pdf,.doc,.docx" onChange={handleCvUpload} disabled={cvUploading} className="hidden" />
                </label>
                {cvUploadSuccess ? <div className="mt-3 font-mono text-[11px] text-[var(--green)]">{cvUploadSuccess}</div> : null}
                {cvUploadError ? <div className="mt-3 font-mono text-[11px] text-[var(--red)]">{cvUploadError}</div> : null}
              </div>
            ) : null}

            <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)]">
              <div className="text-[20px] font-semibold tracking-[-0.02em]">System check</div>
              <div className="mt-4 space-y-3 text-[13px]">
                {[
                  ['Browser', systemCheck.browser, Monitor],
                  ['Connection', systemCheck.connection, Wifi],
                  ['Screen', systemCheck.screen, Monitor],
                  ['Claude access', isStartBlocked ? 'Blocked' : 'Ready', Sparkles],
                ].map(([label, value, Icon]) => (
                  <div key={label} className="flex items-center justify-between border-b border-[var(--line-2)] pb-3 last:border-b-0 last:pb-0">
                    <span className="text-[var(--mute)]">{label}</span>
                    <span className="inline-flex items-center gap-2 text-[var(--ink-2)]">
                      <Icon size={14} />
                      {value}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)]">
              <div className="text-[20px] font-semibold tracking-[-0.02em]">Your rights</div>
              <div className="mt-4 rounded-[14px] bg-[var(--bg-3)] p-4 text-[13px] leading-6 text-[var(--ink-2)]">
                <div className="flex items-start gap-3">
                  <Shield size={18} className="mt-0.5 shrink-0 text-[var(--purple)]" />
                  <div>
                    We record your prompts, Claude responses, accepted edits, and validation runs. We do not record your screen, microphone, or camera.
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        {(previewLoading || previewError) ? (
          <div className="mt-6 rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-5 shadow-[var(--shadow-sm)]">
            <div className="font-mono text-[12px] text-[var(--mute)]">
              {previewLoading ? 'Loading task preview...' : previewError}
            </div>
          </div>
        ) : null}
      </div>

    </div>
  );
};

export default CandidateWelcomePage;
