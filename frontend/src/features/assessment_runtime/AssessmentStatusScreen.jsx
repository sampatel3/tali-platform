import React from 'react';

import { AssessmentBrandGlyph } from './AssessmentBrandGlyph';

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

  const handleClose = () => {
    if (typeof window === 'undefined') return;
    window.close();
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
        <div className="mb-3 font-mono text-[10.5px] uppercase tracking-[0.14em] text-[var(--purple)]">
          ASSESSMENT · COMPLETE
        </div>
        <h1 className="mb-4 font-display text-[34px] font-semibold tracking-[-0.025em] text-[var(--taali-runtime-text)]">
          Task submitted<span className="text-[var(--purple)]">.</span>
        </h1>
        <p className="mb-6 text-[14px] leading-[1.55] text-[var(--taali-runtime-muted)]">
          Your work is locked in. The hiring team will review the transcript, your prompts, and the evidence — you can close this tab.
        </p>
        <button
          type="button"
          className="inline-flex items-center justify-center rounded-full bg-[var(--purple)] px-7 py-3 text-sm font-medium text-white transition-colors hover:bg-[var(--purple-hover,var(--purple))]"
          onClick={handleClose}
        >
          Close window
        </button>
      </div>
    </div>
  );
};
