import React, { useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';

// Priority chips supported on each requirement line. Stored on the wire
// as plain strings ("Must have: 5+ yrs ...") so the existing CV scorer
// keeps reading the shape it already reads — we just structure them in
// the UI. Used on Settings → AI agent (workspace defaults) and on the
// role-page Agent settings tab (per-role overrides) so the two surfaces
// look and behave identically.
export const REQUIREMENT_PRIORITIES = [
  'Must have',
  'Preferred',
  'Nice to have',
  'Disqualifying',
  'Constraint',
];

export const parseRequirement = (line) => {
  const raw = String(line || '').trim();
  if (!raw) return { priority: 'Must have', text: '' };
  for (const priority of REQUIREMENT_PRIORITIES) {
    const re = new RegExp(`^${priority}\\s*:\\s*(.*)$`, 'i');
    const match = raw.match(re);
    if (match) return { priority, text: match[1].trim() };
  }
  return { priority: 'Must have', text: raw };
};

export const formatRequirement = ({ priority, text }) => {
  const safePriority = REQUIREMENT_PRIORITIES.includes(priority) ? priority : 'Must have';
  const safeText = String(text || '').trim();
  return safeText ? `${safePriority}: ${safeText}` : '';
};

const linesEqual = (a, b) => {
  if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
  for (let i = 0; i < a.length; i += 1) {
    if (a[i] !== b[i]) return false;
  }
  return true;
};

export const RequirementsEditor = ({ value, onChange, ariaLabelPrefix = 'Requirement' }) => {
  // We keep local rows so an empty just-added row doesn't disappear
  // (the wire format filters out blanks). The parent only sees
  // non-empty lines via onChange.
  const externalLines = Array.isArray(value) ? value : [];
  const [rows, setRows] = useState(() => externalLines.map(parseRequirement));
  // When the parent value changes from outside (initial load, save
  // round-trip), reseed local rows — but only if the parent's non-empty
  // list differs from what we'd serialize, so user-edited blank rows
  // aren't yanked while typing.
  const lastEmittedRef = useRef(null);
  useEffect(() => {
    const incoming = externalLines.map((s) => String(s || '').trim()).filter(Boolean);
    if (linesEqual(incoming, lastEmittedRef.current || [])) return;
    setRows(externalLines.map(parseRequirement));
    lastEmittedRef.current = incoming;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(externalLines)]);

  const emit = (nextRows) => {
    const lines = nextRows.map(formatRequirement).filter(Boolean);
    lastEmittedRef.current = lines;
    onChange(lines);
  };

  const updateRow = (index, partial) => {
    const next = rows.slice();
    next[index] = { ...next[index], ...partial };
    setRows(next);
    emit(next);
  };
  const removeRow = (index) => {
    const next = rows.slice();
    next.splice(index, 1);
    setRows(next);
    emit(next);
  };
  const addRow = () => {
    const next = [...rows, { priority: 'Must have', text: '' }];
    setRows(next);
    // Don't emit yet — empty rows are filtered out and the user hasn't
    // typed anything. The new blank row stays visible locally.
  };

  return (
    <div className="req-editor">
      {rows.length === 0 ? (
        <div className="req-editor-empty">No requirements yet. Add one to seed every new role.</div>
      ) : (
        <div className="req-editor-list">
          {rows.map((row, index) => (
            <div key={index} className="req-editor-row">
              <select
                className="req-editor-priority"
                value={row.priority}
                onChange={(event) => updateRow(index, { priority: event.target.value })}
                aria-label={`${ariaLabelPrefix} ${index + 1} priority`}
              >
                {REQUIREMENT_PRIORITIES.map((p) => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
              <input
                type="text"
                className="req-editor-text"
                value={row.text}
                onChange={(event) => updateRow(index, { text: event.target.value })}
                placeholder="e.g. 5+ years production Python or Go"
                aria-label={`${ariaLabelPrefix} ${index + 1} text`}
                maxLength={400}
              />
              <button
                type="button"
                className="req-editor-remove"
                onClick={() => removeRow(index)}
                aria-label={`Remove ${ariaLabelPrefix} ${index + 1}`}
              >
                <X size={14} />
              </button>
            </div>
          ))}
        </div>
      )}
      <button type="button" className="btn btn-outline btn-sm req-editor-add" onClick={addRow}>
        + Add requirement
      </button>
    </div>
  );
};

export default RequirementsEditor;
