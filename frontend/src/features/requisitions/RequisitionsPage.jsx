// AI-native Requisition — recruiter page.
//
// VERIFICATION STATUS: written from the codebase's api.js + React conventions but
// NOT yet run through the FE toolchain or routed into the app shell. Wire it into
// the page renderer + run the dev server / vitest before the PR merges (it must
// not ship to prod unverified). Styling is intentionally minimal — align to the
// design system during the verification pass.
import React, { useEffect, useState } from 'react';

import { requisitionApi } from './api';

const LAYER_LISTS = [
  ['must_haves', 'Must-haves'],
  ['preferred', 'Preferred'],
  ['dealbreakers', 'Dealbreakers'],
  ['tradeoffs', 'Trade-offs'],
  ['assessment_focus', 'What to assess'],
  ['evp', 'Selling points'],
];

function List({ items }) {
  if (!items || !items.length) return <span className="req-muted">—</span>;
  return (
    <ul className="req-list">
      {items.map((it, i) => (
        <li key={i}>{typeof it === 'string' ? it : JSON.stringify(it)}</li>
      ))}
    </ul>
  );
}

export default function RequisitionsPage() {
  const [briefs, setBriefs] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [brief, setBrief] = useState(null);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const loadList = async () => {
    try {
      setBriefs(await requisitionApi.list());
    } catch (e) {
      setError('Could not load requisitions.');
    }
  };

  useEffect(() => {
    loadList();
  }, []);

  const select = async (id) => {
    setSelectedId(id);
    setError('');
    try {
      setBrief(await requisitionApi.get(id));
    } catch (e) {
      setError('Could not load this requisition.');
    }
  };

  const createReq = async () => {
    setBusy(true);
    setError('');
    try {
      const created = await requisitionApi.create();
      await loadList();
      await select(created.id);
      setInput('');
    } catch (e) {
      setError('Could not create a requisition.');
    } finally {
      setBusy(false);
    }
  };

  const runIntake = async () => {
    if (!selectedId || !input.trim()) return;
    setBusy(true);
    setError('');
    try {
      const updated = await requisitionApi.runIntake(selectedId, input.trim());
      setBrief(updated);
    } catch (e) {
      setError('Intake failed — the agent could not extract a brief. Try again.');
    } finally {
      setBusy(false);
    }
  };

  const publish = async () => {
    if (!selectedId) return;
    setBusy(true);
    setError('');
    try {
      await requisitionApi.publish(selectedId);
      await select(selectedId);
    } catch (e) {
      setError('Publish failed.');
    } finally {
      setBusy(false);
    }
  };

  const openQuestions = (brief && brief.agent_state && brief.agent_state.open_questions) || [];

  return (
    <div className="req-page" style={{ display: 'flex', gap: 20, padding: 20 }}>
      <aside style={{ width: 260, flex: '0 0 auto' }}>
        <button className="req-btn" onClick={createReq} disabled={busy}>
          + New requisition
        </button>
        <ul className="req-sidelist" style={{ listStyle: 'none', padding: 0, marginTop: 12 }}>
          {briefs.map((b) => (
            <li key={b.id}>
              <button
                onClick={() => select(b.id)}
                style={{
                  display: 'block', width: '100%', textAlign: 'left', padding: '8px 10px',
                  borderRadius: 8, border: '1px solid #e7e9ee', marginBottom: 6,
                  background: b.id === selectedId ? '#ede9fe' : 'transparent', cursor: 'pointer',
                }}
              >
                <strong>{b.title || 'Untitled requisition'}</strong>
                <div className="req-muted" style={{ fontSize: 12 }}>
                  {b.status} · {b.completeness != null ? `${b.completeness}% complete` : 'not started'}
                </div>
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <main style={{ flex: 1, minWidth: 0 }}>
        {error && <div className="req-error" style={{ color: '#b91c1c', marginBottom: 12 }}>{error}</div>}
        {!brief && <div className="req-muted">Create a requisition, then paste a hiring-manager transcript or notes and let the agent draft the brief.</div>}

        {brief && (
          <div>
            <h2 style={{ marginTop: 0 }}>{brief.title || 'Untitled requisition'}</h2>
            <div className="req-muted" style={{ marginBottom: 12 }}>
              {brief.status}
              {brief.completeness != null ? ` · ${brief.completeness}% complete` : ''}
            </div>

            <section style={{ marginBottom: 16 }}>
              <label style={{ fontWeight: 600 }}>Hiring-manager input (transcript / notes / JD)</label>
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                rows={5}
                placeholder="Paste the kickoff-call transcript or your notes…"
                style={{ width: '100%', marginTop: 6, padding: 10, borderRadius: 8, border: '1px solid #e7e9ee' }}
              />
              <div style={{ marginTop: 8, display: 'flex', gap: 8 }}>
                <button className="req-btn" onClick={runIntake} disabled={busy || !input.trim()}>
                  {busy ? 'Working…' : 'Run intake (agent fills the brief)'}
                </button>
                <button className="req-btn" onClick={publish} disabled={busy}>
                  Publish → role
                </button>
              </div>
            </section>

            {openQuestions.length > 0 && (
              <section style={{ marginBottom: 16, padding: 12, background: '#fff7ed', borderRadius: 8 }}>
                <strong>The agent still wants to know:</strong>
                <List items={openQuestions} />
              </section>
            )}

            <section className="req-grid" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
              <Field label="Summary" value={brief.summary} />
              <Field label="Department" value={brief.department} />
              <Field label="Location" value={[brief.location_city, brief.location_country].filter(Boolean).join(', ')} />
              <Field label="Workplace" value={brief.workplace_type} />
              <Field label="Employment" value={brief.employment_type} />
              <Field label="Seniority" value={brief.seniority} />
              <Field label="Openings" value={brief.openings} />
              <Field label="Salary" value={salary(brief)} />
              <Field label="Success profile" value={brief.success_profile} wide />
              {LAYER_LISTS.map(([key, label]) => (
                <Block key={key} label={label}><List items={brief[key]} /></Block>
              ))}
              <Block label="Priorities"><List items={(brief.priorities || []).map((p) => `${p.factor}${p.weight ? ` (${p.weight})` : ''}`)} /></Block>
              <Block label="Calibration"><List items={(brief.calibration_exemplars || []).map((e) => `${e.kind}: ${e.description}`)} /></Block>
            </section>
          </div>
        )}
      </main>
    </div>
  );
}

function salary(b) {
  if (!b.salary_min && !b.salary_max) return null;
  const cur = b.salary_currency || '';
  return `${cur} ${b.salary_min || ''}${b.salary_max ? `–${b.salary_max}` : ''} ${b.salary_period || ''}`.trim();
}

function Field({ label, value, wide }) {
  return (
    <div style={wide ? { gridColumn: '1 / -1' } : undefined}>
      <div className="req-muted" style={{ fontSize: 12, fontWeight: 600 }}>{label}</div>
      <div>{value || <span className="req-muted">—</span>}</div>
    </div>
  );
}

function Block({ label, children }) {
  return (
    <div style={{ gridColumn: '1 / -1' }}>
      <div className="req-muted" style={{ fontSize: 12, fontWeight: 600 }}>{label}</div>
      {children}
    </div>
  );
}
