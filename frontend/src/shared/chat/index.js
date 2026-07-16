// Shared chat kit — the standard chat UI used by the Search page, the Home
// agent dock, and the candidate workspace. Importing from here also pulls in
// the kit's stylesheet.
import './chat-kit.css';

export { ChatComposer } from './ChatComposer';
export { ChatSurface } from './ChatSurface';
export { ChatActivity } from './ChatActivity';
export { ChatArtifact } from './ChatArtifact';
export { ChatMarkdown } from './ChatMarkdown';
export { ChatMessage } from './ChatMessage';
export { ChatEmptyState } from './ChatEmptyState';
export { ThinkingDots } from './ThinkingDots';
export { NewMessageNotice } from './NewMessageNotice';
export { AgentHelperPromptCard, AgentPromptCard, agentPromptTitle } from './AgentPromptCard';
export {
  AgentFeedTimeline,
  AgentStreamTabs,
  CandidateDecisionReference,
  agentFeedAttentionCount,
  agentFeedItemMeta,
  agentTimelineLane,
  splitAgentTimeline,
} from './AgentFeedTimeline';
export { RoleAgentTimeline } from './RoleAgentTimeline';
export { useAgentRequestReply } from './useAgentRequestReply';
export { useAgentUpdateAwareness } from './useAgentUpdateAwareness';
