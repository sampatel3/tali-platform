// The animated typing indicator, shared across chat surfaces.
import { AgentLoop } from '../motion';

export function ThinkingDots({ label = null }) {
  return (
    <span className="tk-thinking">
      <span className="tk-dots">
        <AgentLoop kind="pulse" delay={0} />
        <AgentLoop kind="pulse" delay={0.15} />
        <AgentLoop kind="pulse" delay={0.3} />
      </span>
      {label}
    </span>
  );
}

export default ThinkingDots;
