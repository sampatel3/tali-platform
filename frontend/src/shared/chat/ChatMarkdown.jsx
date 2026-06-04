import ReactMarkdown from 'react-markdown';

// External links open in a new tab, safely. Everything else is plain semantic
// HTML styled by `.tk-md` in chat-kit.css — so assistant text renders the same
// on every chat surface.
const SafeLink = ({ href, children }) => (
  <a href={href} target="_blank" rel="noreferrer noopener">{children}</a>
);

const COMPONENTS = { a: SafeLink };

export function ChatMarkdown({ children }) {
  return (
    <div className="tk-md">
      <ReactMarkdown components={COMPONENTS}>{String(children || '')}</ReactMarkdown>
    </div>
  );
}

export default ChatMarkdown;
