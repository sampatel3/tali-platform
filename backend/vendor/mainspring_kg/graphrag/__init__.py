"""GraphRAG read substrate ported from tali-platform (ADR-0010 KG cutover).

This package holds the Graphiti client lifecycle + the multi-hop Cypher
GraphRAG queries + the prior synthesis. The Cypher query strings and the
``synthesise_prior`` math are a FAITHFUL, CHARACTER-IDENTICAL port of
tali's ``app/candidate_graph/{client,graphrag_queries}.py`` so that, run
over the same Neo4j graph, they produce byte-identical priors by
construction.

Only the *config source* differs from tali: tali reads its pydantic
``settings``; mainspring reads ``os.environ`` (the substrate has no
brand settings object). The config plumbing does not touch the query
strings or the synthesis — those are verbatim.

``graphiti-core`` + ``neo4j`` are an OPTIONAL mainspring extra
(``mainspring[knowledge_graph]``). All imports of those libraries are
lazy (deferred to first real call), so importing this package — and the
:class:`~mainspring.accelerator.knowledge_graph.graphiti.GraphitiBackend`
that wraps it — never requires the libraries to be installed. They are
only needed when an actual Graphiti round-trip happens.
"""
