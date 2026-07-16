import React from 'react';

export const SectionPanel = ({ id, title, subtitle, children, tone = '' }) => (
  <section id={id} className={`settings-panel ${tone}`.trim()}>
    <h2>
      {title}
      <em>.</em>
    </h2>
    <p className="sub">{subtitle}</p>
    {children}
  </section>
);

export const ToggleCard = ({ title, description, checked, onChange, badge = null }) => (
  <div className="settings-toggle-card">
    <div>
      <h4>{title}</h4>
      <p>{description}</p>
    </div>
    <div className="settings-toggle-card-action">
      {badge}
      <button
        type="button"
        className={`sw ${checked ? 'on' : ''}`}
        aria-label={title}
        aria-pressed={checked}
        onClick={() => onChange(!checked)}
      />
    </div>
  </div>
);

export const SettingsNavLink = ({ active, label, onClick }) => (
  <button
    type="button"
    className={`mc-settings-link ${active ? 'on' : ''}`.trim()}
    onClick={onClick}
  >
    {label}
  </button>
);
