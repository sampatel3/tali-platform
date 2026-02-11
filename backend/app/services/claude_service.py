# Re-export shim – canonical location is components.integrations.claude.service
from ..components.integrations.claude.service import ClaudeService  # noqa: F401

__all__ = ["ClaudeService"]
