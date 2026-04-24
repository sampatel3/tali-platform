import React from 'react';

import { AssessmentBrandGlyph } from './AssessmentBrandGlyph';

export const AssessmentStatusScreen = ({
  mode,
  submittedAt = null,
  contactEmail = 'support@taali.ai',
}) => {
  if (mode === 'loading') {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[var(--bg)]">
        <div className="text-center">
          <div className="mx-auto mb-4 animate-pulse w-fit">
            <AssessmentBrandGlyph sizeClass="w-16 h-16" markSizeClass="w-[2.7rem] h-[2.7rem]" />
          </div>
          <p className="font-[var(--font-mono)] text-sm text-[var(--mute)]">
            Loading assessment...
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-[var(--bg)] px-4">
      <div className="max-w-xl rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-10 text-center shadow-[var(--shadow-md)]">
        <div className="mx-auto mb-6 w-fit">
          <AssessmentBrandGlyph sizeClass="w-16 h-16" markSizeClass="w-[2.7rem] h-[2.7rem]" />
        </div>
        <div className="kicker">Assessment submitted</div>
        <h1 className="mt-3 font-[var(--font-display)] text-[38px] font-semibold tracking-[-0.03em] text-[var(--ink)]">You&apos;re all set.</h1>
        <p className="mt-4 text-sm leading-7 text-[var(--ink-2)]">
          Assessment complete. The hiring team will review your results and follow up with next steps.
        </p>
        <div className="mt-5 rounded-[14px] border border-[var(--line)] bg-[var(--bg)] px-4 py-4 text-left font-[var(--font-mono)] text-[12px] text-[var(--mute)]">
          <div>Submitted at: {(submittedAt ? new Date(submittedAt) : new Date()).toLocaleString()}</div>
          <div className="mt-1">Next step: hiring team review and response.</div>
          <div className="mt-1">Questions: contact {contactEmail}.</div>
        </div>
      </div>
    </div>
  );
};
