import React from 'react';

// Presentational shell for one ATS provider on the unified Integrations
// surface: icon + title + connection-status chip, then the provider's own
// body as children. It owns NO connect logic — the body (Workable's inline
// block or <BullhornConnection/>) keeps its flow and behaviour untouched.
export const IntegrationCard = ({ title, subtitle, Icon = null, connected = false, children }) => (
  <div className="settings-integration-card">
    <div className="settings-integration-card-head">
      {Icon ? <Icon size={40} /> : null}
      <div className="settings-integration-card-heading">
        <h3>{title}</h3>
        {subtitle ? <p className="sub">{subtitle}</p> : null}
      </div>
      <span
        className={`settings-integration-chip ${connected ? 'on' : ''}`.trim()}
        data-connected={connected ? 'true' : 'false'}
      >
        {connected ? 'Connected' : 'Not connected'}
      </span>
    </div>
    <div className="settings-integration-card-body">{children}</div>
  </div>
);

export default IntegrationCard;
