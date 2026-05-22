import { useEffect, useRef } from 'react';

const isTypingTarget = (target) => {
  if (!target) return false;
  const tag = String(target.tagName || '').toLowerCase();
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return true;
  if (target.isContentEditable) return true;
  return false;
};

// Register a global keydown handler. The matcher receives the raw event
// and should return true if the handler applies. By default the handler
// is skipped when focus is in an input/textarea/contenteditable element
// — pass `{ allowInTypingFields: true }` for shortcuts like Cmd+K that
// should fire everywhere.
export function useKeyboardShortcut(matcher, handler, options = {}) {
  const handlerRef = useRef(handler);
  const matcherRef = useRef(matcher);
  handlerRef.current = handler;
  matcherRef.current = matcher;

  const { allowInTypingFields = false, enabled = true, target } = options;

  useEffect(() => {
    if (!enabled) return undefined;
    const node = target ?? window;
    const onKeyDown = (event) => {
      if (!allowInTypingFields && isTypingTarget(event.target)) return;
      if (!matcherRef.current(event)) return;
      handlerRef.current(event);
    };
    node.addEventListener('keydown', onKeyDown);
    return () => node.removeEventListener('keydown', onKeyDown);
  }, [allowInTypingFields, enabled, target]);
}

export default useKeyboardShortcut;
