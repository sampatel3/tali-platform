import React from 'react';

import './KpiStrip.css';

// Shared KPI tile + strip used by the home hub and the jobs list so the two
// surfaces are visually identical — one source of truth for the tile look, so
// they can't drift apart. A tile is: a mono label, a big value (+ optional
// muted unit, + purple emphasis when `emph`), an optional budget bar
// ({ pct, over }, e.g. the object returned by metrics.budgetTile), and a
// sub-line. `columns` fixes the wide-screen column count (default 4) so the
// row stays one band instead of wrapping.
export const KpiTile = ({ label, value, unit = null, emph = false, bar = null, sub = null, subTitle = null }) => (
  <div className={`kpi-tile${emph ? ' is-emph' : ''}`}>
    <div className="kpi-l">{label}</div>
    <div className="kpi-v">
      <span className={emph ? 'kpi-accent' : undefined}>{value}</span>
      {unit ? <span className="kpi-unit"> {unit}</span> : null}
    </div>
    {bar ? (
      <div className="kpi-bar" aria-hidden="true">
        <i style={{ width: `${bar.pct}%`, background: bar.over ? 'var(--red)' : 'var(--purple)' }} />
      </div>
    ) : null}
    {sub != null ? <div className="kpi-d" title={subTitle || undefined}>{sub}</div> : null}
  </div>
);

export const KpiStrip = ({ tiles, columns = 4 }) => (
  <div className="kpi-strip" style={{ '--kpi-cols': columns }}>
    {tiles.filter(Boolean).map((tile) => (
      <KpiTile key={tile.key || tile.label} {...tile} />
    ))}
  </div>
);
