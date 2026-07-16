// Recruiter-facing copy for persisted agent pause reasons.
//
// The database intentionally keeps precise machine reasons for operations and
// debugging (for example, "monthly USD cap reached: 5157c >= 5000c"). Those
// values are not product copy. Keep this mapping allowlisted: an unknown value
// must fall back to a safe status instead of exposing another implementation
// detail in the UI.

const normalizePauseReason = (value) => {
  let reason = String(value || '')
    .trim()
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .toLowerCase();

  // Historical values sometimes wrapped the real reason in "role paused:".
  while (reason.startsWith('role paused:')) {
    reason = reason.slice('role paused:'.length).trim();
  }
  return reason;
};

export const getAgentPauseCopy = (value) => {
  const reason = normalizePauseReason(value);

  if (
    /\bpaused by you\b/.test(reason)
    || /\bpaused by (?:a )?(?:recruiter|team member|administrator|admin|user)\b/.test(reason)
  ) {
    return {
      kind: 'manual',
      status: 'Paused manually',
      label: 'Paused manually',
      description: 'A team member paused this agent.',
    };
  }

  if (
    /\b(?:per cycle|cycle|decision|token)\b.*\b(?:budget|cap|limit)\b/.test(reason)
    || /\b(?:decision|token) budget\b/.test(reason)
  ) {
    return {
      kind: 'review-limit',
      status: 'Paused · Review limit reached',
      label: 'Review limit reached',
      description: 'This agent reached its automatic review limit.',
    };
  }

  if (
    /\bmonthly\b.*\b(?:budget|cap)\b/.test(reason)
    || /\bbudget (?:cap )?(?:reached|exhausted|used up)\b/.test(reason)
  ) {
    return {
      kind: 'monthly-budget',
      status: 'Paused · Monthly budget reached',
      label: 'Monthly budget reached',
      description: 'The monthly budget has been reached.',
    };
  }

  if (
    /\binsufficient\b.*\b(?:organization|workspace|org)?\s*credits?\b/.test(reason)
    || /\bcredits?\b.*\b(?:exhausted|used up|insufficient)\b/.test(reason)
  ) {
    return {
      kind: 'workspace-credits',
      status: 'Paused · Workspace credits used up',
      label: 'Workspace credits used up',
      description: 'The workspace does not have enough credits to continue.',
    };
  }

  return {
    kind: 'unknown',
    status: 'Paused',
    label: 'Paused',
    description: 'This agent is paused.',
  };
};

export const formatAgentPauseStatus = (value) => getAgentPauseCopy(value).status;
