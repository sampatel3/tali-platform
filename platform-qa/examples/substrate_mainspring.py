"""Reference stand-in for the **mainspring** substrate's published interface.

In the real `platform-qa`, ``published_interface()`` is generated from
mainspring's actual public surface (Pydantic models / type hints / OpenAPI),
not hand-written. Here it is a small hand-written interface so the contract
mechanism is runnable end-to-end in any container, with no other repos present.

Marker for extraction:
    # REPLACE-WITH-REAL: import mainspring and derive this from its real types.
"""
from __future__ import annotations

import copy


def published_interface() -> dict:
    """The substrate's current public interface (what mainspring exposes today)."""
    return copy.deepcopy(_CURRENT_INTERFACE)


_CURRENT_INTERFACE = {
    "version": "1.4.0",
    "operations": {
        # The substrate's core: advance a stateful business pipeline one step.
        "pipeline.advance": {
            "inputs": {
                "pipeline_id": {"type": "str", "required": True},
                "reason": {"type": "str", "required": False},
            },
            "outputs": {
                "pipeline_id": {"type": "str"},
                "state": {"type": "str"},
                "step_index": {"type": "int"},
            },
        },
        "pipeline.current_state": {
            "inputs": {"pipeline_id": {"type": "str", "required": True}},
            "outputs": {"state": {"type": "str"}, "step_index": {"type": "int"}},
        },
    },
}


def break_remove_output(interface: dict, operation: str, field: str) -> dict:
    """Helper used by tests to simulate an incompatible substrate change:
    remove an output field a brand depends on."""
    mutated = copy.deepcopy(interface)
    mutated["operations"][operation]["outputs"].pop(field, None)
    return mutated


def break_add_required_input(interface: dict, operation: str, field: str) -> dict:
    """Simulate the substrate adding a newly-required input brands don't send."""
    mutated = copy.deepcopy(interface)
    mutated["operations"][operation]["inputs"][field] = {"type": "str", "required": True}
    return mutated
