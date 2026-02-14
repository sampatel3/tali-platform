import React from 'react';
import { BRAND } from '../../config/brand';

export const BrandGlyph = ({
  borderClass = 'border-black',
  sizeClass = 'w-10 h-10',
  markSizeClass = 'w-[1.8rem] h-[1.8rem]',
}) => (
  <div
    className={`${sizeClass} border-2 ${borderClass} flex items-center justify-center`}
    style={{ backgroundColor: 'var(--taali-purple)' }}
    aria-hidden="true"
  >
    <svg viewBox="0 0 24 24" className={markSizeClass} fill="none">
      <path
        d="M6 4.5v15M10 4.5v15M14 4.5v15M18 4.5v15M4 18.5L20 5.5"
        stroke="#FFFFFF"
        strokeWidth="2.5"
        strokeLinecap="round"
      />
    </svg>
  </div>
);

export const Logo = ({ onClick }) => (
  <div className="flex items-center gap-2 cursor-pointer" onClick={onClick}>
    <BrandGlyph />
    <span className="text-xl font-bold tracking-tight">{BRAND.name}</span>
  </div>
);
