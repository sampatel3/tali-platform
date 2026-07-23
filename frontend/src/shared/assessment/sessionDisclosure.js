// Single source of truth for what a candidate is told an assessment session
// records.
//
// Two independent layers write to the session record and the disclosure has to
// name both:
//
//  1. The work transcript — prompts, Claude responses, file changes, validation
//     runs. Always captured for a live assessment.
//  2. Workspace controls (``AssessmentWorkspaceSecurity.jsx``) — advisory
//     browser signals, on for every real assessment regardless of proctoring,
//     and switched off only by an approved clipboard accommodation or in demo
//     mode. Best-effort and candidate-controlled: evidence on the timeline,
//     never proof on their own.
//
// Before this module the in-session summary flag read "Session transcript only"
// while layer 2 was recording copy attempts, blocked right-clicks and tab
// visibility changes. ``sessionDisclosure.test.js`` pins the list below against
// the events the runtime actually emits and the server's allow-list so the two
// cannot drift apart again.

const joinWithAnd = (items, separator = ', ') => {
  const parts = items.filter(Boolean);
  if (parts.length <= 1) return parts.join('');
  if (parts.length === 2) return parts.join(' and ');
  return `${parts.slice(0, -1).join(separator)}${separator}and ${parts[parts.length - 1]}`;
};

// Layer 1 — what we record about the candidate's work.
export const WORK_RECORD_ITEMS = Object.freeze([
  'your prompts',
  "Claude's responses",
  'your file changes',
  'validation runs',
]);

// Layer 2 — advisory workspace signals, grouped so the candidate-facing copy
// stays readable. Every event type the workspace can emit must appear here.
export const WORKSPACE_SIGNAL_GROUPS = Object.freeze([
  Object.freeze({
    label: 'copy, cut, and paste',
    events: Object.freeze([
      'copy_attempt',
      'cut_attempt',
      'internal_paste',
      'external_paste_blocked',
    ]),
  }),
  Object.freeze({
    label: 'right-click, drag-and-drop, and printing',
    events: Object.freeze([
      'context_menu_blocked',
      'drag_drop_blocked',
      'print_attempt',
    ]),
  }),
  Object.freeze({
    label: 'leaving the tab or exiting fullscreen',
    events: Object.freeze(['visibility_hidden', 'fullscreen_exit']),
  }),
]);

export const WORKSPACE_SIGNAL_EVENTS = Object.freeze(
  WORKSPACE_SIGNAL_GROUPS.flatMap((group) => group.events),
);

// Groups carry internal commas, so separate them with semicolons.
export const WORKSPACE_SIGNAL_SUMMARY = joinWithAnd(
  WORKSPACE_SIGNAL_GROUPS.map((group) => group.label),
  '; ',
);

export const WORK_RECORD_SENTENCE = `We record your work in this session: ${joinWithAnd(WORK_RECORD_ITEMS)}.`;

export const WORKSPACE_SIGNAL_SENTENCE = `The workspace also logs advisory signals — ${WORKSPACE_SIGNAL_SUMMARY} — which the hiring team sees alongside your work.`;

// The honest framing the workspace-control layer already uses internally
// (AssessmentWorkspaceSecurity.jsx: "activity signals are advisory"), surfaced
// to the candidate rather than left in the code.
export const WORKSPACE_SIGNAL_CAVEAT = 'They are best-effort browser signals, not proof of anything on their own.';

export const NO_AV_RECORDING_SENTENCE = 'We do not record your screen, camera, or microphone.';

// Summary chip in the live workspace footer. Each branch has to be true of the
// configuration it describes: "Session transcript only" is only honest once the
// workspace-control layer is off.
export const SESSION_TRANSCRIPT_ONLY_FLAG = 'Session transcript only';
export const WORKSPACE_SIGNALS_FLAG = 'Transcript + workspace signals';
export const PROCTORING_FLAG = 'Activity signals enabled';

export const recordingFlagLabel = ({
  proctoringEnabled = false,
  workspaceProtectionEnabled = false,
} = {}) => {
  if (proctoringEnabled) return PROCTORING_FLAG;
  if (workspaceProtectionEnabled) return WORKSPACE_SIGNALS_FLAG;
  return SESSION_TRANSCRIPT_ONLY_FLAG;
};
