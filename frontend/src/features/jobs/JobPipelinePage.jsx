import React from 'react';
import { useParams } from 'react-router-dom';

import { CandidatesDirectoryPage } from '../candidates/CandidatesDirectoryPage';

export const JobPipelinePage = ({ onNavigate, onViewCandidate, NavComponent = null }) => {
  const { roleId } = useParams();
  void onViewCandidate;
  return (
    <CandidatesDirectoryPage
      onNavigate={onNavigate}
      NavComponent={NavComponent}
      lockRoleId={roleId || null}
      useRolePipelineEndpoint
      navCurrentPage="jobs"
      title="Role pipeline"
      subtitle="Review candidates by stage and take action without leaving the role workspace."
    />
  );
};

export default JobPipelinePage;
