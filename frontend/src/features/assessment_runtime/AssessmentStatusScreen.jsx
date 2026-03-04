import React from 'react';

import { AssessmentBrandGlyph } from './AssessmentBrandGlyph';

export const AssessmentStatusScreen = ({
  mode,
  submittedAt = null,
  contactEmail = 'support@taali.ai',
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

  return (
    <div className={`taali-runtime ${lightMode ? 'taali-runtime-light' : 'taali-runtime-dark'} flex h-screen items-center justify-center bg-[var(--taali-runtime-bg)] px-4`}>
      <div className="max-w-md rounded-[var(--taali-radius-panel)] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-panel)] p-12 text-center shadow-[var(--taali-shadow-strong)]">
        <div className="mx-auto mb-6 w-fit">
          <AssessmentBrandGlyph sizeClass="w-16 h-16" markSizeClass="w-[2.7rem] h-[2.7rem]" />
        </div>
        <h1 className="mb-4 text-3xl font-bold text-[var(--taali-runtime-text)]">Assessment Submitted</h1>
        <p className="mb-3 font-mono text-sm text-[var(--taali-runtime-muted)]">
          Assessment complete. The hiring team will review your results and follow up with next steps.
        </p>
        <div className="space-y-1 text-left font-mono text-xs text-[var(--taali-runtime-muted)]">
          <div>Submitted at: {(submittedAt ? new Date(submittedAt) : new Date()).toLocaleString()}</div>
          <div>Next step: hiring team review and response.</div>
          <div>Questions: contact {contactEmail}.</div>
        </div>
      </div>
    </div>
  );
};
