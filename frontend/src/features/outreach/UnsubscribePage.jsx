import React, { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { fetchUnsubscribe, submitUnsubscribe } from '../../shared/api/httpClient';

// Public, no-auth one-click unsubscribe. GET validates the token and shows the
// org name + masked email; the button POSTs the opt-out. No nav chrome — a
// standalone confirmation surface, purple tokens only (never red).
export default function UnsubscribePage() {
  const { token } = useParams();
  const [state, setState] = useState({ loading: true, error: null, data: null });
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  useEffect(() => {
    let alive = true;
    fetchUnsubscribe(token)
      .then((res) => {
        if (alive) setState({ loading: false, error: null, data: res.data });
      })
      .catch((err) => {
        const status = err?.response?.status;
        const msg = status === 404
          ? 'This unsubscribe link is invalid or has expired.'
          : 'Could not load this unsubscribe request.';
        if (alive) setState({ loading: false, error: msg, data: null });
      });
    return () => { alive = false; };
  }, [token]);

  const handleUnsubscribe = () => {
    setSubmitting(true);
    submitUnsubscribe(token)
      .then(() => setDone(true))
      .catch(() => setState((s) => ({ ...s, error: 'Something went wrong. Please try again.' })))
      .finally(() => setSubmitting(false));
  };

  const wrapStyle = {
    maxWidth: 480,
    margin: '0 auto',
    padding: '64px 24px',
    textAlign: 'center',
    color: 'var(--taali-outreach-text)',
    fontFamily: 'inherit',
  };
  const brandStyle = { fontWeight: 700, fontSize: 22, color: 'var(--purple)', marginBottom: 32 };
  const cardStyle = {
    border: '1px solid var(--taali-outreach-border)',
    borderRadius: 12,
    padding: 32,
    background: 'var(--surface)',
  };
  const btnStyle = {
    marginTop: 20,
    padding: '10px 20px',
    borderRadius: 8,
    border: 'none',
    background: 'var(--purple)',
    color: 'var(--taali-on-accent)',
    fontWeight: 600,
    cursor: submitting ? 'default' : 'pointer',
    opacity: submitting ? 0.6 : 1,
  };
  const mutedStyle = { color: 'var(--taali-outreach-muted)' };

  if (state.loading) {
    return (
      <div style={wrapStyle}>
        <div style={mutedStyle}>Loading…</div>
      </div>
    );
  }

  const brand = <div style={brandStyle}>taali<span>.</span></div>;

  if (state.error) {
    return (
      <div style={wrapStyle}>
        {brand}
        <div style={cardStyle}>
          <p style={mutedStyle}>{state.error}</p>
        </div>
      </div>
    );
  }

  const data = state.data || {};
  const orgName = data.organization_name || 'this sender';
  const email = data.email_masked || 'your email';

  if (done) {
    return (
      <div style={wrapStyle}>
        {brand}
        <div style={cardStyle}>
          <h2 style={{ margin: '0 0 12px', fontSize: 18 }}>You're unsubscribed</h2>
          <p style={mutedStyle}>
            {email} will no longer receive outreach emails from {orgName}.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div style={wrapStyle}>
      {brand}
      <div style={cardStyle}>
        <h2 style={{ margin: '0 0 12px', fontSize: 18 }}>Unsubscribe from {orgName}</h2>
        <p style={mutedStyle}>
          Confirm to stop outreach emails to <strong>{email}</strong>.
        </p>
        <button type="button" style={btnStyle} onClick={handleUnsubscribe} disabled={submitting}>
          {submitting ? 'Unsubscribing…' : 'Unsubscribe'}
        </button>
      </div>
    </div>
  );
}
