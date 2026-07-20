import { forwardRef, useEffect, useId, useImperativeHandle, useRef, useState } from 'react';
import { ArrowUp, CircleHelp, Mic, Square, X } from 'lucide-react';

import {
  MotionDisclosure,
  MotionLoop,
  m,
  motionTransition,
  useReducedMotionSync,
} from '../motion';

const autosize = (el, max = 200) => {
  if (!el) return;
  el.style.height = 'auto';
  el.style.height = `${Math.min(el.scrollHeight, max)}px`;
};

// Web Speech API (Chrome + Safari, incl. mobile). Absent in Firefox → the mic
// button simply never renders. Read once at module load.
const SpeechRecognitionImpl =
  typeof window !== 'undefined'
    ? window.SpeechRecognition || window.webkitSpeechRecognition || null
    : null;

// One composer for every chat surface (Search, Home agent dock, candidate
// workspace). Surfaces own where it sits; this renders the box + foot + the
// send/stop control only.
//   submitMode: 'enter' → Enter sends, Shift+Enter newline (Search, Home)
//               'cmd'   → ⌘/Ctrl+Enter sends, Enter newline (candidate workspace)
//   streaming: show a Stop button instead of Send (Search's streamed turns)
//   busy:      a turn is running → disable the input
export const ChatComposer = forwardRef(function ChatComposer({
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
  replyTo = null,
  onCancelReply,
  // Browser writing-assistance hints. Candidate assessment surfaces opt out
  // so typed prompts are not silently sent to spellcheck/autocorrect helpers.
  // Browsers and extensions ultimately control whether they honour the hints.
  writingAssistance = true,
  // Opt-in voice dictation (off by default so existing surfaces are unchanged).
  // When true AND the browser supports the Web Speech API, a mic button appears
  // that dictates into the box — handy for hiring managers briefing on a phone.
  voice = false,
}, forwardedRef) {
  const ref = useRef(null);
  const replyDescriptionId = useId();
  const focusAfterCancelRef = useRef(false);
  const reduced = useReducedMotionSync();
  useImperativeHandle(forwardedRef, () => ref.current);
  useEffect(() => autosize(ref.current), [value]);
  useEffect(() => {
    if (!focusAfterCancelRef.current || replyTo || busy) return;
    focusAfterCancelRef.current = false;
    ref.current?.focus({ preventScroll: true });
  }, [busy, replyTo]);

  // ---- voice dictation ----
  const [listening, setListening] = useState(false);
  const recognitionRef = useRef(null);
  // The text already in the box when dictation started — recognised speech is
  // appended to it so typing + talking compose cleanly.
  const dictateBaseRef = useRef('');
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const voiceSupported = voice && !!SpeechRecognitionImpl;

  const stopVoice = () => {
    try { recognitionRef.current?.stop(); } catch { /* already stopped */ }
  };

  // Tear down any live recognition on unmount.
  useEffect(() => () => {
    try { recognitionRef.current?.abort?.(); } catch { /* noop */ }
    recognitionRef.current = null;
  }, []);

  const toggleVoice = () => {
    if (!voiceSupported || busy) return;
    if (listening) { stopVoice(); return; }
    const rec = new SpeechRecognitionImpl();
    rec.lang = (typeof navigator !== 'undefined' && navigator.language) || 'en-US';
    rec.continuous = true;
    rec.interimResults = true;
    dictateBaseRef.current = value ? `${value.replace(/\s+$/, '')} ` : '';
    rec.onresult = (event) => {
      let transcript = '';
      for (let i = 0; i < event.results.length; i += 1) {
        transcript += event.results[i][0].transcript;
      }
      onChangeRef.current(dictateBaseRef.current + transcript);
    };
    rec.onerror = () => setListening(false);
    rec.onend = () => { setListening(false); recognitionRef.current = null; };
    recognitionRef.current = rec;
    setListening(true);
    try { rec.start(); } catch { setListening(false); }
  };

  const submit = (e) => {
    e?.preventDefault?.();
    if (listening) stopVoice();
    const text = (value || '').trim();
    if (!text || busy) return;
    onSubmit(text);
  };

  const cancelReply = () => {
    if (!onCancelReply) return;
    focusAfterCancelRef.current = true;
    onCancelReply();
  };

  const onKeyDown = (e) => {
    // Escape is also used by IMEs to dismiss or step back through their
    // candidate UI. Treat every composition key as belonging to the IME so it
    // cannot accidentally exit reply mode.
    if (e.nativeEvent?.isComposing || e.keyCode === 229) return;
    if (e.key === 'Escape' && replyTo && onCancelReply) {
      e.preventDefault();
      cancelReply();
      return;
    }
    if (e.key !== 'Enter') return;
    // Don't send while an IME / dictation / autocorrect composition is open:
    // that Enter is committing the composed text, not submitting. Firing the
    // submit here sends the *pre-commit* value (often a partial or the word
    // before the correction) — i.e. "the message that got sent is different
    // from what I typed". `isComposing` (legacy browsers: keyCode 229) stays
    // true for the whole composition; once it commits, onChange writes the
    // final text and the next Enter sends it intact.
    const sends = submitMode === 'cmd' ? (e.metaKey || e.ctrlKey) : !e.shiftKey;
    if (sends) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <m.form
      className="tk-composer"
      data-replying={replyTo ? 'true' : 'false'}
      onSubmit={submit}
      layout={reduced ? false : true}
      transition={reduced ? motionTransition.instant : motionTransition.layout}
    >
      <MotionDisclosure
        open={Boolean(replyTo)}
        className="tk-composer-reply"
        id={replyTo ? replyDescriptionId : undefined}
      >
        <div className="tk-composer-reply-head">
          <span><CircleHelp size={13} aria-hidden="true" /> {replyTo?.label || 'Reply to agent'}</span>
          {onCancelReply ? (
            <button type="button" onClick={cancelReply} aria-label="Cancel reply and restore draft">
              <X size={13} aria-hidden="true" />
            </button>
          ) : null}
        </div>
        {replyTo?.prompt ? <p>{replyTo.prompt}</p> : null}
        {replyTo?.error ? <span className="tk-composer-reply-error" role="alert">{replyTo.error}</span> : null}
      </MotionDisclosure>
      <textarea
        ref={ref}
        rows={1}
        value={value}
        placeholder={replyTo ? 'Write your answer to the agent…' : placeholder}
        aria-label={replyTo ? 'Answer the agent' : 'Chat message'}
        aria-describedby={replyTo ? replyDescriptionId : undefined}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        onPaste={onPaste}
        spellCheck={writingAssistance}
        autoCorrect={writingAssistance ? undefined : 'off'}
        autoCapitalize={writingAssistance ? undefined : 'off'}
        autoComplete={writingAssistance ? undefined : 'off'}
        data-gramm={writingAssistance ? undefined : 'false'}
        data-gramm_editor={writingAssistance ? undefined : 'false'}
        data-enable-grammarly={writingAssistance ? undefined : 'false'}
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
        <div className="tk-composer-actions">
          {voiceSupported ? (
            <MotionLoop
              as="button"
              active={listening}
              kind="signal"
              type="button"
              className={`tk-mic-btn${listening ? ' is-listening' : ''}`}
              onClick={toggleVoice}
              disabled={busy}
              aria-pressed={listening}
              aria-label={listening ? 'Stop dictating' : 'Dictate with voice'}
              title={listening ? 'Stop dictating' : 'Dictate with voice'}
            >
              <Mic size={13} />
              {listening ? <span className="tk-mic-text">listening…</span> : null}
            </MotionLoop>
          ) : null}
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
      </div>
    </m.form>
  );
});

export default ChatComposer;
