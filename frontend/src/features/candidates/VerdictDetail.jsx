// VerdictDetail — two distinct recruiter reads: the causal decision explanation
// and a compact candidate synthesis. Raw rule-path codes stay in audit evidence;
// the API turns them into human language before this component renders them.
import React from 'react';

import { DecisionNarrative } from '../../shared/decisions/DecisionNarrative';

export const VerdictDetail = ({ decision = null }) => {
  if (!decision) return null;
  return <DecisionNarrative decision={decision} />;
};

export default VerdictDetail;
