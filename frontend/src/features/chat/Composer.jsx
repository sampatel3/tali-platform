import React, { useEffect, useRef } from 'react';
import { ArrowUp, Square } from 'lucide-react';

const autosize = (el) => {
  if (!el) return;
  el.style.height = 'auto';
  el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
};

const Composer = ({ value, onChange, onSubmit, isStreaming, onStop }) => {
  const ref = useRef(null);
  useEffect(() => autosize(ref.current), [value]);

  const submit = (e) => {
    e?.preventDefault?.();
    if (!value.trim() || isStreaming) return;
    onSubmit(value);
  };

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <form className="cp-composer" onSubmit={submit}>
      <textarea
        ref={ref}
        rows={1}
        value={value}
        placeholder="Ask anything about your candidates…"
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        disabled={isStreaming}
      />
      <div className="cp-composer-foot">
        <span>
          press <kbd>Enter</kbd> to send · <kbd>Shift</kbd>+<kbd>Enter</kbd> for newline
        </span>
        {isStreaming ? (
          <button type="button" className="cp-stop-btn" onClick={onStop}>
            <Square size={11} fill="currentColor" /> stop
          </button>
        ) : (
          <button type="submit" className="cp-send-btn" disabled={!value.trim()}>
            <ArrowUp size={13} /> send
          </button>
        )}
      </div>
    </form>
  );
};

export default Composer;
