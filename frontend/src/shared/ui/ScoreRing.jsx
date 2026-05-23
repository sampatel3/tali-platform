import React from 'react';

// Circular SVG ring used on the candidate report. Score is 0–100.
// Stroke is the brand purple over a soft track.
export const ScoreRing = ({ score = 0, size = 110, label = 'SCORE', strokeWidth = 6, display = null }) => {
  const safeScore = Math.max(0, Math.min(100, Number(score) || 0));
  const centerText = display != null ? String(display) : String(Math.round(safeScore));
  const r = size / 2 - strokeWidth - 2;
  const c = 2 * Math.PI * r;
  const dash = (safeScore / 100) * c;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-label={`${label} ${Math.round(safeScore)} of 100`}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--bg-3)" strokeWidth={strokeWidth} />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke="var(--purple)"
        strokeWidth={strokeWidth}
        strokeDasharray={`${dash} ${c}`}
        strokeLinecap="round"
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
      <text
        x={size / 2}
        y={size / 2 - 2}
        textAnchor="middle"
        dominantBaseline="middle"
        fontFamily="var(--font-display)"
        fontSize={centerText.length >= 4 ? size * 0.2 : size * 0.28}
        fontWeight="600"
        fill="var(--ink)"
        letterSpacing="-0.02em"
      >
        {centerText}
      </text>
      <text
        x={size / 2}
        y={size / 2 + size * 0.21}
        textAnchor="middle"
        fontFamily="var(--font-mono)"
        fontSize={Math.max(8, size * 0.085)}
        fill="var(--mute)"
        letterSpacing=".05em"
      >
        {label}
      </text>
    </svg>
  );
};

export default ScoreRing;
