"""Typed, domain-neutral contract between query understanding and retrieval.

The plan deliberately describes *what* must match, not whether Postgres, a
graph database, or a hybrid backend should execute it.  Adapters may therefore
compile the same plan to different backends and the offline oracle can evaluate
it independently.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

try:  # Python 3.10 CI; ``typing.Self`` was added in Python 3.11.
    from typing import Self
except ImportError:  # pragma: no cover - exercised by the Python 3.10 CI job
    from typing_extensions import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Scalar = str | int | float | bool
ComparisonValue = Scalar | tuple[Scalar, ...] | None


class BooleanOperator(str, Enum):
    TRUE = "true"
    CRITERION = "criterion"
    ALL = "all"
    ANY = "any"
    NOT = "not"


class ComparisonOperator(str, Enum):
    EXISTS = "exists"
    EQ = "eq"
    NE = "ne"
    CONTAINS = "contains"
    IN = "in"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"


class Modality(str, Enum):
    """How a criterion affects eligibility and ranking."""

    MUST = "must"
    SHOULD = "should"
    MUST_NOT = "must_not"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Predicate(_StrictModel):
    """A semantic relation name, e.g. ``demonstrated`` or ``worked_at``."""

    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        return value.strip()


class SearchObject(_StrictModel):
    """Typed entity/value at either side of a predicate."""

    kind: str = Field(min_length=1, max_length=120)
    value: Scalar | None = None

    @field_validator("kind")
    @classmethod
    def _strip_kind(cls, value: str) -> str:
        return value.strip()


class Comparison(_StrictModel):
    """Comparison applied to a matching fact's scalar value."""

    operator: ComparisonOperator = ComparisonOperator.EXISTS
    value: ComparisonValue = None

    @model_validator(mode="after")
    def _require_value_when_comparing(self) -> Self:
        if self.operator is not ComparisonOperator.EXISTS and self.value is None:
            raise ValueError(f"{self.operator.value} comparison requires a value")
        if self.operator is ComparisonOperator.IN and not isinstance(self.value, tuple):
            raise ValueError("IN comparison requires a tuple value")
        return self


class TemporalConstraint(_StrictModel):
    """Optional time bounds that a fact must satisfy."""

    minimum_duration_months: float | None = Field(default=None, ge=0)
    starts_on_or_before: date | None = None
    ends_on_or_after: date | None = None
    overlaps_from: date | None = None
    overlaps_to: date | None = None
    current_only: bool = False
    as_of: date | None = None

    @model_validator(mode="after")
    def _validate_bounds(self) -> Self:
        if self.overlaps_from and self.overlaps_to:
            if self.overlaps_from > self.overlaps_to:
                raise ValueError("overlaps_from cannot be after overlaps_to")
        if self.current_only and self.as_of is None:
            raise ValueError("current_only requires an as_of date")
        return self


class EvidencePolicy(_StrictModel):
    """Minimum evidence needed before a fact may satisfy a criterion."""

    require_direct_subject: bool = True
    require_citation_span: bool = True
    minimum_sources: int = Field(default=1, ge=0)
    minimum_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    allowed_source_types: tuple[str, ...] = ()


class Criterion(_StrictModel):
    """One independently testable semantic requirement or preference."""

    id: str = Field(min_length=1, max_length=120)
    predicate: Predicate
    subject: SearchObject
    object: SearchObject
    comparison: Comparison = Field(default_factory=Comparison)
    temporal: TemporalConstraint | None = None
    modality: Modality = Modality.MUST
    evidence: EvidencePolicy = Field(default_factory=EvidencePolicy)
    weight: float = Field(default=1.0, gt=0.0)

    @field_validator("id")
    @classmethod
    def _strip_id(cls, value: str) -> str:
        return value.strip()


class Expression(_StrictModel):
    """Recursive boolean expression over criterion IDs."""

    operator: BooleanOperator
    criterion_id: str | None = None
    children: tuple["Expression", ...] = ()

    @model_validator(mode="after")
    def _validate_shape(self) -> Self:
        if self.operator is BooleanOperator.TRUE:
            if self.criterion_id or self.children:
                raise ValueError("TRUE cannot have criterion_id or children")
        elif self.operator is BooleanOperator.CRITERION:
            if not self.criterion_id or self.children:
                raise ValueError("CRITERION requires criterion_id and no children")
        elif self.operator in {BooleanOperator.ALL, BooleanOperator.ANY}:
            if self.criterion_id or not self.children:
                raise ValueError(
                    f"{self.operator.value.upper()} requires children "
                    "and no criterion_id"
                )
        elif self.operator is BooleanOperator.NOT:
            if self.criterion_id or len(self.children) != 1:
                raise ValueError("NOT requires exactly one child and no criterion_id")
        return self

    @classmethod
    def leaf(cls, criterion_id: str) -> "Expression":
        return cls(operator=BooleanOperator.CRITERION, criterion_id=criterion_id)

    @classmethod
    def true(cls) -> "Expression":
        return cls(operator=BooleanOperator.TRUE)

    @classmethod
    def all(cls, *children: "Expression") -> "Expression":
        return cls(operator=BooleanOperator.ALL, children=children)

    @classmethod
    def any(cls, *children: "Expression") -> "Expression":
        return cls(operator=BooleanOperator.ANY, children=children)

    @classmethod
    def not_(cls, child: "Expression") -> "Expression":
        return cls(operator=BooleanOperator.NOT, children=(child,))

    def referenced_criterion_ids(self) -> set[str]:
        if self.operator is BooleanOperator.TRUE:
            return set()
        if self.operator is BooleanOperator.CRITERION:
            return {self.criterion_id} if self.criterion_id else set()
        return set().union(
            *(child.referenced_criterion_ids() for child in self.children)
        )

    def criterion_polarities(
        self,
        *,
        negated: bool = False,
    ) -> dict[str, set[bool]]:
        """Map each leaf to whether it appears under an odd number of NOTs."""

        if self.operator is BooleanOperator.TRUE:
            return {}
        if self.operator is BooleanOperator.CRITERION:
            return {self.criterion_id: {negated}} if self.criterion_id else {}
        child_negated = not negated if self.operator is BooleanOperator.NOT else negated
        merged: dict[str, set[bool]] = {}
        for child in self.children:
            for criterion_id, polarities in child.criterion_polarities(
                negated=child_negated
            ).items():
                merged.setdefault(criterion_id, set()).update(polarities)
        return merged


class SearchPlan(_StrictModel):
    """Backend-independent executable meaning of a search query."""

    version: str = "1.0"
    query: str = Field(min_length=1)
    criteria: tuple[Criterion, ...]
    root: Expression
    limit: int = Field(default=50, ge=1, le=1000)

    @field_validator("query")
    @classmethod
    def _strip_query(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _validate_references(self) -> Self:
        ids = [criterion.id for criterion in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("criterion IDs must be unique")
        known = set(ids)
        referenced = self.root.referenced_criterion_ids()
        unknown = sorted(referenced - known)
        if unknown:
            raise ValueError(f"root references unknown criterion IDs: {unknown}")
        by_id = {criterion.id: criterion for criterion in self.criteria}
        omitted_mandatory = sorted(
            criterion.id
            for criterion in self.criteria
            if criterion.modality is not Modality.SHOULD
            and criterion.id not in referenced
        )
        if omitted_mandatory:
            raise ValueError(
                "mandatory criteria must appear in the eligibility root: "
                f"{omitted_mandatory}"
            )
        accidental_filters = sorted(
            criterion_id
            for criterion_id in referenced
            if by_id[criterion_id].modality is Modality.SHOULD
        )
        if accidental_filters:
            raise ValueError(
                "SHOULD criteria cannot appear in the eligibility root: "
                f"{accidental_filters}"
            )
        polarities = self.root.criterion_polarities()
        unnegated_prohibitions = sorted(
            criterion_id
            for criterion_id, states in polarities.items()
            if by_id[criterion_id].modality is Modality.MUST_NOT
            and states != {True}
        )
        if unnegated_prohibitions:
            raise ValueError(
                "MUST_NOT criteria require NOT polarity: "
                f"{unnegated_prohibitions}"
            )
        negated_requirements = sorted(
            criterion_id
            for criterion_id, states in polarities.items()
            if by_id[criterion_id].modality is Modality.MUST and states != {False}
        )
        if negated_requirements:
            raise ValueError(
                "MUST criteria cannot be negated; use MUST_NOT: "
                f"{negated_requirements}"
            )
        return self

    @property
    def criteria_by_id(self) -> dict[str, Criterion]:
        return {criterion.id: criterion for criterion in self.criteria}

    @property
    def ranking_criteria(self) -> tuple[Criterion, ...]:
        return tuple(
            criterion
            for criterion in self.criteria
            if criterion.modality is Modality.SHOULD
        )

    @property
    def eligibility_criteria(self) -> tuple[Criterion, ...]:
        referenced = self.root.referenced_criterion_ids()
        return tuple(
            criterion for criterion in self.criteria if criterion.id in referenced
        )


Expression.model_rebuild()

__all__ = [
    "BooleanOperator",
    "Comparison",
    "ComparisonOperator",
    "Criterion",
    "EvidencePolicy",
    "Expression",
    "Modality",
    "Predicate",
    "SearchObject",
    "SearchPlan",
    "TemporalConstraint",
]
