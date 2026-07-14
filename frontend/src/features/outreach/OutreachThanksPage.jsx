import React from 'react';

// Public, no-auth thanks page shown after a recipient clicks the outreach CTA
// when the campaign has no job page to redirect to. No nav chrome — a standalone
// informational surface, purple tokens only. A GET is tracked as a click, not
// candidate interest, because mail-security scanners also follow links.
export default function OutreachThanksPage() {
  const wrapStyle = {
    maxWidth: 480,
    margin: '0 auto',
    padding: '64px 24px',
    textAlign: 'center',
    fontFamily: '-apple-system, Segoe UI, Roboto, sans-serif',
    color: 'var(--text, #1a1a2e)',
  };
  return (
    <div style={wrapStyle} data-testid="outreach-thanks">
      <h1 style={{ fontSize: 22, fontWeight: 700, color: 'var(--purple, #6b46c1)' }}>
        Thanks for taking a look.
      </h1>
      <p style={{ color: 'var(--text-muted, #6b6b83)', fontSize: 14, marginTop: 12 }}>
        This role does not currently have an online application link. No application was submitted.
      </p>
    </div>
  );
}
