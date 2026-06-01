#!/usr/bin/env bash
# Mirror-vendor mainspring's knowledge-graph backend into the backend
# (ADR-0010 KG cutover). Post-carve (3-layer), the KG contract lives in
# governance/spec and the concrete backends in the accelerator:
#
#   mainspring/spec/knowledge_graph.py                      — the contract
#       (KnowledgeGraphBackend Protocol + Priors/EpisodePayload/ReplayResult
#        dataclasses). ORM-free, stdlib-only.
#   mainspring/accelerator/knowledge_graph/graphiti.py      — the REAL
#       GraphitiBackend (GraphRAG read path: get_priors over Neo4j).
#   mainspring/accelerator/knowledge_graph/graphrag/{client,graphrag_queries}.py
#       — the Graphiti client lifecycle + the multi-hop Cypher queries +
#         synthesise_prior (a CHARACTER-IDENTICAL port of tali's GraphRAG).
#
# This vendors the REAL get_priors (no longer interface-only): tali's
# graph_priors sub-agent routes its GraphRAG prior through the vendored
# GraphitiBackend. The contract (spec/knowledge_graph.py) is vendored as
# ``base.py`` so existing ``from vendor.mainspring_kg.base import ...``
# imports keep resolving. graphiti-core / neo4j are an OPTIONAL mainspring
# extra and imported lazily, so the vendored modules import without them.
# Mirrors backend/scripts/vendor_mainspring_metering.sh.
#
#   MAINSPRING_DIR=/path/to/mainspring bash backend/scripts/vendor_mainspring_kg.sh
set -euo pipefail

MS="${MAINSPRING_DIR:-../mainspring}"
SPEC_SRC="$MS/mainspring/spec/knowledge_graph.py"
GRAPHITI_SRC="$MS/mainspring/accelerator/knowledge_graph/graphiti.py"
GRAPHRAG_SRC="$MS/mainspring/accelerator/knowledge_graph/graphrag"
DEST="backend/vendor/mainspring_kg"

[ -f "$SPEC_SRC" ]     || { echo "mainspring KG contract not found at $SPEC_SRC (set MAINSPRING_DIR)"; exit 1; }
[ -f "$GRAPHITI_SRC" ] || { echo "mainspring GraphitiBackend not found at $GRAPHITI_SRC (set MAINSPRING_DIR)"; exit 1; }
[ -d "$GRAPHRAG_SRC" ] || { echo "mainspring graphrag submodule not found at $GRAPHRAG_SRC (set MAINSPRING_DIR)"; exit 1; }

mkdir -p "$DEST/graphrag"
# Contract → base.py (keeps ``vendor.mainspring_kg.base`` resolving).
cp "$SPEC_SRC" "$DEST/base.py"
# Real GraphitiBackend (imports ``from .base`` + ``from .graphrag``).
cp "$GRAPHITI_SRC" "$DEST/graphiti.py"
# Ported GraphRAG client + Cypher queries + synthesis (verbatim).
cp "$GRAPHRAG_SRC/__init__.py"        "$DEST/graphrag/__init__.py"
cp "$GRAPHRAG_SRC/client.py"          "$DEST/graphrag/client.py"
cp "$GRAPHRAG_SRC/graphrag_queries.py" "$DEST/graphrag/graphrag_queries.py"

SHA="$(git -C "$MS" rev-parse --short HEAD 2>/dev/null || echo unknown)"
{
  echo "mainspring @ $SHA"
  echo "Files:"
  echo "  base.py                       <- spec/knowledge_graph.py (Protocol + dataclasses, ORM-free)"
  echo "  graphiti.py                   <- accelerator/knowledge_graph/graphiti.py (REAL GraphitiBackend.get_priors)"
  echo "  graphrag/client.py            <- accelerator/knowledge_graph/graphrag/client.py (Graphiti lifecycle)"
  echo "  graphrag/graphrag_queries.py  <- accelerator/knowledge_graph/graphrag/graphrag_queries.py (Cypher + synthesise_prior; char-identical port of tali)"
  echo "ADR-0010 KG cutover: tali's graph_priors sub-agent routes its GraphRAG"
  echo "prior through this vendored GraphitiBackend.get_priors. graphiti-core /"
  echo "neo4j are an OPTIONAL mainspring extra, imported lazily inside the"
  echo "graphrag client, so these modules import without them."
  echo "Re-vendor: bash backend/scripts/vendor_mainspring_kg.sh"
} > "$DEST/MAINSPRING_REF.txt"

echo "vendored mainspring kg backend @ $SHA -> $DEST"
