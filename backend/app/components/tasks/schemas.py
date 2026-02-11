# Re-export from canonical schema location
from ...schemas.task import TaskCreate, TaskResponse, TaskUpdate  # noqa: F401

__all__ = ["TaskCreate", "TaskResponse", "TaskUpdate"]
