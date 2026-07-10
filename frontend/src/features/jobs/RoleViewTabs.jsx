import React, { useCallback } from 'react';
import { Link, useLocation } from 'react-router-dom';

import { useUrlState } from '../../shared/hooks/useUrlState';

const TABS = [
  { id: 'table', label: 'Candidates' },
  { id: 'pipeline', label: 'Pipeline' },
  { id: 'activity', label: 'Job spec' },
  { id: 'role-fit', label: 'Agent settings' },
];

// Sticky sub-tab row for the role detail page. Selection is mirrored
// in `?view=` so ctrl/cmd+click opens the tab in a new browser tab and
// the URL is shareable. `table` is the implicit default (no param).
export function useRoleView() {
  const [activeViewParam, setActiveViewParam] = useUrlState('view', '');
  const activeView = activeViewParam || 'table';
  const setActiveView = useCallback(
    (next) => setActiveViewParam(next === 'table' ? '' : next),
    [setActiveViewParam],
  );
  return [activeView, setActiveView];
}

export const RoleViewTabs = ({ activeView }) => {
  const location = useLocation();
  const hrefFor = (target) => {
    const params = new URLSearchParams(location.search);
    if (target === 'table') params.delete('view');
    else params.set('view', target);
    const qs = params.toString();
    return qs ? `${location.pathname}?${qs}` : location.pathname;
  };
  return (
    <div className="sub-tabs-sticky vtabs">
      {TABS.map((tab) => (
        <Link
          key={tab.id}
          to={hrefFor(tab.id)}
          className={`vtab ${activeView === tab.id ? 'on' : ''}`.trim()}
        >
          {tab.label}
        </Link>
      ))}
    </div>
  );
};

export default RoleViewTabs;
