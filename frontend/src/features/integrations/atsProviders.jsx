import { BullhornLogo, WorkableLogo } from '../../shared/ui/RecruiterDesignPrimitives';
import { BullhornConnection } from './BullhornConnection';

// Registry for the unified Integrations settings surface. Each entry is a
// self-describing ATS provider: how to label it, its icon, whether it's
// available to this org, whether it's connected, and (for self-contained
// bodies) the component to render. Workable's card body is NOT here — it lives
// inline in RecruiterSettingsPage because it's coupled to ~40 pieces of page
// state; the settings page passes it in as a body slot. Adding a future
// provider means one entry here + (if its body is self-contained) a Component.
export const ATS_PROVIDERS = [
  {
    id: 'workable',
    label: 'Workable',
    cardTitle: 'Workable integration',
    cardSubtitle:
      'Pull jobs and candidates from Workable, then write invite and outcome actions back.',
    Icon: WorkableLogo,
    // Body rendered inline by RecruiterSettingsPage (state-coupled) and passed
    // to IntegrationsSection as a slot; no self-contained Component here.
    Component: null,
    available: () => true,
    connected: (org) => Boolean(org?.workable_connected),
  },
  {
    id: 'bullhorn',
    label: 'Bullhorn',
    cardTitle: 'Bullhorn integration',
    cardSubtitle:
      'Connect your Bullhorn ATS to pull job orders and candidates, then write outcomes back. The API-user password is used once for sign-in and never stored.',
    Icon: BullhornLogo,
    Component: BullhornConnection,
    // Platform-level gate — off in every environment until BULLHORN_ENABLED
    // flips (surfaced to the FE as org.bullhorn_enabled).
    available: (org) => Boolean(org?.bullhorn_enabled),
    connected: (org) => Boolean(org?.bullhorn_connected),
  },
];

// Short display labels for the "Active ATS" indicator (distinct from the
// card headings above). Standalone is a posture, not a provider.
export const ACTIVE_ATS_LABELS = {
  workable: 'Workable',
  bullhorn: 'Bullhorn',
  standalone: 'Standalone',
};

export const activeAtsLabel = (activeAts) =>
  ACTIVE_ATS_LABELS[activeAts] || ACTIVE_ATS_LABELS.standalone;

// Derive the active ATS from the org's LIVE connection fields, in registry order
// (Workable-wins), mirroring the backend resolver precedence. We derive rather
// than read the serialized `org.active_ats` so the indicator updates the instant
// a card connects (e.g. the Workable token path flips `workable_connected` in
// local state) — the backend field only refreshes on a full `/organizations/me`
// refetch. Falls back to standalone when no available provider is connected.
export const deriveActiveAts = (org) => {
  for (const provider of ATS_PROVIDERS) {
    if (provider.available(org) && provider.connected(org)) return provider.id;
  }
  return 'standalone';
};
