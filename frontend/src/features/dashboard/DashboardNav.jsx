import React from 'react';

import { AppNav } from '../../shared/layout/TaaliLayout';

export const DashboardNav = ({ currentPage, onNavigate }) => (
  <AppNav currentPage={currentPage} onNavigate={onNavigate} />
);
