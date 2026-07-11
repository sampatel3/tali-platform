import React, { useEffect, useMemo, useState } from 'react';

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
const roleLabel = (value) => TEAM_ROLES.find((r) => r.value === value)?.label || value;

export const HiringTeamPanel = ({ roleId }) => {
  const [members, setMembers] = useState(null);
  const [orgUsers, setOrgUsers] = useState([]);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [pick, setPick] = useState({ user_id: '', team_role: 'interviewer' });

  const reload = () => hiringTeamApi.list(roleId).then(setMembers);

  useEffect(() => {
    if (!roleId) return undefined;
    let cancelled = false;
    Promise.all([
      hiringTeamApi.list(roleId),
      teamApi.list().then((r) => (Array.isArray(r?.data) ? r.data : [])),
    ])
      .then(([team, users]) => {
        if (cancelled) return;
        setMembers(team);
        setOrgUsers(users);
      })
      .catch(() => {
        if (!cancelled) setError('Could not load the hiring team.');
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roleId]);

  const memberIds = useMemo(() => new Set((members || []).map((m) => m.user_id)), [members]);
  const addableUsers = useMemo(
    () => orgUsers.filter((u) => !memberIds.has(u.id)),
    [orgUsers, memberIds],
  );

  const addMember = async () => {
    if (!pick.user_id) return;
    setBusy(true);
    setError(null);
    try {
      await hiringTeamApi.set(roleId, Number(pick.user_id), pick.team_role);
      await reload();
      setPick((p) => ({ ...p, user_id: '' }));
    } catch {
      setError('Could not add that person to the hiring team.');
    } finally {
      setBusy(false);
    }
  };

  const changeRole = async (userId, teamRole) => {
    setError(null);
    try {
      await hiringTeamApi.set(roleId, userId, teamRole);
      await reload();
    } catch {
      setError('Could not update that team role.');
    }
  };

  const removeMember = async (userId) => {
    setError(null);
    try {
      await hiringTeamApi.remove(roleId, userId);
      await reload();
    } catch {
      setError('Could not remove that person.');
    }
  };

  if (members === null && !error) {
    return (
      <div className="flex justify-center py-10">
        <Spinner />
      </div>
    );
  }

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
          <label className="block w-64">
            <span className="mb-1 block text-xs text-[var(--taali-muted)]">Team member</span>
            <Select
              value={pick.user_id}
              onChange={(e) => setPick((p) => ({ ...p, user_id: e.target.value }))}
              placeholder="Choose someone"
            >
              <option value="">Choose someone</option>
              {addableUsers.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.full_name || u.email}
                </option>
              ))}
            </Select>
          </label>
          <label className="block w-44">
            <span className="mb-1 block text-xs text-[var(--taali-muted)]">Role on this job</span>
            <Select
              value={pick.team_role}
              onChange={(e) => setPick((p) => ({ ...p, team_role: e.target.value }))}
            >
              {TEAM_ROLES.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </Select>
          </label>
          <Button variant="primary" disabled={busy || !pick.user_id} onClick={addMember}>
            Add
          </Button>
        </div>
      </Card>

      {(members || []).length === 0 ? (
        <EmptyState
          title="No one on the hiring team yet"
          description="Add hiring managers, recruiters, interviewers and coordinators for this job."
          className="py-8"
        />
      ) : (
        <div className="space-y-2">
          {members.map((m) => (
            <Card key={m.user_id} className="flex items-center justify-between gap-3 px-4 py-3">
              <div>
                <div className="text-sm font-medium text-[var(--taali-text)]">{m.name || m.email}</div>
                {m.email && m.name ? (
                  <div className="text-xs text-[var(--taali-muted)]">{m.email}</div>
                ) : null}
              </div>
              <div className="flex items-center gap-2">
                <Badge variant="purple">{roleLabel(m.team_role)}</Badge>
                <Select
                  value={m.team_role}
                  inline
                  aria-label="Change team role"
                  onChange={(e) => changeRole(m.user_id, e.target.value)}
                >
                  {TEAM_ROLES.map((r) => (
                    <option key={r.value} value={r.value}>
                      {r.label}
                    </option>
                  ))}
                </Select>
                <Button variant="ghost" size="xs" onClick={() => removeMember(m.user_id)}>
                  Remove
                </Button>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
};

export default HiringTeamPanel;
