import React from 'react';

// SVG radar chart per the canvas. Accepts `values` as
// [{ k: 'sysdesign', label: 'Systems design', v: 87 }, …]. Per HANDOFF
// v2 §6 every score is on the 0–100 scale, so max defaults to 100 and
// values land on the outer ring at 100/100.
export const RadarChart = ({ values, max = 100, size = 260 }) => {
  if (!Array.isArray(values) || values.length < 3) {
    return (
      <div
        style={{
          width: size,
          height: size,
          display: 'grid',
          placeItems: 'center',
          fontSize: 12,
          color: 'var(--mute)',
          border: '1px dashed var(--line)',
          borderRadius: 14,
          padding: 16,
          textAlign: 'center',
        }}
      >
        Scoring rolls out once the assessment runtime captures enough signal.
      </div>
    );
  }
  const cx = size / 2;
  const cy = size / 2;
  const r = size / 2 - 44;
  const n = values.length;
  const ang = (i) => (Math.PI * 2 * i) / n - Math.PI / 2;
  const pt = (i, scale) => [cx + Math.cos(ang(i)) * r * scale, cy + Math.sin(ang(i)) * r * scale];

  const poly = values.map((d, i) => pt(i, Math.max(0, Math.min(1, d.v / max))).join(',')).join(' ');

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      role="img"
      aria-label="Six-axis fluency radar"
      style={{ overflow: 'visible' }}
    >
      {[0.25, 0.5, 0.75, 1].map((s, i) => (
        <polygon
          key={i}
          points={values.map((_, j) => pt(j, s).join(',')).join(' ')}
          fill="none"
          stroke="var(--line)"
          strokeWidth="1"
        />
      ))}
      {values.map((_, i) => (
        <line key={i} x1={cx} y1={cy} x2={pt(i, 1)[0]} y2={pt(i, 1)[1]} stroke="var(--line-2)" strokeWidth="1" />
      ))}
      <polygon
        points={poly}
        fill="color-mix(in oklab, var(--purple) 18%, transparent)"
        stroke="var(--purple)"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      {values.map((d, i) => {
        const [px, py] = pt(i, Math.max(0, Math.min(1, d.v / max)));
        const lx = cx + Math.cos(ang(i)) * (r + 18);
        const ly = cy + Math.sin(ang(i)) * (r + 18);
        return (
          <g key={d.k || d.label}>
            <circle cx={px} cy={py} r="3" fill="var(--purple)" />
            <text
              x={lx}
              y={ly}
              fontSize="10"
              fontFamily="var(--font-mono)"
              textAnchor={lx < cx - 4 ? 'end' : lx > cx + 4 ? 'start' : 'middle'}
              dominantBaseline="middle"
              fill="var(--mute)"
              style={{ textTransform: 'uppercase', letterSpacing: '.06em' }}
            >
              {d.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
};

export default RadarChart;
