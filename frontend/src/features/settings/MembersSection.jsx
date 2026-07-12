import React, { useState } from 'react';

import { organizations as orgsApi, team as teamApi } from '../../shared/api';
import { getErrorMessage } from '../../shared/getErrorMessage';

const initialsFor = (value) => {
  const letters = String(value || '')
    .split(/\s+/)
    .filter(Boolean)
    .map((part) => part[0])
    .join('');
  return (letters.slice(0, 2) || 'U').toUpperCase();
};

// MembersSection — the "Members" settings tab body: invite form, member list
// with Resend invite / Revoke / Remove row actions, and the Access
// (allowed-domains) block that renders in the same panel. Extracted from
// RecruiterSettingsPage to keep that page under the architecture-gate line cap.
// Behaviour is identical to the inline version; state that only the section
// touches (invite form + per-row action state) lives here, while shared state
// (teamMembers, accessForm, orgData) is passed down as props.
const MembersSection = ({
  SectionPanel,
  teamMembers,
  setTeamMembers,
  showToast,
  userEmail,
  isOwner,
  accessForm,
  setAccessForm,
  accessSaving,
  setAccessSaving,
  setOrgData,
}) => {
  const [inviteName, setInviteName] = useState('');
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteLoading, setInviteLoading] = useState(false);
  // Per-member action state: which row is mid-request, and which row has an
  // armed inline "Confirm" for a revoke/remove (a two-step swap, no modal).
  const [memberActionId, setMemberActionId] = useState(null);
  const [confirmRemoveId, setConfirmRemoveId] = useState(null);

  const handleInvite = async (event) => {
    event.preventDefault();
    const name = inviteName.trim();
    const email = inviteEmail.trim();
    // Show an error instead of silently doing nothing on an empty/invalid form.
    if (!name || !email) {
      showToast('Enter both a name and an email address to invite a member.', 'error');
      return;
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      showToast('Enter a valid email address (e.g. alex@company.com).', 'error');
      return;
    }
    setInviteLoading(true);
    try {
      const res = await teamApi.invite({
        email,
        full_name: name,
      });
      setTeamMembers((prev) => [...prev, res?.data].filter(Boolean));
      setInviteName('');
      setInviteEmail('');
      // The row is added either way; only the toast changes. Re-inviting a
      // previously removed VERIFIED member restores them directly (status
      // 'active', no email sent — they already have a password), so the
      // email-failure warning only applies to genuinely pending invites.
      if (res?.data?.status === 'active') {
        showToast('Member restored.', 'success');
      } else if (res?.data?.email_sent === false) {
        showToast('Invite created, but the email could not be sent. Use Resend invite.', 'warning');
      } else {
        showToast('Invite sent.', 'success');
      }
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to invite team member.'), 'error');
    } finally {
      setInviteLoading(false);
    }
  };

  const handleResendInvite = async (member) => {
    if (!member?.id) return;
    setMemberActionId(member.id);
    try {
      await teamApi.resendInvite(member.id);
      showToast('Invite resent.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to resend invite.'), 'error');
    } finally {
      setMemberActionId(null);
    }
  };

  // Recovery path for a pending invite whose email failed to send: mint a
  // fresh accept-invite link and copy it so the admin can deliver it manually
  // (Slack, etc.). Non-destructive, so no inline confirm step.
  const handleCopyInviteLink = async (member) => {
    if (!member?.id) return;
    if (!navigator.clipboard?.writeText) {
      showToast('Could not copy the invite link.', 'warning');
      return;
    }
    setMemberActionId(member.id);
    try {
      const res = await teamApi.inviteLink(member.id);
      await navigator.clipboard.writeText(res.data.accept_link);
      showToast('Invite link copied.', 'success');
    } catch (error) {
      showToast('Could not copy the invite link.', 'warning');
    } finally {
      setMemberActionId(null);
    }
  };

  // Backs both "Revoke" (pending invite) and "Remove" (active member) — the
  // backend DELETE handles both. `wasInvited` only picks the success toast copy.
  const handleRemoveMember = async (member, { wasInvited } = {}) => {
    if (!member?.id) return;
    setMemberActionId(member.id);
    try {
      await teamApi.remove(member.id);
      setTeamMembers((prev) => prev.filter((m) => m.id !== member.id));
      setConfirmRemoveId(null);
      showToast(wasInvited ? 'Invite revoked.' : 'Member removed.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, wasInvited ? 'Failed to revoke invite.' : 'Failed to remove member.'), 'error');
    } finally {
      setMemberActionId(null);
    }
  };

  const handleSetRole = async (member, nextRole) => {
    if (!member?.id) return;
    setMemberActionId(member.id);
    try {
      const res = await teamApi.setRole(member.id, nextRole);
      const updated = res?.data;
      setTeamMembers((prev) => prev.map((m) => (m.id === member.id ? { ...m, ...(updated || { role: nextRole }) } : m)));
      showToast(nextRole === 'owner'
        ? `${member.full_name || member.email} is now an owner.`
        : `${member.full_name || member.email} is now a member.`, 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to update member role.'), 'error');
    } finally {
      setMemberActionId(null);
    }
  };

  const handleSaveAccess = async () => {
    setAccessSaving(true);
    const domains = String(accessForm.allowedEmailDomains || '')
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean);
    try {
      const res = await orgsApi.update({
        allowed_email_domains: domains,
      });
      setOrgData(res?.data || null);
      showToast('Roles and access settings saved.', 'success');
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to save roles and access settings.'), 'error');
    } finally {
      setAccessSaving(false);
    }
  };

  return (
    <SectionPanel
      id="members"
      title="Members"
      subtitle={`${teamMembers.length} ${teamMembers.length === 1 ? 'person' : 'people'} in this workspace.`}
    >
      {isOwner ? (
        <form className="settings-invite-form" onSubmit={handleInvite}>
          <label className="field">
            <span className="k">Full name</span>
            <input value={inviteName} onChange={(event) => setInviteName(event.target.value)} placeholder="Alex Weston" />
          </label>
          <label className="field">
            <span className="k">Email</span>
            <input type="email" required value={inviteEmail} onChange={(event) => setInviteEmail(event.target.value)} placeholder="alex@company.com" />
          </label>
          <div className="settings-member-actions">
            <button type="submit" className="btn btn-purple btn-sm" disabled={inviteLoading}>
              {inviteLoading ? 'Inviting...' : '+ Invite member'}
            </button>
          </div>
        </form>
      ) : (
        <div className="settings-inline-note">
          Only a workspace owner can invite members.
        </div>
      )}
      {/* Two roles, deliberately simple: Owner (manages members + access
          settings) and Member. Unverified members render as "Invited".
          Owners get per-row invite-lifecycle actions plus promote/demote. */}
      <div className="members">
        {teamMembers.map((member) => {
          const isSelf = member?.email === userEmail;
          // A member is "invited" (pending) when the server marks
          // status='invited'; fall back to the legacy verified
          // inference for older payloads that lack the field.
          const invited = member?.status
            ? member.status === 'invited'
            : !member?.is_email_verified;
          const memberRole = String(member?.role || '').toLowerCase();
          const role = invited
            ? 'Invited'
            : (memberRole === 'owner' ? 'Owner' : 'Member');
          const busy = memberActionId === member.id;
          const confirming = confirmRemoveId === member.id;
          const showRemove = isOwner && !isSelf;
          return (
            <div key={member.id} className="mb">
              <div className={`av${invited ? ' inv' : ''}`}>{initialsFor(member.full_name || member.email)}</div>
              <div className="who">
                <b>{member.full_name || member.email}</b>
                <div>{isSelf ? 'you' : (member?.email || '—')}</div>
              </div>
              <span className={`chip${invited ? '' : ' purple'}`}>{role}</span>
              {showRemove ? (
                <div className="settings-member-row-actions">
                  {confirming ? (
                    <>
                      <button
                        type="button"
                        className="taali-text-btn taali-text-btn-danger settings-member-link settings-member-link-danger"
                        onClick={() => handleRemoveMember(member, { wasInvited: invited })}
                        disabled={busy}
                      >
                        {busy ? 'Removing...' : 'Confirm'}
                      </button>
                      <button
                        type="button"
                        className="taali-text-btn settings-member-link"
                        onClick={() => setConfirmRemoveId(null)}
                        disabled={busy}
                      >
                        Cancel
                      </button>
                    </>
                  ) : (
                    <>
                      {invited ? (
                        <>
                          <button
                            type="button"
                            className="taali-text-btn settings-member-link"
                            onClick={() => handleResendInvite(member)}
                            disabled={busy}
                          >
                            {busy ? 'Sending...' : 'Resend invite'}
                          </button>
                          <button
                            type="button"
                            className="taali-text-btn settings-member-link"
                            onClick={() => handleCopyInviteLink(member)}
                            disabled={busy}
                          >
                            Copy link
                          </button>
                        </>
                      ) : (
                        <button
                          type="button"
                          className="taali-text-btn settings-member-link"
                          onClick={() => handleSetRole(member, memberRole === 'owner' ? 'member' : 'owner')}
                          disabled={busy}
                        >
                          {busy ? 'Saving...' : memberRole === 'owner' ? 'Make member' : 'Make owner'}
                        </button>
                      )}
                      <button
                        type="button"
                        className="taali-text-btn taali-text-btn-danger settings-member-link settings-member-link-danger"
                        onClick={() => setConfirmRemoveId(member.id)}
                        disabled={busy}
                      >
                        {invited ? 'Revoke' : 'Remove'}
                      </button>
                    </>
                  )}
                </div>
              ) : null}
            </div>
          );
        })}
        {teamMembers.length === 0 ? (
          <div className="settings-empty-state">
            No team members yet.
          </div>
        ) : null}
      </div>

      {/* Access — preview shows this as its own flat divider-led
          section ("Access" / "Limit who can join by email
          domain."). The summary card stays (live-derived, useful)
          but the section now carries the matching heading. */}
      <div className="settings-subcard settings-top-gap">
        <div className="settings-subcard-head">
          <div>
            <h3>Access</h3>
            <p>Limit who can join this workspace by email domain.</p>
          </div>
        </div>
        <div className="row-form">
          <label className="field">
            <span className="k">Allowed email domains (comma separated)</span>
            <input
              value={accessForm.allowedEmailDomains}
              onChange={(event) => setAccessForm((prev) => ({ ...prev, allowedEmailDomains: event.target.value }))}
              placeholder="company.com, subsidiary.org"
            />
          </label>
          <div className="settings-summary-card">
            <div className="settings-summary-label">Current access model</div>
            <div className="settings-summary-value">{teamMembers.length || 0} members</div>
            <div className="settings-summary-note">
              {accessForm.allowedEmailDomains.trim()
                ? `Invites limited to ${accessForm.allowedEmailDomains}.`
                : 'Invites are currently open to any verified domain.'}
            </div>
          </div>
        </div>
        <div className="settings-save-row">
          <div className="settings-inline-note">
            {isOwner
              ? 'Team invites respect the allowed domain list immediately.'
              : 'Only a workspace owner can change access settings.'}
          </div>
          <button type="button" className="btn btn-purple btn-sm" onClick={handleSaveAccess} disabled={accessSaving || !isOwner}>
            {accessSaving ? 'Saving...' : 'Save access settings'}
          </button>
        </div>
      </div>
    </SectionPanel>
  );
};

export default MembersSection;
