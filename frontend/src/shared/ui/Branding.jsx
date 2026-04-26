import React from 'react';
import { BRAND } from '../../config/brand';

const TAALI_MARK_PATH = 'M6 4.5v15M10 4.5v15M14 4.5v15M18 4.5v15M4 18.5L20 5.5';
const TAALI_CONTAINED_MARK_TRANSFORM = 'translate(2.4 2.4) scale(0.8)';
const TAALI_LOGO_FILL_CLASS = 'text-[#7F39FB]';
const TAALI_TILE_CORNER_RADIUS = 5.35;

const GLYPH_VARIANTS = {
  primarySquareRounded: {
    containerClassName: 'drop-shadow-[0_14px_28px_rgba(157,0,255,0.16)]',
    defaultBorderClass: '',
    fillClassName: TAALI_LOGO_FILL_CLASS,
    lineClassName: 'text-[var(--taali-inverse-text)]',
    renderMode: 'tile',
    tileCornerRadius: TAALI_TILE_CORNER_RADIUS,
  },
  compactSquare: {
    containerClassName: '',
    defaultBorderClass: '',
    fillClassName: TAALI_LOGO_FILL_CLASS,
    lineClassName: 'text-[var(--taali-inverse-text)]',
    renderMode: 'tile',
    tileCornerRadius: 4.9,
  },
  inverseSquare: {
    containerClassName: '',
    defaultBorderClass: '',
    fillClassName: 'text-[var(--taali-text)]',
    lineClassName: 'text-[var(--taali-inverse-text)]',
    renderMode: 'tile',
    tileCornerRadius: TAALI_TILE_CORNER_RADIUS,
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
  monoLines: {
    containerClassName: '',
    defaultBorderClass: '',
    lineClassName: 'text-[var(--taali-text)]',
  },
};

const VARIANT_ALIASES = {
  square: 'primarySquareRounded',
  'primary-square-rounded': 'primarySquareRounded',
  'compact-square': 'compactSquare',
  'inverse-square': 'inverseSquare',
  'mono-lines': 'monoLines',
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
  cornerRadius = TAALI_TILE_CORNER_RADIUS,
}) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" aria-hidden="true">
    <rect x="1.25" y="1.25" width="21.5" height="21.5" rx={cornerRadius} className={fillClassName} fill="currentColor" />
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
  const canonicalVariant = VARIANT_ALIASES[variant] || variant;
  const config = GLYPH_VARIANTS[canonicalVariant] || GLYPH_VARIANTS.primarySquareRounded;
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
          cornerRadius={config.tileCornerRadius || TAALI_TILE_CORNER_RADIUS}
        />
      ) : (
        <TaaliLines className={markSizeClass} lineClassName={config.lineClassName} />
      )}
    </div>
  );
};

export const MarketingWordmark = ({
  className = '',
  textClassName = '',
  dotClassName = '',
  variant = 'default',
  onClick,
}) => {
  const variantClassName = variant === 'compact'
    ? 'taali-marketing-wordmark-compact'
    : variant === 'footer'
      ? 'taali-marketing-wordmark-footer'
      : '';

  return (
    <div
      className={`inline-flex items-end ${onClick ? 'cursor-pointer' : ''} ${className}`.trim()}
      onClick={onClick}
    >
      <span className={`taali-marketing-wordmark ${variantClassName} ${textClassName}`.trim()}>
        {BRAND.wordmark || String(BRAND.name || 'taali').toLowerCase()}
      </span>
      <span aria-hidden="true" className={`taali-marketing-wordmark-dot ${dotClassName}`.trim()} />
    </div>
  );
};

export const Logo = ({
  onClick,
  className = '',
  glyphVariant = 'primary-square-rounded',
  glyphBorderClass,
  glyphSizeClass,
  glyphMarkSizeClass,
  showWordmark = true,
  wordmarkDisplay = true,
  wordmarkClassName = 'text-[var(--taali-text)]',
}) => {
  const canonicalVariant = VARIANT_ALIASES[glyphVariant] || glyphVariant;
  const usesContainedMark = canonicalVariant === 'circle'
    || canonicalVariant === 'primarySquareRounded'
    || canonicalVariant === 'compactSquare'
    || canonicalVariant === 'inverseSquare';
  const resolvedGlyphSizeClass = glyphSizeClass || (usesContainedMark ? 'w-10 h-10' : 'w-7 h-7');
  const resolvedGlyphMarkSizeClass = glyphMarkSizeClass || (usesContainedMark ? 'w-[1.8rem] h-[1.8rem]' : 'h-full w-full');

  return (
    <div className={`flex items-center ${usesContainedMark ? 'gap-3' : 'gap-2.5'} ${onClick ? 'cursor-pointer' : ''} ${className}`.trim()} onClick={onClick}>
      <BrandGlyph
        variant={canonicalVariant}
        borderClass={glyphBorderClass}
        sizeClass={resolvedGlyphSizeClass}
        markSizeClass={resolvedGlyphMarkSizeClass}
      />
      {showWordmark ? (
        wordmarkDisplay ? (
          <MarketingWordmark variant="compact" textClassName={wordmarkClassName} />
        ) : (
          <span className={`text-[0.95rem] font-semibold uppercase tracking-[0.16em] ${wordmarkClassName}`.trim()}>
            {BRAND.name}
          </span>
        )
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
