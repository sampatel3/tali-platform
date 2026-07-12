import React, { useEffect, useRef, useState } from 'react';

// Presentational shell for one ATS provider on the unified Integrations
// surface: icon + title + connection-status chip, then the provider's own
// body as children. It owns NO connect logic — the body (Workable's inline
// block or <BullhornConnection/>) keeps its flow and behaviour untouched.
//
// The card is collapsible: the header is a toggle so an ATS you're not using
// (e.g. Bullhorn on a Workable org) folds away instead of taking up space.
// Defaults open when connected, collapsed when not — override with `defaultOpen`.
// The body stays MOUNTED and is hidden via the `hidden` attribute when
// collapsed, so any in-progress input in the provider body (e.g. a half-typed
// Workable subdomain) survives a collapse/expand.
export const IntegrationCard = ({
  title,
  subtitle,
  Icon = null,
  connected = false,
  defaultOpen = null,
  children,
}) => {
  const isAuto = defaultOpen == null;
  const [open, setOpen] = useState(isAuto ? connected : defaultOpen);
  const bodyId = `integration-body-${String(title || '').toLowerCase().replace(/\s+/g, '-')}`;

  // On first mount `orgData` is still null, so a genuinely-connected provider
  // arrives as `connected=false` and the card would seed collapsed and stay that
  // way (a useState initializer runs once). Auto-open on the false→true
  // connection transition so an existing customer's connected card reveals itself
  // once org data loads. Only fires on the transition, so a manual collapse of a
  // connected card is not undone by later re-renders.
  const prevConnected = useRef(connected);
  useEffect(() => {
    if (isAuto && connected && !prevConnected.current) setOpen(true);
    prevConnected.current = connected;
  }, [connected, isAuto]);

  return (
    <div className={`settings-integration-card ${open ? 'is-open' : 'is-collapsed'}`}>
      <button
        type="button"
        className="settings-integration-card-head"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={bodyId}
      >
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
        <span className="settings-integration-card-chevron" aria-hidden="true">
          {open ? '▾' : '▸'}
        </span>
      </button>
      <div id={bodyId} className="settings-integration-card-body" hidden={!open}>
        {children}
      </div>
    </div>
  );
};

export default IntegrationCard;
