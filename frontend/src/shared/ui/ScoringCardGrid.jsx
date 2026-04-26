import React from 'react';
import { Card, cx } from './TaaliPrimitives';

export const ScoringCardGrid = ({ items, className = '', cardClassName = '' }) => (
  <div className={cx('grid md:grid-cols-2 lg:grid-cols-3 gap-4', className)}>
    {items.map((item) => (
      <Card key={item.key || item.title} className={cx('taali-landing-score-card h-full p-6 md:p-7', cardClassName)}>
        <h4 className="taali-landing-score-card-title">{item.title}</h4>
        <p className="taali-landing-score-card-body mt-3">{item.description}</p>
      </Card>
    ))}
  </div>
);
