// Recruiter-side view of the assessment integrity metrics.
//
// The same registry that tells the candidate what is logged
// (``sessionDisclosure.js``) drives what the hiring team sees, so the two
// descriptions of the same data cannot diverge.
//
// Before this module the events reached the recruiter only by accident: the
// generic fallback in ``normalizeTimelineEvents`` prettified them into rows
// labelled "Visibility Hidden" and "Copy Attempt" among the work events, while
// the "Tab switches" card read 0 because ``tab_switch_count`` is only submitted
// when proctoring is on — which it never is. Real signal, invisible; a zeroed
// counter, prominent.

import { WORKSPACE_SIGNAL_EVENTS, WORKSPACE_SIGNAL_GROUPS } from './sessionDisclosure';

const TAB_FOCUS_EVENTS = Object.freeze(['visibility_hidden', 'fullscreen_exit']);

const eventTypeOf = (event) => String(
  event?.event_type || event?.type || event?.event || '',
).toLowerCase();

export const isIntegritySignalEvent = (event) => WORKSPACE_SIGNAL_EVENTS.includes(eventTypeOf(event));

/** Count the advisory integrity events on an assessment timeline, by group. */
export const summarizeIntegritySignals = (timeline) => {
  const events = (Array.isArray(timeline) ? timeline : []).filter(
    (event) => event && typeof event === 'object' && isIntegritySignalEvent(event),
  );

  const groups = WORKSPACE_SIGNAL_GROUPS.map((group) => ({
    label: group.label,
    count: events.filter((event) => group.events.includes(eventTypeOf(event))).length,
  }));

  const tabFocusEvents = events.filter((event) => TAB_FOCUS_EVENTS.includes(eventTypeOf(event)));

  return {
    groups,
    total: events.length,
    // The fraud-relevant one: the workspace can stop content leaving, but not a
    // screenshot and a question asked somewhere else. Surfaced with timestamps
    // so a reviewer can line it up against the work.
    tabFocusCount: tabFocusEvents.length,
    tabFocusTimestamps: tabFocusEvents
      .map((event) => event.timestamp || null)
      .filter(Boolean),
    hasData: events.length > 0,
  };
};

/**
 * Tab-focus losses for the recruiter counter.
 *
 * ``tab_switch_count`` is only populated when proctoring is enabled, so with
 * MVP_DISABLE_PROCTORING it is always 0 even when the timeline holds the
 * events. Prefer the stored count when it is real, else derive it.
 */
export const resolveTabSwitchCount = (assessment, summary) => {
  const stored = Number(assessment?.tab_switch_count);
  if (Number.isFinite(stored) && stored > 0) return stored;
  return summary?.tabFocusCount ?? 0;
};
