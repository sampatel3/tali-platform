/* eslint-disable react/prop-types */
import { useMemo, useState } from 'react';

import './CriteriaEditor.css';

const BUCKETS = [
  { key: 'must', label: 'Must', columnLabel: 'Must-have' },
  { key: 'preferred', label: 'Preferred', columnLabel: 'Preferred' },
  { key: 'constraint', label: 'Constraint', columnLabel: 'Constraint' },
];

const isActive = (chip) => !chip.deleted_at;

const Composer = ({ onAdd, disabled }) => {
  const [text, setText] = useState('');
  const [bucket, setBucket] = useState('must');

  const submit = () => {
    const trimmed = text.trim();
    if (!trimmed) return;
    onAdd({ text: trimmed, bucket });
    setText('');
  };

  return (
    <div className="ce-composer">
      <div className="ce-composer-bucket-segment" role="radiogroup" aria-label="Bucket">
        {BUCKETS.map((b) => {
          const active = bucket === b.key;
          return (
            <button
              key={b.key}
              type="button"
              role="radio"
              aria-checked={active}
              className={`ce-bucket-pill ce-bucket-pill--${b.key}${active ? ' is-active' : ''}`}
              onClick={() => setBucket(b.key)}
              disabled={disabled}
            >
              <span className={`ce-bucket-option-swatch ce-bucket-option-swatch--${b.key}`} aria-hidden />
              {b.label}
            </button>
          );
        })}
      </div>
      <input
        type="text"
        className="ce-composer-input"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            submit();
          }
        }}
        placeholder="Add a criterion (e.g. 5+ years backend)"
        disabled={disabled}
        aria-label="Criterion text"
      />
      <button
        type="button"
        className="ce-composer-add"
        onClick={submit}
        disabled={disabled || !text.trim()}
      >
        Add
      </button>
    </div>
  );
};

const ChipRow = ({
  chip,
  mode,
  onEdit,
  onDelete,
  busy,
}) => {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(chip.text);

  const isWorkspaceDerived = mode === 'role' && chip.org_criterion_id != null;
  const isCustomized = isWorkspaceDerived && chip.customized_at != null;
  const sourceClass = mode === 'role'
    ? (isCustomized ? 'edited' : isWorkspaceDerived ? 'workspace' : 'role')
    : null;
  const sourceTitle = mode === 'role'
    ? (isCustomized
      ? 'From workspace · edited on this role'
      : isWorkspaceDerived ? 'From workspace' : 'Added on this role')
    : null;

  const startEdit = () => {
    setDraft(chip.text);
    setEditing(true);
  };
  const commit = () => {
    const trimmed = draft.trim();
    if (!trimmed || trimmed === chip.text) {
      setEditing(false);
      return;
    }
    onEdit(chip.id, { text: trimmed });
    setEditing(false);
  };

  return (
    <li className={`ce-chip ce-chip--${chip.bucket}`} data-source={sourceClass || undefined}>
      {sourceClass ? (
        <span className={`ce-source-dot ce-source-dot--${sourceClass}`} title={sourceTitle} aria-label={sourceTitle} />
      ) : null}
      {editing ? (
        <input
          type="text"
          className="ce-chip-edit"
          value={draft}
          autoFocus
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              commit();
            } else if (e.key === 'Escape') {
              setEditing(false);
            }
          }}
        />
      ) : (
        <button
          type="button"
          className="ce-chip-text"
          onClick={startEdit}
          disabled={busy}
          title="Click to edit"
        >
          {chip.text}
        </button>
      )}
      <button
        type="button"
        className="ce-chip-delete"
        onClick={() => onDelete(chip.id)}
        disabled={busy}
        aria-label="Remove"
      >
        ×
      </button>
    </li>
  );
};

const Column = ({
  bucket,
  chips,
  mode,
  onEdit,
  onDelete,
  busy,
}) => (
  <div className={`ce-col ce-col--${bucket.key}`}>
    <div className="ce-col-head">
      <span className="ce-col-label">
        <span className={`ce-col-swatch ce-col-swatch--${bucket.key}`} />
        {bucket.columnLabel}
      </span>
      <span className="ce-col-count">{chips.length}</span>
    </div>
    {chips.length ? (
      <ul className="ce-col-list">
        {chips.map((c) => (
          <ChipRow
            key={c.id}
            chip={c}
            mode={mode}
            onEdit={onEdit}
            onDelete={onDelete}
            busy={busy}
          />
        ))}
      </ul>
    ) : (
      <div className="ce-col-empty">Nothing yet</div>
    )}
  </div>
);

const HiddenSection = ({ workspaceCriteria, suppressedIds, onRestore, busy }) => {
  const [open, setOpen] = useState(false);
  const hidden = (workspaceCriteria || []).filter((c) => suppressedIds.includes(c.id));
  if (!hidden.length) return null;
  return (
    <div className="ce-hidden">
      <button
        type="button"
        className="ce-hidden-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        {open ? 'Hide' : 'Show'} {hidden.length} hidden from this role
      </button>
      {open ? (
        <ul className="ce-hidden-list">
          {hidden.map((c) => (
            <li key={c.id} className="ce-hidden-item">
              <span className={`ce-source-dot ce-source-dot--workspace`} aria-hidden="true" />
              <span className="ce-hidden-text">{c.text}</span>
              <span className="ce-hidden-bucket">{c.bucket}</span>
              <button
                type="button"
                className="ce-hidden-restore"
                onClick={() => onRestore(c)}
                disabled={busy}
              >
                Add back
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
};

const RoleStateBar = ({ chips, onSync, onReset, busy, syncing, resetting }) => {
  const counts = chips.reduce(
    (acc, c) => {
      if (c.org_criterion_id != null) {
        acc.workspace += 1;
        if (c.customized_at) acc.customized += 1;
      } else {
        acc.role += 1;
      }
      return acc;
    },
    { workspace: 0, customized: 0, role: 0 },
  );
  const isCustom = counts.customized > 0 || counts.role > 0;
  return (
    <div className="ce-role-state">
      <span className={`ce-role-state-dot ${isCustom ? 'is-custom' : 'is-inheriting'}`} />
      <span className="ce-role-state-text">
        {isCustom ? <strong>Customized for this role</strong> : <strong>Inheriting from workspace</strong>}
        {' · '}
        {counts.workspace} from workspace
        {counts.customized ? ` · ${counts.customized} edited` : ''}
        {counts.role ? ` · ${counts.role} added here` : ''}
      </span>
      <span className="ce-role-state-actions">
        <button
          type="button"
          className="ce-btn"
          onClick={onSync}
          disabled={busy || syncing}
          title="Pull in workspace updates without losing chips you added on this role"
        >
          {syncing ? 'Syncing…' : 'Sync workspace'}
        </button>
        <button
          type="button"
          className="ce-btn ce-btn--ghost"
          onClick={onReset}
          disabled={busy || resetting}
          title="Discard role-level changes and re-snapshot workspace defaults"
        >
          {resetting ? 'Resetting…' : 'Reset to defaults'}
        </button>
      </span>
    </div>
  );
};

const SourceLegend = () => (
  <div className="ce-legend">
    <span className="ce-legend-item">
      <span className="ce-source-dot ce-source-dot--workspace" aria-hidden="true" /> From workspace
    </span>
    <span className="ce-legend-item">
      <span className="ce-source-dot ce-source-dot--edited" aria-hidden="true" /> Edited on this role
    </span>
    <span className="ce-legend-item">
      <span className="ce-source-dot ce-source-dot--role" aria-hidden="true" /> Added here
    </span>
  </div>
);

/**
 * CriteriaEditor — chip composer + 3 columns.
 *
 * Workspace mode (Settings → AI agent):
 *   <CriteriaEditor mode="workspace" criteria={chips} onCreate onUpdate onDelete />
 *
 * Role mode (per-role intent):
 *   <CriteriaEditor
 *     mode="role"
 *     criteria={effectiveChips}
 *     workspaceCriteria={workspaceChips}
 *     suppressedIds={role.suppressed_org_criterion_ids || []}
 *     onCreate onUpdate onDelete onSync onReset onRestoreHidden
 *   />
 */
const CriteriaEditor = ({
  mode = 'workspace',
  criteria,
  workspaceCriteria,
  suppressedIds = [],
  busy = false,
  onCreate,
  onUpdate,
  onDelete,
  onSync,
  onReset,
  onRestoreHidden,
  syncing = false,
  resetting = false,
}) => {
  const active = (criteria || []).filter(isActive);

  const grouped = useMemo(() => {
    const byBucket = { must: [], preferred: [], constraint: [] };
    active.forEach((c) => {
      const bucket = byBucket[c.bucket] ? c.bucket : 'preferred';
      byBucket[bucket].push(c);
    });
    Object.keys(byBucket).forEach((k) => {
      byBucket[k].sort((a, b) => (a.ordering ?? 0) - (b.ordering ?? 0) || a.id - b.id);
    });
    return byBucket;
  }, [active]);

  return (
    <div className={`ce-editor ce-editor--${mode}`} data-busy={busy || undefined}>
      {mode === 'role' ? (
        <RoleStateBar
          chips={active}
          onSync={onSync}
          onReset={onReset}
          busy={busy}
          syncing={syncing}
          resetting={resetting}
        />
      ) : null}

      {mode === 'role' ? <SourceLegend /> : null}

      <Composer onAdd={onCreate} disabled={busy} />

      <div className="ce-columns">
        {BUCKETS.map((bucket) => (
          <Column
            key={bucket.key}
            bucket={bucket}
            chips={grouped[bucket.key]}
            mode={mode}
            onEdit={onUpdate}
            onDelete={onDelete}
            busy={busy}
          />
        ))}
      </div>

      {mode === 'role' ? (
        <HiddenSection
          workspaceCriteria={workspaceCriteria}
          suppressedIds={suppressedIds}
          onRestore={onRestoreHidden}
          busy={busy}
        />
      ) : null}
    </div>
  );
};

export default CriteriaEditor;
