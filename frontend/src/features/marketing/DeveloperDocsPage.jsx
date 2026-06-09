import React from 'react';

// Public, unauthenticated developer documentation for the Taali public API.
// Self-contained + inline-styled so it renders without app context or auth.

const PURPLE = 'var(--purple, #6d28d9)';
const MONO = 'var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace)';

const S = {
  page: { maxWidth: 880, margin: '0 auto', padding: '0 24px 96px', color: 'var(--fg, #14141b)' },
  bar: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    maxWidth: 880, margin: '0 auto', padding: '20px 24px',
  },
  brand: { fontWeight: 700, fontSize: 18, letterSpacing: '-0.01em', color: PURPLE, textDecoration: 'none' },
  hero: { padding: '24px 0 8px' },
  h1: { fontSize: 34, lineHeight: 1.1, margin: '8px 0', letterSpacing: '-0.02em' },
  lede: { fontSize: 17, color: 'var(--mute, #6b7280)', maxWidth: 640 },
  h2: { fontSize: 22, margin: '40px 0 10px', letterSpacing: '-0.01em' },
  h3: { fontSize: 15, margin: '22px 0 6px', fontFamily: MONO },
  p: { fontSize: 15, lineHeight: 1.65, color: 'var(--fg, #14141b)' },
  pre: {
    background: 'var(--code-bg, #0b0b12)', color: '#e7e7f3', borderRadius: 12,
    padding: 16, overflowX: 'auto', fontFamily: MONO, fontSize: 13, lineHeight: 1.55,
  },
  code: { fontFamily: MONO, background: 'var(--purple-soft, rgba(109,40,217,0.08))', color: PURPLE, padding: '1px 6px', borderRadius: 6, fontSize: 13 },
  ep: { display: 'flex', gap: 10, alignItems: 'baseline', fontFamily: MONO, fontSize: 14, margin: '12px 0 2px' },
  verb: { fontWeight: 700, color: PURPLE },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 14, marginTop: 8 },
  th: { textAlign: 'left', padding: '8px 10px', borderBottom: '2px solid var(--line, #eee)', fontSize: 12, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--mute, #6b7280)' },
  td: { padding: '8px 10px', borderBottom: '1px solid var(--line, #f0f0f0)', verticalAlign: 'top' },
  cta: { display: 'inline-block', marginTop: 10, padding: '10px 18px', borderRadius: 10, background: PURPLE, color: '#fff', textDecoration: 'none', fontWeight: 600, fontSize: 14 },
};

const Endpoint = ({ verb, path, children }) => (
  <div>
    <div style={S.ep}><span style={S.verb}>{verb}</span><span>{path}</span></div>
    <p style={{ ...S.p, margin: '2px 0 0', color: 'var(--mute, #6b7280)' }}>{children}</p>
  </div>
);

export const DeveloperDocsPage = () => (
  <div>
    <nav style={S.bar}>
      <a href="/" style={S.brand}>Taali</a>
      <a href="/settings/developers" style={{ ...S.code, textDecoration: 'none' }}>Get a key →</a>
    </nav>

    <main style={S.page}>
      <header style={S.hero}>
        <span style={{ ...S.code }}>API v1</span>
        <h1 style={S.h1}>Taali API</h1>
        <p style={S.lede}>
          Connect your systems to Taali — read roles, candidates, applications and
          assessment results, and create assessments programmatically. Agent-friendly,
          stable, and scoped per organization.
        </p>
        <a href="/settings/developers" style={S.cta}>Create an API key</a>
      </header>

      <h2 style={S.h2}>Authentication</h2>
      <p style={S.p}>
        Every request is authenticated with an API key minted in{' '}
        <a href="/settings/developers">Settings → Developers</a>. Keys are scoped to your
        organization and to specific capabilities; the secret is shown once on creation —
        store it securely and never embed it in client-side code.
      </p>
      <p style={S.p}>Send the key as a Bearer token (or the <code style={S.code}>X-API-Key</code> header):</p>
      <pre style={S.pre}>{`curl https://api.taali.ai/public/v1/roles \\
  -H "Authorization: Bearer tali_live_xxxxxxxx"`}</pre>
      <p style={S.p}>
        Keys carry least-privilege <strong>scopes</strong>:{' '}
        <code style={S.code}>roles:read</code>, <code style={S.code}>applications:read</code>,{' '}
        <code style={S.code}>assessments:read</code>, <code style={S.code}>assessments:write</code>,{' '}
        <code style={S.code}>share-links:write</code>. A request missing the required scope returns{' '}
        <code style={S.code}>403</code>; an invalid or revoked key returns <code style={S.code}>401</code>.
        Use a <code style={S.code}>tali_test_</code> key for sandbox/non-billing calls.
      </p>

      <h2 style={S.h2}>Base URL</h2>
      <pre style={S.pre}>{`https://<your-taali-api-host>/public/v1`}</pre>
      <p style={S.p}>
        The surface is versioned and stable — additive changes only within{' '}
        <code style={S.code}>v1</code>; breaking changes ship under a new version.
      </p>

      <h2 style={S.h2}>Endpoints</h2>

      <h3 style={S.h3}>Catalog</h3>
      <Endpoint verb="GET" path="/public/v1/tests">List your available assessment tasks (the catalog you can assess against).</Endpoint>

      <h3 style={S.h3}>Roles</h3>
      <Endpoint verb="GET" path="/public/v1/roles">List your organization&apos;s roles (paginated via <code style={S.code}>limit</code> / <code style={S.code}>offset</code>).</Endpoint>
      <Endpoint verb="GET" path="/public/v1/roles/{id}">Fetch a single role with its linked assessment tasks.</Endpoint>

      <h3 style={S.h3}>Applications &amp; assessments</h3>
      <Endpoint verb="GET" path="/public/v1/applications/{id}">A candidate application: stage, scores, live recommendation, and CV-fit.</Endpoint>
      <Endpoint verb="GET" path="/public/v1/assessments/{id}">An assessment&apos;s status, scores, and timestamps.</Endpoint>
      <Endpoint verb="POST" path="/public/v1/applications/{id}/share-links">Mint a shareable report link (a <code style={S.code}>results_url</code>). Requires <code style={S.code}>share-links:write</code>.</Endpoint>

      <h2 style={S.h2}>Example</h2>
      <pre style={S.pre}>{`# Mint a 7-day client-facing report link for an application
curl -X POST https://api.taali.ai/public/v1/applications/123/share-links \\
  -H "Authorization: Bearer tali_live_xxxxxxxx" \\
  -H "Content-Type: application/json" \\
  -d '{"mode": "client", "expiry": "7d"}'

# => { "id": 9, "token": "shr_…", "url": "https://taali.ai/share/shr_…",
#      "mode": "client", "expires_at": "…" }`}</pre>

      <h2 style={S.h2}>Errors</h2>
      <table style={S.table}>
        <thead>
          <tr>
            <th style={S.th}>Status</th>
            <th style={S.th}>Meaning</th>
          </tr>
        </thead>
        <tbody>
          <tr><td style={S.td}><code style={S.code}>401</code></td><td style={S.td}>Missing, malformed, invalid, revoked, or expired key.</td></tr>
          <tr><td style={S.td}><code style={S.code}>403</code></td><td style={S.td}>The key lacks a required scope.</td></tr>
          <tr><td style={S.td}><code style={S.code}>404</code></td><td style={S.td}>Not found — or not in your organization (tenant-isolated).</td></tr>
          <tr><td style={S.td}><code style={S.code}>429</code></td><td style={S.td}>Rate limited — retry after the window.</td></tr>
        </tbody>
      </table>

      <h2 style={S.h2}>Workable</h2>
      <p style={S.p}>
        Already on Workable? Taali is also available as a native{' '}
        <strong>Assessments Provider</strong> — attach a Taali assessment to a pipeline
        stage and results land on the candidate&apos;s Workable timeline, no integration code
        required. Ask us to enable the marketplace add-on for your account.
      </p>

      <h2 style={S.h2}>Coming soon</h2>
      <p style={S.p}>
        Outbound <strong>webhooks</strong> (subscribe to <code style={S.code}>application.scored</code>,{' '}
        <code style={S.code}>decision.made</code>, <code style={S.code}>assessment.completed</code>) so your
        systems are notified instead of polling.
      </p>

      <p style={{ ...S.p, marginTop: 40, color: 'var(--mute, #6b7280)' }}>
        Ready to build? <a href="/settings/developers">Create your first key →</a>
      </p>
    </main>
  </div>
);

export default DeveloperDocsPage;
