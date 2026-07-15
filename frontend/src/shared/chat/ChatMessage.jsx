import { ChatMarkdown } from './ChatMarkdown';
import { Sparkles } from 'lucide-react';

// Short local clock time (e.g. "2:34 PM") from an ISO timestamp; '' if absent
// or unparseable, so a missing time just renders nothing.
const fmtTime = (iso) => {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ''
    : d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
};

// Shared message bubble (Search-page look): user = a right-aligned ink pill,
// assistant = borderless markdown. Surface-specific extras (impact cards, tool
// calls, the workspace's cost line) slot in as children under the assistant
// text. `time` is the message's ISO `created_at` — shown under the bubble so
// you can see when each message was sent and when the agent replied.
export function ChatMessage({ role, text, children, time, label }) {
  const stamp = fmtTime(time);
  if (role === 'user') {
    return (
      <div className="tk-msg-user-wrap">
        <div className="tk-msg-user">{text}</div>
        {stamp ? <time className="tk-msg-time">{stamp}</time> : null}
      </div>
    );
  }
  return (
    <article className="tk-msg-assistant">
      {label ? (
        <header className="tk-msg-author">
          <span className="tk-msg-avatar" aria-hidden="true"><Sparkles size={12} /></span>
          <strong>{label}</strong>
          {stamp ? <time className="tk-msg-time">· {stamp}</time> : null}
        </header>
      ) : null}
      <div className="tk-msg-assistant-body">
        {text ? <ChatMarkdown>{text}</ChatMarkdown> : null}
        {children}
      </div>
      {!label && stamp ? <time className="tk-msg-time">{stamp}</time> : null}
    </article>
  );
}

export default ChatMessage;
