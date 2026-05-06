import React from 'react';

// Mono uppercase eyebrow used inside cards, sections, and section heads.
// `tone` switches between purple (default brand-mechanic) and mute (for
// section-internal labels where purple would overpower).
export const Kicker = ({ tone = 'purple', children, className = '', ...rest }) => (
  <div
    className={`mc-kicker ${tone === 'mute' ? 'is-mute' : ''} ${className}`.trim()}
    {...rest}
  >
    {children}
  </div>
);

export default Kicker;
