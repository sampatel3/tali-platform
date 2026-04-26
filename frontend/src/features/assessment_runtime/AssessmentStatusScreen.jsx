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
      <div className="max-w-md rounded-[var(--taali-radius-panel)] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel)] p-12 text-center shadow-[var(--taali-shadow-strong)]">
        <div className="mx-auto mb-6 w-fit">
          <AssessmentBrandGlyph sizeClass="w-16 h-16" markSizeClass="w-[2.7rem] h-[2.7rem]" />
        </div>
        <h1 className="mb-4 text-3xl font-bold text-[var(--taali-runtime-text)]">Task submitted</h1>
        <p className="mb-6 font-mono text-sm text-[var(--taali-runtime-muted)]">
          Your task has been submitted. You can close this tab.
        </p>
        <button
          type="button"
          className="rounded-full bg-[var(--purple)] px-7 py-3 text-sm font-semibold text-[var(--bg)] shadow-[var(--taali-shadow-soft)]"
          onClick={handleClose}
        >
          Close
        </button>
      </div>
    </div>
  );
};
