// IntegrityFlags — the canonical trust readout, shown WITH the summary text:
// the specific things to verify before deciding (amber), plus the positive
// cross-source corroborations we actually confirmed (a quiet green check).
//
// It reads ``score_summary.integrity`` verbatim — the wording is server-canonical
// (fraud_detection.build_integrity_warnings / build_corroboration_notes), so every
// surface that renders this (candidate report hero, agent-decision card) says
// exactly the same thing and the FE never re-derives it.
import React from 'react';
import { Check, ShieldAlert } from 'lucide-react';

const BAND_LABEL = { low: 'low trust', medium: 'medium trust', high: 'high trust' };

export const IntegrityFlags = ({ integrity, style }) => {
  if (!integrity) return null;
  const warnings = Array.isArray(integrity.warnings) ? integrity.warnings.filter(Boolean) : [];
  const corroborations = Array.isArray(integrity.corroborations) ? integrity.corroborations.filter(Boolean) : [];
  if (!warnings.length && !corroborations.length) return null;

  const band = String(integrity.trust_band || '').toLowerCase();
  const bandText = BAND_LABEL[band] ? ` · ${BAND_LABEL[band]}` : '';

  return (
    <div style={{ display: 'grid', gap: 8, maxWidth: 760, ...(style || {}) }}>
      {warnings.length ? (
        <div
          style={{
            padding: '10px 12px',
            borderRadius: 8,
            border: '1px solid var(--amber)',
            background: 'color-mix(in oklab, var(--amber) 8%, transparent)',
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              fontSize: '0.6875rem',
              fontWeight: 700,
              letterSpacing: '.04em',
              textTransform: 'uppercase',
              color: 'var(--amber)',
              marginBottom: 6,
            }}
          >
            <ShieldAlert size={13} strokeWidth={2.2} aria-hidden="true" />
            Verify before deciding{bandText}
          </div>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {warnings.map((flag, i) => (
              <li
                key={`w-${i}`}
                style={{ fontSize: '0.8125rem', color: 'var(--ink)', margin: '3px 0', lineHeight: 1.45 }}
              >
                {flag}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {corroborations.length ? (
        <div style={{ display: 'grid', gap: 4, paddingLeft: 2 }}>
          {corroborations.map((note, i) => (
            <div
              key={`c-${i}`}
              style={{ display: 'flex', alignItems: 'flex-start', gap: 6, fontSize: '0.8125rem', color: 'var(--ink-2)', lineHeight: 1.45 }}
            >
              <Check size={14} strokeWidth={2.4} aria-hidden="true" style={{ color: 'var(--green)', marginTop: 2, flexShrink: 0 }} />
              <span>{note}</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
};

export default IntegrityFlags;
