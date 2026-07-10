import React, { useState } from 'react';
import { Check, Plus, Sparkles, X } from 'lucide-react';

import { Select } from '../../shared/ui/TaaliPrimitives';

// The live brief panel — rendered FROM the org's requisition spec template.
// Each template section + field is shown with its captured value from the
// brief (a top-level column, or `custom_fields[key]` for keys without a
// column), with a completeness meter on top and gap markers on fields the
// agent still wants. Every field is click-to-edit with an inline editor
// appropriate to its declared type; saving calls `onSave(key, value, isCustom)`.

// Template field keys that map onto real brief columns. Anything NOT in here
// is treated as a custom field and read/written under `custom_fields`.
//
// MUST mirror the backend's requisition_template_service.BRIEF_COLUMN_KEYS
// (the RoleBrief column names) + TEMPLATE_KEY_TO_COLUMN. A template field key
// resolves to a column via `columnFor`; if that column isn't here, the value
// lives in `custom_fields` under the template key.
const COLUMN_KEYS = new Set([
  'title', 'summary', 'department', 'location_city', 'location_country',
  'workplace_type', 'employment_type', 'seniority', 'salary_min', 'salary_max',
  'salary_currency', 'salary_period', 'openings', 'target_start',
  'must_haves', 'preferred', 'dealbreakers', 'success_profile', 'priorities',
  'tradeoffs', 'calibration_exemplars', 'sourcing_signals', 'assessment_focus',
  'process', 'evp',
]);

// A few template field keys differ from their RoleBrief column name.
const TEMPLATE_KEY_TO_COLUMN = { target_start_date: 'target_start' };
const columnFor = (key) => TEMPLATE_KEY_TO_COLUMN[key] || key;

const isCustomKey = (key) => !COLUMN_KEYS.has(columnFor(key));

// Read a field's current value off the brief: a column (by its real column
// name), else custom_fields keyed by the template field key.
const readValue = (brief, key) => {
  if (!brief) return undefined;
  if (isCustomKey(key)) return (brief.custom_fields || {})[key];
  return brief[columnFor(key)];
};

const isEmptyValue = (v) => (
  v == null
  || v === ''
  || (Array.isArray(v) && v.length === 0)
);

// Render a non-editing value as readable text / chips.
function ValueDisplay({ field, value }) {
  if (isEmptyValue(value)) return <span className="rq-field-value is-empty">—</span>;

  if (field.type === 'list') {
    const items = Array.isArray(value) ? value : [value];
    return (
      <div className="rq-chips">
        {items.map((it, i) => (
          <span key={i} className="rq-chip">{typeof it === 'string' ? it : formatStructRow(it)}</span>
        ))}
      </div>
    );
  }

  if (field.type === 'struct_list') {
    const rows = Array.isArray(value) ? value : [];
    return (
      <div className="rq-chips">
        {rows.map((row, i) => (
          <span key={i} className="rq-chip">{formatStructRow(row)}</span>
        ))}
      </div>
    );
  }

  return <span className="rq-field-value">{String(value)}</span>;
}

// "factor (weight)" / "kind: description" / first two values joined.
function formatStructRow(row) {
  if (row == null) return '';
  if (typeof row === 'string') return row;
  if (row.factor != null) return `${row.factor}${row.weight != null ? ` (${row.weight})` : ''}`;
  if (row.kind != null) return `${row.kind}${row.description != null ? `: ${row.description}` : ''}`;
  const vals = Object.values(row).filter((v) => v != null && v !== '');
  return vals.slice(0, 2).join(': ');
}

// struct_list rows come from the agent in one of a few real shapes:
//   Weighted priorities   → { factor, weight }
//   Calibration examples   → { kind, description }
//   generic / hand-added   → { label, detail }
// The inline editor is two columns; this maps a field key + existing rows onto
// the right key PAIR so we read/write the ORIGINAL keys (never blank a
// populated row by reading label/detail off a factor/weight row). Detect by
// the field key first (stable), then by the shape of an existing row, then
// fall back to label/detail.
const STRUCT_SHAPES = {
  priorities: ['factor', 'weight'],
  calibration_exemplars: ['kind', 'description'],
};
const structKeysFor = (fieldKey, rows) => {
  if (STRUCT_SHAPES[fieldKey]) return STRUCT_SHAPES[fieldKey];
  const sample = Array.isArray(rows) ? rows.find((r) => r && typeof r === 'object') : null;
  if (sample) {
    if ('factor' in sample || 'weight' in sample) return ['factor', 'weight'];
    if ('kind' in sample || 'description' in sample) return ['kind', 'description'];
  }
  return ['label', 'detail'];
};

// Inline editor — shape depends on the field type.
function FieldEditor({ field, value, onCancel, onSave, saving }) {
  // For struct_list, resolve the real {factor,weight}/{kind,description}/
  // {label,detail} key pair ONCE from the field key + existing rows, and reuse
  // it for both seeding the editor and writing back — so a populated row never
  // opens blank and Save never wipes data by writing the wrong keys.
  const structKeys = field.type === 'struct_list' ? structKeysFor(field.key, value) : null;
  const [draft, setDraft] = useState(() => seedDraft(field, value, structKeys));
  const [chipInput, setChipInput] = useState('');

  const commit = () => onSave(normalizeDraft(field, draft, structKeys, value));

  if (field.type === 'longtext') {
    return (
      <div className="rq-edit">
        <textarea
          autoFocus
          rows={4}
          value={draft}
          placeholder={field.question || `Add ${field.label.toLowerCase()}…`}
          onChange={(e) => setDraft(e.target.value)}
        />
        <EditActions onCancel={onCancel} onSave={commit} saving={saving} />
      </div>
    );
  }

  if (field.type === 'select') {
    const options = Array.isArray(field.options) ? field.options : [];
    return (
      <div className="rq-edit">
        <Select value={draft} onChange={(e) => setDraft(e.target.value)} aria-label={field.label}>
          <option value="">— Select —</option>
          {options.map((opt) => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </Select>
        <EditActions onCancel={onCancel} onSave={commit} saving={saving} />
      </div>
    );
  }

  if (field.type === 'list') {
    const items = Array.isArray(draft) ? draft : [];
    const addChip = () => {
      const v = chipInput.trim();
      if (!v) return;
      setDraft([...items, v]);
      setChipInput('');
    };
    return (
      <div className="rq-edit">
        <div className="rq-chips">
          {items.map((it, i) => (
            <span key={i} className="rq-chip">
              {it}
              <button type="button" className="rq-chip-x" aria-label="Remove" onClick={() => setDraft(items.filter((_, j) => j !== i))}>
                <X size={11} />
              </button>
            </span>
          ))}
        </div>
        <div className="rq-chip-add">
          <input
            autoFocus
            value={chipInput}
            placeholder="Add an item, press Enter"
            onChange={(e) => setChipInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addChip(); } }}
          />
          <button type="button" className="rq-btn-sm is-ghost" onClick={addChip}><Plus size={13} /></button>
        </div>
        <EditActions onCancel={onCancel} onSave={commit} saving={saving} />
      </div>
    );
  }

  if (field.type === 'struct_list') {
    // draft rows are internal { col1, col2 }; the real keys are re-applied in
    // normalizeDraft. Column placeholders are labelled from the real key pair
    // so the recruiter sees "Factor / Weight" vs "Kind / Description".
    const rows = Array.isArray(draft) ? draft : [];
    const [k1, k2] = structKeys || ['label', 'detail'];
    const ph1 = k1.charAt(0).toUpperCase() + k1.slice(1);
    const ph2 = k2.charAt(0).toUpperCase() + k2.slice(1);
    const setRow = (i, patch) => setDraft(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
    return (
      <div className="rq-edit">
        <div className="rq-struct-rows">
          {rows.map((row, i) => (
            <div key={i} className="rq-struct-row">
              <input
                value={row.col1 ?? ''}
                placeholder={ph1}
                onChange={(e) => setRow(i, { col1: e.target.value })}
              />
              <input
                value={row.col2 ?? ''}
                placeholder={ph2}
                onChange={(e) => setRow(i, { col2: e.target.value })}
              />
              <button type="button" className="rq-chip-x" aria-label="Remove row" onClick={() => setDraft(rows.filter((_, j) => j !== i))}>
                <X size={13} />
              </button>
            </div>
          ))}
        </div>
        <button type="button" className="rq-btn-sm is-ghost" onClick={() => setDraft([...rows, { col1: '', col2: '' }])}>
          <Plus size={13} /> Add row
        </button>
        <EditActions onCancel={onCancel} onSave={commit} saving={saving} />
      </div>
    );
  }

  // text / number / date
  return (
    <div className="rq-edit">
      <input
        autoFocus
        type={field.type === 'number' ? 'number' : field.type === 'date' ? 'date' : 'text'}
        value={draft}
        placeholder={field.question || `Add ${field.label.toLowerCase()}…`}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); commit(); } }}
      />
      <EditActions onCancel={onCancel} onSave={commit} saving={saving} />
    </div>
  );
}

function EditActions({ onCancel, onSave, saving }) {
  return (
    <div className="rq-edit-actions">
      <button type="button" className="rq-btn-sm is-primary" onClick={onSave} disabled={saving}>
        {saving ? <span className="rq-spinner" /> : <Check size={13} />} Save
      </button>
      <button type="button" className="rq-btn-sm is-ghost" onClick={onCancel} disabled={saving}>Cancel</button>
    </div>
  );
}

function seedDraft(field, value, structKeys) {
  if (field.type === 'list') return Array.isArray(value) ? [...value] : [];
  if (field.type === 'struct_list') {
    const [k1, k2] = structKeys || ['label', 'detail'];
    // Map each real row onto the editor's internal { col1, col2 } off the
    // resolved key pair — so a { factor, weight } / { kind, description } row
    // shows its populated values instead of opening blank.
    return Array.isArray(value)
      ? value.map((r) => (typeof r === 'string'
        ? { col1: r, col2: '' }
        : { col1: r?.[k1] == null ? '' : String(r[k1]), col2: r?.[k2] == null ? '' : String(r[k2]) }))
      : [];
  }
  return value == null ? '' : String(value);
}

function normalizeDraft(field, draft, structKeys, existingValue) {
  if (field.type === 'list') return (Array.isArray(draft) ? draft : []).filter((s) => String(s).trim());
  if (field.type === 'struct_list') {
    const [k1, k2] = structKeys || ['label', 'detail'];
    const rows = (Array.isArray(draft) ? draft : [])
      .map((r) => ({ [k1]: String(r.col1 || '').trim(), [k2]: String(r.col2 || '').trim() }))
      .filter((r) => r[k1] || r[k2]);
    // Guard: never save an all-empty draft over data that was already there —
    // that would silently wipe captured rows. Refuse the save (signalled by
    // returning undefined so the caller keeps the existing value + editor).
    if (rows.length === 0 && Array.isArray(existingValue) && existingValue.length > 0) {
      return undefined;
    }
    return rows;
  }
  if (field.type === 'number') {
    const n = Number(draft);
    return draft === '' || Number.isNaN(n) ? null : n;
  }
  const s = String(draft ?? '').trim();
  return s === '' ? null : s;
}

function Field({ field, brief, isGap, editing, onEdit, onCancel, onSave, saving, readOnly }) {
  const value = readValue(brief, field.key);
  // Applied briefs are frozen — show values as plain text, no click-to-edit.
  if (readOnly) {
    return (
      <div className="rq-field">
        <div className="rq-field-head">
          <span className="rq-field-label">{field.label}</span>
        </div>
        {field.type === 'list' || field.type === 'struct_list' ? (
          <div className="rq-field-value"><ValueDisplay field={field} value={value} /></div>
        ) : (
          <div className={`rq-field-value${isEmptyValue(value) ? ' is-empty' : ''}`}>
            {isEmptyValue(value) ? '—' : String(value)}
          </div>
        )}
      </div>
    );
  }
  return (
    <div className="rq-field">
      <div className="rq-field-head">
        <span className="rq-field-label">{field.label}</span>
        {isGap ? (
          <span className="rq-field-needed"><Sparkles size={9} /> needed</span>
        ) : null}
      </div>
      {editing ? (
        <FieldEditor
          field={field}
          value={value}
          onCancel={onCancel}
          onSave={(v) => onSave(field, v)}
          saving={saving}
        />
      ) : field.type === 'list' || field.type === 'struct_list' ? (
        <button type="button" className="rq-field-value" onClick={onEdit} style={{ cursor: 'pointer' }}>
          <ValueDisplay field={field} value={value} />
        </button>
      ) : (
        <button
          type="button"
          className={`rq-field-value${isEmptyValue(value) ? ' is-empty' : ''}`}
          onClick={onEdit}
        >
          {isEmptyValue(value) ? '—' : String(value)}
        </button>
      )}
    </div>
  );
}

export function LiveBrief({ template, brief, onSave, savingKey, readOnly = false }) {
  const [editingKey, setEditingKey] = useState(null);
  const sections = Array.isArray(template?.sections) ? template.sections : [];
  const completeness = Math.max(0, Math.min(100, Number(brief?.completeness) || 0));
  const gaps = Array.isArray(brief?.gaps) ? brief.gaps : [];
  const gapKeys = new Set(gaps.map((g) => g.key));

  const handleSave = async (field, value) => {
    // normalizeDraft returns undefined to REFUSE a save that would wipe
    // populated struct_list data with an all-empty draft — keep the editor open
    // and the existing value untouched.
    if (value === undefined) return;
    const custom = isCustomKey(field.key);
    // Column fields PATCH under their real column name (e.g. target_start_date
    // → target_start); custom fields PATCH under their template key.
    await onSave(custom ? field.key : columnFor(field.key), value, custom);
    setEditingKey(null);
  };

  return (
    <div className="rq-brief">
      <div className="rq-brief-scroll">
        <div className="rq-meter">
          <div className="rq-meter-top">
            <span className="rq-meter-label">Brief completeness</span>
            <span className="rq-meter-pct">{completeness}%</span>
          </div>
          <div
            className="rq-meter-track"
            role="progressbar"
            aria-label="Brief completeness"
            aria-valuenow={completeness}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div className="rq-meter-fill" style={{ width: `${completeness}%` }} />
          </div>
          {gaps.length > 0 ? (
            <div className="rq-gaps-line">
              <strong>{gaps.length}</strong> field{gaps.length === 1 ? '' : 's'} still needed —
              {' '}the agent will ask, or click any field to fill it in.
            </div>
          ) : (
            <div className="rq-gaps-line">All template fields captured.</div>
          )}
        </div>

        {sections.length === 0 ? (
          <div className="rq-side-empty">No template configured. Set one up in Settings → Requisition template.</div>
        ) : (
          sections.map((section) => (
            <div key={section.key} className="rq-section">
              <h3 className="rq-section-label">{section.label}</h3>
              {(Array.isArray(section.fields) ? section.fields : []).map((field) => (
                <Field
                  key={field.key}
                  field={field}
                  brief={brief}
                  isGap={gapKeys.has(field.key)}
                  editing={editingKey === field.key}
                  onEdit={() => setEditingKey(field.key)}
                  onCancel={() => setEditingKey(null)}
                  onSave={handleSave}
                  saving={savingKey === field.key}
                  readOnly={readOnly}
                />
              ))}
            </div>
          ))
        )}
      </div>
    </div>
  );
}

export default LiveBrief;
