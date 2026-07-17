// Recent decisions — a deliberately minimal list of the calls a recruiter (the
// human in the loop) has made on candidates: who, what was decided, when, and a
// link to their report. Use it to find a candidate again after they've moved on
// (e.g. advanced to Workable). The full audit trail lives on Analytics →
// Decision log; the rich pending queue lives above this.

import React, { useEffect, useState } from 'react';

import { agent as agentApi } from '../../shared/api';
import { Avatar, formatRelativeAge, initialsFrom } from './atoms';
import { pathForPage } from '../../app/routing';

// Map a resolved decision to the plain-English outcome the recruiter landed on.
// On an override the recruiter chose differently, so read the override action;
// otherwise the agent's recommendation (decision_type) is what they approved.
const outcomeFor = (row) => {
  const status = String(row?.status || '').toLowerCase();
  const overrideAction = String(row?.override_action || '').toLowerCase();
  const basis = status === 'overridden' && overrideAction ? overrideAction : String(row?.decision_type || '').toLowerCase();
  if (basis.includes('reject') || basis.includes('skip')) return { label: 'Rejected', tone: 'mute' };
  if (basis.includes('advance')) return { label: 'Advanced', tone: 'purple' };
  if (basis.includes('send') || basis.includes('assessment') || basis.includes('invite')) return { label: 'Assessment sent', tone: 'purple' };
  return { label: status === 'overridden' ? 'Overridden' : 'Decided', tone: 'mute' };
};

export const RecentDecisions = ({ roleId = null, collapsedCount = 5, refreshKey = 0 }) => {
  const [expanded, setExpanded] = useState(false);
  // The hub's main feed loads PENDING decisions, so fetch the human-made calls
  // (approved / overridden) ourselves — scoped to the selected role, newest
  // first. Use status=decided rather than the broader ``resolved`` so the row
  // limit isn't spent on bulk discarded/expired rows, which would push genuine
  // decisions out of the window and blank this panel.
  // refreshKey is bumped by the hub after every approve/override/snooze so the
  // decision the recruiter just made appears here without a page reload — the
  // whole point of "find a candidate again after they've moved on".
  const [rows, setRows] = useState([]);
  useEffect(() => {
    let cancelled = false;
    // A background refetch (focus/visibility) must not clobber a good list with
    // an empty one if the request transiently fails — only replace on success.
    const load = () => agentApi
      .listDecisions({ status: 'decided', role_id: roleId || undefined, limit: 25 })
      .then((res) => { if (!cancelled) setRows(Array.isArray(res?.data) ? res.data : []); })
      .catch(() => {});
    void load();
    // Re-pull when the tab regains focus so a decision made elsewhere (or a
    // cold-load fetch that lost the auth race and came back empty) shows up
    // without a manual refresh — refreshKey only covers actions taken here.
    const refresh = () => { if (document.visibilityState === 'visible') void load(); };
    window.addEventListener('focus', refresh);
    document.addEventListener('visibilitychange', refresh);
    return () => {
      cancelled = true;
      window.removeEventListener('focus', refresh);
      document.removeEventListener('visibilitychange', refresh);
    };
  }, [roleId, refreshKey]);
  // Only human-made calls (defensive — the fetch already scopes to decided).
  const decided = rows.filter((r) => {
    const s = String(r?.status || '').toLowerCase();
    return s === 'approved' || s === 'overridden';
  });
  if (!decided.length) return null;
  const shown = expanded ? decided : decided.slice(0, collapsedCount);

  return (
    <section className="home-section rq-recent-card">
      <div className="home-section-head">
        <div>
          <span className="kicker">RECENT DECISIONS</span>
          <p className="home-section-sub" style={{ marginTop: 4 }}>
            Calls you&apos;ve made — find a candidate again after they&apos;ve moved on.
          </p>
        </div>
      </div>
      <ul className="rq-recent-list">
        {shown.map((row) => {
          const outcome = outcomeFor(row);
          return (
            <li key={row.id} className="rq-recent-row">
              <Avatar initials={initialsFrom(row.candidate_name)} size={28} />
              <a
                href={pathForPage('candidate-report', {
                  candidateApplicationId: row.application_id,
                  fromHome: true,
                  viewRoleId: row.role_id,
                })}
                target="_blank"
                rel="noopener noreferrer"
                className="rq-recent-name"
                title="Open candidate report in a new tab"
              >
                {row.candidate_name || `Application #${row.application_id}`}
              </a>
              <span className={`rq-recent-outcome rq-recent-outcome--${outcome.tone}`}>{outcome.label}</span>
              <span className="rq-recent-age">{formatRelativeAge(row.resolved_at || row.created_at)} ago</span>
            </li>
          );
        })}
      </ul>
      {decided.length > collapsedCount ? (
        <button type="button" className="rq-feed-toggle" onClick={() => setExpanded((v) => !v)}>
          {expanded ? 'Show fewer' : `Show all ${decided.length} decisions`}
        </button>
      ) : null}
    </section>
  );
};

export default RecentDecisions;
