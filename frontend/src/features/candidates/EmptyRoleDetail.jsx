import React from 'react';
import { Plus } from 'lucide-react';

import { Button, EmptyState } from '../../shared/ui/TaaliPrimitives';

export const EmptyRoleDetail = ({ onCreateRole }) => (
  <EmptyState
    title="No role selected"
    description="Create a role to start managing candidates."
    action={(
      <Button type="button" variant="primary" size="sm" onClick={onCreateRole}>
        <Plus size={15} />
        Create your first role
      </Button>
    )}
  />
);
