import React, { useEffect, useState } from 'react';
import { Trash2 } from 'lucide-react';

import { hiringTeam as hiringTeamApi, team as teamApi } from '../../shared/api';
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Select,
  Spinner,
} from '../../shared/ui/TaaliPrimitives';

const TEAM_ROLES = [
  { value: 'hiring_manager', label: 'Hiring manager' },
  { value: 'recruiter', label: 'Recruiter' },
  { value: 'interviewer', label: 'Interviewer' },
  { value: 'coordinator', label: 'Coordinator' },
];
const roleLabel = (v) => TEAM_ROLES.find((r) => r.value === v)?.label || v;

export const HiringTeamPanel = ({ roleId }) => {
  const [members, setMembers] = useState(null);
  const [orgUsers, setOrgUsers] = useState([]);
  const [error, setError] = useState(null);
  const [pickUser, setPickUser] = useState('');
  const [pickRole, setPickRole] = useState('interviewer');
  const [saving, setSaving] = useState(false);

  const reload = () => hiringTeamApi.list(roleId).then(setMembers);

  useEffect(() => {
    let cancelled = false;
    Promise.all([hiringTeamApi.list(roleId), teamApi.list()])
      .then(([team, usersRes]) => {
        if (cancelled) return;
        setMembers(team);
        setOrgUsers(Array.isArray(usersRes?.data) ? usersRes.data : []);
      })
      .catch(() => { if (!cancelled) setError('Failed to load the hiring team.'); });
    return () => { cancelled = true; };
  }, [roleId]);

  const addMember = async () => {
    if (!pickUser) return;
    setSaving(true);
    setError(null);
    try {
      await hiringTeamApi.set(roleId, { user_id: Number(pickUser), team_role: pickRole });
      await reload();
      setPickUser('');
    } catch {
      setError('Could not add that member.');
    } finally {
      setSaving(false);
    }
  };

  const removeMember = async (userId) => {
    setError(null);
    try {
      await hiringTeamApi.remove(roleId, userId);
      await reload();
    } catch {
      setError('Could not remove that member.');
    }
  };

  if (members === null && !error) {
    return <div className="flex justify-center py-10"><Spinner /></div>;
  }

  const userName = (u) => u.full_name || u.name || u.email;

  return (
    <div className="space-y-4 py-2">
      {error ? (
        <Card className="border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] px-3 py-2 text-sm text-[var(--taali-danger)]">
          {error}
        </Card>
      ) : null}

      <Card className="px-4 py-4">
        <h3 className="text-sm font-semibold text-[var(--taali-text)]">Add to hiring team</h3>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="block min-w-[200px] flex-1">
            <span className="mb-1 block text-xs text-[var(--taali-muted)]">Team member</span>
            <Select value={pickUser} onChange={(e) => setPickUser(e.target.value)}>
              <option value="">Select a user…</option>
              {orgUsers.map((u) => <option key={u.id} value={u.id}>{userName(u)}</option>)}
            </Select>
          </label>
          <label className="block min-w-[160px]">
            <span className="mb-1 block text-xs text-[var(--taali-muted)]">Role</span>
            <Select value={pickRole} onChange={(e) => setPickRole(e.target.value)}>
              {TEAM_ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
            </Select>
          </label>
          <Button variant="primary" disabled={!pickUser || saving} onClick={addMember}>Add</Button>
        </div>
      </Card>

      {(members || []).length === 0 ? (
        <EmptyState title="No hiring team yet" description="Add a hiring manager or interviewers to this role." className="py-8" />
      ) : (
        <div className="space-y-2">
          {members.map((m) => (
            <Card key={m.user_id} className="flex items-center justify-between gap-3 px-4 py-3">
              <div className="min-w-0">
                <div className="truncate text-sm font-medium text-[var(--taali-text)]">{m.name || m.email}</div>
                {m.name ? <div className="truncate text-xs text-[var(--taali-muted)]">{m.email}</div> : null}
              </div>
              <div className="flex shrink-0 items-center gap-3">
                <Badge variant="info">{roleLabel(m.team_role)}</Badge>
                <button
                  type="button"
                  onClick={() => removeMember(m.user_id)}
                  className="text-[var(--taali-muted)] hover:text-[var(--taali-danger)]"
                  aria-label="Remove"
                >
                  <Trash2 size={16} />
                </button>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
};

export default HiringTeamPanel;
