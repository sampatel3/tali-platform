import React, { useCallback, useId } from 'react';
import { Link, useLocation } from 'react-router-dom';

import { useUrlState } from '../../shared/hooks/useUrlState';
import { LayoutGroup, m, motionTransition, useReducedMotionSync } from '../../shared/motion';

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

export const RoleViewTabs = ({ activeView }) => {
  const location = useLocation();
  const reduced = useReducedMotionSync();
  const layoutId = `role-view-tab-${useId().replace(/:/g, '')}`;
  const hrefFor = (target) => {
    const params = new URLSearchParams(location.search);
    if (target === 'table') params.delete('view');
    else params.set('view', target);
    const qs = params.toString();
    return qs ? `${location.pathname}?${qs}` : location.pathname;
  };
  return (
    <LayoutGroup id={layoutId}>
      <nav className="sub-tabs-sticky vtabs" aria-label="Job views">
        {TABS.map((tab) => {
          const active = activeView === tab.id;
          return (
            <Link
              key={tab.id}
              to={hrefFor(tab.id)}
              className={`vtab ${active ? 'on' : ''}`.trim()}
              aria-current={active ? 'page' : undefined}
            >
              {tab.label}
              {active ? (
                <m.span
                  aria-hidden="true"
                  className="vtab-motion-indicator"
                  layoutId={`${layoutId}-indicator`}
                  transition={reduced ? motionTransition.instant : motionTransition.layout}
                />
              ) : null}
            </Link>
          );
        })}
      </nav>
    </LayoutGroup>
  );
};

export default RoleViewTabs;
