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

// Layer 1 — what we record about the candidate's work. Covers the engagement
// beacons too (`runtime_loaded`, `file_opened`): opening a file is not changing
// one, so the copy has to say "open and change", not just "changes".
export const WORK_RECORD_ITEMS = Object.freeze([
  'your prompts',
  "Claude's responses",
  'the files you open and change',
  'validation runs',
]);

// Layer 2 — integrity metrics from the assessment tab, grouped so the
// candidate-facing copy stays short. Every event type the workspace can emit
// must appear here, so adding one forces a look at the disclosure.
export const WORKSPACE_SIGNAL_GROUPS = Object.freeze([
  Object.freeze({
    label: 'clipboard use',
    events: Object.freeze([
      'copy_attempt',
      'cut_attempt',
      'internal_paste',
      'external_paste_blocked',
    ]),
  }),
  Object.freeze({
    label: 'blocked export attempts',
    events: Object.freeze([
      'context_menu_blocked',
      'drag_drop_blocked',
      'print_attempt',
    ]),
  }),
  Object.freeze({
    label: 'when the tab loses focus',
    events: Object.freeze(['visibility_hidden', 'fullscreen_exit']),
  }),
]);

export const WORKSPACE_SIGNAL_EVENTS = Object.freeze(
  WORKSPACE_SIGNAL_GROUPS.flatMap((group) => group.events),
);

export const WORKSPACE_SIGNAL_SUMMARY = joinWithAnd(
  WORKSPACE_SIGNAL_GROUPS.map((group) => group.label),
);

export const WORK_RECORD_SENTENCE = `We record your work in this session: ${joinWithAnd(WORK_RECORD_ITEMS)}.`;

// Says what it is and why, without enumerating nine event types at someone
// who is about to sit an assessment.
export const WORKSPACE_SIGNAL_SENTENCE = `To keep the assessment fair we also log activity metrics from this tab — ${WORKSPACE_SIGNAL_SUMMARY}.`;

// Every field persisted for one of these events — the envelope written by
// `append_assessment_timeline_event` (event_type, timestamp) plus the advisory
// payload from candidate_integrity_routes.py. The caveat below is an
// affirmative "each one records X" claim, so X has to be all of them, or it is
// the same "reads as exhaustive but isn't" defect this module exists to
// prevent. `advisory` is an internal marker, not candidate data.
export const WORKSPACE_SIGNAL_PAYLOAD_FIELDS = Object.freeze([
  'event_type',
  'timestamp',
  'source',
  'length',
  'file_path',
]);

export const WORKSPACE_SIGNAL_CAVEAT = 'Each one records which metric it was, when it happened, where in the workspace, how many characters were involved, and the file you were in — never the content of what you type or copy.';

// Every candidate-facing file that watches the assessment tab's focus. There is
// more than one write path: the workspace posts `visibility_hidden` through the
// runtime-event allow-list, while the understanding check counts switches
// per question and submits them with the answer, bypassing that allow-list
// entirely. The disclosure covers both ("when the tab loses focus"), and the
// test pins this list so a third path cannot land undisclosed.
export const TAB_FOCUS_TELEMETRY_SITES = Object.freeze([
  'AssessmentPageContent.jsx',
  'UnderstandingCheck.jsx',
]);

export const NO_AV_RECORDING_SENTENCE = 'We do not record your screen, camera, or microphone.';

// Summary chip in the live workspace footer. Each branch has to be true of the
// configuration it describes: "Session transcript only" is only honest once the
// workspace-control layer is off.
export const SESSION_TRANSCRIPT_ONLY_FLAG = 'Session transcript only';
export const WORKSPACE_SIGNALS_FLAG = 'Transcript + activity metrics';
export const PROCTORING_FLAG = 'Activity signals enabled';

export const recordingFlagLabel = ({
  proctoringEnabled = false,
  workspaceProtectionEnabled = false,
} = {}) => {
  if (proctoringEnabled) return PROCTORING_FLAG;
  if (workspaceProtectionEnabled) return WORKSPACE_SIGNALS_FLAG;
  return SESSION_TRANSCRIPT_ONLY_FLAG;
};
