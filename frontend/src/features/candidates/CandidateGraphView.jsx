import React, { useEffect, useMemo, useRef, useState } from 'react';
import cytoscape from 'cytoscape';
import { X } from 'lucide-react';

/**
 * Force-directed graph view of NL-search results.
 *
 * Renders {nodes, edges} returned from the API. Uses cytoscape's `cose`
 * layout (built-in, no extra packages). Click any node → side panel.
 *
 * The graph is read-only — all filtering happens via the search bar
 * above. Past ~200 nodes the layout slows; we surface a banner instead
 * of degrading silently.
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
    const cy = cytoscape({
      container: containerRef.current,
      elements,
      style: STYLE,
      layout: { name: 'cose', animate: false, padding: 30, idealEdgeLength: 80 },
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

function buildElements(subgraph) {
  if (!subgraph || !Array.isArray(subgraph.nodes)) {
    return { elements: [], nodeCount: 0, overflow: false };
  }
  const allNodes = subgraph.nodes;
  const nodes = allNodes.slice(0, MAX_NODES);
  const idSet = new Set(nodes.map((n) => n.id));
  const elements = [];
  for (const node of nodes) {
    elements.push({
      data: {
        id: node.id,
        label: node.name,
        kind: node.label,
        extra: node.extra || {},
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

const STYLE = [
  {
    selector: 'node',
    style: {
      'background-color': '#9d8df1',
      label: 'data(label)',
      'font-size': 11,
      'text-wrap': 'wrap',
      'text-max-width': 80,
      'text-valign': 'bottom',
      'text-margin-y': 4,
      color: '#1a1a2e',
      width: 28,
      height: 28,
    },
  },
  {
    selector: 'node[kind = "Person"]',
    style: {
      'background-color': '#6c5ce7',
      shape: 'round-rectangle',
      width: 36,
      height: 28,
      color: '#0e0e1a',
      'font-weight': 600,
    },
  },
  {
    selector: 'node[kind = "Company"]',
    style: {
      'background-color': '#22c1c3',
      shape: 'ellipse',
    },
  },
  {
    selector: 'node[kind = "School"]',
    style: {
      'background-color': '#fdcb6e',
      shape: 'diamond',
    },
  },
  {
    selector: 'node[kind = "Skill"]',
    style: {
      'background-color': '#a0a0a0',
      shape: 'round-tag',
      width: 24,
      height: 24,
      'font-size': 10,
    },
  },
  {
    selector: 'edge',
    style: {
      width: 1.4,
      'line-color': 'rgba(150,150,160,0.55)',
      'curve-style': 'bezier',
      'target-arrow-shape': 'none',
      opacity: 0.85,
    },
  },
  {
    selector: 'edge[label = "WORKED_AT"]',
    style: {
      width: 2.2,
      'line-color': 'rgba(108,92,231,0.55)',
    },
  },
  {
    selector: 'edge[label = "STUDIED_AT"]',
    style: {
      width: 1.6,
      'line-color': 'rgba(253,203,110,0.55)',
    },
  },
  {
    selector: 'edge[label = "HAS_SKILL"]',
    style: {
      width: 1,
      'line-color': 'rgba(160,160,160,0.4)',
      'line-style': 'dashed',
    },
  },
];

function GraphSidePanel({ node, subgraph, onClose, onSelectCandidate }) {
  const candidateMatches =
    node.kind === 'Company' || node.kind === 'School'
      ? findCandidatesConnectedTo(subgraph, node.id)
      : [];
  return (
    <aside className="graph-side" role="dialog" aria-label={`${node.kind} details`}>
      <header className="graph-side__head">
        <span className="graph-side__kind">{node.kind}</span>
        <h3 className="graph-side__name">{node.label || '—'}</h3>
        <button type="button" onClick={onClose} aria-label="Close">
          <X size={16} />
        </button>
      </header>
      <div className="graph-side__body">
        {node.kind === 'Person' && node.extra?.headline ? (
          <p className="graph-side__headline">{node.extra.headline}</p>
        ) : null}
        {node.kind === 'Person' && onSelectCandidate ? (
          <button
            type="button"
            className="btn btn-purple btn-sm"
            onClick={() => onSelectCandidate(node.id.replace(/^person:/, ''))}
          >
            Open profile
          </button>
        ) : null}
        {candidateMatches.length > 0 ? (
          <div>
            <h4 className="graph-side__subhead">Connected candidates</h4>
            <ul className="graph-side__list">
              {candidateMatches.map((c) => (
                <li key={c.id}>
                  {onSelectCandidate ? (
                    <button
                      type="button"
                      className="link"
                      onClick={() => onSelectCandidate(c.id.replace(/^person:/, ''))}
                    >
                      {c.label || c.id}
                    </button>
                  ) : (
                    <span>{c.label || c.id}</span>
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

function findCandidatesConnectedTo(subgraph, nodeId) {
  if (!subgraph) return [];
  const peopleIds = new Set();
  for (const edge of subgraph.edges || []) {
    if (edge.target === nodeId && edge.source.startsWith('person:')) {
      peopleIds.add(edge.source);
    }
  }
  return (subgraph.nodes || []).filter((n) => peopleIds.has(n.id));
}
