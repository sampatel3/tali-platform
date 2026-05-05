"""MCP server exposing read-only access to roles, applications, and candidates.

Mounted onto the main FastAPI app at ``/mcp`` (see ``main.py``). Auth is the
same fastapi-users JWT used by the rest of the API; a bearer token is
extracted from the ``Authorization`` header on every tool/resource call.
"""

from .server import mcp_app

__all__ = ["mcp_app"]
