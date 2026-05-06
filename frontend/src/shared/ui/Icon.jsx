import React from 'react';

// Lucide-stroke SVG wrapper used by the Mission Control shell + agent bar.
// Accepts a path `d` so callers can inline custom marks alongside lucide-react
// icons. For named icons, prefer `lucide-react` directly.
export const Icon = ({ d, size = 16, sw = 1.8, style, className, ...rest }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth={sw}
    strokeLinecap="round"
    strokeLinejoin="round"
    style={style}
    className={className}
    aria-hidden="true"
    {...rest}
  >
    <path d={d} />
  </svg>
);

export default Icon;
