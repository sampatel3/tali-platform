import React from 'react';

import { BrandGlyph } from '../../shared/ui/Branding';

export const AssessmentBrandGlyph = ({
  variant = 'square',
  sizeClass = 'w-8 h-8',
  markSizeClass = 'w-6 h-6',
  borderClass,
}) => (
  <BrandGlyph
    variant={variant}
    sizeClass={sizeClass}
    markSizeClass={markSizeClass}
    borderClass={borderClass}
  />
);
