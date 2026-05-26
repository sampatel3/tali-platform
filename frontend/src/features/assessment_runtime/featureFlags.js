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

const readWindowFlag = (key) => {
  if (typeof window === 'undefined') return false;
  try {
    return window[key] === true;
  } catch {
    return false;
  }
};

/**
 * Returns true when the new HTTP-based Claude chat component should be
 * mounted in place of the legacy WebSocket-on-PTY chat. Defaults to off.
 *
 * Toggle in browser devtools:
 *   window.__TAALI_AGENTIC_CHAT__ = true; // then reload
 */
export const useAgenticClaudeChat = () => readWindowFlag('__TAALI_AGENTIC_CHAT__');
