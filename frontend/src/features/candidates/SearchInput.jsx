import React from 'react';
import { Search } from 'lucide-react';

import { Input } from '../../shared/ui/TaaliPrimitives';

export const SearchInput = ({ value, onChange, placeholder }) => (
  <div className="relative">
    <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
    <Input
      type="text"
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      className="pl-9"
    />
  </div>
);
