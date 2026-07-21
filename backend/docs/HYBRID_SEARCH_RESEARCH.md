# General candidate search: origin/main architecture and hybrid-search seams

Status: framework implemented; offline verification complete
Baseline: `origin/main` at `f594de67d1c771ee1345b17cdd2b5a5923a52665`
Scope: candidate retrieval, ranking, and evidence grounding across arbitrary recruiter queries
Source convention: every `path:lines` citation below refers to that exact baseline commit and can be inspected with `git show f594de67:<path>`.

## Implementation summary

The branch now implements the recommended architecture without product-name
special cases:

- a strict backend-independent `SearchPlan` with stable criterion IDs,
  nested Boolean expressions, modality, comparisons, temporal constraints,
  and evidence policy;
- graph semantic recall and PostgreSQL retrieval behind one typed backend
  contract, with graph-weighted reciprocal-rank fusion;
- PostgreSQL re-authorization for organization, role, lifecycle, deletion,
  stage, source, outcome, score, and assessment scope before a graph hit can
  become an application result;
- original Graphiti episodes as evidence sources (never generated edge text),
  direct candidate ownership, clause-specific source checks, explicit backend
  health/cap/exhaustiveness, and an `is_exact_empty` zero-claim guard;
- bounded retrieval, page-only public traces, a tenant/role/query/limit-scoped
  graph cache, and provider-safe offline tests;
- a constructed-fact oracle that derives truth rather than storing expected
  candidate IDs, plus graph/PostgreSQL/hybrid ablations, criterion-linked
  citation checks, tri-state negation, calendar-time semantics, false-positive
  and exact-empty metrics, and a version-pinned fixture digest.

The same framework is now the semantic-search boundary for the Applications
HTTP API, MCP, Recruiter/Taali Chat, Role Agent Chat, and autonomous Agent
Runtime. The legacy `graph_search_candidates` name remains for compatibility,
but delegates to the shared hybrid runner, inherits role scope, and labels
generated graph edge text as non-citation topology context. Deterministic
score/stage/outcome/name filters intentionally remain direct SQL operations.
CI runs the offline eval suite and the cross-surface role/parity contract with
provider credentials cleared, so regression checks cannot spend external API
credits.

Exact colleague and arbitrary N-hop constraints intentionally fail closed
until a parameterized path retriever can return inspectable path evidence.
The runtime capability gate returns an explicit partial/unsupported result for
those constraints, and for skill-specific duration that the compatibility DTO
cannot bind exactly; neither case is reported as a zero-candidate fact.
Graph coverage is also treated as unknown unless an authoritative completeness
watermark exists. Consequently, a hybrid empty result is not presented as an
exact zero merely because semantic search returned no hit. Live provider and
production-data validation remain opt-in and require explicit cost approval;
the implemented test suite uses only local fakes and retained source fixtures.

The compatibility parser still cannot bind a duration to a particular skill
(for example, distinguish five years total experience from five years using one
technology), and the legacy opt-in reranker emits a model decision rather than
criterion-linked citation spans. Those cases must remain unverified/partial;
the cited `find_top_candidates` path is the qualification surface until both
are migrated to the generic evidence contract. Person retrieval is deliberately
bounded to 1,000 people with conservative `capped`/`exhaustive` reporting rather
than claiming an unbounded total.

## Executive finding

The platform already has PostgreSQL candidate retrieval and a Graphiti/Neo4j knowledge graph, but it does **not** currently have a graph-first hybrid-search framework.

The production runner is PostgreSQL-first: it parses a query, applies SQL filters, optionally intersects an explicit graph-predicate result, optionally asks a model to verify a bounded window, and only fetches a graph payload when the caller requests graph display. The MCP handler makes the intended boundary explicit: ordinary skills search stays entirely in PostgreSQL and graph retrieval is opt-in for relationships or visualisation (`backend/app/candidate_search/runner.py:76-355`; `backend/app/mcp/handlers.py:413-471`).

Graphiti is a valuable recall and relationship substrate, but it cannot yet be the sole candidate source:

- automatic graph ingestion intentionally excludes candidates who have not advanced past screening, so the graph is a sparse projection rather than the full searchable pool (`backend/app/candidate_graph/sync.py:43-94`; `backend/app/tasks/graph_ingest_tasks.py:93-144`);
- ingestion is asynchronous and explicitly lags PostgreSQL by the worker queue and extraction time (`backend/app/candidate_graph/listeners.py:1-29`);
- the graph predicate vocabulary is only four relationship shapes (`backend/app/candidate_search/schemas.py:15-32`);
- Graphiti search is capped at 50 facts and candidate identity is recovered heuristically from extracted attributes or fact text (`backend/app/candidate_graph/search.py:38-45,107-163,602-629`);
- exact lifecycle, role, outcome, deletion, and numeric constraints remain authoritative in PostgreSQL.

The appropriate target is therefore **graph-first retrieval when graph coverage and health support it, PostgreSQL-authoritative filtering and fallback, union/fusion for recall, and source-addressable evidence for every claim**. “Graph-first” should describe retrieval order and semantic recall—not permission to hide graph gaps or ignore the canonical database.

## 1. Current public surfaces

### 1.1 Applications HTTP API

`GET /applications` accepts `nl_query`, optional role scope, `view=list|graph`, and opt-in `rerank`. It builds an organization- and deletion-scoped base query before invoking `run_search`, then reapplies the ordinary stage/outcome/source filters and preserves the runner's person-deduplicated order (`backend/app/domains/assessments_runtime/applications_routes.py:1571-1667,1694-1714,1827-1851`).

The response exposes the parsed filter, warnings, verification coverage/results, and an optional subgraph (`backend/app/domains/assessments_runtime/applications_routes.py:1668-1691,1910-1926`). This response seam can carry a new hybrid trace without breaking the basic item list.

### 1.2 MCP and chat tools

There are three overlapping search products:

1. `nl_search_candidates`: exhaustive person-deduplicated retrieval with optional bounded verification and graph context (`backend/app/mcp/catalog.py:375-384`). It delegates to the same `run_search` used by the HTTP API (`backend/app/mcp/handlers.py:413-517`).
2. `find_top_candidates`: bounded discovery with criterion verdicts and cited evidence (`backend/app/mcp/catalog.py:353-363`; `backend/app/mcp/handlers.py:520-616`).
3. `graph_search_candidates`: a separate graph-only tool, rather than one branch of a shared hybrid planner (`backend/app/mcp/catalog.py:385-393`; `backend/app/mcp/handlers.py:716-798`).

The first implementation goal should be one internal search contract shared by all three surfaces. Product-specific policies—exhaustive versus bounded, open-pipeline versus historical pool, and whether deep verification is requested—belong in request policy, not separate retrieval engines.

## 2. Current query representation and parsing

### 2.1 `ParsedFilter` is a useful but narrow DTO

The current filter has skills/titles AND/OR lists, candidate location, minimum years, four graph predicates, required qualitative criteria, preferred criteria, residual keywords, and a degraded-parse bit (`backend/app/candidate_search/schemas.py:35-81`). It cannot represent:

- arbitrary boolean groups or negation;
- maximums/ranges except as untyped prose;
- temporal scope such as “in the last three years”;
- evidence subject, actor, action, object, or provenance requirements;
- graph path patterns beyond the four fixed predicates;
- per-clause backend strategy, confidence, or fail-closed policy.

This makes it a presentation-oriented filter, not a general search plan.

### 2.2 Deterministic-first, model fallback

The zero-model parser handles conservative high-volume searches and returns `None` for ambiguous prose (`backend/app/candidate_search/deterministic_parser.py:1-7,190-288`). The fallback parser uses a model to produce `ParsedFilter`; without organization attribution or on any model/schema failure it returns a keyword-only degraded filter (`backend/app/candidate_search/parser.py:85-108,119-157,195-217`).

That deterministic-first shape should be retained, but the output should become a backend-independent `SearchPlan` with stable clause IDs. A provider-based parser should remain an optional adapter, never the source of truth for backend semantics.

### 2.3 Current graph parsing has boolean ambiguity

The parser prompt maps “worked at Google or Meta” to two graph predicates (`backend/app/candidate_search/prompts.py:109,128-129`), while graph execution intersects **all** predicates (`backend/app/candidate_graph/search.py:138-163`). The current schema has no boolean operator to say those company predicates are alternatives. A typed boolean AST is required before expanding graph usage.

The `n_hops` field is accepted by the schema, but `_query_for_predicate` ignores its value and emits the same natural-language query for every hop count (`backend/app/candidate_search/schemas.py:23-32`; `backend/app/candidate_graph/search.py:93-104`). Exact path constraints should compile to parameterized Cypher rather than prompt phrasing.

## 3. Current PostgreSQL retrieval

### 3.1 Canonical data

The person record contains profile text, location, skills, education, experience, raw CV text, and parsed CV sections (`backend/app/models/candidate.py:7-70`). The application record carries the authoritative organization/role/lifecycle scope, per-application CV and ATS evidence, scores, decisions, and recruiter context (`backend/app/models/candidate_application.py:20-74,100-149,169-181`).

This split matters: candidate-level facts are reusable across roles, but application-level evidence can supersede or contextualize the same person's profile. A hybrid hit should therefore identify both `candidate_id` and the selected `application_id`, with an explicit evidence-source precedence policy.

### 3.2 SQL capabilities

`query_builder_sql.py` currently supports:

- alias-aware skill matching against JSON and title fields (`backend/app/candidate_search/query_builder_sql.py:95-175`);
- current and historical titles (`backend/app/candidate_search/query_builder_sql.py:178-197`);
- current or work-history countries (`backend/app/candidate_search/query_builder_sql.py:200-227`);
- an approximate minimum-years test based on the earliest recorded start year (`backend/app/candidate_search/query_builder_sql.py:230-248`);
- PostgreSQL full-text CV/profile retrieval with structured-profile fallback (`backend/app/candidate_search/query_builder_sql.py:251-292`);
- additive lexical relevance followed by stable recency/id tie-breaks (`backend/app/candidate_search/query_builder_sql.py:375-424`).

Migration 160 supplies trigram indexes for skills, experience, and profile text and GIN full-text indexes for candidate/application CVs (`backend/alembic/versions/160_add_candidate_search_indexes.py:22-67`). This is an effective exact/fallback retriever and should not be replaced by duplicating the corpus elsewhere.

Important limitations are already acknowledged in source: experience years do not account for gaps or concurrency (`backend/app/candidate_search/query_builder_sql.py:230-237`), and qualitative phrases are lexical unless the caller defers them to verification (`backend/app/candidate_search/query_builder_sql.py:311-370`).

## 4. Current graph architecture

### 4.1 Client and tenancy

Graphiti runs over Neo4j, with Anthropic extraction and Voyage embeddings. It is enabled only when Neo4j and Voyage are configured (`backend/app/candidate_graph/client.py:1-17,51-66,142-217`). A shared background event loop adapts Graphiti's async API to the synchronous application and propagates metering context (`backend/app/candidate_graph/client.py:69-139`).

Organization tenancy is represented by Graphiti `group_id = org-{organization_id}` (`backend/app/candidate_graph/client.py:14-16,64-66`). Every new graph retriever must require this scope and PostgreSQL must re-authorize every returned identifier before hydration.

### 4.2 Ingestion and coverage

Candidate episodes include identity/summary/location, skills and education, one episode per bounded experience record, and optional raw CV text (`backend/app/candidate_graph/episodes.py:65-199`). Interview transcripts/summaries and meaningful pipeline events are additional episode types (`backend/app/candidate_graph/episodes.py:202-255,258-337`).

Writes are dispatched through Graphiti with metered provider calls (`backend/app/candidate_graph/episodes.py:345-448`). Candidate content is fingerprinted to avoid unnecessary re-extraction (`backend/app/candidate_graph/sync.py:119-199`). SQLAlchemy listeners enqueue committed changes to Celery, so graph state is eventually consistent rather than transactionally current (`backend/app/candidate_graph/listeners.py:1-29,152-205`). High-value decision, outcome, recruiter-action, and role-intent episodes have a separate durable PostgreSQL outbox (`backend/app/candidate_graph/episode_outbox.py:1-18,68-103`).

The largest search constraint is the ingestion cost gate: automatic candidate sync occurs only after an application reaches `in_assessment`/`advanced` or a post-handover ATS stage (`backend/app/candidate_graph/sync.py:43-94`). The worker repeats that gate and also skips work for paused/non-running roles (`backend/app/tasks/graph_ingest_tasks.py:93-144`). A graph-first full-pool search must either expand a low-cost deterministic graph projection to every searchable candidate or calculate and expose graph coverage so missing graph presence never becomes a negative signal.

`GraphSyncState` records last sync, version, and content hash and is an existing seam for freshness/coverage checks (`backend/app/models/graph_sync_state.py:1-41`).

### 4.3 Search behavior

For explicit predicates, the adapter turns a structured predicate back into natural-language text, calls `graphiti.search`, caps results at 50, and extracts PostgreSQL IDs from model-extracted properties or `taali_id` text (`backend/app/candidate_graph/search.py:38-45,93-163,602-629`). This is semantic fact search, not exact Cypher traversal.

The graph-view query is different: `subgraph_for_query` performs a case-insensitive **substring** match against one edge's fact text (`backend/app/candidate_graph/search.py:305-363`). It is not a general semantic or multi-hop retriever.

There is also a concrete identity seam to fix before reusing `graph_search_candidates`: direct-Cypher adaptation encodes `taali_id` in `node.id` but does not copy it into `node.extra` (`backend/app/candidate_graph/search.py:422-489,842-851`), while the MCP handler reads only `node.extra["taali_id"]` (`backend/app/mcp/handlers.py:747-766`). Its test fabricates `extra.taali_id` instead of exercising the real adapter (`backend/tests/test_taali_chat_handlers.py:665-729`).

The repository also has parameterized, temporal multi-hop Cypher functions for graph priors, proving the codebase can support exact path queries (`backend/app/candidate_graph/graphrag_queries.py:31-71,79-120,264-339`). Those are decision-prior queries rather than a candidate-search API, but their parameterized execution and explicit time anchor are the better pattern for exact graph constraints.

## 5. Current ranking and grounding

### 5.1 Basic runner

`run_search` applies SQL first, intersects graph candidate IDs only when graph predicates exist, orders/deduplicates the complete SQL result, and optionally verifies the first 50 candidates (`backend/app/candidate_search/runner.py:169-229,241-309`). If Neo4j is unavailable or errors, graph predicates are dropped and the broader SQL result is returned with a warning (`backend/app/candidate_search/runner.py:358-404`). That is acceptable for exploratory display only; it is unsafe for a required relationship constraint because failure broadens the result.

The basic reranker sends one compact candidate summary and optional graph neighbourhood per candidate, then accepts a model-emitted boolean and reason (`backend/app/candidate_search/rerank.py:92-145,174-264`). It retains explicit error states and bounded coverage, but its decision is not tied to source spans (`backend/app/candidate_search/rerank.py:267-378`).

### 5.2 Grounded top-candidate flow

The more trustworthy `find_top_candidates` path separates structured population retrieval from qualitative grounding (`backend/app/candidate_search/top_candidates.py:505-569`). It fails closed on degraded parsing, zero structural matches, too many required criteria, and unavailable required-evidence verification (`backend/app/candidate_search/top_candidates.py:600-705,768-816`).

Its grounder requires a verbatim citation for a verdict to be marked grounded and marks omitted criteria as errors rather than evidence of absence (`backend/app/candidate_search/grounded_evidence.py:251-348`). Evidence reads capped CV and notes documents, caches by exact document/criterion hash, and preserves explicit failure (`backend/app/candidate_search/grounded_evidence.py:419-471,545-654`).

However, grounding is bounded to at most eight criteria and a 50-candidate window (`backend/app/candidate_search/top_candidates.py:68-91,854-883`). This is a verification layer over retrieved candidates, not a recall mechanism. The general framework must report `UNKNOWN` outside the checked window and must never turn “not retrieved from a sparse graph” into “not qualified.”

## 6. Production constraints the new framework must preserve

1. **Tenant and lifecycle authority:** every base SQL query is organization-scoped and excludes deleted rows before search (`backend/app/candidate_search/runner.py:91-98`; `backend/app/mcp/handlers.py:438-458`). Graph IDs must always be rehydrated through this scope.
2. **Eventual graph consistency:** graph writes happen after root commit on workers and may lag (`backend/app/candidate_graph/listeners.py:152-205`). Search must expose graph freshness and compensate with PostgreSQL.
3. **Sparse graph coverage:** the automatic cost gate excludes early/rejected candidates (`backend/app/candidate_graph/sync.py:43-58`). Coverage must be measured per requested pool.
4. **Optional infrastructure:** Graphiti is deliberately disabled when Neo4j/Voyage is not configured (`backend/app/platform/config.py:309-333`). Required graph clauses must fail closed; optional graph enrichment may fall back.
5. **Provider admission:** parser, rerank, grounding, Graphiti extraction, and graph query embeddings reserve organization/role usage before provider work (`backend/app/candidate_search/metering.py:22-66`; `backend/app/candidate_graph/search.py:48-90`).
6. **Bounded fan-out:** graph search, grounding windows, criteria, and response graph size are capped. The framework should make budgets part of `SearchPolicy`, not scattered constants.
7. **Single-worker assumptions:** parser cache and API rate limiting are process-local by design (`backend/app/candidate_search/cache.py:1-10`; `backend/app/candidate_search/rate_limit.py:1-18`). A multi-worker rollout needs Redis-backed equivalents.

## 7. Recommended target framework

### 7.1 Backend-independent search plan

Introduce an immutable `SearchPlan` with stable clause IDs:

```text
SearchPlan
  scope: organization, roles, lifecycle/pool policy
  expression: AND | OR | NOT of typed clauses
  clauses:
    Skill, Occupation, Employer, Education, Location, ExperienceDuration,
    Temporal, RelationshipPath, NumericConstraint, QualitativeClaim
  preferences: typed optional clauses
  ranking_intent: relevance | fit | recency | explicit score
  evidence_policy: required source kinds and fail-closed behavior
```

Each clause should preserve the original text, normalized entities, operator, subject/actor/action/object, time range, required/optional priority, parser provenance, and confidence. Compilers—not the parser—decide whether a clause is supported by graph, PostgreSQL, or both.

### 7.2 Retriever interfaces

Use adapters that return the same source-addressable hit shape:

```text
Retriever.search(plan, policy) -> RetrievalResult
RetrievalResult:
  candidate hits: candidate_id, application candidates, score, matched clause IDs
  evidence refs: source kind, record/fact/episode ID, offsets/properties, timestamps
  coverage: eligible population, indexed population, freshness, caps, errors
```

- `GraphRetriever`: primary semantic and relational recall. Use Graphiti semantic search for unstructured claims; use parameterized Cypher for exact employer/education/path/time clauses. Return fact/edge/episode IDs, not rendered prose alone.
- `PostgresRetriever`: authoritative role/lifecycle/deletion/numeric filtering plus exact structured and FTS recall. Return row/field/span references.
- `HybridPlanner`: choose graph-first only when the graph is healthy and coverage passes policy; always apply PostgreSQL authority filters; run PostgreSQL as fallback or parallel recall where graph coverage is incomplete.

### 7.3 Fusion and candidate identity

Merge at `candidate_id`, then select the relevant `application_id` under the request's role/pool policy. Use a deterministic fusion method such as reciprocal-rank fusion initially; retain per-backend ranks and clause matches for debugging. Do not use a score earned for another role as evidence of relevance to a new requirement.

For required clauses, distinguish:

- `MATCH`: supported by valid evidence;
- `NO_MATCH`: searched with sufficient coverage and contradictory/negative evidence is valid;
- `UNKNOWN`: corpus/index/backend coverage is insufficient;
- `ERROR`: execution failed.

Only `MATCH` qualifies. `UNKNOWN` and `ERROR` must not be rendered as negative evidence or as a definitive zero-candidate claim.

### 7.4 Grounding contract

Build a `CandidateEvidenceBundle` from both stores before any model judgement. Every positive criterion verdict must cite a retriever-produced evidence reference that can be revalidated against the current source. Graph facts should carry validity timestamps and the source episode; PostgreSQL evidence should carry table/row/field and text offsets or a content hash.

The model, if used, should classify an already bounded evidence bundle rather than search the corpus itself. Deterministic validators should enforce citation existence, candidate ownership, source text equality, temporal validity, numeric operators, and criterion-to-evidence entity binding. Required criteria fail closed when citations are invalid or coverage is incomplete.

## 8. Evaluation framework required before rollout

`origin/main` has shared agreement/calibration math, but explicitly leaves pipeline harnesses beside individual consumers (`backend/app/evals/__init__.py:1-10`; `backend/app/evals/metrics.py:1-16`). Search tests are mostly contract tests: SQL tests compile PostgreSQL syntax without executing a database (`backend/tests/test_candidate_search_sql.py:1-7`), endpoint tests mock the runner (`backend/tests/test_api_nl_search.py:1-10,83-110`), and graph adapter tests mock Graphiti/Neo4j (`backend/tests/test_candidate_graph_queries.py:1-12`). There is no end-to-end known-truth search corpus on the baseline.

The new framework should add layered, general-purpose evals:

1. **Parser truth:** query -> complete typed `SearchPlan`, including boolean/priority/negation/time semantics.
2. **PostgreSQL retrieval truth:** real temporary PostgreSQL corpus -> expected IDs and evidence refs.
3. **Graph retrieval truth:** deterministic Neo4j/Graphiti fixture -> expected paths, IDs, facts, and coverage. No live provider dependency in CI.
4. **Hybrid fusion truth:** known backend result sets -> expected recall, ordering, deduplication, and fallback state.
5. **Grounding truth:** candidate evidence bundle -> expected per-clause `MATCH|NO_MATCH|UNKNOWN|ERROR` plus valid citation targets.
6. **End-to-end truth:** query + dual-store fixture -> exact acceptable candidate set, exclusions, ordering bands, evidence, coverage, and degradation behavior.

The corpus should be domain-neutral and stratified across occupation, skill, domain experience, employer, education, location, duration, numeric constraints, temporal constraints, relationships, negation, preferences, ambiguous wording, typos, and multi-clause boolean queries. Every positive should have paired hard negatives and metamorphic variants such as candidate-vs-team action, employer-vs-candidate location, exact-vs-related technology, and sufficient-vs-insufficient duration.

Core release metrics should include parser clause F1, candidate recall@K and precision@K, MRR/nDCG for ranked cases, required-clause false-positive rate, citation validity, evidence ownership accuracy, graph/PostgreSQL disagreement, `UNKNOWN` calibration, backend coverage, latency, and provider-call/cost budgets. Required-criterion false positives and invalid citations should be zero-tolerance gates; recall thresholds should be stratified by query family so a common easy class cannot hide failure on relationship or temporal searches.

Any live-model eval must be an explicitly approved, opt-in job that declares model, case count, trials, maximum calls/tokens, and estimated cost before execution. Deterministic CI and local implementation tests must use fakes or retained responses and make no paid provider calls.

## 9. Recommended implementation order

1. Add the generic `SearchPlan`, `SearchPolicy`, `RetrievalResult`, `EvidenceRef`, and verdict contracts with fixture validation.
2. Build the known-truth dual-store harness first, including graph-unavailable, graph-sparse, graph-stale, PostgreSQL-only, and backend-disagreement cases.
3. Extract the existing SQL builder behind `PostgresRetriever` without changing product behavior.
4. Implement `GraphRetriever` with canonical ID propagation, exact parameterized Cypher clauses, semantic fact retrieval, coverage/freshness reporting, and no hidden 50-result truncation.
5. Add the hybrid planner and deterministic fusion; make PostgreSQL tenant/lifecycle validation mandatory for every graph hit.
6. Replace ad hoc boolean reranking with the generic evidence bundle and grounding contract.
7. Migrate `run_search`, `find_top_candidates`, and `graph_search_candidates` onto the same orchestrator while preserving their product policies and response compatibility.
8. Add observability for per-backend candidates, overlap, dropped IDs, graph coverage/freshness, clause verdicts, caps, latency, and cost.
9. Only after deterministic and retained-response gates pass, request approval for a small live-model preflight; do not start a full paid matrix automatically.

## 10. Lessons from the abandoned Agentforce-specific branch

The unmerged branch demonstrates two useful ideas—known-truth PostgreSQL system cases and strict citation/fail-closed checks—but implements them by adding thousands of product-name-specific lines to the central grounder and a single-domain golden set. Those should not be merged as the architecture. Carry forward the test discipline and generic contracts; express Agentforce, Treasury, healthcare, Java, employer-history, and every future topic as fixture data consumed by the same typed search/evidence framework.
