import { render, screen } from '@testing-library/react';
import { FileText } from 'lucide-react';
import { describe, expect, it } from 'vitest';

import { ChatArtifact, ChatSurface } from './index';

describe('ChatArtifact', () => {
  it('provides one accessible hierarchy for status, body, and actions', () => {
    render(
      <ChatSurface density="compact">
        <ChatArtifact
          eyebrow="Grounded shortlist"
          title="Top 5 candidates"
          summary="Matched against two must-haves"
          meta="5 of 42 shown"
          status={{ label: 'Partial evidence', detail: '4 of 5 evidence checks completed', tone: 'warning' }}
          icon={FileText}
          footer={<a href="/report/demo">Open report</a>}
        >
          <p>Candidate evidence</p>
        </ChatArtifact>
      </ChatSurface>,
    );

    const artifact = screen.getByRole('region', { name: 'Top 5 candidates' });
    expect(artifact).toHaveAttribute('data-artifact-status', 'warning');
    expect(screen.getByText('Partial evidence')).toBeInTheDocument();
    expect(screen.getByText('4 of 5 evidence checks completed')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open report' })).toHaveAttribute('href', '/report/demo');
  });
});
