import React from 'react';
import { Card, cx } from './TaaliPrimitives';

export const ScoringCardGrid = ({ items, className = '', cardClassName = '' }) => (
  <div className={cx('grid md:grid-cols-2 lg:grid-cols-3 gap-4', className)}>
    {items.map((item) => (
      <Card key={item.key || item.title} className={cx('p-4', cardClassName)}>
        <h4 className="text-base font-bold text-gray-900">{item.title}</h4>
        <p className="mt-1 text-sm text-gray-600">{item.description}</p>
      </Card>
    ))}
  </div>
);
