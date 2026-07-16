import React, { useEffect, useMemo, useRef } from 'react';
import cytoscape from 'cytoscape';

import './GraphView.css';

// Inline, compact graph visualisation for ``graph_search_candidates``
// tool results. Smaller and read-only compared to
// ``features/candidates/CandidateGraphView`` — no side panel, no
// drill-in, just enough to show the recruiter the shape of the
// returned subgraph (Person, Company, School, Skill, Country nodes
// with WORKED_AT / STUDIED_AT / HAS_SKILL / LOCATED_IN edges).

// Cytoscape renders to <canvas>, so resolve this component's scoped semantic
// tokens before handing the palette to its renderer.
const NODE_COLOR_TOKEN = {
  Person: '--chat-evidence-graph-person-node',
  Company: '--chat-evidence-graph-company-node',
  School: '--chat-evidence-graph-school-node',
  Skill: '--chat-evidence-graph-skill-node',
  Country: '--chat-evidence-graph-country-node',
};

const readGraphPalette = (element) => {
  const computedStyle = getComputedStyle(element);
  const readToken = (token) => computedStyle.getPropertyValue(token).trim();
  return {
    nodes: Object.fromEntries(
      Object.entries(NODE_COLOR_TOKEN).map(([kind, token]) => [kind, readToken(token)])
    ),
    defaultNode: readToken('--chat-evidence-graph-default-node'),
    label: readToken('--chat-evidence-graph-label'),
    edge: readToken('--chat-evidence-graph-edge'),
  };
};

const buildElements = (graph) => {
  const elements = [];
  if (!graph) return elements;
  const nodeIds = new Set();
  for (const n of graph.nodes || []) {
    nodeIds.add(n.id);
    elements.push({
      data: { id: n.id, label: n.name || n.label, kind: n.label },
    });
  }
  // Use a stable id for every edge so cytoscape doesn't drop dupes.
  // Skip edges whose source/target isn't in the kept node set —
  // cytoscape throws synchronously on dangling endpoints, which would
  // bubble up to the React error boundary.
  const seen = new Set();
  for (const e of graph.edges || []) {
    if (!nodeIds.has(e.source) || !nodeIds.has(e.target)) continue;
    const key = `${e.source}->${e.target}|${e.label}`;
    if (seen.has(key)) continue;
    seen.add(key);
    elements.push({
      data: {
        id: key,
        source: e.source,
        target: e.target,
        rel: e.label,
        fact: e.fact || '',
      },
    });
  }
  return elements;
};

const buildGraphStyle = (palette) => [
  {
    selector: 'node',
    style: {
      'background-color': (ele) => palette.nodes[ele.data('kind')] || palette.defaultNode,
      label: 'data(label)',
      color: palette.label,
      'font-size': '10px',
      'font-family': 'Geist, Inter, system-ui, sans-serif',
      'text-valign': 'bottom',
      'text-margin-y': 6,
      'text-max-width': '120px',
      'text-wrap': 'ellipsis',
      'border-width': 0,
      width: (ele) => (ele.data('kind') === 'Company' ? 22 : 14),
      height: (ele) => (ele.data('kind') === 'Company' ? 22 : 14),
    },
  },
  {
    selector: 'edge',
    style: {
      width: 1,
      'line-color': palette.edge,
      'curve-style': 'bezier',
      'target-arrow-shape': 'triangle',
      'target-arrow-color': palette.edge,
      'arrow-scale': 0.6,
    },
  },
];

const GraphView = ({ graph }) => {
  const containerRef = useRef(null);
  const cyRef = useRef(null);
  const elements = useMemo(() => buildElements(graph), [graph]);

  useEffect(() => {
    if (!containerRef.current) return undefined;
    if (cyRef.current) {
      cyRef.current.destroy();
      cyRef.current = null;
    }
    if (!elements.length) return undefined;
    const palette = readGraphPalette(containerRef.current);
    const cy = cytoscape({
      container: containerRef.current,
      elements,
      style: buildGraphStyle(palette),
      layout: {
        name: 'cose',
        animate: false,
        padding: 24,
        idealEdgeLength: 70,
        nodeRepulsion: () => 4500,
        gravity: 0.3,
      },
      wheelSensitivity: 0.2,
      autounselectify: true,
    });
    cyRef.current = cy;
    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, [elements]);

  if (!graph || (!graph.nodes?.length && !graph.edges?.length)) return null;

  return (
    <div className="cp-graph">
      <div className="cp-graph-head">
        <span>{graph.nodes.length} entries · {graph.edges.length} connections</span>
        <span className="cp-graph-legend">
          <span className="cp-graph-dot" style={{ background: `var(${NODE_COLOR_TOKEN.Person})` }} /> Person
          <span className="cp-graph-dot" style={{ background: `var(${NODE_COLOR_TOKEN.Company})` }} /> Company
          <span className="cp-graph-dot" style={{ background: `var(${NODE_COLOR_TOKEN.School})` }} /> School
          <span className="cp-graph-dot" style={{ background: `var(${NODE_COLOR_TOKEN.Skill})` }} /> Skill
        </span>
      </div>
      <div ref={containerRef} className="cp-graph-canvas" />
    </div>
  );
};

export default GraphView;
