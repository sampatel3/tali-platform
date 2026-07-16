import { useCallback, useEffect, useState } from 'react';

import { clientApi } from '../clients/api';
import { getErrorMessage } from '../candidates/candidatesUiUtils';

// Owns the two optimistic job-brief controls shown together on the Job spec
// tab. Keeping their authorization guard, rollback, version conflict handling,
// and client option loading together prevents the page shell from duplicating
// mutation mechanics.
export const useRoleBriefControls = ({
  canControlRole,
  handleRoleVersionConflict,
  role,
  roleId,
  rolesApi,
  setRole,
  showToast,
}) => {
  const [savingJobStatus, setSavingJobStatus] = useState(false);
  const [clients, setClients] = useState([]);
  const [savingClient, setSavingClient] = useState(false);

  useEffect(() => {
    let cancelled = false;
    clientApi
      .list()
      .then((rows) => { if (!cancelled) setClients(Array.isArray(rows) ? rows : []); })
      .catch(() => { if (!cancelled) setClients([]); });
    return () => { cancelled = true; };
  }, []);

  const setJobStatus = useCallback(async (nextStatus) => {
    if (!Number.isFinite(roleId) || !nextStatus || !canControlRole) return;
    const previous = role?.job_status;
    if (nextStatus === previous) return;
    setSavingJobStatus(true);
    setRole((current) => (current ? { ...current, job_status: nextStatus } : current));
    try {
      const response = await rolesApi.setJobStatus(
        roleId,
        nextStatus,
        undefined,
        role?.version,
      );
      if (response?.data) setRole(response.data);
      showToast('Job status updated.', 'success');
    } catch (error) {
      if (!handleRoleVersionConflict(error)) {
        setRole((current) => (current ? { ...current, job_status: previous } : current));
        showToast(getErrorMessage(error, 'Failed to update job status.'), 'error');
      }
    } finally {
      setSavingJobStatus(false);
    }
  }, [canControlRole, handleRoleVersionConflict, role?.job_status, role?.version, roleId, rolesApi, setRole, showToast]);

  const setClient = useCallback(async (nextClientId) => {
    if (!Number.isFinite(roleId) || !canControlRole) return;
    const previousId = role?.client_id ?? null;
    const previousName = role?.client_name ?? null;
    if ((nextClientId ?? null) === previousId) return;
    const nextName = nextClientId == null
      ? null
      : (clients.find((client) => client.id === nextClientId)?.name ?? null);
    setSavingClient(true);
    setRole((current) => (current ? {
      ...current,
      client_id: nextClientId ?? null,
      client_name: nextName,
    } : current));
    try {
      const response = await rolesApi.setClient(roleId, nextClientId, role?.version);
      if (response?.data) setRole(response.data);
      showToast(
        nextClientId == null ? 'Hiring department cleared.' : 'Hiring department assigned.',
        'success',
      );
    } catch (error) {
      if (!handleRoleVersionConflict(error)) {
        setRole((current) => (current ? {
          ...current,
          client_id: previousId,
          client_name: previousName,
        } : current));
        showToast(getErrorMessage(error, 'Failed to update hiring department.'), 'error');
      }
    } finally {
      setSavingClient(false);
    }
  }, [canControlRole, clients, handleRoleVersionConflict, role?.client_id, role?.client_name, role?.version, roleId, rolesApi, setRole, showToast]);

  return { clients, savingClient, savingJobStatus, setClient, setJobStatus };
};

export default useRoleBriefControls;
