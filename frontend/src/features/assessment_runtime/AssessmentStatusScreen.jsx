import React, { useState } from 'react';
import { Check } from 'lucide-react';

import { MotionLoop } from '../../shared/motion';
import { AssessmentBrandGlyph } from './AssessmentBrandGlyph';

// Modern browsers block ``window.close()`` on any tab the user opened
// themselves (only windows spawned via ``window.open`` can self-close).
// The candidate landed on this tab from an email link, so the call
// almost always no-ops. Detect the platform so we can show the right
// keyboard shortcut as the fallback ask (Sam, 2026-05-26).
const detectMacShortcut = () => {
  if (typeof navigator === 'undefined') return false;
  const platform = String(navigator.platform || '').toLowerCase();
  const ua = String(navigator.userAgent || '').toLowerCase();
  return platform.includes('mac') || ua.includes('mac os');
};

export const AssessmentStatusScreen = ({
  mode,
  lightMode = false,
  submissionReceiptReconciled = false,
}) => {
  if (mode === 'loading') {
    return (
      <div className={`taali-runtime ${lightMode ? 'taali-runtime-light' : 'taali-runtime-dark'} flex h-screen items-center justify-center bg-[var(--taali-runtime-bg)]`}>
        <div className="text-center">
          <MotionLoop as="div" kind="pulse" className="mx-auto mb-4 w-fit">
            <AssessmentBrandGlyph sizeClass="w-16 h-16" markSizeClass="w-[2.7rem] h-[2.7rem]" />
          </MotionLoop>
          <p className="font-mono text-sm text-[var(--taali-runtime-muted)]">
            Loading assessment...
          </p>
        </div>
      </div>
    );
  }

  // Two-step close: first click tries ``window.close()`` (works for the
  // rare case the candidate's email client opened the link in a new
  // window via ``target="_blank"`` with the opener retained). If the
  // tab is still here after a short tick, swap the button for the
  // keyboard-shortcut hint — the only honest path left.
  const [closeAttempted, setCloseAttempted] = useState(false);
  const isMac = detectMacShortcut();
  const shortcutLabel = isMac ? '⌘W' : 'Ctrl+W';

  const handleClose = () => {
    if (typeof window === 'undefined') return;
    try {
      window.close();
    } catch {
      // Some embedded views throw — fall through to the hint UI.
    }
    // Give the browser a tick to actually close. If we're still here,
    // surface the keyboard shortcut.
    window.setTimeout(() => {
      setCloseAttempted(true);
    }, 200);
  };

  // Mirrors the client-intake "done" surface (ci-state in clientintake.css):
  // a clean centered column on the page background — no card chrome — with the
  // ``taali.`` wordmark, a purple check tile, a display heading and a muted line.
  // Tokens stay on the ``--taali-runtime-*`` family so the screen still adapts
  // to the candidate's light/dark preference.
  return (
    <div className={`taali-runtime ${lightMode ? 'taali-runtime-light' : 'taali-runtime-dark'} flex h-screen items-center justify-center bg-[var(--taali-runtime-bg)] px-6`}>
      <div className="mx-auto w-full max-w-[520px] py-[72px] text-center">
        <div className="mb-7 text-[20px] font-extrabold tracking-[-0.01em] text-[var(--taali-runtime-text)]">
          taali<span className="text-[var(--purple)]">.</span>
        </div>
        <div className="mx-auto mb-5 grid h-14 w-14 place-items-center rounded-[16px] bg-[var(--purple)] text-white">
          <Check size={26} />
        </div>
        <h1 className="mb-2.5 font-[var(--font-display)] text-[24px] font-bold leading-[1.18] tracking-[-0.02em] text-[var(--taali-runtime-text)]">
          Task submitted
        </h1>
        <p className="mb-0 text-[14.5px] leading-[1.6] text-[var(--taali-runtime-muted)]">
          {submissionReceiptReconciled
            ? 'The server confirmed that your submission snapshot was already locked. Any editor change that could not save before finalization was not included.'
            : 'Your work is locked in. The hiring team will review the transcript, your prompts, and the evidence.'}
        </p>
        {closeAttempted ? (
          <div className="mt-6 rounded-[14px] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel)] px-5 py-4 text-[0.84375rem] leading-6 text-[var(--taali-runtime-text)]">
            Your browser doesn’t allow this page to close itself. Press{' '}
            <kbd className="mx-1 rounded-md border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel-alt,var(--taali-runtime-panel))] px-2 py-0.5 font-mono text-[0.75rem] text-[var(--taali-runtime-text)]">
              {shortcutLabel}
            </kbd>
            to close this tab.
          </div>
        ) : (
          <button
            type="button"
            className="taali-btn taali-btn-primary taali-btn-lg mt-6"
            onClick={handleClose}
          >
            Close window
          </button>
        )}
      </div>
    </div>
  );
};
