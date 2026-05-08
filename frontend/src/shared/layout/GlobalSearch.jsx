// Global search — the top-right pill that used to be a no-op placeholder.
//
// Plain word search across candidates / roles / tasks (the three things
// the placeholder copy promised). Lists are fetched once on first focus
// and cached in component state; filtering is client-side so typing
// stays instant. There is always an "Ask Search AI" escape hatch at the
// bottom of the dropdown that hands the query off to the /chat tab —
// that's where natural-language / semantic search lives, not here.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Briefcase, CheckSquare, MessageSquare, Search, User } from 'lucide-react';

import { candidates as candidatesApi, roles as rolesApi, tasks as tasksApi } from '../api';

const MAX_PER_GROUP = 5;

const norm = (value) => String(value || '').toLowerCase();

const candidateLabel = (row) => (
  String(row?.full_name || row?.name || row?.email || `Candidate #${row?.id}`).trim()
);
const candidateSub = (row) => (
  String(row?.email || row?.position || '').trim()
);
const candidateSearchText = (row) => [
  row?.full_name,
  row?.name,
  row?.email,
  row?.position,
  row?.id,
].map(norm).join(' ');

const roleLabel = (row) => (
  String(row?.short_name || row?.name || `Role #${row?.id}`).trim()
);
const roleSub = (row) => (
  String(row?.location || row?.seniority || row?.department || '').trim()
);
const roleSearchText = (row) => [
  row?.name,
  row?.short_name,
  row?.location,
  row?.seniority,
  row?.department,
  row?.id,
].map(norm).join(' ');

const taskLabel = (row) => (
  String(row?.name || row?.task_key || `Task ${row?.id || ''}`).trim()
);
const taskSub = (row) => (
  String(row?.role || row?.role_name || row?.category || row?.difficulty || '').trim()
);
const taskSearchText = (row) => [
  row?.name,
  row?.task_key,
  row?.description,
  row?.scenario,
  row?.role,
  row?.role_name,
  row?.difficulty,
].map(norm).join(' ');

const groupConfig = [
  { id: 'candidates', label: 'Candidates', Icon: User, sub: candidateSub, primary: candidateLabel },
  { id: 'roles', label: 'Roles', Icon: Briefcase, sub: roleSub, primary: roleLabel },
  { id: 'tasks', label: 'Tasks', Icon: CheckSquare, sub: taskSub, primary: taskLabel },
];

export const GlobalSearch = ({ onNavigate }) => {
  const wrapRef = useRef(null);
  const inputRef = useRef(null);
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [data, setData] = useState({ candidates: [], roles: [], tasks: [] });

  const ensureLoaded = useCallback(async () => {
    if (loaded || loading) return;
    setLoading(true);
    setError(null);
    try {
      const [candRes, rolesRes, tasksRes] = await Promise.all([
        candidatesApi.list().catch(() => ({ data: [] })),
        rolesApi.list().catch(() => ({ data: [] })),
        tasksApi.list().catch(() => ({ data: [] })),
      ]);
      setData({
        candidates: Array.isArray(candRes?.data) ? candRes.data : [],
        roles: Array.isArray(rolesRes?.data) ? rolesRes.data : [],
        tasks: Array.isArray(tasksRes?.data) ? tasksRes.data : [],
      });
      setLoaded(true);
    } catch (err) {
      setError(err?.message || 'Failed to load search index.');
    } finally {
      setLoading(false);
    }
  }, [loaded, loading]);

  // cmd/ctrl-K opens and focuses; Escape closes.
  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        setOpen(true);
        void ensureLoaded();
        window.requestAnimationFrame(() => inputRef.current?.focus());
      } else if (e.key === 'Escape' && open) {
        setOpen(false);
        inputRef.current?.blur();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [ensureLoaded, open]);

  // click-outside closes the dropdown.
  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) {
      return {
        candidates: data.candidates.slice(0, MAX_PER_GROUP),
        roles: data.roles.slice(0, MAX_PER_GROUP),
        tasks: data.tasks.slice(0, MAX_PER_GROUP),
      };
    }
    const candidates = data.candidates.filter((row) => candidateSearchText(row).includes(q)).slice(0, MAX_PER_GROUP);
    const roles = data.roles.filter((row) => roleSearchText(row).includes(q)).slice(0, MAX_PER_GROUP);
    const tasks = data.tasks.filter((row) => taskSearchText(row).includes(q)).slice(0, MAX_PER_GROUP);
    return { candidates, roles, tasks };
  }, [data, query]);

  const totalMatches = filtered.candidates.length + filtered.roles.length + filtered.tasks.length;

  const handleSelect = (group, row) => {
    setOpen(false);
    setQuery('');
    if (group === 'candidates') {
      // Candidates don't have a 1:1 application id without role context;
      // jump to the directory pre-filtered by name so the user lands on
      // the correct row.
      onNavigate?.('candidates', { search: candidateLabel(row) });
    } else if (group === 'roles') {
      onNavigate?.('job-pipeline', { roleId: row?.id });
    } else if (group === 'tasks') {
      onNavigate?.('tasks', { focusTaskId: row?.id || row?.task_key });
    }
  };

  const handleAskAi = () => {
    setOpen(false);
    onNavigate?.('chat', { initialQuery: query.trim() || undefined });
    setQuery('');
  };

  // Pressing Enter with a query: jump straight to the first match across
  // all groups, or fall through to Ask AI if there are no matches.
  const handleSubmit = (e) => {
    e.preventDefault();
    if (filtered.candidates[0]) handleSelect('candidates', filtered.candidates[0]);
    else if (filtered.roles[0]) handleSelect('roles', filtered.roles[0]);
    else if (filtered.tasks[0]) handleSelect('tasks', filtered.tasks[0]);
    else handleAskAi();
  };

  return (
    <div ref={wrapRef} className={`mc-nav-search-wrap ${open ? 'open' : ''}`.trim()}>
      <form className="mc-nav-search-form" onSubmit={handleSubmit} role="search">
        <Search size={13} strokeWidth={2} aria-hidden="true" />
        <input
          ref={inputRef}
          type="search"
          className="mc-nav-search-input"
          value={query}
          placeholder="Search candidates, roles, tasks…"
          onChange={(e) => setQuery(e.target.value)}
          onFocus={() => {
            setOpen(true);
            void ensureLoaded();
          }}
          aria-label="Search candidates, roles, and tasks"
        />
        <kbd>⌘K</kbd>
      </form>

      {open ? (
        <div className="mc-nav-search-popover" role="listbox">
          {loading ? (
            <div className="mc-nav-search-empty">Loading…</div>
          ) : error ? (
            <div className="mc-nav-search-empty">{error}</div>
          ) : totalMatches === 0 ? (
            <div className="mc-nav-search-empty">
              {query.trim()
                ? `No matches for "${query.trim()}".`
                : 'Start typing to search candidates, roles, or tasks.'}
            </div>
          ) : (
            <div className="mc-nav-search-groups">
              {groupConfig.map(({ id, label, Icon, sub, primary }) => {
                const rows = filtered[id];
                if (!rows.length) return null;
                return (
                  <div key={id} className="mc-nav-search-group">
                    <div className="mc-nav-search-grouphead">
                      <Icon size={12} strokeWidth={1.8} aria-hidden="true" />
                      <span>{label}</span>
                      <span className="count">{rows.length}</span>
                    </div>
                    {rows.map((row) => (
                      <button
                        key={`${id}-${row.id || primary(row)}`}
                        type="button"
                        className="mc-nav-search-row"
                        onClick={() => handleSelect(id, row)}
                      >
                        <span className="title">{primary(row)}</span>
                        {sub(row) ? <span className="sub">{sub(row)}</span> : null}
                      </button>
                    ))}
                  </div>
                );
              })}
            </div>
          )}

          <button
            type="button"
            className="mc-nav-search-ai"
            onClick={handleAskAi}
          >
            <MessageSquare size={13} strokeWidth={1.8} aria-hidden="true" />
            <span>
              Ask Search AI
              {query.trim() ? <em> · &ldquo;{query.trim()}&rdquo;</em> : null}
            </span>
            <span className="arrow" aria-hidden="true">→</span>
          </button>
        </div>
      ) : null}
    </div>
  );
};

export default GlobalSearch;
