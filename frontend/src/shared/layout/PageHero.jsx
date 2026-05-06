import React from 'react';

// Hero block at the top of every recruiter page. Pattern from HANDOFF §3.2:
//   01 · KICKER LABEL                 (mono, uppercase, --purple)
//   Page title<purple period>.        (60px display, weight 600, -0.03em)
//   One-line subtitle                 (15px --mute)
//
// Pages can pass `title` as a string (a trailing purple period is appended
// automatically) or as a React node (e.g. `<>5 active <em>roles</em></>`)
// to compose `<em>` highlights or skip the period.
export const PageHero = ({
  kicker,
  title,
  subtitle,
  actions,
  period = true,
  children,
}) => {
  const renderTitle = () => {
    if (title == null || title === '') return null;
    return (
      <h1 className="mc-h-display">
        {title}
        {period ? <span className="mc-period">.</span> : null}
      </h1>
    );
  };

  return (
    <header className="mc-page-head">
      <div>
        {kicker ? <div className="mc-kicker">{kicker}</div> : null}
        {renderTitle()}
        {subtitle ? <p className="mc-subtitle">{subtitle}</p> : null}
        {children}
      </div>
      {actions ? <div className="mc-page-head-actions">{actions}</div> : null}
    </header>
  );
};

export default PageHero;
