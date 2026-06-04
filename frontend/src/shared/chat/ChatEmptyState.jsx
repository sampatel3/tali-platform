import { MessageSquare } from 'lucide-react';

const DefaultGlyph = () => <MessageSquare size={22} />;

// The dark rounded glyph + heading + suggestion cards from the Search page.
// `title` is a node so callers can accent it (e.g. <>What are you looking for<em>?</em></>).
// `suggestions` items are strings or { tag, q } objects (tag = the mono kicker).
// `compact` shrinks it for narrow surfaces (the Home dock) — smaller heading,
// single-column suggestions.
export function ChatEmptyState({ glyph, title, sub, suggestions = [], onPick, compact = false }) {
  return (
    <div className={`tk-empty${compact ? ' is-compact' : ''}`}>
      <div className="tk-empty-glyph">{glyph || <DefaultGlyph />}</div>
      <h1 className="tk-empty-h1">{title}</h1>
      {sub ? <p className="tk-empty-sub">{sub}</p> : null}
      {suggestions.length > 0 && (
        <div className="tk-suggest-grid">
          {suggestions.map((s) => {
            const tag = typeof s === 'object' ? s.tag : null;
            const q = typeof s === 'object' ? s.q : s;
            return (
              <button key={q} type="button" className="tk-suggest" onClick={() => onPick?.(q)}>
                {tag ? <span className="tk-suggest-tag">{tag}</span> : null}
                <span className="tk-suggest-q">{q}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default ChatEmptyState;
