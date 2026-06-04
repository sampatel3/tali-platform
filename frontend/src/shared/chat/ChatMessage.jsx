import { ChatMarkdown } from './ChatMarkdown';

// Shared message bubble (Search-page look): user = a right-aligned ink pill,
// assistant = borderless markdown. Surface-specific extras (impact cards, tool
// calls, the workspace's cost line) slot in as children under the assistant
// text.
export function ChatMessage({ role, text, children, time }) {
  if (role === 'user') {
    return <div className="tk-msg-user">{text}</div>;
  }
  return (
    <div className="tk-msg-assistant">
      {text ? <ChatMarkdown>{text}</ChatMarkdown> : null}
      {children}
      {time ? <span className="tk-msg-time">{time}</span> : null}
    </div>
  );
}

export default ChatMessage;
