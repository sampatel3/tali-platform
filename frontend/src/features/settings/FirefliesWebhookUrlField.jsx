import React from 'react';

export default function FirefliesWebhookUrlField({ value }) {
  return (
    <label className="field">
      <span className="k">Webhook URL</span>
      <input type="url" readOnly value={value || ''} aria-label="Fireflies webhook URL" />
    </label>
  );
}
