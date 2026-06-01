import React, { useState } from 'react';

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
}) => {
  if (mode === 'loading') {
    return (
      <div className={`taali-runtime ${lightMode ? 'taali-runtime-light' : 'taali-runtime-dark'} flex h-screen items-center justify-center bg-[var(--taali-runtime-bg)]`}>
        <div className="text-center">
          <div className="mx-auto mb-4 animate-pulse w-fit">
            <AssessmentBrandGlyph sizeClass="w-16 h-16" markSizeClass="w-[2.7rem] h-[2.7rem]" />
          </div>
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

  return (
    <div className={`taali-runtime ${lightMode ? 'taali-runtime-light' : 'taali-runtime-dark'} flex h-screen items-center justify-center bg-[var(--taali-runtime-bg)] px-4`}>
      <div className="max-w-md rounded-[var(--radius-xl)] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel)] p-10 text-center shadow-[var(--shadow-lg)]">
        <div className="mx-auto mb-6 w-fit">
          {/* Purple background tile variant — Sam's preference. ``compactSquare``
              has no drop-shadow glow (vs. the default ``primarySquareRounded``
              which does), so the tile reads as identity rather than a glowing
              UI sticker. */}
          <AssessmentBrandGlyph
            variant="compactSquare"
            sizeClass="w-14 h-14"
            markSizeClass="w-9 h-9"
          />
        </div>
        <div className="mb-3 font-mono text-[0.6875rem] uppercase tracking-[0.12em] text-[var(--purple)]">
          ASSESSMENT · COMPLETE
        </div>
        <h1 className="mb-4 font-[var(--font-display)] text-[2.125rem] font-semibold leading-[1] tracking-[-0.025em] text-[var(--taali-runtime-text)]">
          Task submitted<span className="text-[var(--purple)]">.</span>
        </h1>
        <p className="mb-6 text-[0.875rem] leading-[1.55] text-[var(--taali-runtime-muted)]">
          Your work is locked in. The hiring team will review the transcript, your prompts, and the evidence.
        </p>
        {closeAttempted ? (
          <div className="rounded-[14px] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-bg)] px-5 py-4 text-[0.84375rem] leading-6 text-[var(--taali-runtime-text)]">
            Your browser doesn’t allow this page to close itself. Press{' '}
            <kbd className="mx-1 rounded-md border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel-alt,var(--taali-runtime-panel))] px-2 py-0.5 font-mono text-[0.75rem] text-[var(--taali-runtime-text)]">
              {shortcutLabel}
            </kbd>
            to close this tab.
          </div>
        ) : (
          <button
            type="button"
            className="inline-flex items-center justify-center rounded-full bg-[var(--purple)] px-7 py-3 text-sm font-medium text-white transition-colors hover:bg-[var(--purple-hover,var(--purple))]"
            onClick={handleClose}
          >
            Close window
          </button>
        )}
      </div>
    </div>
  );
};
