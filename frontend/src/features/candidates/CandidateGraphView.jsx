import React, { useEffect, useMemo, useRef, useState } from 'react';
import cytoscape from 'cytoscape';
import { X } from 'lucide-react';

import { Button } from '../../shared/ui/TaaliPrimitives';
import './candidateVisualTokens.css';

/**
 * Force-directed graph view of NL-search results.
 *
 * Person nodes are coloured and sized by cv_match_score (green=high,
 * amber=medium, red=low, grey=unscored). Company nodes are large hubs so
 * the cose layout naturally clusters candidates around shared employers.
 *
 * Props:
 *   subgraph: { nodes: [{id,label,name,extra}], edges: [{source,target,label,extra}] }
 *   onSelectCandidate(personId): optional drill-in handler for the side panel
 *   isLoading: boolean
 */
export function CandidateGraphView({ subgraph, onSelectCandidate, isLoading = false }) {
  const containerRef = useRef(null);
  const cyRef = useRef(null);
  const [selected, setSelected] = useState(null);

  const { elements, nodeCount, overflow } = useMemo(
    () => buildElements(subgraph),
    [subgraph]
  );

  useEffect(() => {
    if (!containerRef.current) return;
    if (cyRef.current) {
      cyRef.current.destroy();
      cyRef.current = null;
    }
    const palette = readGraphPalette(containerRef.current);
    const cy = cytoscape({
      container: containerRef.current,
      elements,
      style: buildGraphStyle(palette),
      layout: {
        name: 'cose',
        animate: false,
        padding: 40,
        idealEdgeLength: 100,
        nodeRepulsion: () => 8000,
        gravity: 0.25,
      },
      wheelSensitivity: 0.2,
    });
    cy.on('tap', 'node', (event) => {
      const data = event.target.data();
      setSelected(data);
    });
    cy.on('tap', (event) => {
      if (event.target === cy) setSelected(null);
    });
    cyRef.current = cy;
    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, [elements]);

  if (isLoading) {
    return (
      <div className="graph-view graph-view--loading" role="status">
        Building graph…
      </div>
    );
  }

  if (!subgraph || elements.length === 0) {
    return (
      <div className="graph-view graph-view--empty">
        <p>No graph data for this query.</p>
        <p className="muted">
          Try a query that mentions companies, schools, or skills — e.g. "Worked at Google" or
          "Python and Kubernetes".
        </p>
      </div>
    );
  }

  return (
    <div className="graph-view">
      {overflow ? (
        <div className="graph-view__banner">
          Showing top {nodeCount} nodes. Refine the query to narrow further.
        </div>
      ) : null}
      <ScoreLegend />
      <div className="graph-view__canvas" ref={containerRef} />
      {selected ? (
        <GraphSidePanel
          node={selected}
          subgraph={subgraph}
          onClose={() => setSelected(null)}
          onSelectCandidate={onSelectCandidate}
        />
      ) : null}
    </div>
  );
}

const MAX_NODES = 200;

/** Map a 0-100 score to a visual band used in cytoscape selectors. */
function scoreBand(score) {
  if (score == null) return 'unscored';
  if (score >= 75) return 'high';
  if (score >= 50) return 'medium';
  return 'low';
}

/** Map score to node diameter (Person nodes only). */
function scoreSize(score) {
  if (score == null) return 28;
  if (score >= 75) return 44;
  if (score >= 50) return 36;
  return 28;
}

function buildElements(subgraph) {
  if (!subgraph || !Array.isArray(subgraph.nodes)) {
    return { elements: [], nodeCount: 0, overflow: false };
  }
  const allNodes = subgraph.nodes;
  const nodes = allNodes.slice(0, MAX_NODES);
  const idSet = new Set(nodes.map((n) => n.id));
  const elements = [];
  for (const node of nodes) {
    const score = node.extra?.cv_match_score ?? null;
    const band = node.label === 'Person' ? scoreBand(score) : null;
    const size = node.label === 'Person' ? scoreSize(score) : undefined;
    elements.push({
      data: {
        id: node.id,
        label: node.name,
        kind: node.label,
        extra: node.extra || {},
        scoreBand: band,
        score,
        ...(size !== undefined ? { nodeSize: size } : {}),
      },
    });
  }
  for (const edge of subgraph.edges || []) {
    if (!idSet.has(edge.source) || !idSet.has(edge.target)) continue;
    elements.push({
      data: {
        id: `${edge.source}->${edge.target}:${edge.label}`,
        source: edge.source,
        target: edge.target,
        label: edge.label,
        extra: edge.extra || {},
      },
    });
  }
  return {
    elements,
    nodeCount: nodes.length,
    overflow: allNodes.length > MAX_NODES,
  };
}

const GRAPH_COLOR_TOKENS = {
  nodeDefault: '--candidate-search-graph-node-default',
  label: '--candidate-search-graph-label',
  personLabel: '--candidate-search-graph-person-label',
  scoreHigh: '--candidate-search-graph-score-high',
  scoreMedium: '--candidate-search-graph-score-medium',
  scoreLow: '--candidate-search-graph-score-low',
  scoreUnscored: '--candidate-search-graph-score-unscored',
  company: '--candidate-search-graph-company',
  school: '--candidate-search-graph-school',
  skill: '--candidate-search-graph-skill',
  skillLabel: '--candidate-search-graph-skill-label',
  country: '--candidate-search-graph-country',
  edge: '--candidate-search-graph-edge',
  workEdge: '--candidate-search-graph-work-edge',
  studyEdge: '--candidate-search-graph-study-edge',
  skillEdge: '--candidate-search-graph-skill-edge',
};

const readGraphPalette = (element) => {
  const computedStyle = getComputedStyle(element);
  return Object.fromEntries(
    Object.entries(GRAPH_COLOR_TOKENS).map(([name, token]) => [
      name,
      computedStyle.getPropertyValue(token).trim(),
    ])
  );
};

const buildGraphStyle = (palette) => [
  // ── Base ──────────────────────────────────────────────────────────────────
  {
    selector: 'node',
    style: {
      'background-color': palette.nodeDefault,
      label: 'data(label)',
      'font-size': 11,
      'text-wrap': 'wrap',
      'text-max-width': 80,
      'text-valign': 'bottom',
      'text-margin-y': 4,
      color: palette.label,
      width: 28,
      height: 28,
    },
  },
  // ── Person: size driven by data(nodeSize), colour by scoreBand ────────────
  {
    selector: 'node[kind = "Person"]',
    style: {
      shape: 'round-rectangle',
      width: 'data(nodeSize)',
      height: 'data(nodeSize)',
      'font-weight': 600,
      color: palette.personLabel,
    },
  },
  {
    selector: 'node[kind = "Person"][scoreBand = "high"]',
    style: { 'background-color': palette.scoreHigh },
  },
  {
    selector: 'node[kind = "Person"][scoreBand = "medium"]',
    style: { 'background-color': palette.scoreMedium },
  },
  {
    selector: 'node[kind = "Person"][scoreBand = "low"]',
    style: { 'background-color': palette.scoreLow },
  },
  {
    selector: 'node[kind = "Person"][scoreBand = "unscored"]',
    style: { 'background-color': palette.scoreUnscored },
  },
  // ── Company: large hubs so cose clusters candidates around them ───────────
  {
    selector: 'node[kind = "Company"]',
    style: {
      'background-color': palette.company,
      shape: 'ellipse',
      width: 56,
      height: 56,
      'font-size': 12,
      'font-weight': 700,
      'text-max-width': 100,
    },
  },
  // ── Other node types ──────────────────────────────────────────────────────
  {
    selector: 'node[kind = "School"]',
    style: {
      'background-color': palette.school,
      shape: 'diamond',
      width: 40,
      height: 40,
    },
  },
  {
    selector: 'node[kind = "Skill"]',
    style: {
      'background-color': palette.skill,
      color: palette.skillLabel,
      shape: 'round-tag',
      width: 24,
      height: 24,
      'font-size': 10,
    },
  },
  {
    selector: 'node[kind = "Country"]',
    style: {
      'background-color': palette.country,
      shape: 'hexagon',
      width: 32,
      height: 32,
    },
  },
  // ── Edges ─────────────────────────────────────────────────────────────────
  {
    selector: 'edge',
    style: {
      width: 1.4,
      'line-color': palette.edge,
      'curve-style': 'bezier',
      'target-arrow-shape': 'none',
      opacity: 0.8,
    },
  },
  {
    selector: 'edge[label = "WORKED_AT"]',
    style: { width: 2.4, 'line-color': palette.workEdge },
  },
  {
    selector: 'edge[label = "STUDIED_AT"]',
    style: { width: 1.6, 'line-color': palette.studyEdge },
  },
  {
    selector: 'edge[label = "HAS_SKILL"]',
    style: {
      width: 1,
      'line-color': palette.skillEdge,
      'line-style': 'dashed',
    },
  },
];

// ── Legend ────────────────────────────────────────────────────────────────────

function ScoreLegend() {
  return (
    <div className="graph-legend">
      <span className="graph-legend__item graph-legend__item--high">High score ≥75</span>
      <span className="graph-legend__item graph-legend__item--medium">Medium ≥50</span>
      <span className="graph-legend__item graph-legend__item--low">Low &lt;50</span>
      <span className="graph-legend__item graph-legend__item--unscored">Unscored</span>
      <span className="graph-legend__divider" />
      <span className="graph-legend__item graph-legend__item--company">Company hub</span>
    </div>
  );
}

// ── Side panel ────────────────────────────────────────────────────────────────

function GraphSidePanel({ node, subgraph, onClose, onSelectCandidate }) {
  const connectedCandidates =
    node.kind === 'Company' || node.kind === 'School'
      ? findCandidatesConnectedTo(subgraph, node.id)
      : [];

  // Sort connected candidates by score descending so top candidates appear first
  const sortedCandidates = [...connectedCandidates].sort((a, b) => {
    const sa = a.extra?.cv_match_score ?? -1;
    const sb = b.extra?.cv_match_score ?? -1;
    return sb - sa;
  });

  return (
    <aside className="graph-side" role="dialog" aria-label={`${node.kind} details`}>
      <header className="graph-side__head">
        <span className="graph-side__kind">{node.kind}</span>
        <h3 className="graph-side__name">{node.label || '—'}</h3>
        <Button variant="ghost" size="xs" iconOnly onClick={onClose} aria-label="Close">
          <X size={16} />
        </Button>
      </header>
      <div className="graph-side__body">
        {node.kind === 'Person' && (
          <>
            {node.extra?.headline ? (
              <p className="graph-side__headline">{node.extra.headline}</p>
            ) : null}
            <ScoreBadge score={node.score} />
            {onSelectCandidate ? (
              <button
                type="button"
                className="btn btn-purple btn-sm"
                onClick={() => onSelectCandidate(node.id.replace(/^person:/, ''))}
              >
                Open profile
              </button>
            ) : null}
          </>
        )}
        {sortedCandidates.length > 0 ? (
          <div>
            <h4 className="graph-side__subhead">
              Connected candidates ({sortedCandidates.length})
            </h4>
            <ul className="graph-side__list">
              {sortedCandidates.map((c) => (
                <li key={c.id} className="graph-side__candidate-row">
                  <ScoreBadge score={c.extra?.cv_match_score ?? null} compact />
                  {onSelectCandidate ? (
                    <button
                      type="button"
                      className="taali-text-btn link"
                      onClick={() => onSelectCandidate(c.id.replace(/^person:/, ''))}
                    >
                      {c.name || c.id}
                    </button>
                  ) : (
                    <span>{c.name || c.id}</span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </aside>
  );
}

function ScoreBadge({ score, compact = false }) {
  if (score == null) {
    return compact ? null : <span className="score-badge score-badge--unscored">Not scored</span>;
  }
  const band = scoreBand(score);
  return (
    <span className={`score-badge score-badge--${band}`}>
      {Math.round(score)}
      {compact ? '' : ' / 100'}
    </span>
  );
}

function findCandidatesConnectedTo(subgraph, nodeId) {
  if (!subgraph) return [];
  const peopleIds = new Set();
  for (const edge of subgraph.edges || []) {
    if (edge.target === nodeId && edge.source.startsWith('person:')) {
      peopleIds.add(edge.source);
    }
    // Also catch reverse direction in case Graphiti flips source/target
    if (edge.source === nodeId && edge.target.startsWith('person:')) {
      peopleIds.add(edge.target);
    }
  }
  return (subgraph.nodes || []).filter((n) => peopleIds.has(n.id));
}
