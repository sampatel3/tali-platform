/**
 * TokenGate — minimal, best-practice URL-token gate for dev/investor pages.
 *
 * NOT a security boundary: anyone with the token URL can view. The job is to
 * keep these pages out of search engines and off the public site nav, while
 * giving us a one-click shareable link for investors / internal demos.
 *
 * Token comes from `import.meta.env.VITE_DEV_TOKEN` (single shared token; rotate
 * by changing the env var). First valid `?k=<token>` visit caches in
 * localStorage so subsequent visits don't need the query string.
 *
 * Sets `<meta name="robots" content="noindex,nofollow">` on every render of
 * the gate (whether passing or failing) so Google never indexes these paths.
 */

import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

const STORAGE_KEY = 'tali.dev_token';
const META_ID = 'tali-dev-noindex';

function setNoindexMeta() {
  if (typeof document === 'undefined') return;
  let meta = document.getElementById(META_ID);
  if (!meta) {
    meta = document.createElement('meta');
    meta.id = META_ID;
    meta.name = 'robots';
    document.head.appendChild(meta);
  }
  meta.content = 'noindex,nofollow';
}

function clearNoindexMeta() {
  if (typeof document === 'undefined') return;
  const meta = document.getElementById(META_ID);
  if (meta) meta.remove();
}

function readStoredToken() {
  try {
    return window.localStorage.getItem(STORAGE_KEY) || '';
  } catch {
    return '';
  }
}

function storeToken(token) {
  try {
    window.localStorage.setItem(STORAGE_KEY, token);
  } catch {
    // ignore — private mode etc.
  }
}

function expectedToken() {
  // Vite inlines this at build time. If unset, gate is permissive in dev only.
  return (import.meta.env?.VITE_DEV_TOKEN || '').trim();
}

export default function TokenGate({ children, label = 'this page' }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    setNoindexMeta();
    return () => clearNoindexMeta();
  }, []);

  useEffect(() => {
    const expected = expectedToken();
    if (!expected) {
      // No token configured. In dev, allow through; in prod, this means
      // someone forgot to set VITE_DEV_TOKEN — fail closed.
      if (import.meta.env?.DEV) {
        setAuthed(true);
        return;
      }
      setAuthed(false);
      return;
    }

    const fromUrl = (searchParams.get('k') || '').trim();
    if (fromUrl && fromUrl === expected) {
      storeToken(fromUrl);
      // Strip token from URL so it isn't shoulder-surfed or kept in history.
      const next = new URLSearchParams(searchParams);
      next.delete('k');
      setSearchParams(next, { replace: true });
      setAuthed(true);
      return;
    }

    if (readStoredToken() === expected) {
      setAuthed(true);
      return;
    }

    setAuthed(false);
  }, [searchParams, setSearchParams]);

  if (authed) return children;

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: '#0d0a14',
        color: '#e8def8',
        fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif',
        padding: '24px',
      }}
    >
      <div style={{ maxWidth: 420, textAlign: 'center' }}>
        <div
          style={{
            fontSize: 14,
            letterSpacing: '0.16em',
            textTransform: 'uppercase',
            opacity: 0.6,
            marginBottom: 12,
          }}
        >
          Tali · Internal
        </div>
        <h1 style={{ fontSize: 28, margin: '0 0 12px', fontWeight: 600 }}>
          Access required
        </h1>
        <p style={{ margin: '0 0 24px', lineHeight: 1.5, opacity: 0.75 }}>
          {label} is for invited viewers. If you have an access link, open it
          again — the token in the URL grants entry. Otherwise contact{' '}
          <a
            href="mailto:sam@taali.ai"
            style={{ color: '#c8a8ff', textDecoration: 'underline' }}
          >
            sam@taali.ai
          </a>
          .
        </p>
        <a
          href="https://taali.ai"
          style={{
            display: 'inline-block',
            padding: '10px 18px',
            background: '#7f39fb',
            color: '#fff',
            borderRadius: 8,
            textDecoration: 'none',
            fontWeight: 500,
          }}
        >
          Go to taali.ai
        </a>
      </div>
    </div>
  );
}
