"""Substrate↔brand contract compatibility checking.

This is the core of Tier 2's contract layer. The idea (consumer-driven
contract testing):

  * The **substrate** (mainspring) publishes its public interface as a
    versioned, machine-readable contract: a set of named *operations*, each
    with required input fields and guaranteed output fields, plus field types.
  * Each **brand** (taali, cadence) declares the *subset* of that interface it
    actually consumes — its ``required_interface``.
  * ``check_compatibility(provider, consumer)`` returns the list of ways the
    provider FAILS to satisfy the consumer. Empty list == compatible.

A substrate change that removes an operation, drops a field a brand reads,
adds a newly-required input the brand doesn't send, or narrows a type is an
*incompatibility* — caught here, before it reaches a brand's production deploy.

The interface format is deliberately language-agnostic (plain dicts / JSON) so
the same checker works whether the real interface is extracted from Python
type hints, Pydantic models, or an OpenAPI doc.

Interface shape::

    {
      "version": "1.4.0",
      "operations": {
        "<op_name>": {
          "inputs":  {"<field>": {"type": "str", "required": true}, ...},
          "outputs": {"<field>": {"type": "str"}, ...},
        },
        ...
      }
    }
"""
from __future__ import annotations

from dataclasses import dataclass

# Type narrowing we consider breaking: provider type must be the consumer's
# type or a wider one. We keep this table tiny and explicit on purpose.
_WIDER_THAN = {
    "int": {"int", "float", "str"},
    "float": {"float", "str"},
    "bool": {"bool", "int", "str"},
    "str": {"str"},
}


def _is_compatible_type(provider_type: str, consumer_expected: str) -> bool:
    """True if a value the provider produces/accepts as ``provider_type`` is
    usable where the consumer expects ``consumer_expected``."""
    if provider_type == consumer_expected:
        return True
    # Provider output may be wider than what the consumer reads and still work
    # only when the consumer's expectation contains the provider's type.
    return provider_type in _WIDER_THAN.get(consumer_expected, {consumer_expected})


@dataclass(frozen=True)
class Incompatibility:
    operation: str
    kind: str            # missing_operation | missing_output | missing_input | type_mismatch | newly_required_input
    detail: str

    def __str__(self) -> str:  # localizing message — names the symbol + reason
        return f"[{self.kind}] {self.operation}: {self.detail}"


def check_compatibility(provider: dict, consumer: dict) -> list[Incompatibility]:
    """Return every way ``provider`` (substrate) fails to satisfy ``consumer``
    (brand). Empty list == the substrate still honours the brand's contract."""
    problems: list[Incompatibility] = []
    provider_ops = provider.get("operations", {})
    consumer_ops = consumer.get("operations", {})

    for op_name, consumer_op in consumer_ops.items():
        provider_op = provider_ops.get(op_name)
        if provider_op is None:
            problems.append(Incompatibility(
                op_name, "missing_operation",
                "substrate no longer exposes this operation the brand depends on",
            ))
            continue

        # Outputs the brand reads must still be produced, with compatible types.
        prov_outputs = provider_op.get("outputs", {})
        for field, spec in consumer_op.get("outputs", {}).items():
            if field not in prov_outputs:
                problems.append(Incompatibility(
                    op_name, "missing_output",
                    f"output field '{field}' the brand reads was removed",
                ))
                continue
            want = spec.get("type", "str")
            got = prov_outputs[field].get("type", "str")
            if not _is_compatible_type(got, want):
                problems.append(Incompatibility(
                    op_name, "type_mismatch",
                    f"output '{field}': brand expects '{want}', substrate now returns '{got}'",
                ))

        # Inputs: the brand must be able to call the operation. Two breaks:
        #  (a) an input the brand sends is no longer accepted (type mismatch);
        #  (b) the substrate added a newly-required input the brand doesn't send.
        prov_inputs = provider_op.get("inputs", {})
        cons_inputs = consumer_op.get("inputs", {})
        for field, spec in cons_inputs.items():
            if field in prov_inputs:
                want = spec.get("type", "str")
                got = prov_inputs[field].get("type", "str")
                if not _is_compatible_type(want, got):
                    problems.append(Incompatibility(
                        op_name, "type_mismatch",
                        f"input '{field}': brand sends '{want}', substrate now requires '{got}'",
                    ))
        for field, spec in prov_inputs.items():
            if spec.get("required") and field not in cons_inputs:
                problems.append(Incompatibility(
                    op_name, "newly_required_input",
                    f"substrate now requires input '{field}' the brand does not send",
                ))

    return problems
