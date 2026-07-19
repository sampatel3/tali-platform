import React, { useCallback } from 'react';
import { useLocation } from 'react-router-dom';

import { useUrlState } from '../../shared/hooks/useUrlState';
import { FocusedSectionNav } from '../../shared/ui/SectionNavigation';

const TABS = [
  { id: 'table', label: 'Candidates' },
  { id: 'pipeline', label: 'Pipeline' },
  { id: 'activity', label: 'Job spec' },
  { id: 'role-fit', label: 'Agent settings' },
  { id: 'hiring-team', label: 'Hiring team' },
];
const TAB_IDS = new Set(TABS.map((tab) => tab.id));

// Sticky sub-tab row for the role detail page. Selection is mirrored
// in `?view=` so ctrl/cmd+click opens the tab in a new browser tab and
// the URL is shareable. `table` is the implicit default (no param).
export function useRoleView() {
  const [activeViewParam, setActiveViewParam] = useUrlState('view', '');
  // Retired/unknown deep links (notably the old manual `?view=find` surface)
  // settle on Candidates so the page always has a selected, visible tab.
  const activeView = TAB_IDS.has(activeViewParam) ? activeViewParam : 'table';
  const setActiveView = useCallback(
    (next) => setActiveViewParam(next === 'table' ? '' : next),
    [setActiveViewParam],
  );
  return [activeView, setActiveView];
}

export const RoleViewTabs = ({ activeView, onBeforeNavigate }) => {
  const location = useLocation();
  const hrefFor = (target) => {
    const params = new URLSearchParams(location.search);
    if (target === 'table') params.delete('view');
    else params.set('view', target);
    const qs = params.toString();
    return qs ? `${location.pathname}?${qs}` : location.pathname;
  };

  const items = TABS.map((tab) => ({
    ...tab,
    to: hrefFor(tab.id),
    onClick: (event) => onBeforeNavigate?.(event, tab.id),
  }));

  return (
    <FocusedSectionNav
      items={items}
      activeId={TAB_IDS.has(activeView) ? activeView : 'table'}
      ariaLabel="Job views"
      idPrefix="role-view"
      className="sub-tabs-sticky"
      variant="bar"
      sticky
    />
  );
};

export default RoleViewTabs;
