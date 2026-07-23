import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Monitor,
  Shield,
  Sparkles,
  Wifi,
} from 'lucide-react';

import { assessments as assessmentsApi } from '../../shared/api';
import { CandidateMiniNav } from '../../shared/layout/TaaliLayout';
import { Spinner } from '../../shared/ui/TaaliPrimitives';
import {
  NO_AV_RECORDING_SENTENCE,
  WORKSPACE_SIGNAL_CAVEAT,
  WORKSPACE_SIGNAL_SENTENCE,
  WORK_RECORD_SENTENCE,
} from '../../shared/assessment/sessionDisclosure';
import { getOrCreateCandidateSessionKey } from './assessmentSessionBinding';
import {
  CandidateProofUnavailableError,
  rememberCandidateRuntime,
} from '../../shared/assessment/candidateProofBinding';

const CANDIDATE_START_BLOCKED_MESSAGE = 'This assessment is not available yet. Please contact the hiring team to continue.';

const InfoRow = ({ label, value }) => (
  <div className="rounded-[16px] border border-[var(--line)] bg-[var(--bg)] p-4">
    <div className="font-mono text-[0.6875rem] uppercase tracking-[0.12em] text-[var(--mute)]">{label}</div>
    <div className="mt-2 text-[0.875rem] font-medium text-[var(--ink-2)]">{value}</div>
  </div>
);

const getFirstName = (fullName) => {
  const first = String(fullName || '').trim().split(/\s+/)[0];
  return first || 'there';
};

// The preview API returns ``task.role`` as the slug we store in the DB
// (``data_engineer``, ``ai_engineer``, etc.) — a backend enum key, not
// a label. Render it as a human title (``Data Engineer``) for the
// candidate. Sam, 2026-05-26: "why is it data_engineer?" — yeah, slug
// leaking into UI.
const formatRoleLabel = (slug) => {
  const raw = String(slug || '').trim();
  if (!raw) return 'Engineering';
  return raw
    .replace(/[_-]+/g, ' ')
    .split(/\s+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(' ');
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

export const CandidateWelcomePage = ({ token, onNavigate, onStarted }) => {
  // Start in the loading state when we have a token to resolve, so the
  // page never first-paints a no-name / placeholder version. The whole
  // welcome page renders in one go once the preview lands (incl. "Hi Sam").
  const [previewLoading, setPreviewLoading] = useState(Boolean(token));
  const [previewData, setPreviewData] = useState(null);
  const [previewError, setPreviewError] = useState('');
  const [loadingStart, setLoadingStart] = useState(false);
  const [startError, setStartError] = useState('');
  const [systemCheck, setSystemCheck] = useState({
    browser: 'Checking...',
    connection: 'Checking...',
    screen: 'Checking...',
  });
  const mountedRef = useRef(false);
  const currentTokenRef = useRef(token);
  currentTokenRef.current = token;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

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
        }
      } catch (err) {
        if (!cancelled) {
          setPreviewData(null);
          const detail = err?.response?.data?.detail;
          setPreviewError(typeof detail === 'string' && detail.trim() ? detail : 'Task preview is not available yet.');
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
      screen: window.innerWidth < 1024
        ? 'Small screen · laptop or desktop recommended'
        : `${window.screen.width} × ${window.screen.height}`,
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
    const startedToken = token;
    const isCurrentStart = () => (
      mountedRef.current && currentTokenRef.current === startedToken
    );
    try {
      const candidateSessionKey = getOrCreateCandidateSessionKey(startedToken);
      const res = await assessmentsApi.start(startedToken, {
        candidate_session_key: candidateSessionKey,
      });
      if (!isCurrentStart()) return;
      const payload = res?.data || {};
      rememberCandidateRuntime(startedToken, payload.assessment_id);
      // Keep the invite only in React memory + tab-scoped recovery. The live
      // URL is deliberately token-free after the signed start succeeds.
      onStarted?.({ ...payload, token: startedToken });
      onNavigate?.('assessment', { assessmentToken: null, replace: true });
    } catch (err) {
      if (!isCurrentStart()) return;
      const detail = err?.response?.data?.detail;
      setStartError(
        err instanceof CandidateProofUnavailableError
          ? err.message
          : (typeof detail === 'string' && detail.trim() ? detail : 'Failed to start assessment.'),
      );
    } finally {
      if (isCurrentStart()) setLoadingStart(false);
    }
  };

  const visibleError = startError || (isStartBlocked ? startBlockedMessage : previewError);
  const metaTitle = [
    organizationName || 'TAALI',
    taskPreview?.role ? formatRoleLabel(taskPreview.role) : 'Candidate assessment',
    candidateName || null,
  ].filter(Boolean).join(' · ');

  // Render the page in ONE GO once the preview resolves: while it's in
  // flight, show a clean full-page loader rather than the page with a
  // blank/placeholder heading that then fades in. The candidate sees
  // nothing → the complete page with "Hi Sam …" already present.
  if (previewLoading) {
    return (
      <div className="min-h-screen bg-[var(--bg)] text-[var(--ink)]">
        <CandidateMiniNav />
        <div className="flex min-h-[60vh] items-center justify-center px-6">
          <div className="flex items-center gap-3 text-[var(--mute)]">
            <Spinner size={20} className="text-[var(--purple)]" />
            <span className="text-[0.9375rem]">Preparing your assessment…</span>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[var(--bg)] text-[var(--ink)]">
      <CandidateMiniNav />

      <div className="mx-auto max-w-[70rem] px-6 py-10 md:px-10 md:py-14">
        <div className="grid items-start gap-6 lg:grid-cols-[1.08fr_.92fr]">
          <div className="relative overflow-hidden rounded-[var(--radius-xl)] border border-[var(--line)] bg-[var(--bg-2)] p-8 shadow-[var(--shadow-lg)]">
            <div className="absolute right-[-3.75rem] top-[-3.75rem] h-56 w-56 rounded-full bg-[radial-gradient(circle,var(--purple-soft),transparent_68%)] opacity-80" />
            <div className="relative">
              <div className="kicker">{organizationName ? `Invited by ${organizationName}` : 'Candidate assessment'}</div>
              {/* The whole page only renders once the preview has
                  resolved (see the loader gate above), so the named
                  greeting is present on first paint — no placeholder,
                  no fade. The clamp bounds are in REM so the heading
                  scales with the global root font-size (80%) like the
                  rest of the page; px bounds stayed full-size and made
                  this heading oversized + wrap to 4 lines, stretching
                  the column (Sam, 2026-06-02). */}
              <h1 className="mt-4 font-[var(--font-display)] text-[clamp(2.25rem,4vw,3.25rem)] font-semibold leading-[0.98] tracking-[-0.04em]">
                {candidateName ? (
                  <>Hi {getFirstName(candidateName)} — ready to show your <em>work</em>?</>
                ) : (
                  <>Ready to show your <em>work</em>?</>
                )}
              </h1>
              <p className="mt-4 max-w-[38.75rem] text-[0.9375rem] leading-7 text-[var(--mute)]">
                This is a real engineering task, not a puzzle. You’ll work in a browser-based IDE with the same repo, runtime, and AI tooling your hiring team wants to evaluate. The brief opens when you click start.
              </p>

              <div className="mt-6 grid gap-4 md:grid-cols-3">
                <InfoRow label="Role" value={formatRoleLabel(taskPreview?.role)} />
                <InfoRow label="Duration" value={`${durationMinutes} min`} />
                <InfoRow label="Submit by" value={formatDeadline(previewData?.expires_at)} />
              </div>

              <div className="mt-6 space-y-3">
                {[
                  'A real prompt, not a riddle.',
                  'Work normally inside the workspace with Claude, an AI assistant, and the live repo. Copy and paste continue to work between its editor and chat.',
                  'You are scored on how you steer and the design decisions you make — not on whether you reach working code. The agent can write code; the judgment is yours to show.',
                  'The session transcript is reviewed — not your screen, mic, or camera.',
                ].map((item) => (
                  <div key={item} className="flex items-start gap-3 rounded-[14px] border border-[var(--line)] bg-[var(--bg)] px-4 py-3">
                    <CheckCircle2 size={18} className="mt-0.5 shrink-0 text-[var(--purple)]" />
                    <div className="text-[0.8125rem] leading-6 text-[var(--ink-2)]">{item}</div>
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
                      <Spinner size={18} className="!text-current" />
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
              <div className="font-mono text-[0.6875rem] uppercase tracking-[0.12em] text-[var(--purple-2)]">What to expect</div>
              <h2 className="mt-4 font-[var(--font-display)] text-[1.875rem] font-semibold leading-[1] tracking-[-0.03em]">
                Repo, editor, and Claude — all in one workspace.
              </h2>
              <p className="mt-4 text-[0.875rem] leading-7 text-white/72">
                {previewData?.allow_external_clipboard
                  ? 'Your approved clipboard accommodation is active. File import and switching to a different browser after starting remain restricted.'
                  : (
                    <>
                      Your files stay in this workspace. External paste, file import, and switching to a different browser after starting are restricted. If you need an accessibility accommodation, contact{' '}
                      <a className="underline underline-offset-2" href="mailto:support@taali.ai?subject=Assessment%20accessibility%20accommodation">
                        support@taali.ai
                      </a>{' '}
                      before starting.
                    </>
                  )}
              </p>
              <div className="mt-5 rounded-[14px] border border-white/10 bg-white/10 px-4 py-3 font-mono text-[0.6875rem] uppercase tracking-[0.12em] text-white/80">
                {metaTitle || 'Candidate workspace'}
              </div>
            </div>

            <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)]">
              <div className="text-[1.25rem] font-semibold tracking-[-0.02em]">System check</div>
              <div className="mt-4 space-y-3 text-[0.8125rem]">
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
              <div className="text-[1.25rem] font-semibold tracking-[-0.02em]">Your rights</div>
              <div className="mt-4 rounded-[14px] bg-[var(--bg-3)] p-4 text-[0.8125rem] leading-6 text-[var(--ink-2)]">
                <div className="flex items-start gap-3">
                  <Shield size={18} className="mt-0.5 shrink-0 text-[var(--purple)]" />
                  <div data-testid="welcome-recording-disclosure">
                    <p>{WORK_RECORD_SENTENCE}</p>
                    {previewData?.allow_external_clipboard ? null : (
                      <p className="mt-2">{WORKSPACE_SIGNAL_SENTENCE} {WORKSPACE_SIGNAL_CAVEAT}</p>
                    )}
                    <p className="mt-2">{NO_AV_RECORDING_SENTENCE}</p>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

      </div>

    </div>
  );
};

export default CandidateWelcomePage;
