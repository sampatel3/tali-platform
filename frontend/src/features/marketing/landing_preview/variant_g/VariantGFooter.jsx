import React from 'react';

import { FOOTER_COLS } from './variantG.data';

// Footer — brand blurb + 5 link columns (Product / Solutions / Resources /
// Company / Legal) + a bottom bar. Collapses 5→2 columns below 880px (CSS).
// Links scroll back to the top of the page via onSection('g-top').

export const VariantGFooter = ({ onSection }) => {
  const top = (e) => {
    e.preventDefault();
    if (onSection) onSection('g-top');
  };
  return (
    <footer>
      <div className="wrap">
        <div className="foot-grid">
          <div className="foot-brand">
            <div className="brand">
              <div className="brand-mark">t</div>
              <div className="brand-word">taali<span className="dot">.</span></div>
            </div>
            <p>
              The agentic hiring platform. One governed agent runs your funnel — you decide every call
              that matters.
            </p>
          </div>
          {Object.entries(FOOTER_COLS).map(([head, items]) => (
            <div className="foot-col" key={head}>
              <h5>{head}</h5>
              <ul>
                {items.map((i) => (
                  <li key={i}><a href="#g-top" onClick={top}>{i}</a></li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <div className="foot-bottom">
          <span>© 2026 TAALI, INC. · SAN FRANCISCO</span>
          <span>hello@taali.ai</span>
        </div>
      </div>
    </footer>
  );
};

export default VariantGFooter;
