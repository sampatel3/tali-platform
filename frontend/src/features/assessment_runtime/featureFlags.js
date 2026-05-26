// Runtime feature flags for the candidate-facing assessment runtime.
//
// These flags exist so we can ship the agentic Claude chat (HTTP-based,
// no PTY/WebSocket) behind a toggle while the backend route lands in a
// separate PR. Once the new route is shipped and the legacy terminal
// chat path is removed, these helpers will be deleted alongside it.
//
// The flags are read from a global on `window` rather than env vars or
// the preview payload so we can flip them in production without a
// rebuild (e.g. via a devtools snippet) and so the backend can later
// inject the value into the runtime payload by setting the global
// before the SPA boots.

/**
 * Returns true when the new HTTP-based Claude chat component should be
 * mounted in place of the legacy WebSocket-on-PTY chat.
 *
 * **Defaults to ON.** No historical assessments existed to regress against,
 * so the rollout strategy is: turn it on by default for everyone, validate
 * with the test candidate, then delete the legacy CLI/PTY path in a
 * follow-up cleanup PR.
 *
 * Disable in browser devtools if a hot rollback is ever needed:
 *   window.__TAALI_AGENTIC_CHAT__ = false; // then reload
 */
export const useAgenticClaudeChat = () => {
  if (typeof window === 'undefined') return true;
  try {
    return window.__TAALI_AGENTIC_CHAT__ !== false;
  } catch {
    return true;
  }
};
