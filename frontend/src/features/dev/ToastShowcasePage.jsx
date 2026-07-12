import React, { useState } from 'react';
import { CheckCircle2, Loader2, X } from 'lucide-react';

import { useToast } from '../../context/ToastContext';

/**
 * ToastShowcasePage
 *
 * Internal review surface for every toast / notification variant in the
 * codebase. Routed at /dev/toasters. Two goals:
 *
 *   1. See every variant — the lightweight ToastContext popups (3 types,
 *      167 call sites) and the persistent BackgroundJobsToaster (5 states
 *      across 5 job kinds).
 *   2. Compare the current Tailwind-color implementation against the
 *      Taali design tokens (--taali-success/--taali-danger/--taali-info
 *      already exist in index.css) so we can decide what to standardise on.
 *
 * Not deployed to recruiters — accessed manually for design review.
 */
export const ToastShowcasePage = () => {
  const { showToast } = useToast();

  return (
    <div className="min-h-screen bg-[var(--bg)] text-[var(--ink)]">
      <div className="mx-auto max-w-5xl px-6 py-10 space-y-12">
        <Header />
        <DesignTokensSection />
        <LiveToastsSection showToast={showToast} />
        <ToastVariantComparisonSection />
        <BackgroundJobsToasterSection />
        <CallSiteInventorySection />
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

const Header = () => (
  <header className="border-b border-[var(--taali-border)] pb-6">
    <p className="text-xs uppercase tracking-wider text-[var(--ink-soft)]">
      Design review · /dev/toasters
    </p>
    <h1 className="mt-1 text-2xl font-semibold text-[var(--ink)]">
      Toaster system
    </h1>
    <p className="mt-2 max-w-2xl text-sm text-[var(--ink-2)]">
      One central toaster (3 variants, 167 call sites) plus the persistent
      BackgroundJobsToaster (4 job kinds, 5 states each). Below: live demo,
      side-by-side comparison of the current Tailwind colours vs the Taali
      semantic tokens already defined in index.css, every BackgroundJobsToaster
      state, and a per-feature call-site count.
    </p>
  </header>
);

// ---------------------------------------------------------------------------
// Design tokens
// ---------------------------------------------------------------------------

const TOKEN_GROUPS = [
  {
    label: 'Success',
    tokens: [
      ['--taali-success', '#15a36a (var(--green))'],
      ['--taali-success-soft', 'mix(green 10%, bg-2)'],
      ['--taali-success-border', 'mix(green 28%, line)'],
    ],
  },
  {
    label: 'Danger / Error',
    tokens: [
      ['--taali-danger', '#e64a4a (var(--red))'],
      ['--taali-danger-soft', 'mix(red 10%, bg-2)'],
      ['--taali-danger-border', 'mix(red 28%, line)'],
    ],
  },
  {
    label: 'Warning',
    tokens: [
      ['--taali-warning', '#d88a1c (var(--amber))'],
      ['--taali-warning-soft', 'mix(amber 12%, bg-2)'],
      ['--taali-warning-border', 'mix(amber 28%, line)'],
    ],
  },
  {
    label: 'Info / Neutral',
    tokens: [
      ['--taali-info', '#5e3aa8 (var(--purple))'],
      ['--taali-info-soft', 'mix(purple 10%, bg-2)'],
      ['--taali-info-border', 'mix(purple 28%, line)'],
    ],
  },
];

const DesignTokensSection = () => (
  <Section
    title="Taali semantic tokens"
    subtitle="Defined in index.css. The current toaster bypasses these and uses raw Tailwind colours (border-red-300 bg-red-50). Switching to these tokens is a one-line change in ToastContext."
  >
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      {TOKEN_GROUPS.map((group) => (
        <div
          key={group.label}
          className="rounded-lg border border-[var(--taali-border)] bg-white p-4"
        >
          <p className="text-sm font-medium text-[var(--ink)]">{group.label}</p>
          <div className="mt-3 space-y-2">
            {group.tokens.map(([name, note]) => (
              <div key={name} className="flex items-center gap-3">
                <span
                  className="h-6 w-6 rounded border border-[var(--taali-border)]"
                  style={{ background: `var(${name})` }}
                />
                <code className="text-xs text-[var(--ink-2)]">{name}</code>
                <span className="text-xs text-[var(--ink-soft)]">{note}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  </Section>
);

// ---------------------------------------------------------------------------
// Live toasts (uses real ToastContext)
// ---------------------------------------------------------------------------

const LIVE_EXAMPLES = [
  { label: 'Success', message: 'Pipeline stage updated', type: 'success' },
  { label: 'Error', message: 'Failed to load role tasks', type: 'error' },
  { label: 'Info', message: 'Graph is already up to date', type: 'info' },
  { label: 'Long success', message: 'Bulk reject finished. 17/20 updated, 3 failed (Workable disqualify failed for 2; candidate already closed for 1).', type: 'success' },
  { label: 'Warning (unused today)', message: 'Workable token expires in 3 days — reconnect to keep sync running.', type: 'warning' },
];

const LiveToastsSection = ({ showToast }) => (
  <Section
    title="Live toasts"
    subtitle="Click each button to fire the real ToastContext popup. Auto-dismisses after 5 s; bottom-right of the viewport. Type 'warning' isn't called anywhere in the codebase — listed here for completeness."
  >
    <div className="flex flex-wrap gap-2">
      {LIVE_EXAMPLES.map((ex) => (
        <button
          key={ex.label}
          type="button"
          onClick={() => showToast(ex.message, ex.type)}
          className="taali-btn taali-btn-secondary taali-btn-sm"
        >
          Fire: {ex.label}
        </button>
      ))}
    </div>
  </Section>
);

// ---------------------------------------------------------------------------
// Side-by-side: current vs proposed
// ---------------------------------------------------------------------------

const VARIANTS = [
  {
    type: 'success',
    label: 'Success',
    sample: 'Pipeline stage updated',
    current: 'border-red border-2 rounded-lg bg-green-50 border-green-300 text-green-900',
    proposedBg: 'var(--taali-success-soft)',
    proposedBorder: 'var(--taali-success-border)',
    proposedText: 'var(--taali-success)',
  },
  {
    type: 'error',
    label: 'Error',
    sample: 'Failed to load role tasks',
    proposedBg: 'var(--taali-danger-soft)',
    proposedBorder: 'var(--taali-danger-border)',
    proposedText: 'var(--taali-danger)',
  },
  {
    type: 'warning',
    label: 'Warning',
    sample: 'Workable token expires in 3 days',
    proposedBg: 'var(--taali-warning-soft)',
    proposedBorder: 'var(--taali-warning-border)',
    proposedText: 'var(--taali-warning)',
  },
  {
    type: 'info',
    label: 'Info / Neutral',
    sample: 'Graph is already up to date',
    proposedBg: 'var(--taali-info-soft)',
    proposedBorder: 'var(--taali-info-border)',
    proposedText: 'var(--taali-info)',
  },
];

const CurrentToastSample = ({ type, message }) => {
  // Replica of the current ToastContext rendering at lines 82-99.
  const cls = (() => {
    if (type === 'error') return 'border-red-300 bg-red-50 text-red-900';
    if (type === 'success') return 'border-green-300 bg-green-50 text-green-900';
    return 'border-[var(--taali-border)] bg-[var(--taali-surface)] text-[var(--taali-text)]';
  })();
  return (
    <div className={`rounded-lg border-2 px-4 py-3 shadow-lg text-sm ${cls}`}>
      <p className="break-words">{message}</p>
      <button
        type="button"
        className="taali-text-btn mt-2"
      >
        Dismiss
      </button>
    </div>
  );
};

const ProposedToastSample = ({ message, bg, border, text }) => (
  <div
    className="rounded-lg border px-4 py-3 shadow-sm text-sm"
    style={{
      background: bg,
      borderColor: border,
      color: 'var(--ink)',
    }}
  >
    <p className="break-words">
      <span
        className="inline-block h-2 w-2 rounded-full mr-2 align-middle"
        style={{ background: text }}
      />
      {message}
    </p>
    <button
      type="button"
      className="taali-text-btn mt-2"
    >
      Dismiss
    </button>
  </div>
);

const ToastVariantComparisonSection = () => (
  <Section
    title="Current vs proposed"
    subtitle="Left column: today's render (raw Tailwind colours, mismatch with the design system). Right column: same content rendered via --taali-*-soft / --taali-*-border / --taali-* tokens. The info row on the left already uses Taali tokens — the others jump out because they bypass them."
  >
    <div className="grid grid-cols-1 md:grid-cols-[120px_1fr_1fr] gap-4 items-start">
      <div className="hidden md:block" />
      <p className="text-xs uppercase tracking-wider text-[var(--ink-soft)] hidden md:block">Current (in production)</p>
      <p className="text-xs uppercase tracking-wider text-[var(--ink-soft)] hidden md:block">Proposed (Taali tokens)</p>

      {VARIANTS.map((v) => (
        <React.Fragment key={v.label}>
          <p className="text-sm font-medium text-[var(--ink)]">{v.label}</p>
          <CurrentToastSample type={v.type} message={v.sample} />
          <ProposedToastSample
            message={v.sample}
            bg={v.proposedBg}
            border={v.proposedBorder}
            text={v.proposedText}
          />
        </React.Fragment>
      ))}
    </div>
  </Section>
);

// ---------------------------------------------------------------------------
// BackgroundJobsToaster — every state, statically rendered
// ---------------------------------------------------------------------------

const JOB_STATE_FIXTURES = [
  {
    label: 'Running (single role, scoring)',
    title: 'Engineering: Scoring CVs',
    detail: '42/120 processed · 8 scored · 2 errors · 78 remaining',
    pct: 35,
    status: 'running',
  },
  {
    label: 'Running (cascade — fetch + pre-screen + score)',
    title: 'Senior Backend: Processing',
    detail: 'Fetch 20/20 (18 got CV, 2 unavailable) · Pre-screen 14/20 · Score 8/14',
    pct: 60,
    status: 'running',
  },
  {
    label: 'Cancelling',
    title: 'Engineering: cancelling…',
    detail: '42/120 processed · 8 scored',
    pct: 35,
    status: 'cancelling',
  },
  {
    label: 'Cancelled',
    title: 'Engineering: Scoring cancelled',
    detail: '42/120 processed · 8 scored · 2 errors',
    pct: 35,
    status: 'cancelled',
  },
  {
    label: 'Completed',
    title: 'Engineering: Scoring complete',
    detail: '120/120 processed · 117 scored · 3 errors',
    pct: 100,
    status: 'completed',
  },
  {
    label: 'Completed (with pre-screen filtering)',
    title: 'Senior Backend: Pre-screening complete',
    detail: '50/50 processed · 14 scored · 36 filtered',
    pct: 100,
    status: 'completed',
  },
  {
    label: 'Failed',
    title: 'Engineering: Scoring failed',
    detail: '12/120 processed · 0 scored · 12 errors',
    pct: 10,
    status: 'failed',
  },
  {
    label: 'Knowledge graph sync',
    title: 'Knowledge graph: Syncing to graph',
    detail: '34/220 processed · 2 errors · 186 remaining',
    pct: 15,
    status: 'running',
  },
];

const StaticJobRow = ({ title, detail, pct, status, onCancel, onDismiss }) => {
  const isTerminal = status === 'cancelled' || status === 'completed' || status === 'failed';
  const isCancelling = status === 'cancelling';
  return (
    <div className="bg-jobs-row">
      <div className="bg-jobs-icon">
        {isTerminal ? <CheckCircle2 size={18} /> : <Loader2 size={18} className="animate-spin" />}
      </div>
      <div className="bg-jobs-body">
        <div className="bg-jobs-title">{title}</div>
        <div className="bg-jobs-detail">{detail}</div>
        <div className="bg-jobs-bar" aria-hidden="true">
          <div className="bg-jobs-bar-fill" style={{ width: `${pct}%` }} />
        </div>
        <div className="bg-jobs-actions">
          {!isTerminal && (
            <button
              type="button"
              className="taali-btn taali-btn-secondary taali-btn-xs bg-jobs-cancel"
              onClick={onCancel}
              disabled={isCancelling}
            >
              {isCancelling ? 'Cancelling…' : 'Cancel'}
            </button>
          )}
          {isTerminal && (
            <button
              type="button"
              className="taali-icon-btn taali-icon-btn-ghost taali-icon-btn-sm bg-jobs-dismiss-row"
              aria-label="Dismiss background job"
              onClick={onDismiss}
            >
              <X size={14} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

const BackgroundJobsToasterSection = () => (
  <Section
    title="BackgroundJobsToaster — all states"
    subtitle="Persistent floating panel (separate from the popup toaster). Tracks 4 job kinds: batch scoring, CV fetching, pre-screening, knowledge-graph sync. State lives in JobStatusContext so it survives navigation. Renders every visible row in a single container — these are the 8 fixtures that cover the full state machine."
  >
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      {JOB_STATE_FIXTURES.map((fx) => (
        <div key={fx.label}>
          <p className="text-xs uppercase tracking-wider text-[var(--ink-soft)] mb-2">
            {fx.label}
          </p>
          <div
            className="bg-jobs-toaster"
            style={{ position: 'static', width: 'auto', maxHeight: 'none' }}
          >
            <StaticJobRow {...fx} />
          </div>
        </div>
      ))}
    </div>
  </Section>
);

// ---------------------------------------------------------------------------
// Call site inventory
// ---------------------------------------------------------------------------

const CALL_SITES = [
  { area: 'Settings', file: 'features/settings/RecruiterSettingsPage.jsx', count: 49 },
  { area: 'Jobs', file: 'features/jobs/JobPipelinePage.jsx', count: 30 },
  { area: 'Candidates', file: 'features/candidates/CandidateStandingReportPage.jsx', count: 10 },
  { area: 'Jobs', file: 'features/jobs/useCandidateTriage.js', count: 8 },
  { area: 'Dashboard', file: 'features/dashboard/DashboardPageContent.jsx', count: 5 },
  { area: 'Candidates', file: 'features/candidates/CandidateEvaluateTab.jsx', count: 3 },
  { area: 'Tasks', file: 'features/tasks/TasksPage.jsx', count: 2 },
];

const VARIANT_COUNTS = [
  { label: 'success', count: 56 },
  { label: 'error', count: 93 },
  { label: 'info', count: 18 },
  { label: 'warning', count: 0 },
];

const CallSiteInventorySection = () => (
  <Section
    title="Call site inventory"
    subtitle="167 showToast() calls across 9 source files. Errors dominate (93 / 167) which is worth knowing — the danger style appears more than the success style on a typical session."
  >
    <div className="grid grid-cols-1 gap-6 md:grid-cols-[1fr_1fr]">
      <div className="rounded-lg border border-[var(--taali-border)] bg-white p-4">
        <p className="text-sm font-medium text-[var(--ink)]">By variant</p>
        <ul className="mt-3 space-y-1 text-sm text-[var(--ink-2)]">
          {VARIANT_COUNTS.map((v) => (
            <li key={v.label} className="flex justify-between">
              <code>{v.label}</code>
              <span className="font-medium tabular-nums text-[var(--ink)]">{v.count}</span>
            </li>
          ))}
        </ul>
      </div>
      <div className="rounded-lg border border-[var(--taali-border)] bg-white p-4">
        <p className="text-sm font-medium text-[var(--ink)]">By file (top 9)</p>
        <ul className="mt-3 space-y-1 text-sm text-[var(--ink-2)]">
          {CALL_SITES.map((cs) => (
            <li key={cs.file} className="flex justify-between gap-4">
              <code className="truncate">{cs.file}</code>
              <span className="font-medium tabular-nums text-[var(--ink)]">{cs.count}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  </Section>
);

// ---------------------------------------------------------------------------
// Section primitive
// ---------------------------------------------------------------------------

const Section = ({ title, subtitle, children }) => (
  <section>
    <h2 className="text-lg font-semibold text-[var(--ink)]">{title}</h2>
    {subtitle && (
      <p className="mt-1 max-w-3xl text-sm text-[var(--ink-2)]">{subtitle}</p>
    )}
    <div className="mt-4">{children}</div>
  </section>
);

export default ToastShowcasePage;
