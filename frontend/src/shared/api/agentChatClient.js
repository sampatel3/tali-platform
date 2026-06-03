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

  markRead: (roleId) => api.post(`/agent-chat/conversations/${roleId}/read`),

  // The agent's clarifying questions (kind === 'needs_input' in the timeline).
  answerNeedsInput: (needsInputId, response) =>
    api.post(`/agent-needs-input/${needsInputId}/answer`, { response }),
  dismissNeedsInput: (needsInputId) =>
    api.post(`/agent-needs-input/${needsInputId}/dismiss`),
};
