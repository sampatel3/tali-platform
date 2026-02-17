import React from 'react';

export const AssessmentBrandGlyph = ({ sizeClass = 'w-8 h-8', markSizeClass = 'w-6 h-6' }) => (
  <div
    className={`${sizeClass} border-2 border-[var(--taali-border)] flex items-center justify-center bg-[var(--taali-purple)]`}
    aria-hidden="true"
  >
    <svg viewBox="0 0 24 24" className={markSizeClass} fill="none">
      <path
        d="M6 4.5v15M10 4.5v15M14 4.5v15M18 4.5v15M4 18.5L20 5.5"
        stroke="#FFFFFF"
        strokeWidth="2.5"
        strokeLinecap="round"
      />
    </svg>
  </div>
);
