import React, { useEffect, useMemo, useRef } from 'react';
import cytoscape from 'cytoscape';
import { Network } from 'lucide-react';

import { ChatArtifact } from '../../shared/chat';

// Inline, compact, read-only graph visualisation for
// ``graph_search_candidates`` tool results. It shows the recruiter the shape
// of the returned subgraph (Person, Company, School, Skill, Country nodes with
// WORKED_AT / STUDIED_AT / HAS_SKILL / LOCATED_IN edges).

// Cytoscape renders to <canvas> — it does not resolve CSS custom properties.
// Keep these literals in sync with the design tokens in colors_and_type.css /
// :root in index.css (--purple, --ink, --green, --orange, --mute).
const NODE_COLOR = {
  Person: '#B450FF',
  Company: '#15121a',
  School: '#15A36A',
  Skill: '#D88A1C',
  Country: '#8b8595',
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

const STYLE = [
  {
    selector: 'node',
    style: {
      'background-color': (ele) => NODE_COLOR[ele.data('kind')] || '#9E96AE',
      label: 'data(label)',
      color: '#15121a',
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
      'line-color': '#E7E0F0',
      'curve-style': 'bezier',
      'target-arrow-shape': 'triangle',
      'target-arrow-color': '#E7E0F0',
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
    const cy = cytoscape({
      container: containerRef.current,
      elements,
      style: STYLE,
      layout: {
        name: 'cose',
        animate: false,
        padding: 24,
        idealEdgeLength: 70,
        nodeRepulsion: () => 4500,
        gravity: 0.3,
      },
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
    <ChatArtifact
      eyebrow="Relationship graph"
      title="Candidate connections"
      meta={`${graph.nodes.length} entries · ${graph.edges.length} connections`}
      icon={Network}
      flush
    >
      <div className="cp-graph">
      <div className="cp-graph-head">
        <span className="cp-graph-legend">
          <span className="cp-graph-dot" style={{ background: NODE_COLOR.Person }} /> Person
          <span className="cp-graph-dot" style={{ background: NODE_COLOR.Company }} /> Company
          <span className="cp-graph-dot" style={{ background: NODE_COLOR.School }} /> School
          <span className="cp-graph-dot" style={{ background: NODE_COLOR.Skill }} /> Skill
        </span>
      </div>
      <div ref={containerRef} className="cp-graph-canvas" />
      </div>
    </ChatArtifact>
  );
};

export default GraphView;
