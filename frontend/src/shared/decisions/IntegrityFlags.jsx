// IntegrityFlags — the canonical trust readout, shown WITH the summary text:
// the specific things to verify before deciding (amber), plus the positive
// cross-source corroborations we actually confirmed (a quiet check).
//
// It reads ``score_summary.integrity`` verbatim — the wording is server-canonical
// (fraud_detection.build_integrity_warnings / build_corroboration_notes), so every
// surface that renders this (candidate report, agent-decision card) says exactly
// the same thing and the FE never re-derives it.
//
// Presentation matches the design preview: a bright-amber "Flags · N to verify"
// kicker + alert-triangle rows (not a boxed list), capped with a "+N more"
// toggle. Inline styles so it renders identically wherever it's mounted, with no
// per-surface CSS dependency.
import React, { useState } from 'react';
import { AlertTriangle, Check, Flag } from 'lucide-react';

const toggleStyle = {
  background: 'none',
  border: 0,
  padding: '7px 0 0',
  font: 'inherit',
  fontSize: '0.8125rem',
  fontWeight: 500,
  color: 'var(--purple)',
  cursor: 'pointer',
};

export const IntegrityFlags = ({ integrity, style, collapsedCount = 3 }) => {
  const [showAll, setShowAll] = useState(false);
  if (!integrity) return null;
  const warnings = Array.isArray(integrity.warnings) ? integrity.warnings.filter(Boolean) : [];
  const corroborations = Array.isArray(integrity.corroborations) ? integrity.corroborations.filter(Boolean) : [];
  if (!warnings.length && !corroborations.length) return null;

  const visible = showAll ? warnings : warnings.slice(0, collapsedCount);
  const hidden = warnings.length - visible.length;

  return (
    <div style={{ maxWidth: 760, ...(style || {}) }}>
      {warnings.length ? (
        <>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              fontSize: '0.6875rem',
              fontWeight: 600,
              letterSpacing: '.04em',
              textTransform: 'uppercase',
              color: 'var(--amber)',
              marginBottom: 6,
            }}
          >
            <Flag size={13} strokeWidth={2.2} aria-hidden="true" /> Flags · {warnings.length} to verify
          </div>
          {visible.map((flag, i) => (
            <div
              key={`w-${i}`}
              style={{ display: 'flex', gap: 9, alignItems: 'flex-start', padding: '5px 0', fontSize: '0.8125rem', color: 'var(--ink)', lineHeight: 1.45 }}
            >
              <AlertTriangle size={15} strokeWidth={2.2} aria-hidden="true" style={{ color: 'var(--amber)', marginTop: 1, flexShrink: 0 }} />
              <span>{flag}</span>
            </div>
          ))}
          {hidden > 0 ? (
            <button type="button" onClick={() => setShowAll(true)} style={toggleStyle}>
              + {hidden} more flag{hidden === 1 ? '' : 's'}
            </button>
          ) : showAll && warnings.length > collapsedCount ? (
            <button type="button" onClick={() => setShowAll(false)} style={toggleStyle}>
              Show fewer
            </button>
          ) : null}
        </>
      ) : null}

      {corroborations.length ? (
        <div style={{ display: 'grid', gap: 4, paddingLeft: 2, marginTop: warnings.length ? 10 : 0 }}>
          {corroborations.map((note, i) => (
            <div
              key={`c-${i}`}
              style={{ display: 'flex', alignItems: 'flex-start', gap: 6, fontSize: '0.8125rem', color: 'var(--ink-2)', lineHeight: 1.45 }}
            >
              <Check size={14} strokeWidth={2.4} aria-hidden="true" style={{ color: 'var(--purple)', marginTop: 2, flexShrink: 0 }} />
              <span>{note}</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
};

export default IntegrityFlags;
