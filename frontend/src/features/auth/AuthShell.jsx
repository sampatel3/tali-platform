import React from 'react';

import { TaaliTile } from '../../shared/ui/Branding';

// AuthShell — two-pane layout used by login / register / forgot / reset /
// verify-email per HANDOFF §4.3. Editorial pane (deep purple gradient with
// a customer testimonial + compliance row) on the left, form pane on the
// right with kicker → title → subtitle → form children → footer.
export const AuthShell = ({
  kicker,
  title,
  sub,
  children,
  footer,
  topRight = null,
}) => (
  <div className="mc-auth">
    <aside className="mc-auth-editorial">
      <div className="mc-auth-editorial-bg" aria-hidden="true" />
      <div className="mc-auth-editorial-logo">
        <TaaliTile
          className="h-7 w-7 rounded-[7px]"
          fillClassName="text-white"
          lineClassName="text-[var(--purple)]"
          strokeWidth={2.4}
          cornerRadius={6.5}
        />
        <span>taali<span style={{ opacity: 0.7 }}>.</span></span>
      </div>
      <blockquote className="mc-auth-quote">
        <div className="mc-auth-quote-kicker">FROM THE TEAM</div>
        <p>
          &ldquo;We hired our first three engineers without a single take-home review meeting. The
          standing report did the work.&rdquo;
        </p>
        <cite>— Iris Park, Head of Talent · Linear</cite>
      </blockquote>
      <div className="mc-auth-compliance">
        <span>SOC 2 TYPE II</span>
        <span>·</span>
        <span>EU/US DATA RESIDENCY</span>
        <span>·</span>
        <span>GDPR</span>
      </div>
    </aside>
    <main className="mc-auth-form-pane">
      <div className="mc-auth-top-right">{topRight}</div>
      <div className="mc-auth-form">
        {kicker ? <div className="mc-kicker">{kicker}</div> : null}
        {title ? <h1 className="mc-auth-title">{title}</h1> : null}
        {sub ? <p className="mc-auth-sub">{sub}</p> : null}
        {children}
      </div>
      {footer ? <div className="mc-auth-footer">{footer}</div> : null}
    </main>
  </div>
);

export const AuthField = ({
  label,
  type = 'text',
  value,
  defaultValue,
  onChange,
  placeholder,
  helper,
  error,
  autoFocus = false,
  autoComplete,
  name,
  id,
  required,
}) => {
  const fieldId = id || (name ? `auth-${name}` : undefined);
  return (
    <div className="mc-auth-field">
      {label ? (
        <label htmlFor={fieldId} className="mc-auth-field-label">
          {label}
        </label>
      ) : null}
      <input
        id={fieldId}
        name={name}
        type={type}
        value={value}
        defaultValue={defaultValue}
        onChange={onChange}
        placeholder={placeholder}
        autoFocus={autoFocus}
        autoComplete={autoComplete}
        required={required}
        className={`mc-auth-input ${error ? 'is-error' : ''}`.trim()}
        aria-invalid={error ? 'true' : undefined}
      />
      {error ? (
        <div className="mc-auth-field-error">{error}</div>
      ) : helper ? (
        <div className="mc-auth-field-helper">{helper}</div>
      ) : null}
    </div>
  );
};

export default AuthShell;
