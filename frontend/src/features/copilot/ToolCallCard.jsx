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

const summarizeArgs = (args) => {
  if (!args || typeof args !== 'object') return '';
  const pieces = [];
  for (const [k, v] of Object.entries(args)) {
    if (v == null) continue;
    if (Array.isArray(v)) {
      pieces.push(<span key={k}><b>{k}=</b>[{v.join(',')}]</span>);
    } else if (typeof v === 'object') {
      pieces.push(<span key={k}><b>{k}=</b>{'{…}'}</span>);
    } else {
      pieces.push(<span key={k}><b>{k}=</b>{String(v)} </span>);
    }
  }
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
