export const APPLICATION_ROSTER_PAGE_SIZE = 200;

const BASE_QUERY = {
  sort_by: 'pre_screen_score',
  sort_order: 'desc',
  limit: APPLICATION_ROSTER_PAGE_SIZE,
};

const pagePayload = (response) => {
  const payload = response?.data;
  if (Array.isArray(payload)) return { items: payload, total: null };
  const rawTotal = payload?.total;
  return {
    items: Array.isArray(payload?.items) ? payload.items : [],
    total: rawTotal != null && Number.isFinite(Number(rawTotal)) ? Number(rawTotal) : null,
  };
};

/**
 * Load one outcome at a time and one DB page at a time. The progress callback
 * paints the first open page immediately while later pages keep accumulating.
 * Array responses remain supported for legacy servers and lightweight mocks.
 */
export const loadApplicationRoster = async ({
  rolesApi,
  roleId,
  isSister = false,
  isCurrent = () => true,
  onProgress,
}) => {
  const applications = [];
  const seenIds = new Set();
  const outcomes = isSister
    ? ['open', 'rejected', 'hired', 'withdrawn']
    : ['open', 'rejected'];
  let firstError = null;

  for (const applicationOutcome of outcomes) {
    let offset = 0;
    let knownTotal = null;
    while (true) {
      let response;
      try {
        const params = { ...BASE_QUERY, application_outcome: applicationOutcome, offset };
        response = typeof rolesApi.listApplicationsPage === 'function'
          ? await rolesApi.listApplicationsPage(roleId, params)
          : await rolesApi.listApplications(roleId, params);
      } catch (error) {
        firstError ||= error;
        break;
      }
      if (!isCurrent()) return { applications, error: firstError, cancelled: true };

      const { items, total } = pagePayload(response);
      if (total != null) knownTotal = total;
      let added = 0;
      for (const application of items) {
        const id = application?.id;
        if (id != null && seenIds.has(id)) continue;
        if (id != null) seenIds.add(id);
        applications.push(application);
        added += 1;
      }
      onProgress?.([...applications]);

      offset += items.length;
      if (
        items.length === 0
        || (knownTotal == null && added === 0)
        || (knownTotal != null ? offset >= knownTotal : items.length < APPLICATION_ROSTER_PAGE_SIZE)
      ) break;
    }
  }

  return { applications, error: firstError, cancelled: false };
};
