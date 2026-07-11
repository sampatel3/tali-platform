import React, { useState } from 'react';
import { ChevronRight, Loader2, Search, AlertTriangle } from 'lucide-react';

const TOOL_LABELS = {
  list_roles: 'Listing roles',
  get_role: 'Fetching role',
  search_applications: 'Searching applications',
  nl_search_candidates: 'Searching candidates',
  graph_search_candidates: 'Searching the skills graph',
  get_application: 'Fetching application',
  get_candidate: 'Fetching candidate',
  get_candidate_cv: 'Fetching CV',
  compare_applications: 'Comparing candidates',
  find_top_candidates: 'Ranking top candidates',
  screen_pool_against_requirement: 'Screening the pool',
};

// Human-friendly one-line summary of the most meaningful call arguments — a
// recruiter reads "for 'Senior Backend', top 10", not a key="value" dump of
// internal field names and IDs.
const summarizeArgs = (args) => {
  if (!args || typeof args !== 'object') return '';
  const parts = [];
  // Lead with the search subject if present.
  const subject = args.role_name || args.query || args.requirement || args.role || args.name;
  if (typeof subject === 'string' && subject.trim()) parts.push(`for "${subject.trim()}"`);
  // Then a count, if present.
  const count = args.limit ?? args.top_n ?? args.count;
  if (typeof count === 'number' && count > 0) parts.push(`top ${count}`);
  return parts.join(', ');
};

const resultCount = (toolName, result) => {
  if (!result) return null;
  if (Array.isArray(result)) return `${result.length} results`;
  if (toolName === 'nl_search_candidates' || toolName === 'graph_search_candidates') {
    if (Array.isArray(result.applications)) {
      const total = result.total_matched ?? result.applications.length;
      return `${result.applications.length} of ${total}`;
    }
  }
  // Grounded top-N / rediscovery: mirror search-preview's "3 of 41" — shown
  // candidates over the pool that was ranked.
  if (
    toolName === 'find_top_candidates' ||
    toolName === 'screen_pool_against_requirement'
  ) {
    if (Array.isArray(result.candidates)) {
      const shown = result.shown ?? result.candidates.length;
      if (typeof result.total_matched === 'number') return `${shown} of ${result.total_matched}`;
      return `${shown} shown`;
    }
  }
  if (toolName === 'compare_applications' && Array.isArray(result.applications)) {
    return `${result.applications.length} compared`;
  }
  return null;
};

const ToolCallCard = ({ part }) => {
  const [open, setOpen] = useState(false);
  const { toolName, args, result, status } = part;
  const isPending = status === 'streaming' || status === 'awaiting_result';
  const isError = status === 'error';
  const count = isPending ? null : resultCount(toolName, result);

  return (
    <div
      className={[
        'cp-tool',
        open ? 'cp-tool-open' : '',
        isPending ? 'cp-tool-pending' : '',
        isError ? 'cp-tool-error' : '',
      ].join(' ')}
    >
      <button type="button" className="cp-tool-head" onClick={() => setOpen((v) => !v)}>
        <span className="cp-tool-glyph">
          {isError ? (
            <AlertTriangle size={13} strokeWidth={2.2} />
          ) : isPending ? (
            <Loader2 size={13} strokeWidth={2.2} className="cp-spin" />
          ) : (
            <Search size={13} strokeWidth={2.2} />
          )}
        </span>
        <span className="cp-tool-tname">{TOOL_LABELS[toolName] || String(toolName).replace(/_/g, ' ')}</span>
        <span className="cp-tool-args">{summarizeArgs(args || {})}</span>
        <span className={[
          'cp-tool-count',
          isPending ? 'cp-pending' : '',
          isError ? 'cp-error' : '',
        ].join(' ')}>
          {isError ? 'error' : isPending ? 'running…' : count || 'done'}
        </span>
        <ChevronRight size={14} className="cp-tool-chev" />
      </button>
      {open ? (
        <div className="cp-tool-body">
          <div className="cp-tool-summary">
            {TOOL_LABELS[toolName] || String(toolName).replace(/_/g, ' ')}
            {summarizeArgs(args || {}) ? ` ${summarizeArgs(args || {})}` : ''}
            {count ? ` — ${count}` : ''}
          </div>
          {/* Raw args/result are developer internals — behind the dev flag only,
              never shown to recruiters. */}
          {import.meta.env.DEV ? (
            <div className="cp-tool-kv">
              <div className="k">args</div>
              <div className="v">
                <pre className="cp-tool-raw">{JSON.stringify(args || {}, null, 2)}</pre>
              </div>
              {result !== undefined ? (
                <>
                  <div className="k">result</div>
                  <div className="v">
                    <pre className="cp-tool-raw">{JSON.stringify(result, null, 2)}</pre>
                  </div>
                </>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
};

export default ToolCallCard;
