import React from 'react';

import { AssessmentBrandGlyph } from './AssessmentBrandGlyph';

export const AssessmentStatusScreen = ({ mode }) => {
  if (mode === 'loading') {
    return (
      <div className="h-screen flex items-center justify-center bg-white">
        <div className="text-center">
          <div className="mx-auto mb-4 animate-pulse w-fit">
            <AssessmentBrandGlyph sizeClass="w-16 h-16" markSizeClass="w-[2.7rem] h-[2.7rem]" />
          </div>
          <p className="font-mono text-sm text-gray-600">
            Loading assessment...
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="h-screen flex items-center justify-center bg-white">
      <div className="text-center border-2 border-black p-12 max-w-md">
        <div className="mx-auto mb-6 w-fit">
          <AssessmentBrandGlyph sizeClass="w-16 h-16" markSizeClass="w-[2.7rem] h-[2.7rem]" />
        </div>
        <h1 className="text-3xl font-bold mb-4">Assessment Submitted</h1>
        <p className="font-mono text-sm text-gray-600 mb-2">
          Thank you for completing the assessment.
        </p>
        <p className="font-mono text-sm text-gray-600">
          Your results will be reviewed and you&apos;ll hear back soon.
        </p>
      </div>
    </div>
  );
};
