const rowKey = (row) => String(row?.id ?? row?.task_key ?? row?.name ?? '');

export const loadAllPages = async (
  fetchPage,
  { initialItems = [], pageSize = 100, params = {} } = {},
) => {
  const items = [...initialItems];
  const seen = new Set(items.map(rowKey));
  let offset = items.length;
  while (true) {
    const response = await fetchPage({ ...params, limit: pageSize, offset });
    const page = Array.isArray(response?.data) ? response.data : [];
    page.forEach((row) => {
      const key = rowKey(row);
      if (!seen.has(key)) {
        seen.add(key);
        items.push(row);
      }
    });
    if (page.length < pageSize) return items;
    offset += page.length;
  }
};

export default loadAllPages;
