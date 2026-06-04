import { useEffect, useRef } from 'react';
import { ArrowUp, Square } from 'lucide-react';

const autosize = (el, max = 200) => {
  if (!el) return;
  el.style.height = 'auto';
  el.style.height = `${Math.min(el.scrollHeight, max)}px`;
};

// One composer for every chat surface (Search, Home agent dock, candidate
// workspace). Surfaces own where it sits; this renders the box + foot + the
// send/stop control only.
//   submitMode: 'enter' → Enter sends, Shift+Enter newline (Search, Home)
//               'cmd'   → ⌘/Ctrl+Enter sends, Enter newline (candidate workspace)
//   streaming: show a Stop button instead of Send (Search's streamed turns)
//   busy:      a turn is running → disable the input
export function ChatComposer({
  value,
  onChange,
  onSubmit,
  placeholder = 'Ask anything…',
  busy = false,
  streaming = false,
  onStop,
  submitMode = 'enter',
  foot = true,
  onPaste,
}) {
  const ref = useRef(null);
  useEffect(() => autosize(ref.current), [value]);

  const submit = (e) => {
    e?.preventDefault?.();
    const text = (value || '').trim();
    if (!text || busy) return;
    onSubmit(text);
  };

  const onKeyDown = (e) => {
    if (e.key !== 'Enter') return;
    const sends = submitMode === 'cmd' ? (e.metaKey || e.ctrlKey) : !e.shiftKey;
    if (sends) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <form className="tk-composer" onSubmit={submit}>
      <textarea
        ref={ref}
        rows={1}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        onPaste={onPaste}
        disabled={busy}
      />
      <div className="tk-composer-foot">
        {foot ? (
          submitMode === 'cmd' ? (
            <span>press <kbd>⌘</kbd><kbd>Enter</kbd> to send · <kbd>Enter</kbd> for newline</span>
          ) : (
            <span>press <kbd>Enter</kbd> to send · <kbd>Shift</kbd>+<kbd>Enter</kbd> for newline</span>
          )
        ) : (
          <span />
        )}
        {streaming ? (
          <button type="button" className="tk-stop-btn" onClick={onStop}>
            <Square size={11} fill="currentColor" /> stop
          </button>
        ) : (
          <button type="submit" className="tk-send-btn" disabled={!value.trim() || busy}>
            <ArrowUp size={13} /> send
          </button>
        )}
      </div>
    </form>
  );
}

export default ChatComposer;
