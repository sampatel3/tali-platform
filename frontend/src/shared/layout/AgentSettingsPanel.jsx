import React, { useEffect } from 'react';
import { Info, X } from 'lucide-react';

import { Select } from '../ui/TaaliPrimitives';

const Switch = ({ on, onChange, label }) => (
  <button
    type="button"
    className={`mc-switch ${on ? 'on' : ''}`.trim()}
    onClick={() => onChange?.(!on)}
    role="switch"
    aria-checked={on}
    aria-label={label}
  />
);

// Slide-out drawer used on (a) the role-detail "Agent settings" tab and
// (b) Settings → AI tooling. Pure presentation — controlled state lives
// in the parent so the same panel can drive role overrides or org defaults.
//
// Props:
//   open        — drawer visibility
//   onClose     — close handler
//   scope       — 'role' | 'org' (changes copy + which sections appear)
//   value       — { enabled, budget_cents, pause_threshold_pct, autonomy: {...} }
//   onChange    — (next) => void
//   roleSummary — { name, must_haves: string[], on_org_defaults_link?: string }
export const AgentSettingsPanel = ({
  open,
  onClose,
  scope = 'role',
  value = {},
  onChange,
  roleSummary,
}) => {
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key === 'Escape') onClose?.();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;
  const isRole = scope === 'role';
  const v = {
    enabled: true,
    budget_cents: 5000,
    pause_threshold_pct: 80,
    autonomy: {
      auto_invite_above: true,
      auto_reject_below: true,
      auto_advance_high_score: false,
      passive_outbound: false,
    },
    ...value,
  };
  const update = (patch) => onChange?.({ ...v, ...patch });
  const updateAutonomy = (patch) =>
    onChange?.({ ...v, autonomy: { ...v.autonomy, ...patch } });

  const budgetDollars = Math.round((v.budget_cents || 0) / 100);

  return (
    <div className="mc-panel-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose?.()}>
      <aside className="mc-panel-drawer" role="dialog" aria-label="Agent settings">
        <div className="mc-panel-head">
          <div>
            <div className="mc-panel-kicker">{isRole ? 'AGENT · THIS ROLE ONLY' : 'AGENT · ORG-WIDE DEFAULTS'}</div>
            <div className="mc-panel-title">
              {isRole ? roleSummary?.name || 'Role-level agent settings' : 'Defaults for new roles'}
            </div>
          </div>
          <button type="button" className="mc-icon-btn" onClick={onClose} aria-label="Close panel">
            <X size={16} strokeWidth={1.7} />
          </button>
        </div>

        <div className="mc-panel-body">
          <section className="mc-panel-card">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16 }}>
              <div>
                <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>
                  {v.enabled ? 'Agent is on' : 'Agent is off'}
                </div>
                <div className="mc-panel-help" style={{ marginTop: 4 }}>
                  {isRole
                    ? 'Overrides org defaults for this role only. Changes apply immediately.'
                    : 'Apply to every new role. Per-role overrides win.'}
                </div>
              </div>
              <Switch
                on={v.enabled}
                onChange={(next) => update({ enabled: next })}
                label="Enable agent"
              />
            </div>
          </section>

          <section className="mc-panel-card">
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)', marginBottom: 4 }}>Spend budget</div>
            <div className="mc-panel-help" style={{ marginBottom: 12 }}>
              Hard cap. Agent pauses when reached.
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <input
                type="number"
                min={0}
                step={5}
                value={budgetDollars}
                onChange={(e) => update({ budget_cents: Math.max(0, Math.round(Number(e.target.value || 0) * 100)) })}
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  border: '1px solid var(--line)',
                  borderRadius: 8,
                  fontFamily: 'inherit',
                  fontSize: 13.5,
                  background: 'var(--bg)',
                }}
                aria-label="Monthly budget (USD)"
              />
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11.5, color: 'var(--mute)' }}>USD/mo</span>
            </div>
          </section>

          <section className="mc-panel-card">
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)', marginBottom: 4 }}>Autonomy</div>
            <div className="mc-panel-help" style={{ marginBottom: 12 }}>
              What the agent can do without asking.
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12, fontSize: 13 }}>
              {[
                { key: 'auto_invite_above', label: 'Auto-invite candidates with CV match ≥ 75%' },
                { key: 'auto_reject_below', label: 'Auto-reject candidates with CV match < 50%' },
                { key: 'auto_advance_high_score', label: 'Auto-advance assessments scoring ≥ 85%' },
                { key: 'passive_outbound', label: 'Outbound to passive candidates' },
              ].map(({ key, label }) => (
                <label key={key} style={{ display: 'flex', gap: 10, alignItems: 'center', cursor: 'pointer' }}>
                  <Switch
                    on={Boolean(v.autonomy?.[key])}
                    onChange={(next) => updateAutonomy({ [key]: next })}
                    label={label}
                  />
                  <span style={{ color: v.autonomy?.[key] ? 'var(--ink)' : 'var(--ink-2)' }}>{label}</span>
                </label>
              ))}
            </div>
          </section>

          {isRole && roleSummary?.must_haves?.length ? (
            <section className="mc-panel-card">
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)', marginBottom: 4 }}>
                Must-have requirements
              </div>
              <div className="mc-panel-help" style={{ marginBottom: 12 }}>
                The agent rejects below these — no exceptions.
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 13, color: 'var(--ink-2)' }}>
                {roleSummary.must_haves.map((line) => (
                  <div key={line}>· {line}</div>
                ))}
              </div>
            </section>
          ) : null}

          <section className="mc-panel-card">
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)', marginBottom: 4 }}>Pause threshold</div>
            <div className="mc-panel-help" style={{ marginBottom: 12 }}>
              Agent pauses itself when budget reaches this %.
            </div>
            <Select
              value={v.pause_threshold_pct}
              onChange={(e) => update({ pause_threshold_pct: Number(e.target.value) })}
            >
              <option value={70}>70%</option>
              <option value={80}>80%</option>
              <option value={90}>90%</option>
            </Select>
          </section>

          <div className="mc-info-callout">
            <Info size={14} strokeWidth={2} />
            <span>
              Agent will pause itself if budget reaches the threshold above, or if 3 consecutive scores
              fall outside the expected range.
            </span>
          </div>
        </div>
      </aside>
    </div>
  );
};

export default AgentSettingsPanel;
