import React, { useEffect, useMemo, useRef } from 'react';
import cytoscape from 'cytoscape';

// Inline, compact graph visualisation for ``graph_search_candidates``
// tool results. Smaller and read-only compared to
// ``features/candidates/CandidateGraphView`` — no side panel, no
// drill-in, just enough to show the recruiter the shape of the
// returned subgraph (Person, Company, School, Skill, Country nodes
// with WORKED_AT / STUDIED_AT / HAS_SKILL / LOCATED_IN edges).

const NODE_COLOR = {
  Person: '#7F39FB',
  Company: '#1D1730',
  School: '#15A36A',
  Skill: '#D88A1C',
  Country: '#6E6580',
};

const buildElements = (graph) => {
  const elements = [];
  if (!graph) return elements;
  for (const n of graph.nodes || []) {
    elements.push({
      data: { id: n.id, label: n.name || n.label, kind: n.label },
    });
  }
  // Use a stable id for every edge so cytoscape doesn't drop dupes.
  const seen = new Set();
  for (const e of graph.edges || []) {
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
      color: '#1D1730',
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
        <span>{graph.nodes.length} nodes · {graph.edges.length} edges</span>
        <span className="cp-graph-legend">
          <span className="cp-graph-dot" style={{ background: NODE_COLOR.Person }} /> Person
          <span className="cp-graph-dot" style={{ background: NODE_COLOR.Company }} /> Company
          <span className="cp-graph-dot" style={{ background: NODE_COLOR.School }} /> School
          <span className="cp-graph-dot" style={{ background: NODE_COLOR.Skill }} /> Skill
        </span>
      </div>
      <div ref={containerRef} className="cp-graph-canvas" />
    </div>
  );
};

export default GraphView;
