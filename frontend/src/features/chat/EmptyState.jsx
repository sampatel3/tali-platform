import React from 'react';
import { ChatEmptyState } from '../../shared/chat';

// Suggestion content matches the design's "Triage · Senior Backend" pattern:
// a category tag (mono kicker) above a natural-language question that reads
// like something a recruiter actually does, not the underlying tool name.
const SUGGESTIONS = [
  { tag: 'Triage · Senior Backend', q: 'Find me a Senior Backend Engineer with 5+ years and Python expertise' },
  { tag: 'Pipeline · Review stage', q: 'Who is currently in review across all open roles?' },
  { tag: 'Compare · Top three', q: 'Compare the top 3 candidates for the Senior Engineer role' },
  { tag: 'Graph · Skills + companies', q: 'Anyone who worked at a YC company and knows Postgres?' },
  { tag: 'Roles · Pipeline counts', q: 'Show me every active role and how many candidates are in each pipeline stage' },
  { tag: 'CV · Highest scoring', q: 'Pull the CV details for the highest-scoring candidate this month' },
];

const EmptyState = ({ onPick }) => (
  <ChatEmptyState
    title={<>What are you looking for<em>?</em></>}
    sub="Ask in plain language. Taali can search your candidates, compare them, walk through reject reasons, or explore the skills graph. Citations link back to the underlying record."
    suggestions={SUGGESTIONS}
    onPick={onPick}
  />
);

export default EmptyState;
