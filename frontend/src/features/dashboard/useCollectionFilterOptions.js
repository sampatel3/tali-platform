import { useCallback, useEffect, useRef, useState } from 'react';

import { loadAllPages } from '../../shared/api/loadAllPages';

const PAGE_SIZE = 100;

const totalHeader = (response) => {
  const raw = response?.headers?.get?.('x-total-count')
    ?? response?.headers?.['x-total-count'];
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : null;
};

export const useCollectionFilterOptions = (tasksApi, rolesApi) => {
  const [tasks, setTasks] = useState([]);
  const [roles, setRoles] = useState([]);
  const [rolesCount, setRolesCount] = useState(0);
  const tasksComplete = useRef(false);
  const rolesComplete = useRef(false);
  const tasksLoading = useRef(false);
  const rolesLoading = useRef(false);

  useEffect(() => {
    let active = true;
    tasksApi.list({ limit: PAGE_SIZE, offset: 0 }).then((response) => {
      if (!active) return;
      const page = Array.isArray(response?.data) ? response.data : [];
      setTasks(page);
      tasksComplete.current = page.length < PAGE_SIZE;
    }).catch(() => {});
    if (rolesApi?.list) {
      rolesApi.list({ limit: PAGE_SIZE, offset: 0, include_total: true }).then((response) => {
        if (!active) return;
        const page = Array.isArray(response?.data) ? response.data : [];
        setRoles(page);
        setRolesCount(totalHeader(response) ?? page.length);
        rolesComplete.current = page.length < PAGE_SIZE;
      }).catch(() => {});
    }
    return () => { active = false; };
  }, [rolesApi, tasksApi]);

  const loadAllTasks = useCallback(async () => {
    if (tasksComplete.current || tasksLoading.current) return;
    tasksLoading.current = true;
    try {
      setTasks(await loadAllPages(tasksApi.list, { initialItems: tasks, pageSize: PAGE_SIZE }));
      tasksComplete.current = true;
    } finally {
      tasksLoading.current = false;
    }
  }, [tasks, tasksApi]);

  const loadAllRoles = useCallback(async () => {
    if (!rolesApi?.list || rolesComplete.current || rolesLoading.current) return;
    rolesLoading.current = true;
    try {
      setRoles(await loadAllPages(rolesApi.list, { initialItems: roles, pageSize: PAGE_SIZE }));
      rolesComplete.current = true;
    } finally {
      rolesLoading.current = false;
    }
  }, [roles, rolesApi]);

  return {
    tasks,
    roles,
    rolesCount,
    loadAllTasks,
    loadAllRoles,
  };
};

export default useCollectionFilterOptions;
