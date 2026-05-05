"""Autonomous per-job recruiting agent.

Mirrors ``taali_chat`` (which is a recruiter-driven interactive agent)
but runs without a human in the loop on Celery triggers. High-stakes
decisions (advance, reject) are queued via ``app.actions.queue_decision``
for one-click recruiter approval rather than executed.

Entry points:
- ``orchestrator.run_cycle(db, role, *, trigger, ...)`` — one autonomous cycle
- ``triggers.celery_tasks.agent_react_to_event(role_id, application_id)`` — event-driven
"""

from .orchestrator import run_cycle

__all__ = ["run_cycle"]
