// Settings → Requisition template editor.
//
// The org's canonical definition of a COMPLETE requisition spec — comp,
// location, logistics, requirements, agent-context, not just a JD. This
// template drives BOTH the live brief on the Requisitions page AND the fields
// the intake agent tries to fill. Recruiters edit sections + fields here; the
// backend hands back a sensible DEFAULT when none is set.
//
// Matches the recruiter-page chrome (light AgentHeader + mc-page) and the
// global form/btn/chip classes + the rq-* styles, so it reads like the rest
// of Settings. Purple accents only.
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowDown, ArrowUp, Plus, Save, Trash2, X } from 'lucide-react';

import { useToast } from '../../context/ToastContext';
import { AgentHeader } from '../../shared/layout/AgentHeader';
import { Spinner } from '../../shared/ui/TaaliPrimitives';
import { requisitionApi } from '../requisitions/api';
import '../requisitions/requisitions.css';
import './requisition-template.css';

const FIELD_TYPES = [
  { value: 'text', label: 'Text' },
  { value: 'longtext', label: 'Long text' },
  { value: 'number', label: 'Number' },
  { value: 'date', label: 'Date' },
  { value: 'select', label: 'Select (one of)' },
  { value: 'list', label: 'List' },
  { value: 'struct_list', label: 'Structured list' },
];

// Slugify a label into a stable-ish key when the recruiter hasn't typed one.
const slugify = (s) => String(s || '')
  .toLowerCase()
  .replace(/[^a-z0-9]+/g, '_')
  .replace(/^_+|_+$/g, '')
  .slice(0, 48);

let uidSeq = 0;
const uid = (prefix) => `${prefix}_${Date.now()}_${uidSeq++}`;

const blankField = () => ({
  key: '',
  label: '',
  type: 'text',
  required: false,
  question: '',
  options: [],
});

const blankSection = () => ({
  key: '',
  label: '',
  fields: [blankField()],
});

// Move an item within an array by index (up = -1, down = +1).
const moveItem = (arr, index, delta) => {
  const next = [...arr];
  const target = index + delta;
  if (target < 0 || target >= next.length) return arr;
  [next[index], next[target]] = [next[target], next[index]];
  return next;
};

function OptionsEditor({ options, onChange }) {
  const [draft, setDraft] = useState('');
  const list = Array.isArray(options) ? options : [];
  const add = () => {
    const v = draft.trim();
    if (!v || list.includes(v)) { setDraft(''); return; }
    onChange([...list, v]);
    setDraft('');
  };
  return (
    <div className="rqt-options">
      <div className="rq-chips">
        {list.map((opt, i) => (
          <span key={i} className="rq-chip">
            {opt}
            <button type="button" className="rq-chip-x" aria-label={`Remove ${opt}`} onClick={() => onChange(list.filter((_, j) => j !== i))}>
              <X size={11} />
            </button>
          </span>
        ))}
        {list.length === 0 ? <span className="rqt-muted">No options yet.</span> : null}
      </div>
      <div className="rq-chip-add">
        <input
          value={draft}
          placeholder="Add an option, press Enter"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add(); } }}
        />
        <button type="button" className="rq-btn-sm is-ghost" onClick={add}><Plus size={13} /></button>
      </div>
    </div>
  );
}

function FieldRow({ field, index, count, onPatch, onMove, onRemove }) {
  return (
    <div className="rqt-field">
      <div className="rqt-field-grid">
        <label className="field">
          <span className="k">Label</span>
          <input
            value={field.label}
            placeholder="Salary range"
            onChange={(e) => {
              const label = e.target.value;
              // Auto-fill the key from the label until the user edits the key.
              onPatch(field._keyTouched ? { label } : { label, key: slugify(label) });
            }}
          />
        </label>
        <label className="field">
          <span className="k">Key</span>
          <input
            value={field.key}
            placeholder="salary_range"
            onChange={(e) => onPatch({ key: slugify(e.target.value), _keyTouched: true })}
          />
        </label>
        <label className="field">
          <span className="k">Type</span>
          <select value={field.type} onChange={(e) => onPatch({ type: e.target.value })}>
            {FIELD_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
        </label>
        <div className="rqt-field-controls">
          <label className="rqt-req">
            <input
              type="checkbox"
              checked={Boolean(field.required)}
              onChange={(e) => onPatch({ required: e.target.checked })}
            />
            Required
          </label>
          <div className="rqt-row-btns">
            <button type="button" className="rqt-icon-btn" aria-label="Move field up" disabled={index === 0} onClick={() => onMove(-1)}><ArrowUp size={14} /></button>
            <button type="button" className="rqt-icon-btn" aria-label="Move field down" disabled={index === count - 1} onClick={() => onMove(1)}><ArrowDown size={14} /></button>
            <button type="button" className="rqt-icon-btn is-danger" aria-label="Remove field" onClick={onRemove}><Trash2 size={14} /></button>
          </div>
        </div>
      </div>

      <label className="field rqt-full">
        <span className="k">Agent question</span>
        <input
          value={field.question}
          placeholder="What's the budgeted salary range for this role?"
          onChange={(e) => onPatch({ question: e.target.value })}
        />
      </label>

      {field.type === 'select' ? (
        <label className="field rqt-full">
          <span className="k">Options</span>
          <OptionsEditor options={field.options} onChange={(options) => onPatch({ options })} />
        </label>
      ) : null}
    </div>
  );
}

function SectionCard({ section, index, count, onPatch, onMove, onRemove, onAddField, onPatchField, onMoveField, onRemoveField }) {
  const fields = Array.isArray(section.fields) ? section.fields : [];
  return (
    <div className="rqt-section">
      <div className="rqt-section-head">
        <div className="rqt-section-head-fields">
          <label className="field">
            <span className="k">Section label</span>
            <input
              value={section.label}
              placeholder="Compensation"
              onChange={(e) => {
                const label = e.target.value;
                onPatch(section._keyTouched ? { label } : { label, key: slugify(label) });
              }}
            />
          </label>
          <label className="field">
            <span className="k">Section key</span>
            <input
              value={section.key}
              placeholder="compensation"
              onChange={(e) => onPatch({ key: slugify(e.target.value), _keyTouched: true })}
            />
          </label>
        </div>
        <div className="rqt-row-btns">
          <button type="button" className="rqt-icon-btn" aria-label="Move section up" disabled={index === 0} onClick={() => onMove(-1)}><ArrowUp size={15} /></button>
          <button type="button" className="rqt-icon-btn" aria-label="Move section down" disabled={index === count - 1} onClick={() => onMove(1)}><ArrowDown size={15} /></button>
          <button type="button" className="rqt-icon-btn is-danger" aria-label="Remove section" onClick={onRemove}><Trash2 size={15} /></button>
        </div>
      </div>

      <div className="rqt-fields">
        {fields.map((field, fi) => (
          <FieldRow
            key={field._id}
            field={field}
            index={fi}
            count={fields.length}
            onPatch={(patch) => onPatchField(fi, patch)}
            onMove={(delta) => onMoveField(fi, delta)}
            onRemove={() => onRemoveField(fi)}
          />
        ))}
      </div>

      <button type="button" className="rq-btn-sm is-ghost rqt-add-field" onClick={onAddField}>
        <Plus size={13} /> Add field
      </button>
    </div>
  );
}

export const RequisitionTemplatePage = ({ onNavigate, NavComponent = null }) => {
  const { showToast } = useToast();
  const [sections, setSections] = useState([]);
  const [version, setVersion] = useState(1);
  const [jdTemplate, setJdTemplate] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  // Attach stable client ids so React keys survive reorders/edits, and seed
  // the *Touched flags so auto-keying only applies to genuinely new rows.
  const hydrate = useCallback((template) => {
    const tpl = template || {};
    setVersion(Number(tpl.version) || 1);
    setJdTemplate(typeof tpl.jd_template === 'string' ? tpl.jd_template : '');
    setSections(
      (Array.isArray(tpl.sections) ? tpl.sections : []).map((s) => ({
        _id: uid('sec'),
        _keyTouched: Boolean(s.key),
        key: s.key || '',
        label: s.label || '',
        fields: (Array.isArray(s.fields) ? s.fields : []).map((f) => ({
          _id: uid('fld'),
          _keyTouched: Boolean(f.key),
          key: f.key || '',
          label: f.label || '',
          type: FIELD_TYPES.some((t) => t.value === f.type) ? f.type : 'text',
          required: Boolean(f.required),
          question: f.question || '',
          options: Array.isArray(f.options) ? f.options : [],
        })),
      }))
    );
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await requisitionApi.getTemplate();
      hydrate(res?.template || { version: 1, sections: [] });
    } catch {
      showToast('Could not load the requisition template.', 'error');
      hydrate({ version: 1, sections: [] });
    } finally {
      setLoading(false);
    }
  }, [hydrate, showToast]);

  useEffect(() => { void load(); }, [load]);

  // ---- section ops ----
  const patchSection = (si, patch) => setSections((prev) => prev.map((s, i) => (i === si ? { ...s, ...patch } : s)));
  const moveSection = (si, delta) => setSections((prev) => moveItem(prev, si, delta));
  const removeSection = (si) => setSections((prev) => prev.filter((_, i) => i !== si));
  const addSection = () => setSections((prev) => [...prev, { _id: uid('sec'), _keyTouched: false, ...blankSection(), fields: [{ _id: uid('fld'), _keyTouched: false, ...blankField() }] }]);

  // ---- field ops ----
  const patchField = (si, fi, patch) => setSections((prev) => prev.map((s, i) => (
    i === si ? { ...s, fields: s.fields.map((f, j) => (j === fi ? { ...f, ...patch } : f)) } : s
  )));
  const moveField = (si, fi, delta) => setSections((prev) => prev.map((s, i) => (
    i === si ? { ...s, fields: moveItem(s.fields, fi, delta) } : s
  )));
  const removeField = (si, fi) => setSections((prev) => prev.map((s, i) => (
    i === si ? { ...s, fields: s.fields.filter((_, j) => j !== fi) } : s
  )));
  const addField = (si) => setSections((prev) => prev.map((s, i) => (
    i === si ? { ...s, fields: [...s.fields, { _id: uid('fld'), _keyTouched: false, ...blankField() }] } : s
  )));

  // Strip client-only fields and validate before save. NOTE: jd_template must
  // be sent alongside version + sections — the backend stores the whole
  // template object, so omitting it here would WIPE the org's JD template.
  const serialize = useMemo(() => () => ({
    version,
    jd_template: jdTemplate,
    sections: sections.map((s) => ({
      key: s.key || slugify(s.label),
      label: s.label,
      fields: (s.fields || []).map((f) => {
        const out = {
          key: f.key || slugify(f.label),
          label: f.label,
          type: f.type,
          required: Boolean(f.required),
          question: f.question || '',
        };
        if (f.type === 'select') out.options = Array.isArray(f.options) ? f.options : [];
        return out;
      }),
    })),
  }), [sections, version, jdTemplate]);

  const validate = (template) => {
    for (const s of template.sections) {
      if (!s.label.trim() || !s.key.trim()) return 'Every section needs a label and a key.';
      for (const f of s.fields) {
        if (!f.label.trim() || !f.key.trim()) return `Every field in "${s.label}" needs a label and a key.`;
        if (f.type === 'select' && (!f.options || f.options.length === 0)) {
          return `Select field "${f.label}" needs at least one option.`;
        }
      }
    }
    const keys = template.sections.flatMap((s) => s.fields.map((f) => f.key));
    if (new Set(keys).size !== keys.length) return 'Field keys must be unique across the template.';
    return null;
  };

  const save = async () => {
    const template = serialize();
    const err = validate(template);
    if (err) { showToast(err, 'error'); return; }
    setSaving(true);
    try {
      const res = await requisitionApi.saveTemplate(template);
      hydrate(res?.template || template);
      showToast('Requisition template saved.', 'success');
    } catch {
      showToast('Failed to save the requisition template.', 'error');
    } finally {
      setSaving(false);
    }
  };

  const totalFields = sections.reduce((n, s) => n + (s.fields?.length || 0), 0);

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="settings" onNavigate={onNavigate} /> : null}
      <AgentHeader
        breadcrumbs={[
          { label: 'Settings', page: 'settings' },
          { label: 'Requisition template' },
        ]}
        kicker="SETTINGS · REQUISITIONS"
        title="Requisition template"
        subtitle="Define what a complete requisition looks like — comp, location, logistics, requirements, agent context. This drives the live brief and the questions the intake agent asks."
        actions={(
          <button type="button" className="rq-publish-btn" onClick={save} disabled={saving || loading}>
            {saving ? <span className="rq-spinner" /> : <Save size={15} />} Save template
          </button>
        )}
      />
      <div className="mc-page mc-page-narrow">
        {loading ? (
          <div className="flex min-h-[16.25rem] items-center justify-center"><Spinner size={32} /></div>
        ) : (
          <>
            <div className="rqt-summary">
              {sections.length} section{sections.length === 1 ? '' : 's'} · {totalFields} field{totalFields === 1 ? '' : 's'}
            </div>

            {/* Job spec (JD) template — markdown with {{placeholder}} tokens,
                filled live from the captured brief on the Requisitions page. */}
            <div className="rqt-section rqt-jd">
              <div className="rqt-jd-head">
                <h2 className="rqt-jd-title">Job spec template</h2>
                <p className="rqt-jd-sub">
                  The job-description document shown live on the Requisitions page. Write it in markdown;
                  the agent fills <code>{'{{placeholder}}'}</code> tokens from the captured brief as it goes.
                </p>
              </div>
              <label className="field rqt-full">
                <span className="k">Template (markdown)</span>
                <textarea
                  className="rqt-jd-textarea"
                  rows={16}
                  value={jdTemplate}
                  placeholder={'# {{title}}\n\n{{summary}}\n\n## What you\'ll need\n{{must_haves}}'}
                  onChange={(e) => setJdTemplate(e.target.value)}
                  spellCheck={false}
                />
              </label>
              <p className="rqt-jd-placeholders">
                <strong>Placeholders:</strong>{' '}
                {'{{title}}'}, {'{{summary}}'}, {'{{location}}'}, {'{{workplace_type}}'}, {'{{employment_type}}'},
                {' '}{'{{seniority}}'}, {'{{openings}}'}, {'{{salary}}'}, {'{{urgency}}'}, {'{{must_haves}}'},
                {' '}{'{{preferred}}'}, {'{{dealbreakers}}'}, {'{{success_profile}}'}, {'{{assessment_focus}}'},
                {' '}{'{{evp}}'}
              </p>
            </div>

            {sections.map((section, si) => (
              <SectionCard
                key={section._id}
                section={section}
                index={si}
                count={sections.length}
                onPatch={(patch) => patchSection(si, patch)}
                onMove={(delta) => moveSection(si, delta)}
                onRemove={() => removeSection(si)}
                onAddField={() => addField(si)}
                onPatchField={(fi, patch) => patchField(si, fi, patch)}
                onMoveField={(fi, delta) => moveField(si, fi, delta)}
                onRemoveField={(fi) => removeField(si, fi)}
              />
            ))}

            <button type="button" className="rqt-add-section" onClick={addSection}>
              <Plus size={16} /> Add section
            </button>

            <div className="rqt-footer">
              <button type="button" className="rq-publish-btn" onClick={save} disabled={saving}>
                {saving ? <span className="rq-spinner" /> : <Save size={15} />} Save template
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
};

export default RequisitionTemplatePage;
