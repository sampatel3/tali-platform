import ReactMarkdown from 'react-markdown';

// External links open in a new tab, safely. Everything else is plain semantic
// HTML styled by `.tk-md` in chat-kit.css — so assistant text renders the same
// on every chat surface.
const SafeLink = ({ href, children }) => (
  <a href={href} target="_blank" rel="noreferrer noopener">{children}</a>
);

const COMPONENTS = { a: SafeLink };

// Candidate assessment transcripts are untrusted model output. Rendering a
// URL as inert text avoids one-click navigation out of the workspace, and
// suppressing markdown images avoids an automatic request to a model-chosen
// remote host. This is browser UX containment, not an OS-level guarantee: a
// candidate can still retype a visible URL in another browser or device.
const InertLink = ({ children }) => (
  <span data-assessment-link-disabled="true">{children}</span>
);

const InertImage = ({ alt }) => (
  alt ? <span data-assessment-image-disabled="true">{alt}</span> : null
);

const CONTAINED_COMPONENTS = { a: InertLink, img: InertImage };

export function ChatMarkdown({ children, disableLinks = false }) {
  return (
    <div className="tk-md">
      <ReactMarkdown components={disableLinks ? CONTAINED_COMPONENTS : COMPONENTS}>
        {String(children || '')}
      </ReactMarkdown>
    </div>
  );
}

export default ChatMarkdown;
