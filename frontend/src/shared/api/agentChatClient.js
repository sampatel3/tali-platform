import api from './httpClient';

// Role-agent chat: conversational steering of a role's autonomous agent.
// One shared thread per role, keyed by role_id. The timeline merges chat
// messages with the agent's open questions and queued decisions; questions
// and decisions are still answered/approved through their own endpoints
// (agent-needs-input / agent-decisions), surfaced here for convenience.
export const agentChat = {
  // Sidebar: every active agent + attention counts (unread / questions / pending).
  listConversations: () => api.get('/agent-chat/conversations'),

  // Merged timeline for one role's agent (also marks the thread read).
  getTimeline: (roleId) => api.get(`/agent-chat/conversations/${roleId}/timeline`),

  // Send a message → runs the agent turn. Returns { messages, timeline, agent }.
  sendMessage: (roleId, message) =>
    api.post(`/agent-chat/conversations/${roleId}/messages`, { message }),

  // Fan one message out to several roles' agents at once. Each runs in its own
  // thread (separate audit); replies land per-thread as the background job drains.
  // Returns { requested, accepted, skipped }.
  bulkMessage: (roleIds, message) =>
    api.post('/agent-chat/bulk-message', { role_ids: roleIds, message }),

  markRead: (roleId) => api.post(`/agent-chat/conversations/${roleId}/read`),

  // The agent's clarifying questions (kind === 'needs_input' in the timeline).
  answerNeedsInput: (needsInputId, response) =>
    api.post(`/agent-needs-input/${needsInputId}/answer`, { response }),
  dismissNeedsInput: (needsInputId) =>
    api.post(`/agent-needs-input/${needsInputId}/dismiss`),

  // Draft-task review (the draft_task_review card). Approve activates the
  // draft; revise re-authors it from structured reject feedback (no delete).
  // Both return { ok, timeline } so the dock can refresh in place.
  approveDraftTask: (roleId, taskId) =>
    api.post(`/agent-chat/conversations/${roleId}/draft-tasks/${taskId}/approve`),
  reviseDraftTask: (roleId, taskId, { answers, note } = {}) =>
    api.post(`/agent-chat/conversations/${roleId}/draft-tasks/${taskId}/revise`, {
      answers: answers || {},
      note: note || null,
    }),

  // The pending_reject_sweep card (posted when auto-reject is turned on with
  // pre-screen reject cards already pending). Apply funnels the CURRENT
  // pending queue through the normal bulk-approve path; dismiss keeps the
  // cards for manual review. Both return { ok, timeline }.
  applyPendingRejects: (roleId) =>
    api.post(`/agent-chat/conversations/${roleId}/pending-rejects/apply`),
  dismissPendingRejects: (roleId) =>
    api.post(`/agent-chat/conversations/${roleId}/pending-rejects/dismiss`),
};
