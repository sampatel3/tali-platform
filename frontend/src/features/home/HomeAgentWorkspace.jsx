import { lazy, Suspense } from 'react';
import { MessageSquare } from 'lucide-react';

import { MotionAttentionBadge } from '../../shared/motion';
import { AgentSidebar } from './agentchat/AgentSidebar';

const LazyAgentChatDock = lazy(() => import('./agentchat/AgentChatDock').then((module) => ({
  default: module.AgentChatDock,
})));

export function HomeAgentWorkspace({
  activeAgent,
  activeRoleId,
  agents,
  bulkMode,
  bulkSelected,
  bulkSelectedRoles,
  chatHidden,
  children,
  onClearBulk,
  onHideChat,
  onNavigate,
  onReload,
  onSelectAgent,
  onSendBulk,
  onShowChat,
  onToggleBulkMode,
  onToggleSelected,
  totalAttention,
}) {
  const dockOpen = bulkMode || (activeRoleId != null && !chatHidden);
  return (
    <>
      <div className={`ac-shell ${dockOpen ? '' : 'ac-dock-collapsed'}`}>
        <AgentSidebar
          agents={agents}
          activeRoleId={activeRoleId}
          onSelect={onSelectAgent}
          bulkMode={bulkMode}
          bulkSelected={bulkSelected}
          onToggleBulkMode={onToggleBulkMode}
          onToggleSelected={onToggleSelected}
        />
        <div className="ac-main">{children}</div>
        {dockOpen ? (
          <Suspense fallback={null}>
            <LazyAgentChatDock
              roleId={activeRoleId}
              roleName={activeAgent?.role_name}
              agentEnabled={activeAgent ? activeAgent.agent_enabled : true}
              onReload={onReload}
              onCollapse={() => { if (bulkMode) onClearBulk(); else onHideChat(); }}
              bulkSelectedRoles={bulkSelectedRoles}
              onSendBulk={onSendBulk}
              onClearBulk={onClearBulk}
            />
          </Suspense>
        ) : null}
        {(activeRoleId != null && chatHidden && !bulkMode) ? (
          <button
            type="button"
            className="ac-reopen"
            onClick={onShowChat}
            title="Show agent chat"
            aria-label="Show agent chat"
          >
            <MessageSquare size={18} />
            <MotionAttentionBadge
              value={totalAttention}
              className="ac-badge-count"
              aria-label={`${totalAttention} agent update${totalAttention === 1 ? '' : 's'} awaiting you`}
            />
          </button>
        ) : null}
      </div>
      <button
        type="button"
        className="ac-mobile-cta"
        onClick={() => onNavigate?.('chat-agents', { roleId: activeRoleId || undefined })}
      >
        <MessageSquare size={16} /> Chat with your agents
        <MotionAttentionBadge
          value={totalAttention}
          className="ac-badge-count"
          aria-label={`${totalAttention} agent update${totalAttention === 1 ? '' : 's'} awaiting you`}
        />
      </button>
    </>
  );
}

export default HomeAgentWorkspace;
