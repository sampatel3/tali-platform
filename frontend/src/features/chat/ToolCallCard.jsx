import React from 'react';
import { AlertTriangle, Search } from 'lucide-react';

import { ChatActivity } from '../../shared/chat';
import { MotionSpinner } from '../../shared/motion';

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
  get_recruiting_overview: 'Checking recruiting operations',
  list_assessments: 'Checking assessments',
};

// Human-friendly one-line summary of the meaningful call arguments. Recruiters
// see the subject and count; raw tool payloads remain development-only.
const summarizeArgs = (args) => {
  if (!args || typeof args !== 'object') return '';
  const parts = [];
  const subject = args.role_name || args.query || args.requirement || args.role || args.name;
  if (typeof subject === 'string' && subject.trim()) parts.push(`for “${subject.trim()}”`);
  const count = args.limit ?? args.top_n ?? args.count;
  if (typeof count === 'number' && count > 0) parts.push(`top ${count}`);
  return parts.join(' · ');
};

const resultCount = (toolName, result) => {
  if (!result) return null;
  if (Array.isArray(result)) return `${result.length} results`;
  if (toolName === 'nl_search_candidates' || toolName === 'graph_search_candidates') {
    if (Array.isArray(result.applications)) {
      const total = result.total_matched
        ?? result.retrieval_matches
        ?? result.database_matches
        ?? result.applications.length;
      return `${result.applications.length} of ${total}`;
    }
  }
  if (toolName === 'find_top_candidates' || toolName === 'screen_pool_against_requirement') {
    if (Array.isArray(result.candidates)) {
      const shown = result.shown ?? result.candidates.length;
      return typeof result.total_matched === 'number' ? `${shown} of ${result.total_matched}` : `${shown} shown`;
    }
  }
  if (toolName === 'compare_applications' && Array.isArray(result.applications)) {
    return `${result.applications.length} compared`;
  }
  if (toolName === 'list_assessments' && typeof result.total === 'number') {
    return `${result.items?.length || 0} of ${result.total}`;
  }
  if (toolName === 'get_recruiting_overview' && typeof result.assessments?.needs_attention === 'number') {
    return `${result.assessments.needs_attention} need attention`;
  }
  return null;
};

const PendingIcon = () => <MotionSpinner size={13} />;

const ToolCallCard = ({ part }) => {
  const { toolName, args, result, status } = part;
  const isPending = status === 'streaming' || status === 'awaiting_result';
  const isError = status === 'error';
  const label = TOOL_LABELS[toolName] || String(toolName).replace(/_/g, ' ');
  const argSummary = summarizeArgs(args || {});
  const count = isPending ? null : resultCount(toolName, result);
  const summary = isError
    ? 'The tool did not complete.'
    : [argSummary, count].filter(Boolean).join(' · ')
      || (isPending ? 'Working…' : 'Completed');
  // The visible row already carries the human summary. Keep disclosure for
  // development diagnostics only, so production never hides duplicate prose
  // behind an otherwise empty "Details" affordance.
  const details = [];

  if (import.meta.env.DEV) {
    details.push({
      label: 'Arguments',
      value: <pre className="tk-activity-raw">{JSON.stringify(args || {}, null, 2)}</pre>,
    });
    if (result !== undefined) {
      details.push({
        label: 'Result',
        value: <pre className="tk-activity-raw">{JSON.stringify(result, null, 2)}</pre>,
      });
    }
  }

  return (
    <ChatActivity
      severity={isError ? 'error' : 'info'}
      severityLabel={isError ? 'Error' : isPending ? 'Running' : 'Completed'}
      typeLabel="Tool activity"
      title={label}
      summary={summary}
      icon={isError ? AlertTriangle : isPending ? PendingIcon : Search}
      details={details}
      disclosureLabel="Details"
      disclosureAriaLabel={`${label} details`}
      aria-label={`${isError ? 'Error' : isPending ? 'Running' : 'Completed'} tool activity: ${label}`}
    />
  );
};

export default ToolCallCard;
