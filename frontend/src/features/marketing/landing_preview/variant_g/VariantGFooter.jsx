import React from 'react';

import { TaaliLogo } from '../../../../shared/layout/TaaliLayout';
import { PageLink } from '../../../../shared/ui/PageLink';
import { FOOTER_COLS } from './variantG.data';

// Footer — real Taali logo + brand blurb + honest link columns + a bottom bar.
// Every link resolves to a real destination (no dead #-only links, no pages that
// don't exist): in-page section anchors scroll via onSection; route links render
// as real <a href> through <PageLink> (ctrl/cmd-click friendly + SPA nav); the
// contact is a mailto. Collapses 4→2 columns below 880px (CSS).

const FootLink = ({ link, onSection }) => {
  if (link.section) {
    return (
      <a
        href={`#${link.section}`}
        onClick={(e) => {
          e.preventDefault();
          if (onSection) onSection(link.section);
        }}
      >
        {link.label}
      </a>
    );
  }
  if (link.page) {
    return <PageLink page={link.page}>{link.label}</PageLink>;
  }
  return <a href={link.href}>{link.label}</a>;
};

export const VariantGFooter = ({ onSection }) => (
  <footer>
    <div className="wrap">
      <div className="foot-grid">
        <div className="foot-brand">
          <div className="brand">
            <TaaliLogo page="landing" />
          </div>
          <p>
            The agentic hiring platform. One governed agent runs your funnel — you decide every call
            that matters.
          </p>
        </div>
        {FOOTER_COLS.map((col) => (
          <div className="foot-col" key={col.head}>
            <h5>{col.head}</h5>
            <ul>
              {col.links.map((link) => (
                <li key={link.label}>
                  <FootLink link={link} onSection={onSection} />
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      <div className="foot-bottom">
        <span>© 2026 Taali</span>
      </div>
    </div>
  </footer>
);

export default VariantGFooter;
