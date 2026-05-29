"""Reference stand-in for the **taali** brand's consumed interface.

``required_interface()`` is the subset of the substrate that taali actually
uses. In real `platform-qa` this is declared by the brand (a committed
consumer contract the brand owns and updates when it starts/stops using a
substrate feature).

Marker for extraction:
    # REPLACE-WITH-REAL: derive from taali-brand's declared substrate usage.
"""
from __future__ import annotations


def required_interface() -> dict:
    """What taali depends on from the substrate. Note it does NOT use
    ``pipeline.current_state`` and does NOT read ``step_index`` — a contract
    declares only what the consumer actually relies on, so unused substrate
    changes don't generate false breakages."""
    return {
        "operations": {
            "pipeline.advance": {
                "inputs": {
                    "pipeline_id": {"type": "str"},
                    "reason": {"type": "str"},
                },
                "outputs": {
                    "pipeline_id": {"type": "str"},
                    "state": {"type": "str"},
                },
            },
        },
    }
