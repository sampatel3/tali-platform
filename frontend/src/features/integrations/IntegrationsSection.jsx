import React from 'react';

import { IntegrationCard } from './IntegrationCard';
import { ATS_PROVIDERS, activeAtsLabel } from './atsProviders';

// The unified Integrations surface: an "Active ATS" indicator derived from the
// backend-resolved org.active_ats, an informational standalone state when no
// ATS is connected, then one card per AVAILABLE provider. Provider bodies come
// from `bodies[id]` (a slot — used for Workable's state-coupled inline block)
// or fall back to the provider's registry Component (e.g. BullhornConnection).
export const IntegrationsSection = ({ org = null, providers = ATS_PROVIDERS, bodies = {} }) => {
  const activeAts = org?.active_ats || 'standalone';
  const isStandalone = activeAts === 'standalone';
  const available = providers.filter((p) => p.available(org));

  return (
    <div className="settings-integrations">
      <div className="settings-integrations-active">
        <span className="mono-label">Active ATS</span>
        <span className={`settings-integration-chip on active-ats-${activeAts}`.trim()}>
          {activeAtsLabel(activeAts)}
        </span>
      </div>

      {isStandalone ? (
        <p className="settings-inline-note settings-integrations-standalone">
          No ATS connected — Taali runs standalone; your candidates and pipeline
          live in Taali. Connect a provider below to sync jobs and candidates.
        </p>
      ) : null}

      <div className="settings-integrations-list">
        {available.map((provider) => {
          const body = Object.prototype.hasOwnProperty.call(bodies, provider.id)
            ? bodies[provider.id]
            : provider.Component
              ? <provider.Component orgData={org} />
              : null;
          return (
            <IntegrationCard
              key={provider.id}
              title={provider.cardTitle}
              subtitle={provider.cardSubtitle}
              Icon={provider.Icon}
              connected={provider.connected(org)}
            >
              {body}
            </IntegrationCard>
          );
        })}
      </div>
    </div>
  );
};

export default IntegrationsSection;
