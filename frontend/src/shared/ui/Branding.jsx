import React from 'react';
import { BRAND } from '../../config/brand';

const TAALI_MARK_PATH = 'M6 4.5v15M10 4.5v15M14 4.5v15M18 4.5v15M4 18.5L20 5.5';
const TAALI_CONTAINED_MARK_TRANSFORM = 'translate(2.4 2.4) scale(0.8)';
const TAALI_LOGO_FILL_CLASS = 'text-[#7F39FB]';

const GLYPH_VARIANTS = {
  square: {
    containerClassName: 'drop-shadow-[0_14px_28px_rgba(157,0,255,0.16)]',
    defaultBorderClass: '',
    fillClassName: TAALI_LOGO_FILL_CLASS,
    lineClassName: 'text-[var(--taali-inverse-text)]',
    renderMode: 'tile',
  },
  circle: {
    containerClassName: 'drop-shadow-[0_14px_28px_rgba(157,0,255,0.18)]',
    defaultBorderClass: '',
    fillClassName: TAALI_LOGO_FILL_CLASS,
    lineClassName: 'text-[var(--taali-inverse-text)]',
    renderMode: 'roundel',
  },
  lines: {
    containerClassName: '',
    defaultBorderClass: '',
    lineClassName: 'text-[var(--taali-purple)]',
  },
  linesDeep: {
    containerClassName: '',
    defaultBorderClass: '',
    lineClassName: 'text-[#7F39FB]',
  },
  linesSoft: {
    containerClassName: '',
    defaultBorderClass: '',
    lineClassName: 'text-[#B06BFF]',
  },
};

export const TaaliLines = ({
  className = 'w-6 h-6',
  lineClassName = 'text-[var(--taali-purple)]',
  strokeWidth = 2.5,
}) => (
  <svg viewBox="0 0 24 24" className={`${className} ${lineClassName}`} fill="none" aria-hidden="true">
    <path
      d={TAALI_MARK_PATH}
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
    />
  </svg>
);

export const TaaliRoundel = ({
  className = 'w-10 h-10',
  fillClassName = TAALI_LOGO_FILL_CLASS,
  lineClassName = 'text-[var(--taali-inverse-text)]',
  strokeWidth = 2.5,
}) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" aria-hidden="true">
    <circle cx="12" cy="12" r="11" className={fillClassName} fill="currentColor" />
    <g transform={TAALI_CONTAINED_MARK_TRANSFORM}>
      <path
        d={TAALI_MARK_PATH}
        className={lineClassName}
        stroke="currentColor"
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
    </g>
  </svg>
);

export const TaaliTile = ({
  className = 'w-10 h-10',
  fillClassName = TAALI_LOGO_FILL_CLASS,
  lineClassName = 'text-[var(--taali-inverse-text)]',
  strokeWidth = 2.4,
}) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" aria-hidden="true">
    <rect x="1.25" y="1.25" width="21.5" height="21.5" rx="5.5" className={fillClassName} fill="currentColor" />
    <g transform={TAALI_CONTAINED_MARK_TRANSFORM}>
      <path
        d={TAALI_MARK_PATH}
        className={lineClassName}
        stroke="currentColor"
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
    </g>
  </svg>
);

export const BrandGlyph = ({
  variant = 'square',
  borderClass,
  sizeClass = 'w-10 h-10',
  markSizeClass = 'w-[1.8rem] h-[1.8rem]',
  className = '',
}) => {
  const config = GLYPH_VARIANTS[variant] || GLYPH_VARIANTS.circle;
  const resolvedBorderClass = config.defaultBorderClass ? (borderClass || config.defaultBorderClass) : '';
  const resolvedContainerClassName = [
    sizeClass,
    'flex shrink-0 items-center justify-center',
    config.containerClassName,
    resolvedBorderClass,
    className,
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div className={resolvedContainerClassName} aria-hidden="true">
      {config.renderMode === 'roundel' ? (
        <TaaliRoundel
          className="h-full w-full"
          fillClassName={config.fillClassName}
          lineClassName={config.lineClassName}
        />
      ) : config.renderMode === 'tile' ? (
        <TaaliTile
          className="h-full w-full"
          fillClassName={config.fillClassName}
          lineClassName={config.lineClassName}
        />
      ) : (
        <TaaliLines className={markSizeClass} lineClassName={config.lineClassName} />
      )}
    </div>
  );
};

export const Logo = ({
  onClick,
  className = '',
  glyphVariant = 'square',
  glyphBorderClass,
  glyphSizeClass,
  glyphMarkSizeClass,
  showWordmark = true,
  wordmarkClassName = 'text-[var(--taali-text)]',
}) => {
  const usesContainedMark = glyphVariant === 'circle' || glyphVariant === 'square';
  const resolvedGlyphSizeClass = glyphSizeClass || (usesContainedMark ? 'w-10 h-10' : 'w-7 h-7');
  const resolvedGlyphMarkSizeClass = glyphMarkSizeClass || (usesContainedMark ? 'w-[1.8rem] h-[1.8rem]' : 'h-full w-full');

  return (
    <div className={`flex items-center ${usesContainedMark ? 'gap-3' : 'gap-2.5'} ${onClick ? 'cursor-pointer' : ''} ${className}`.trim()} onClick={onClick}>
      <BrandGlyph
        variant={glyphVariant}
        borderClass={glyphBorderClass}
        sizeClass={resolvedGlyphSizeClass}
        markSizeClass={resolvedGlyphMarkSizeClass}
      />
      {showWordmark ? (
        <span className={`taali-display text-xl font-semibold tracking-tight ${wordmarkClassName}`}>{BRAND.name}</span>
      ) : null}
    </div>
  );
};

export const BrandLabel = ({
  children,
  className = '',
  toneClassName = 'text-[var(--taali-purple)]',
  lineClassName,
  markClassName = 'h-4 w-4',
  strokeWidth = 2.9,
}) => (
  <div className={`inline-flex items-center gap-2 font-mono text-xs font-bold uppercase tracking-[0.14em] ${toneClassName} ${className}`.trim()}>
    <TaaliLines
      className={markClassName}
      lineClassName={lineClassName || toneClassName}
      strokeWidth={strokeWidth}
    />
    <span>{children}</span>
  </div>
);
