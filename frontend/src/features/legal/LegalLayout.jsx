import React from 'react';
import { useLocation } from 'react-router-dom';

import { TaaliLogo } from '../../shared/layout/TaaliLayout';
import { PageLink } from '../../shared/ui/PageLink';
import './legal.css';

// Shared chrome for the three public legal pages (/privacy, /terms,
// /subprocessors). Text-first, light theme, purple tones only. The sticky
// header reuses the shared `.app-nav` bar: the Taali logo links home and the
// `.legal-tabs` switch between the three pages. Makes NO API calls — every
// dependency here (logo, links) is presentational, so a logged-out visit is
// safe.

const LEGAL_TABS = [
  { page: 'privacy', label: 'Privacy', path: '/privacy' },
  { page: 'terms', label: 'Terms', path: '/terms' },
  { page: 'subprocessors', label: 'Subprocessors', path: '/subprocessors' },
];

export const LegalLayout = ({ kicker, title, updated, children }) => {
  const { pathname } = useLocation();

  return (
    <div className="legal-wrap">
      <div className="app-nav">
        <div className="app-nav-inner">
          <TaaliLogo page="landing" />
          <nav className="legal-tabs" aria-label="Legal pages">
            {LEGAL_TABS.map((tab) => (
              <PageLink
                key={tab.page}
                page={tab.page}
                className={`legal-tab${pathname === tab.path ? ' is-active' : ''}`}
                aria-current={pathname === tab.path ? 'page' : undefined}
              >
                {tab.label}
              </PageLink>
            ))}
          </nav>
        </div>
      </div>

      <div className="legal-container">
        <header className="legal-head">
          {kicker ? <div className="legal-kicker">{kicker}</div> : null}
          <h1>{title}</h1>
          {updated ? <div className="legal-meta">Last updated: {updated}</div> : null}
        </header>

        <main className="legal-prose">{children}</main>

        <footer className="legal-footer">
          <span>© {new Date().getFullYear()} Taali</span>
          <span className="sep">·</span>
          <PageLink page="privacy">Privacy</PageLink>
          <span className="sep">·</span>
          <PageLink page="terms">Terms</PageLink>
          <span className="sep">·</span>
          <PageLink page="subprocessors">Subprocessors</PageLink>
          <span className="sep">·</span>
          <a href="mailto:hello@taali.ai">hello@taali.ai</a>
        </footer>
      </div>
    </div>
  );
};

export default LegalLayout;
