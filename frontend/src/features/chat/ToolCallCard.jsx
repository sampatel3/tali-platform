import React, { useState } from 'react';
import { ChevronRight, Loader2, Search, AlertTriangle } from 'lucide-react';

const TOOL_LABELS = {
  list_roles: 'list_roles',
  get_role: 'get_role',
  search_applications: 'search_applications',
  nl_search_candidates: 'nl_search_candidates',
  graph_search_candidates: 'graph_search_candidates',
  get_application: 'get_application',
  get_candidate: 'get_candidate',
  get_candidate_cv: 'get_candidate_cv',
  compare_applications: 'compare_applications',
};

// Render an arg value the way search-preview does: string values quoted
// ("Senior Backend"), numbers/booleans bare, arrays bracketed.
const fmtVal = (v) => {
  if (Array.isArray(v)) return `[${v.join(',')}]`;
  if (typeof v === 'string') return `"${v}"`;
  return String(v);
};

// search-preview shows args as `key="value" · key=value` — the parts joined by
// a mono middot, not a bare space.
const summarizeArgs = (args) => {
  if (!args || typeof args !== 'object') return '';
  const entries = Object.entries(args).filter(([, v]) => v != null);
  const pieces = [];
  entries.forEach(([k, v], i) => {
    pieces.push(
      <span key={k}>
        <b>{k}=</b>
        {typeof v === 'object' && !Array.isArray(v) ? '{…}' : fmtVal(v)}
      </span>,
    );
    if (i < entries.length - 1) pieces.push(<span key={`${k}-sep`}> · </span>);
  });
  return pieces;
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
        <span className="cp-tool-tname">{TOOL_LABELS[toolName] || toolName}</span>
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
          <div className="cp-tool-kv">
            <div className="k">tool</div><div className="v">{toolName}</div>
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
        </div>
      ) : null}
    </div>
  );
};

export default ToolCallCard;
