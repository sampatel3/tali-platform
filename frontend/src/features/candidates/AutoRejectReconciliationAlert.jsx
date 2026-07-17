import React from 'react';

import {
  AtsReconciliationPanel,
  hasExactAtsResolution,
  needsAtsReconciliation,
} from './AtsReconciliationPanel';

const RECEIPT_KEY = 'auto_reject_operation';

export const needsAutoRejectReconciliation = (application) => {
  const receipt = application?.integration_sync_state?.[RECEIPT_KEY];
  if (needsAtsReconciliation(receipt, RECEIPT_KEY)) return true;
  return String(application?.auto_reject_state || '').trim().toLowerCase()
    === 'manual_reconciliation_required'
    && !hasExactAtsResolution(receipt, RECEIPT_KEY);
};

export const AutoRejectReconciliationAlert = ({ application }) => {
  if (!needsAutoRejectReconciliation(application)) return null;
  const receipt = application?.integration_sync_state?.[RECEIPT_KEY] || {};
  const provider = String(receipt?.provider || '').trim().toLowerCase();
  const providerLabel = provider === 'bullhorn'
    ? 'Bullhorn'
    : provider === 'workable'
      ? 'Workable'
      : 'The ATS';
  const legacyReceipt = needsAtsReconciliation(receipt, RECEIPT_KEY)
    ? receipt
    : {
      ...receipt,
      status: 'manual_reconciliation_required',
      manual_reconciliation_required: true,
    };
  const state = { [RECEIPT_KEY]: legacyReceipt };
  return <AtsReconciliationPanel
    application={{ ...application, integration_sync_state: state }}
    canMutate={false}
    heading={`${providerLabel} rejection needs manual reconciliation.`}
  />;
};

export default AutoRejectReconciliationAlert;
