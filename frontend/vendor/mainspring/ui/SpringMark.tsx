/**
 * The Mainspring mark — three concentric rings + diagonal tang, with a
 * red core dot at the centre (the "mainspring" under tension).
 * Sibling of Taali's geometric four-bars-and-slash.
 */
export function SpringMark({
  size = 32, color = "#3ECF8E", dotColor = "#EF4444",
}: { size?: number; color?: string; dotColor?: string }) {
  return (
    <svg
      viewBox="0 0 120 120" width={size} height={size}
      xmlns="http://www.w3.org/2000/svg" aria-label="Mainspring"
    >
      <g fill="none" stroke={color} strokeWidth={8} strokeLinecap="round">
        <circle cx="60" cy="60" r="44" />
        <circle cx="60" cy="60" r="29" />
        <circle cx="60" cy="60" r="14" />
        <line x1="94" y1="34" x2="108" y2="20" />
      </g>
      {/* Red core dot — the loaded mainspring at the centre. */}
      <circle cx="60" cy="60" r="7" fill={dotColor} />
    </svg>
  );
}
