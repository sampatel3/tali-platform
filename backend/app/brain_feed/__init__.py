"""Outbound feed to the mainspring cross-vertical brain.

Tali runs on the mainspring substrate; this package is the OUTBOUND half of the
connection — it pushes anonymized learning signal back so the cross-vertical
brain improves. See ``anonymize`` (the privacy boundary), ``sweep`` (gather +
enqueue), and ``outbox`` (durable queue + drain to the ingest API). The whole
feature is gated by ``MAINSPRING_BRAIN_FEED_ENABLED`` (default off).
"""

from . import anonymize, outbox, sweep

__all__ = ["anonymize", "outbox", "sweep"]
