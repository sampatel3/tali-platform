// Shown for any unknown URL (the catch-all route). Previously the app silently
// redirected unknown paths to "/", which then bounced authed users to /home —
// a mistyped or truncated candidate link looked like the app "just went Home",
// with no signal the link was broken. This gives clear feedback and two ways
// back in. On-brand, minimal, semantic tokens only.
import React from 'react';
import { Link } from 'react-router-dom';

export function NotFoundPage() {
  return (
    <main
      style={{
        minHeight: '100dvh',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 20,
        padding: 24,
        textAlign: 'center',
        background: 'var(--bg)',
        color: 'var(--ink)',
      }}
    >
      <p
        style={{
          fontFamily: 'var(--font-mono)',
          fontSize: '0.75rem',
          letterSpacing: '0.12em',
          textTransform: 'uppercase',
          color: 'var(--purple)',
          margin: 0,
        }}
      >
        Page not found
      </p>
      <h1 style={{ fontSize: '1.75rem', fontWeight: 650, margin: 0 }}>
        We couldn&rsquo;t find that page
      </h1>
      <p style={{ maxWidth: 460, color: 'var(--mute)', margin: 0, lineHeight: 1.5 }}>
        The link may be mistyped or out of date. Double-check it, or head back to one
        of these.
      </p>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', justifyContent: 'center' }}>
        <Link to="/home" className="btn btn-purple">Go to Home</Link>
        <Link to="/jobs" className="btn btn-outline">Go to Jobs</Link>
      </div>
    </main>
  );
}

export default NotFoundPage;
