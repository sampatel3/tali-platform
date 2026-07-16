import React from 'react';

export const OWNER_SETTINGS_MESSAGE =
  'Only a workspace owner can change these settings. You can still review the current configuration.';

const FIELDSET_STYLE = {
  border: 0,
  margin: 0,
  minWidth: 0,
  padding: 0,
};

export const OwnerOnlyNotice = ({ children = OWNER_SETTINGS_MESSAGE }) => (
  <div className="settings-inline-note settings-top-gap" role="note">
    {children}
  </div>
);

export const OwnerOnlyFieldset = ({ canManage, children, message = OWNER_SETTINGS_MESSAGE }) => (
  <>
    {!canManage ? <OwnerOnlyNotice>{message}</OwnerOnlyNotice> : null}
    <fieldset disabled={!canManage} aria-disabled={!canManage} style={FIELDSET_STYLE}>
      {children}
    </fieldset>
  </>
);
