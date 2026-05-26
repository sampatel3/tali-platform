"""Anthropic ``claude-agent-sdk`` integration for the assessment chat path.

Replaces the hand-rolled ``messages.create`` tool-use loop
(``..claude.agentic_chat``) with the official SDK so the platform inherits
session resumption, CLI-managed tool-use, and the bundled MCP transport.

Layout
------

- ``types``                — dataclass exports (``ChatTurn``)
- ``service``              — ``AgentSDKChatService`` (the runtime entry)
- ``usage_reconciler``     — aggregated ``UsageEvent`` writer
- (leaf A) ``sandbox_tools``  — MCP server wrapping ``AssessmentToolExecutor``

Imports of the leaf-A sandbox tool server are wrapped in ``try/except
ImportError`` inside ``service`` so this package loads cleanly in branches
that don't carry leaf A yet.
"""

from .types import ChatTurn

__all__ = ["ChatTurn"]
