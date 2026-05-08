// Global search — the top-right pill that used to be a no-op placeholder.
//
// Strategy is split per source:
//
// * Candidates: server-side substring search via /applications?search=… —
//   each row is an application (candidate × role) so a candidate who
//   applied to three roles shows three times, and selecting one lands
//   on that specific standing report. Debounced 200ms while typing.
// * Roles + tasks: cardinality is small (<100 in practice), the API
//   returns flat arrays, so we cache once on first focus and filter
//   client-side. Cheap and instant.
//
// "Ask Search AI" is the always-on escape hatch at the bottom of the
// dropdown — hands the typed phrase to the /chat tab where natural-
// language / semantic search lives. This pill stays plain word search.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Briefcase, CheckSquare, MessageSquare, Search, User } from 'lucide-react';

import { roles as rolesApi, tasks as tasksApi } from '../api';

const MAX_PER_GROUP = 5;
const SERVER_SEARCH_LIMIT = 20;
const DEBOUNCE_MS = 200;

const norm = (value) => String(value || '').toLowerCase();

// Each row is an application — one (candidate, role) pair.
const candidateLabel = (row) => (
  String(row?.candidate_name || row?.candidate_email || `Application #${row?.id}`).trim()
);
const candidateSub = (row) => {
  const role = String(row?.role_name || row?.candidate_position || '').trim();
  const email = String(row?.candidate_email || '').trim();
  if (role && email) return `${role} · ${email}`;
  return role || email;
};

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

// Roles + tasks list endpoints return flat arrays today (no pagination,
// no q param), so the array.isArray fallback is correct. Candidates is
// the one that returns {items, total, limit, offset} — read .items.
const arrayBody = (res) => (Array.isArray(res?.data) ? res.data : []);
const itemsBody = (res) => {
  const body = res?.data;
  if (Array.isArray(body)) return body;
  if (body && Array.isArray(body.items)) return body.items;
  return [];
};

export const GlobalSearch = ({ onNavigate }) => {
  const wrapRef = useRef(null);
  const inputRef = useRef(null);
  const candidateRequestRef = useRef(0);
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [staticLoaded, setStaticLoaded] = useState(false);
  const [staticLoading, setStaticLoading] = useState(false);
  const [candidateLoading, setCandidateLoading] = useState(false);
  const [error, setError] = useState(null);
  const [staticData, setStaticData] = useState({ roles: [], tasks: [] });
  const [recentCandidates, setRecentCandidates] = useState([]);
  const [candidateMatches, setCandidateMatches] = useState([]);

  // Roles + tasks: cache once. Recent applications also fetched here
  // (limit=20) for the no-query "recent" panel; live queries refetch
  // with search=.
  const ensureStaticLoaded = useCallback(async () => {
    if (staticLoaded || staticLoading) return;
    setStaticLoading(true);
    setError(null);
    try {
      const [appsRes, rolesRes, tasksRes] = await Promise.all([
        rolesApi.listApplicationsGlobal({
          limit: SERVER_SEARCH_LIMIT,
          application_outcome: 'all',
        }).catch(() => null),
        rolesApi.list().catch(() => null),
        tasksApi.list().catch(() => null),
      ]);
      setRecentCandidates(itemsBody(appsRes));
      setStaticData({
        roles: arrayBody(rolesRes),
        tasks: arrayBody(tasksRes),
      });
      setStaticLoaded(true);
    } catch (err) {
      setError(err?.message || 'Failed to load search index.');
    } finally {
      setStaticLoading(false);
    }
  }, [staticLoaded, staticLoading]);

  // Candidate live search: debounced server call. ``candidateRequestRef``
  // lets a slow request lose to a faster newer one without overwriting
  // results when the user has already moved on.
  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setCandidateMatches([]);
      setCandidateLoading(false);
      return undefined;
    }
    const requestId = candidateRequestRef.current + 1;
    candidateRequestRef.current = requestId;
    setCandidateLoading(true);
    const handle = window.setTimeout(async () => {
      try {
        const res = await rolesApi.listApplicationsGlobal({
          search: q,
          limit: SERVER_SEARCH_LIMIT,
          application_outcome: 'all',
        });
        if (candidateRequestRef.current !== requestId) return;
        setCandidateMatches(itemsBody(res));
      } catch (err) {
        if (candidateRequestRef.current !== requestId) return;
        setError(err?.message || 'Candidate search failed.');
      } finally {
        if (candidateRequestRef.current === requestId) setCandidateLoading(false);
      }
    }, DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [query]);

  // cmd/ctrl-K opens and focuses; Escape closes.
  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        setOpen(true);
        void ensureStaticLoaded();
        window.requestAnimationFrame(() => inputRef.current?.focus());
      } else if (e.key === 'Escape' && open) {
        setOpen(false);
        inputRef.current?.blur();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [ensureStaticLoaded, open]);

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
        candidates: recentCandidates.slice(0, MAX_PER_GROUP),
        roles: staticData.roles.slice(0, MAX_PER_GROUP),
        tasks: staticData.tasks.slice(0, MAX_PER_GROUP),
      };
    }
    return {
      candidates: candidateMatches.slice(0, MAX_PER_GROUP),
      roles: staticData.roles.filter((row) => roleSearchText(row).includes(q)).slice(0, MAX_PER_GROUP),
      tasks: staticData.tasks.filter((row) => taskSearchText(row).includes(q)).slice(0, MAX_PER_GROUP),
    };
  }, [recentCandidates, staticData, candidateMatches, query]);

  const totalMatches = filtered.candidates.length + filtered.roles.length + filtered.tasks.length;
  const isLoading = staticLoading || candidateLoading;

  const handleSelect = (group, row) => {
    setOpen(false);
    setQuery('');
    if (group === 'candidates') {
      onNavigate?.('candidate-report', { candidateApplicationId: row?.id });
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
            void ensureStaticLoaded();
          }}
          aria-label="Search candidates, roles, and tasks"
        />
        <kbd>⌘K</kbd>
      </form>

      {open ? (
        <div className="mc-nav-search-popover" role="listbox">
          {isLoading && totalMatches === 0 ? (
            <div className="mc-nav-search-empty">Searching…</div>
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
