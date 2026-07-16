import React, { useEffect, useState } from 'react';

import {
  API_BASE,
  CHANGELOG,
  ENDPOINT_GROUPS,
  ERRORS,
  SCOPES,
  SECTIONS,
} from './apiReference';
import './DeveloperPortalPage.css';

// Self-contained, public (no auth/app context), and DARK by default — the
// developer surface deliberately reads differently from the light product app.
// The palette is fixed (not pulled from the app's light tokens) so it stays
// dark regardless of the surrounding theme. Scoped `.devx-` styles live in one
// <style> block so there's no CSS-import wiring.
const PORTAL_CSS = `
.devx {
  color: var(--x-fg);
  background: var(--x-bg);
  min-height: 100vh;
}
.devx, .devx * { box-sizing: border-box; }
html { scroll-behavior: smooth; }
@media (prefers-reduced-motion: reduce) { html { scroll-behavior: auto; } }
body { background: var(--developer-portal-page-bg); }
.devx ::selection { background: rgba(167,139,250,0.30); color: var(--x-selection-foreground); }
.devx-bar { position: sticky; top: 0; z-index: 30; display: flex; align-items: center; justify-content: space-between; padding: 14px 24px; background: color-mix(in srgb, var(--x-bg) 82%, transparent); backdrop-filter: blur(10px); border-bottom: 1px solid var(--x-line); }
.devx-brand { font-weight: 700; font-size: 18px; letter-spacing: -0.01em; color: var(--x-purple); text-decoration: none; }
.devx-bar-right { display: flex; align-items: center; gap: 16px; }
.devx-bar-link { font-size: 14px; color: var(--x-mute); text-decoration: none; }
.devx-bar-link:hover { color: var(--x-fg); }
.devx-body { display: grid; grid-template-columns: 220px minmax(0, 1fr); gap: 48px; max-width: 1080px; margin: 0 auto; padding: 28px 24px 120px; }
.devx-nav { position: sticky; top: 78px; align-self: start; display: flex; flex-direction: column; gap: 1px; }
.devx-nav a { padding: 6px 12px; border-radius: 8px; color: var(--x-mute); text-decoration: none; font-size: 14px; border-left: 2px solid transparent; transition: color .12s; }
.devx-nav a:hover { color: var(--x-fg); }
.devx-nav a.on { color: var(--x-purple); border-left-color: var(--x-purple); font-weight: 600; }
.devx-main { min-width: 0; }
.devx-kicker { display: inline-block; font-family: var(--font-mono, monospace); font-size: 11px; letter-spacing: 0.12em; text-transform: uppercase; color: var(--x-purple); background: var(--x-purple-soft); padding: 3px 10px; border-radius: 999px; }
.devx-h1 { font-size: clamp(30px, 4vw, 40px); line-height: 1.04; letter-spacing: -0.02em; margin: 12px 0 10px; color: var(--x-strong); }
.devx-lede { font-size: 17px; line-height: 1.55; color: var(--x-mute); max-width: 640px; }
.devx section { scroll-margin-top: 80px; padding-top: 36px; }
.devx h2 { font-size: 23px; letter-spacing: -0.01em; margin: 0 0 12px; color: var(--x-strong); }
.devx h3 { font-size: 12px; font-family: var(--font-mono, monospace); text-transform: uppercase; letter-spacing: 0.05em; color: var(--x-mute); margin: 22px 0 6px; }
.devx p { font-size: 15px; line-height: 1.65; margin: 8px 0; }
.devx a.inline { color: var(--x-purple); }
.devx code { font-family: var(--font-mono, monospace); font-size: 13px; background: var(--x-purple-soft); color: var(--x-purple); padding: 1px 6px; border-radius: 6px; }
.devx-codewrap { position: relative; margin: 12px 0; }
.devx-pre { background: var(--x-surface); color: var(--x-code-foreground); border: 1px solid var(--x-line); border-radius: 12px; padding: 16px; overflow-x: auto; font-family: var(--font-mono, monospace); font-size: 13px; line-height: 1.6; margin: 0; }
.devx-copy { position: absolute; top: 10px; right: 10px; }
.devx-grp { margin-top: 20px; }
.devx-grp-title { font-weight: 700; font-size: 13px; margin-bottom: 4px; color: var(--x-fg); }
.devx-ep { display: grid; grid-template-columns: 56px minmax(0,1fr); gap: 12px; padding: 12px 0; border-top: 1px solid var(--x-line); }
.devx-verb { font-family: var(--font-mono, monospace); font-weight: 700; font-size: 12px; color: var(--x-purple); padding-top: 1px; }
.devx-path { font-family: var(--font-mono, monospace); font-size: 14px; word-break: break-word; color: var(--x-fg); }
.devx-epdesc { color: var(--x-mute); font-size: 13px; margin-top: 3px; }
.devx-scope { font-family: var(--font-mono, monospace); font-size: 11px; color: var(--x-purple); background: var(--x-purple-soft); padding: 1px 6px; border-radius: 6px; }
.devx-table { width: 100%; border-collapse: collapse; font-size: 14px; }
.devx-table th { text-align: left; padding: 8px 10px; border-bottom: 2px solid var(--x-line); font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--x-mute); }
.devx-table td { padding: 8px 10px; border-bottom: 1px solid var(--x-line); vertical-align: top; }
.devx-cl { border-left: 2px solid var(--x-line); padding-left: 16px; margin-top: 8px; }
.devx-cl-date { font-family: var(--font-mono, monospace); font-size: 13px; color: var(--x-purple); }
.devx-cl ul { margin: 4px 0 16px; padding-left: 18px; }
.devx-cl li { font-size: 14px; line-height: 1.6; color: var(--x-mute); }
.devx-foot { color: var(--x-mute); font-size: 14px; margin-top: 48px; border-top: 1px solid var(--x-line); padding-top: 20px; }
@media (max-width: 860px) { .devx-body { grid-template-columns: 1fr; gap: 0; } .devx-nav { display: none; } }
`;

const CodeBlock = ({ children }) => {
  const [copied, setCopied] = useState(false);
  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(children);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable — user can select manually */
    }
  };
  return (
    <div className="devx-codewrap">
      <button type="button" className="taali-btn taali-btn-secondary taali-btn-xs devx-copy" onClick={onCopy}>
        {copied ? 'Copied' : 'Copy'}
      </button>
      <pre className="devx-pre">{children}</pre>
    </div>
  );
};

export const DeveloperPortalPage = () => {
  const [active, setActive] = useState(SECTIONS[0].id);

  useEffect(() => {
    if (typeof IntersectionObserver === 'undefined') return undefined;
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) setActive(entry.target.id);
        });
      },
      { rootMargin: '-78px 0px -68% 0px', threshold: 0 }
    );
    SECTIONS.forEach((s) => {
      const el = document.getElementById(s.id);
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, []);

  return (
    <div className="devx">
      <style>{PORTAL_CSS}</style>

      <nav className="devx-bar">
        <a href="/" className="devx-brand">Taali</a>
        <div className="devx-bar-right">
          <a href="#endpoints" className="devx-bar-link">Endpoints</a>
          <a href="/settings/developers" className="taali-btn taali-btn-primary taali-btn-sm">Sign in for API keys</a>
        </div>
      </nav>

      <div className="devx-body">
        <aside className="devx-nav" aria-label="Developer documentation sections">
          {SECTIONS.map((s) => (
            <a key={s.id} href={`#${s.id}`} className={active === s.id ? 'on' : ''}>
              {s.label}
            </a>
          ))}
        </aside>

        <main className="devx-main">
          <section id="overview">
            <span className="devx-kicker">API v1</span>
            <h1 className="devx-h1">Taali Developer Portal</h1>
            <p className="devx-lede">
              Connect your systems to Taali — read roles, candidates, applications and
              assessment results, and create assessments programmatically. Stable,
              versioned, and scoped per organization.
            </p>
            <p>
              The API is a small, curated surface under <code>/public/v1</code>, separate
              from the app’s internal endpoints. Start by{' '}
              <a className="inline" href="/settings/developers">signing in to create an API key</a>.
            </p>
          </section>

          <section id="authentication">
            <h2>Authentication</h2>
            <p>
              Every request carries an API key minted in{' '}
              <a className="inline" href="/settings/developers">Settings → Developers</a>.
              Keys are scoped to your organization; the secret is shown once on creation —
              store it securely and never embed it in client-side code.
            </p>
            <p>Send it as a Bearer token (or the <code>X-API-Key</code> header):</p>
            <CodeBlock>{`curl ${API_BASE}/tests \\
  -H "Authorization: Bearer tali_live_xxxxxxxx"`}</CodeBlock>
            <h3>Scopes</h3>
            <p>Keys carry least-privilege scopes. Grant only what your integration needs:</p>
            <table className="devx-table">
              <thead>
                <tr><th>Scope</th><th>Grants</th></tr>
              </thead>
              <tbody>
                {SCOPES.map((s) => (
                  <tr key={s.id}>
                    <td><code>{s.id}</code></td>
                    <td>{s.desc}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p style={{ marginTop: 12 }}>
              A missing scope returns <code>403</code>; an invalid or revoked key returns{' '}
              <code>401</code>. Use a <code>tali_test_</code> key for sandbox / non-billing calls.
            </p>
          </section>

          <section id="base-url">
            <h2>Base URL</h2>
            <CodeBlock>{API_BASE}</CodeBlock>
            <p>
              The surface is versioned and stable — additive changes only within{' '}
              <code>v1</code>; anything breaking ships under a new version.
            </p>
          </section>

          <section id="endpoints">
            <h2>Endpoints</h2>
            {ENDPOINT_GROUPS.map((group) => (
              <div className="devx-grp" key={group.name}>
                <div className="devx-grp-title">{group.name}</div>
                {group.endpoints.map((ep) => (
                  <div className="devx-ep" key={`${ep.method} ${ep.path}`}>
                    <span className="devx-verb">{ep.method}</span>
                    <div>
                      <span className="devx-path">{ep.path}</span>
                      <div className="devx-epdesc">
                        {ep.desc} · <span className="devx-scope">{ep.scope}</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ))}
            <h3>Example</h3>
            <CodeBlock>{`# Mint a 7-day client-facing report link for an application
curl -X POST ${API_BASE}/applications/123/share-links \\
  -H "Authorization: Bearer tali_live_xxxxxxxx" \\
  -H "Content-Type: application/json" \\
  -d '{"mode": "client", "expiry": "7d"}'

# => { "id": 9, "token": "shr_…", "url": "https://taali.ai/share/shr_…",
#      "mode": "client", "expires_at": "…" }`}</CodeBlock>
          </section>

          <section id="errors">
            <h2>Errors &amp; status codes</h2>
            <p>Errors return a JSON body with a <code>detail</code> field describing the cause.</p>
            <table className="devx-table">
              <thead>
                <tr><th>Status</th><th>Meaning</th></tr>
              </thead>
              <tbody>
                {ERRORS.map((e) => (
                  <tr key={e.code}>
                    <td><code>{e.code}</code></td>
                    <td>{e.meaning}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section id="webhooks">
            <h2>Webhooks <span className="devx-kicker">Coming soon</span></h2>
            <p>
              Subscribe to events so your systems are notified instead of polling. Planned:{' '}
              <code>application.scored</code>, <code>decision.made</code>,{' '}
              <code>assessment.completed</code> — HMAC-signed, retried, idempotent.
            </p>
          </section>

          <section id="workable">
            <h2>Workable</h2>
            <p>
              Already on Workable? Taali is also available as a native{' '}
              <strong>Assessments Provider</strong> — attach a Taali assessment to a
              pipeline stage and results land on the candidate’s Workable timeline, no
              integration code required. Ask us to enable the marketplace add-on for your
              account.
            </p>
          </section>

          <section id="changelog">
            <h2>Changelog</h2>
            {CHANGELOG.map((entry) => (
              <div className="devx-cl" key={entry.date}>
                <div className="devx-cl-date">{entry.date}</div>
                <ul>
                  {entry.items.map((it, i) => (
                    <li key={i}>{it}</li>
                  ))}
                </ul>
              </div>
            ))}
          </section>

          <p className="devx-foot">
            Ready to build? <a className="inline" href="/settings/developers">Sign in to create your first key →</a>
          </p>
        </main>
      </div>
    </div>
  );
};

export default DeveloperPortalPage;
