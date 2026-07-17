const DEFAULT_PAGE_SIZE = 200;

export const decisionCursorParams = (row) => {
  const createdAt = row?.created_at;
  const id = Number(row?.id);
  if (!createdAt || !Number.isInteger(id) || id < 1) return null;
  return { before_created_at: createdAt, before_id: id };
};

export const listAllDecisionPages = async (
  requestPage,
  params = {},
  pageSize = DEFAULT_PAGE_SIZE,
) => {
  const rows = [];
  const seenIds = new Set();
  let cursor = null;
  let previousCursorKey = null;

  for (;;) {
    const response = await requestPage({
      ...params,
      limit: pageSize,
      ...(cursor || {}),
    });
    const page = Array.isArray(response?.data) ? response.data : [];
    page.forEach((row) => {
      const key = String(row?.id ?? '');
      if (!key || seenIds.has(key)) return;
      seenIds.add(key);
      rows.push(row);
    });
    if (page.length < pageSize) break;

    cursor = decisionCursorParams(page[page.length - 1]);
    const cursorKey = cursor
      ? `${cursor.before_created_at}:${cursor.before_id}`
      : null;
    if (!cursorKey || cursorKey === previousCursorKey) {
      throw new Error('Decision pagination did not return a usable next cursor.');
    }
    previousCursorKey = cursorKey;
  }

  return { data: rows };
};

export const DECISION_PAGE_SIZE = DEFAULT_PAGE_SIZE;
