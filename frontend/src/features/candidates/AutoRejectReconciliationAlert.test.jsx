import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AutoRejectReconciliationAlert } from './AutoRejectReconciliationAlert';

describe('AutoRejectReconciliationAlert', () => {
  it('elevates a provider-success/local-authority drift as an alert', () => {
    render(<AutoRejectReconciliationAlert application={{
      auto_reject_state: 'manual_reconciliation_required',
      auto_reject_reason: 'Workable rejected the candidate while Taali preserved hired.',
      integration_sync_state: {
        auto_reject_operation: { provider: 'workable' },
      },
    }} />);

    expect(screen.getByRole('alert')).toHaveTextContent('Workable rejection needs manual reconciliation');
    expect(screen.getByRole('alert')).toHaveTextContent('Taali preserved hired');
  });

  it('elevates an unconfirmed provider outcome instead of implying a safe retry', () => {
    render(<AutoRejectReconciliationAlert application={{
      auto_reject_state: 'manual_reconciliation_required',
      auto_reject_reason: 'Bullhorn rejection could not be confirmed. Check both systems before retrying.',
      integration_sync_state: {
        auto_reject_operation: {
          provider: 'bullhorn',
          provider_called: null,
          provider_succeeded: null,
          provider_outcome_uncertain: true,
        },
      },
    }} />);

    expect(screen.getByRole('alert')).toHaveTextContent('Bullhorn rejection needs manual reconciliation');
    expect(screen.getByRole('alert')).toHaveTextContent('could not be confirmed');
    expect(screen.getByRole('alert')).toHaveTextContent('before retrying');
  });

  it('renders nothing for reconciled applications', () => {
    const { container } = render(<AutoRejectReconciliationAlert application={{
      auto_reject_state: 'rejected',
    }} />);

    expect(container).toBeEmptyDOMElement();
  });
});
