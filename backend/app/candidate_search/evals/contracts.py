"""Strict contracts for constructed search worlds and ablation reports."""

from __future__ import annotations

from datetime import date
from enum import Enum
from hashlib import sha256

try:  # Python 3.10 CI; ``typing.Self`` was added in Python 3.11.
    from typing import Self
except ImportError:  # pragma: no cover - exercised by the Python 3.10 CI job
    from typing_extensions import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..search_plan import Scalar, SearchObject, SearchPlan


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Citation(_StrictModel):
    document_id: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)

    @model_validator(mode="after")
    def _end_follows_start(self) -> Self:
        if self.end <= self.start:
            raise ValueError("citation end must be greater than start")
        return self


class Document(_StrictModel):
    id: str = Field(min_length=1)
    entity_id: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    source_id: str | None = Field(default=None, min_length=1)
    content: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _content_hash_matches(self) -> Self:
        actual = sha256(self.content.encode("utf-8")).hexdigest()
        if self.content_sha256 != actual:
            raise ValueError("content_sha256 does not match document content")
        return self

    @property
    def independent_source_id(self) -> str:
        """Stable origin used for minimum-source policies.

        Multiple chunks from one CV/profile are one source by default.  Importers
        can provide ``source_id`` when a source type has multiple independent
        origins (for example two separate references).
        """

        return self.source_id or f"{self.entity_id}:{self.source_type}"


class WorldEntity(_StrictModel):
    id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    attributes: dict[str, Scalar] = Field(default_factory=dict)


class Fact(_StrictModel):
    """A constructed-world assertion; never an expected search answer."""

    id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    object: SearchObject
    value: Scalar | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    ongoing: bool = False
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    direct_subject: bool = True
    provenance: tuple[Citation, ...] = ()

    @model_validator(mode="after")
    def _valid_date_range(self) -> Self:
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("fact valid_from cannot be after valid_to")
        if self.ongoing and (self.valid_from is None or self.valid_to is not None):
            raise ValueError("ongoing facts require valid_from and no valid_to")
        return self


class ConstructedWorld(_StrictModel):
    id: str = Field(min_length=1)
    entities: tuple[WorldEntity, ...]
    documents: tuple[Document, ...] = ()
    facts: tuple[Fact, ...] = ()
    closed_world_predicates: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_references(self) -> Self:
        entity_ids = [entity.id for entity in self.entities]
        document_ids = [document.id for document in self.documents]
        fact_ids = [fact.id for fact in self.facts]
        closed_predicates = [
            value.strip().casefold() for value in self.closed_world_predicates
        ]
        for label, values in (
            ("entity", entity_ids),
            ("document", document_ids),
            ("fact", fact_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} IDs must be unique")
        if len(closed_predicates) != len(set(closed_predicates)):
            raise ValueError("closed_world_predicates must be unique")
        known_entities = set(entity_ids)
        known_documents = set(document_ids)
        documents_by_id = {document.id: document for document in self.documents}
        for document in self.documents:
            if document.entity_id not in known_entities:
                raise ValueError(f"document {document.id} has unknown entity")
        for fact in self.facts:
            if fact.subject_id not in known_entities:
                raise ValueError(f"fact {fact.id} has unknown subject")
            for citation in fact.provenance:
                if citation.document_id not in known_documents:
                    raise ValueError(f"fact {fact.id} cites unknown document")
                document = documents_by_id[citation.document_id]
                if citation.end > len(document.content):
                    raise ValueError(f"fact {fact.id} citation exceeds document")
                actual = document.content[citation.start : citation.end]
                if citation.quote != actual:
                    raise ValueError(f"fact {fact.id} citation quote does not match")
                if fact.direct_subject and document.entity_id != fact.subject_id:
                    raise ValueError(
                        f"direct fact {fact.id} must cite its subject's document"
                    )
        return self

    def predicate_is_closed(self, predicate: str) -> bool:
        wanted = predicate.strip().casefold()
        return any(
            value.strip().casefold() == wanted
            for value in self.closed_world_predicates
        )


class RetrievalCorpus(_StrictModel):
    """System-visible data; deliberately excludes gold facts and plans."""

    entities: tuple[WorldEntity, ...]
    documents: tuple[Document, ...]


class RetrievalCase(_StrictModel):
    """Only input exposed to a backend during an evaluation run."""

    id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    corpus: RetrievalCorpus


class QueryIntent(_StrictModel):
    """An independently authored query meaning; contains no expected IDs."""

    id: str = Field(min_length=1)
    plan: SearchPlan


class ConstructedDataset(_StrictModel):
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    world: ConstructedWorld
    intents: tuple[QueryIntent, ...]
    required_stages: tuple[str, ...] = ("retrieval", "final")

    @model_validator(mode="after")
    def _unique_intents(self) -> Self:
        ids = [intent.id for intent in self.intents]
        if len(ids) != len(set(ids)):
            raise ValueError("intent IDs must be unique")
        stage_names = [stage.strip() for stage in self.required_stages]
        if not stage_names or any(not stage for stage in stage_names):
            raise ValueError("required_stages must contain non-empty stage names")
        if len(stage_names) != len(set(stage_names)):
            raise ValueError("required_stages must be unique")
        return self

    @property
    def retrieval_corpus(self) -> RetrievalCorpus:
        return RetrievalCorpus(
            entities=self.world.entities,
            documents=self.world.documents,
        )


class CriterionEvidence(_StrictModel):
    """Citations a backend claims support one specific search criterion."""

    criterion_id: str = Field(min_length=1)
    citations: tuple[Citation, ...] = Field(min_length=1)


class ResultStatus(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class RankedHit(_StrictModel):
    entity_id: str = Field(min_length=1)
    score: float
    evidence: tuple[CriterionEvidence, ...] = ()
    # A backend must explicitly claim that it checked and did not find a
    # forbidden criterion.  The evaluator validates that claim against the
    # oracle; it must never fill negative truth from hidden gold on its own.
    verified_absent_criterion_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _unique_criterion_evidence(self) -> Self:
        ids = [row.criterion_id for row in self.evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("criterion evidence IDs must be unique within a hit")
        absent_ids = list(self.verified_absent_criterion_ids)
        if len(absent_ids) != len(set(absent_ids)):
            raise ValueError("verified-absent criterion IDs must be unique within a hit")
        return self


class RetrievalStageOutput(_StrictModel):
    stage: str = Field(min_length=1)
    hits: tuple[RankedHit, ...]
    status: ResultStatus = ResultStatus.OK
    capped: bool = False
    exhaustive: bool = True

    @field_validator("stage")
    @classmethod
    def _strip_stage(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _unique_hits(self) -> Self:
        entity_ids = [hit.entity_id for hit in self.hits]
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError("hit entity IDs must be unique within a stage")
        if self.status in {ResultStatus.UNAVAILABLE, ResultStatus.ERROR}:
            if self.hits:
                raise ValueError("failed or unavailable stages cannot contain hits")
            if self.exhaustive:
                raise ValueError("failed or unavailable stages cannot be exhaustive")
        if self.status is ResultStatus.PARTIAL and self.exhaustive:
            raise ValueError("partial stages cannot be exhaustive")
        if self.capped and self.exhaustive:
            raise ValueError("capped stages cannot be exhaustive")
        return self

    @property
    def is_exact_empty(self) -> bool:
        return (
            self.status is ResultStatus.OK
            and self.exhaustive
            and not self.capped
            and not self.hits
        )


class TruthValue(str, Enum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


class CriterionJudgment(_StrictModel):
    criterion_id: str
    truth: TruthValue
    supporting_citations: tuple[Citation, ...] = ()


class OracleJudgment(_StrictModel):
    entity_id: str
    eligibility: TruthValue
    relevance: float = Field(ge=0.0)
    matched_criteria: tuple[str, ...] = ()
    failed_criteria: tuple[str, ...] = ()
    unknown_criteria: tuple[str, ...] = ()
    criterion_judgments: tuple[CriterionJudgment, ...] = ()

    @model_validator(mode="after")
    def _unique_criterion_judgments(self) -> Self:
        ids = [row.criterion_id for row in self.criterion_judgments]
        if len(ids) != len(set(ids)):
            raise ValueError("criterion judgment IDs must be unique")
        return self

    @property
    def eligible(self) -> bool:
        return self.eligibility is TruthValue.TRUE


class StageMetrics(_StrictModel):
    k: int = Field(ge=1)
    retrieved_count: int = Field(ge=0)
    relevant_count: int = Field(ge=0)
    precision_at_k: float = Field(ge=0.0, le=1.0)
    recall_at_k: float = Field(ge=0.0, le=1.0)
    mrr: float = Field(ge=0.0, le=1.0)
    ndcg_at_k: float = Field(ge=0.0, le=1.0)
    false_positive_count_at_k: int = Field(ge=0)
    exact_empty_accuracy: float = Field(ge=0.0, le=1.0)
    citation_span_validity: float = Field(ge=0.0, le=1.0)
    citation_support_validity: float = Field(ge=0.0, le=1.0)
    citation_count: int = Field(ge=0)
    valid_citation_count: int = Field(ge=0)
    supported_citation_count: int = Field(ge=0)
    grounded_hit_coverage: float = Field(ge=0.0, le=1.0)
    grounded_hit_count: int = Field(ge=0)


class StageEvaluation(_StrictModel):
    stage: str
    metrics: StageMetrics


class QueryEvaluation(_StrictModel):
    intent_id: str
    judgments: tuple[OracleJudgment, ...]
    stages: tuple[StageEvaluation, ...]


class StageAggregate(_StrictModel):
    stage: str
    query_count: int = Field(ge=1)
    precision_at_k: float
    recall_at_k: float
    mrr: float
    ndcg_at_k: float
    false_positive_count_at_k: int = Field(ge=0)
    exact_empty_accuracy: float
    citation_span_validity: float
    citation_support_validity: float
    citation_count: int = Field(ge=0)
    valid_citation_count: int = Field(ge=0)
    supported_citation_count: int = Field(ge=0)
    grounded_hit_coverage: float
    grounded_hit_count: int = Field(ge=0)


class BackendVariantEvaluation(_StrictModel):
    backend: str = Field(min_length=1)
    queries: tuple[QueryEvaluation, ...]
    stage_aggregates: tuple[StageAggregate, ...]


class BackendAblationReport(_StrictModel):
    """Serializable comparison of identical cases across retrieval backends."""

    dataset_id: str
    dataset_version: str
    k: int = Field(ge=1)
    variants: tuple[BackendVariantEvaluation, ...]

    def best_backend(self, *, stage: str, metric: str) -> str:
        allowed = {
            "precision_at_k",
            "recall_at_k",
            "mrr",
            "ndcg_at_k",
            "exact_empty_accuracy",
            "citation_span_validity",
            "citation_support_validity",
            "grounded_hit_coverage",
        }
        if metric not in allowed:
            raise ValueError(f"unsupported metric: {metric}")
        candidates: list[tuple[float, str]] = []
        for variant in self.variants:
            aggregate = next(
                (row for row in variant.stage_aggregates if row.stage == stage), None
            )
            if aggregate is not None:
                candidates.append((float(getattr(aggregate, metric)), variant.backend))
        if not candidates:
            raise ValueError(f"stage not found in report: {stage}")
        return max(candidates, key=lambda row: row[0])[1]


__all__ = [
    "BackendAblationReport",
    "BackendVariantEvaluation",
    "Citation",
    "ConstructedDataset",
    "ConstructedWorld",
    "CriterionEvidence",
    "CriterionJudgment",
    "Document",
    "Fact",
    "OracleJudgment",
    "QueryEvaluation",
    "QueryIntent",
    "RankedHit",
    "ResultStatus",
    "RetrievalCase",
    "RetrievalCorpus",
    "RetrievalStageOutput",
    "StageAggregate",
    "StageEvaluation",
    "StageMetrics",
    "TruthValue",
    "WorldEntity",
]
