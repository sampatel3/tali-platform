import React from 'react';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { AssessmentResultDeliveryPanel } from './AssessmentResultDeliveryPanel';

const assessment = (overrides = {}) => ({
  id: 42,
  workable_result_delivery: {
    status: 'manual_reconciliation_required',
    operation_id: 'safe-operation-id',
    provider_attempts: 1,
    publish_attempts: 2,
    configuration_attempts: 0,
    provider_outcome_uncertain: true,
    last_error_code: 'workable_network_error',
    reconciliation_required: true,
    can_reconcile: true,
    ...overrides,
  },
});

const deferred = () => {
  let resolve;
  const promise = new Promise((resolvePromise) => { resolve = resolvePromise; });
  return { promise, resolve };
};

describe('AssessmentResultDeliveryPanel', () => {
  it('shows safe status to members but keeps recovery owner-only', () => {
    render(
      <AssessmentResultDeliveryPanel
        assessment={assessment({ can_reconcile: false })}
        assessmentsApi={{}}
      />,
    );

    expect(screen.getByRole('alert')).toHaveTextContent('Delivery outcome needs verification');
    expect(screen.getByText(/workspace owner must verify/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Authorize one new/i })).toBeNull();
    expect(screen.queryByText(/candidate-|member-|access token/i)).toBeNull();
  });

  it('cannot retry until absence is explicitly attested', async () => {
    const reconcileWorkableResultDelivery = vi.fn().mockResolvedValue({
      data: { status: 'pending' },
    });
    const onResolved = vi.fn();
    render(
      <AssessmentResultDeliveryPanel
        assessment={assessment()}
        assessmentsApi={{ reconcileWorkableResultDelivery }}
        onResolved={onResolved}
      />,
    );
    const retry = screen.getByRole('button', { name: /Authorize one new delivery/i });
    expect(retry).toBeDisabled();

    fireEvent.click(screen.getByLabelText(/confirmed this exact assessment result is absent/i));
    expect(retry).toBeEnabled();
    fireEvent.click(retry);

    await waitFor(() => expect(reconcileWorkableResultDelivery).toHaveBeenCalledWith(42, {
      action: 'retry_after_provider_absence',
      expected_operation_id: 'safe-operation-id',
      provider_result_present_attested: false,
      provider_result_absent_attested: true,
    }));
    expect(onResolved).toHaveBeenCalledWith({ status: 'pending' });
  });

  it('confirms an observed result without authorizing a send', async () => {
    const reconcileWorkableResultDelivery = vi.fn().mockResolvedValue({
      data: { status: 'confirmed', dispatch_status: 'not_sent' },
    });
    render(
      <AssessmentResultDeliveryPanel
        assessment={assessment()}
        assessmentsApi={{ reconcileWorkableResultDelivery }}
      />,
    );
    const confirm = screen.getByRole('button', { name: /Mark delivered without sending/i });
    expect(confirm).toBeDisabled();
    fireEvent.click(screen.getByLabelText(/confirmed this exact assessment result is present/i));
    fireEvent.click(confirm);

    await waitFor(() => expect(reconcileWorkableResultDelivery).toHaveBeenCalledWith(42, {
      action: 'confirm_delivered',
      expected_operation_id: 'safe-operation-id',
      provider_result_present_attested: true,
      provider_result_absent_attested: false,
    }));
  });

  it('keeps the successful mutation truthful when report revalidation fails', async () => {
    const reconcileWorkableResultDelivery = vi.fn().mockResolvedValue({
      data: {
        status: 'pending',
        operation_id: 'replacement-operation-id',
        workable_result_delivery: {
          status: 'pending',
          operation_id: 'replacement-operation-id',
          provider_attempts: 0,
          publish_attempts: 1,
          reconciliation_required: false,
          can_reconcile: false,
        },
      },
    });
    const onResolved = vi.fn().mockResolvedValue(false);
    render(
      <AssessmentResultDeliveryPanel
        assessment={assessment()}
        assessmentsApi={{ reconcileWorkableResultDelivery }}
        onResolved={onResolved}
      />,
    );

    fireEvent.click(screen.getByLabelText(/confirmed this exact assessment result is absent/i));
    fireEvent.click(screen.getByRole('button', { name: /Authorize one new delivery/i }));

    expect(await screen.findByText('Delivery queued')).toBeInTheDocument();
    expect(screen.getByText(
      /reconciliation was saved, but fresh report data could not be loaded/i,
    )).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Authorize one new delivery/i })).toBeNull();
    expect(reconcileWorkableResultDelivery).toHaveBeenCalledTimes(1);
    expect(onResolved).toHaveBeenCalledWith(expect.objectContaining({
      operation_id: 'replacement-operation-id',
      status: 'pending',
    }));
  });

  it('uses refreshed owner capabilities when operation identity and status stay unchanged', async () => {
    const reconcileWorkableResultDelivery = vi.fn().mockResolvedValue({
      data: {
        status: 'dispatch_failed',
        operation_id: 'safe-operation-id',
        workable_result_delivery: {
          status: 'dispatch_failed',
          operation_id: 'safe-operation-id',
          provider_attempts: 1,
          publish_attempts: 2,
          reconciliation_required: true,
        },
      },
    });

    function RefreshHarness() {
      const [currentAssessment, setCurrentAssessment] = React.useState(assessment({
        status: 'dispatch_failed',
      }));
      return (
        <AssessmentResultDeliveryPanel
          assessment={currentAssessment}
          assessmentsApi={{ reconcileWorkableResultDelivery }}
          onResolved={async () => {
            setCurrentAssessment(assessment({
              status: 'dispatch_failed',
              provider_attempts: 2,
              can_reconcile: true,
            }));
            return true;
          }}
        />
      );
    }

    render(<RefreshHarness />);
    fireEvent.click(screen.getByLabelText(/confirmed this exact assessment result is absent/i));
    fireEvent.click(screen.getByRole('button', { name: /Authorize one new delivery/i }));

    await waitFor(() => expect(screen.getByText(/Provider calls 2/)).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /Authorize one new delivery/i })).toBeDisabled();
    expect(screen.queryByText(/workspace owner must verify/i)).toBeNull();
  });

  it('accepts a later same-identity refresh after immediate revalidation failed', async () => {
    const reconcileWorkableResultDelivery = vi.fn().mockResolvedValue({
      data: {
        status: 'dispatch_failed',
        operation_id: 'safe-operation-id',
        workable_result_delivery: {
          status: 'dispatch_failed',
          operation_id: 'safe-operation-id',
          provider_attempts: 1,
          publish_attempts: 2,
          reconciliation_required: true,
          can_reconcile: false,
        },
      },
    });
    const onResolved = vi.fn().mockResolvedValue(false);
    const { rerender } = render(
      <AssessmentResultDeliveryPanel
        assessment={assessment({ status: 'dispatch_failed' })}
        assessmentsApi={{ reconcileWorkableResultDelivery }}
        onResolved={onResolved}
      />,
    );
    fireEvent.click(screen.getByLabelText(/confirmed this exact assessment result is absent/i));
    fireEvent.click(screen.getByRole('button', { name: /Authorize one new delivery/i }));
    expect(await screen.findByText(/fresh report data could not be loaded/i)).toBeInTheDocument();

    rerender(
      <AssessmentResultDeliveryPanel
        assessment={assessment({
          status: 'dispatch_failed',
          provider_attempts: 2,
          can_reconcile: true,
        })}
        assessmentsApi={{ reconcileWorkableResultDelivery }}
        onResolved={onResolved}
      />,
    );

    await waitFor(() => expect(screen.getByText(/Provider calls 2/)).toBeInTheDocument());
    expect(screen.queryByText(/fresh report data could not be loaded/i)).toBeNull();
    expect(screen.getByRole('button', { name: /Authorize one new delivery/i })).toBeDisabled();
  });

  it('renders same-identity server evidence before passive reset effects run', async () => {
    const reconcileWorkableResultDelivery = vi.fn().mockResolvedValue({
      data: {
        status: 'dispatch_failed',
        operation_id: 'safe-operation-id',
        workable_result_delivery: {
          status: 'dispatch_failed',
          operation_id: 'safe-operation-id',
          provider_attempts: 1,
          publish_attempts: 2,
          reconciliation_required: true,
          can_reconcile: false,
        },
      },
    });
    const observations = [];
    const onResolved = vi.fn().mockResolvedValue(false);

    function AuthorityProbe({ currentAssessment }) {
      const hostRef = React.useRef(null);
      React.useLayoutEffect(() => {
        observations.push(hostRef.current?.textContent || '');
      }, [currentAssessment]);
      return (
        <div ref={hostRef}>
          <AssessmentResultDeliveryPanel
            assessment={currentAssessment}
            assessmentsApi={{ reconcileWorkableResultDelivery }}
            onResolved={onResolved}
          />
        </div>
      );
    }

    const { rerender } = render(
      <AuthorityProbe currentAssessment={assessment({ status: 'dispatch_failed' })} />,
    );
    fireEvent.click(screen.getByLabelText(/confirmed this exact assessment result is absent/i));
    fireEvent.click(screen.getByRole('button', { name: /Authorize one new delivery/i }));
    expect(await screen.findByText(/fresh report data could not be loaded/i)).toBeInTheDocument();

    rerender(
      <AuthorityProbe currentAssessment={assessment({
        status: 'dispatch_failed',
        provider_attempts: 2,
        can_reconcile: true,
      })} />,
    );

    expect(observations.at(-1)).toContain('Provider calls 2');
    expect(observations.at(-1)).not.toContain('Provider calls 1');
  });

  it('drops an old reconciliation response after assessment operation identity changes', async () => {
    const oldRequest = deferred();
    const reconcileWorkableResultDelivery = vi.fn()
      .mockReturnValueOnce(oldRequest.promise)
      .mockResolvedValue({ data: { status: 'pending' } });
    const onResolved = vi.fn();
    const { rerender } = render(
      <AssessmentResultDeliveryPanel
        assessment={assessment({ operation_id: 'operation-a' })}
        assessmentsApi={{ reconcileWorkableResultDelivery }}
        onResolved={onResolved}
      />,
    );
    fireEvent.click(screen.getByLabelText(/confirmed this exact assessment result is absent/i));
    fireEvent.click(screen.getByRole('button', { name: /Authorize one new delivery/i }));

    rerender(
      <AssessmentResultDeliveryPanel
        assessment={{ ...assessment({ operation_id: 'operation-b' }), id: 43 }}
        assessmentsApi={{ reconcileWorkableResultDelivery }}
        onResolved={onResolved}
      />,
    );
    await waitFor(() => expect(
      screen.getByRole('button', { name: /Authorize one new delivery/i }),
    ).toBeDisabled());
    await act(async () => {
      oldRequest.resolve({ data: { status: 'pending', operation_id: 'operation-a' } });
      await oldRequest.promise;
    });

    expect(onResolved).not.toHaveBeenCalled();
    expect(screen.queryByText('Delivery queued')).toBeNull();
    fireEvent.click(screen.getByLabelText(/confirmed this exact assessment result is absent/i));
    fireEvent.click(screen.getByRole('button', { name: /Authorize one new delivery/i }));
    await waitFor(() => expect(reconcileWorkableResultDelivery).toHaveBeenLastCalledWith(43, {
      action: 'retry_after_provider_absence',
      expected_operation_id: 'operation-b',
      provider_result_present_attested: false,
      provider_result_absent_attested: true,
    }));
  });

  it('surfaces API failure and retains both guarded choices', async () => {
    const reconcileWorkableResultDelivery = vi.fn().mockRejectedValue({
      response: { data: { detail: 'The receipt changed. Refresh first.' } },
    });
    render(
      <AssessmentResultDeliveryPanel
        assessment={assessment()}
        assessmentsApi={{ reconcileWorkableResultDelivery }}
      />,
    );
    fireEvent.click(screen.getByLabelText(/confirmed this exact assessment result is absent/i));
    fireEvent.click(screen.getByRole('button', { name: /Authorize one new delivery/i }));

    expect(await screen.findByText('The receipt changed. Refresh first.')).toBeInTheDocument();
    expect(screen.getByLabelText(/confirmed this exact assessment result is present/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/confirmed this exact assessment result is absent/i)).toBeInTheDocument();
  });

  it('resets stale attestation when the visible operation changes', async () => {
    const reconcileWorkableResultDelivery = vi.fn().mockResolvedValue({
      data: { status: 'pending' },
    });
    const { rerender } = render(
      <AssessmentResultDeliveryPanel
        assessment={assessment({ operation_id: 'operation-a' })}
        assessmentsApi={{ reconcileWorkableResultDelivery }}
      />,
    );
    fireEvent.click(screen.getByLabelText(/confirmed this exact assessment result is absent/i));
    expect(screen.getByRole('button', { name: /Authorize one new delivery/i })).toBeEnabled();

    rerender(
      <AssessmentResultDeliveryPanel
        assessment={assessment({ operation_id: 'operation-b' })}
        assessmentsApi={{ reconcileWorkableResultDelivery }}
      />,
    );

    await waitFor(() => expect(
      screen.getByRole('button', { name: /Authorize one new delivery/i }),
    ).toBeDisabled());
    fireEvent.click(screen.getByLabelText(/confirmed this exact assessment result is absent/i));
    fireEvent.click(screen.getByRole('button', { name: /Authorize one new delivery/i }));
    await waitFor(() => expect(reconcileWorkableResultDelivery).toHaveBeenCalledWith(42, {
      action: 'retry_after_provider_absence',
      expected_operation_id: 'operation-b',
      provider_result_present_attested: false,
      provider_result_absent_attested: true,
    }));
  });

  it('labels dispatching delivery without presenting it as unavailable', () => {
    render(
      <AssessmentResultDeliveryPanel
        assessment={assessment({
          status: 'dispatching',
          reconciliation_required: false,
        })}
        assessmentsApi={{}}
      />,
    );

    expect(screen.getByRole('status')).toHaveTextContent('Delivery dispatching');
    expect(screen.queryByText(/status unavailable/i)).toBeNull();
  });
});
