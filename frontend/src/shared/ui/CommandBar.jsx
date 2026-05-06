import React, { useEffect, useRef, useState } from 'react';
import { ChevronDown, Sparkles } from 'lucide-react';

const SCOPES = ['Candidates', 'Roles', 'Tasks', 'Reports'];

// Mission Control command bar (⌘K). Inkblock with white search input and a
// scope picker. Submit triggers `onSubmit({ scope, query })`. Pages that
// route the bar to native search dispatch a navigation; pages that pipe
// the bar through chat dispatch an agent query.
export const CommandBar = ({
  initialScope = 'Candidates',
  scopes = SCOPES,
  placeholder = 'Find or ask — “borderline candidates with backend depth”',
  onSubmit,
  ai = true,
}) => {
  const [scope, setScope] = useState(initialScope);
  const [query, setQuery] = useState('');
  const [scopeOpen, setScopeOpen] = useState(false);
  const inputRef = useRef(null);
  const scopeRef = useRef(null);

  useEffect(() => {
    const onKey = (e) => {
      const isCmdK = (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k';
      if (isCmdK) {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => {
    if (!scopeOpen) return undefined;
    const onClickAway = (e) => {
      if (scopeRef.current && !scopeRef.current.contains(e.target)) setScopeOpen(false);
    };
    document.addEventListener('mousedown', onClickAway);
    return () => document.removeEventListener('mousedown', onClickAway);
  }, [scopeOpen]);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!query.trim()) return;
    onSubmit?.({ scope, query: query.trim() });
  };

  return (
    <form className="mc-cmd" onSubmit={handleSubmit} role="search">
      <span className="mc-cmd-prefix">⌘K</span>
      <span className="mc-cmd-scope" ref={scopeRef}>
        in
        <button
          type="button"
          className="mc-cmd-scope-btn"
          aria-haspopup="listbox"
          aria-expanded={scopeOpen}
          onClick={() => setScopeOpen((open) => !open)}
        >
          {scope}
          <ChevronDown size={11} strokeWidth={2.4} />
        </button>
        {scopeOpen ? (
          <ul className="mc-cmd-scope-menu" role="listbox">
            {scopes.map((s) => (
              <li key={s}>
                <button
                  type="button"
                  role="option"
                  aria-selected={s === scope}
                  className={`mc-cmd-scope-option ${s === scope ? 'on' : ''}`.trim()}
                  onClick={() => {
                    setScope(s);
                    setScopeOpen(false);
                  }}
                >
                  {s}
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </span>
      <input
        ref={inputRef}
        className="mc-cmd-input"
        placeholder={placeholder}
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        aria-label="Search"
      />
      {ai ? (
        <span className="mc-cmd-ai">
          <Sparkles size={11} strokeWidth={2} />
          AI
        </span>
      ) : null}
    </form>
  );
};

export default CommandBar;
