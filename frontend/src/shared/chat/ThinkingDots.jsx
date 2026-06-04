// The animated typing indicator, shared across chat surfaces.
export function ThinkingDots({ label = null }) {
  return (
    <span className="tk-thinking">
      <span className="tk-dots"><span /><span /><span /></span>
      {label}
    </span>
  );
}

export default ThinkingDots;
