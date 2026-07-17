export const loadDecisionWorkableStages = async (
  organizationsApi,
  decision,
  alternative,
) => {
  if (!alternative?.requireStagePick || !decision?.workable_job_id) return [];
  if (!organizationsApi?.getWorkableStages) {
    throw new Error('Workable stage lookup is unavailable.');
  }
  const response = await organizationsApi.getWorkableStages({
    shortcode: decision.workable_job_id,
  });
  return Array.isArray(response?.data?.stages) ? response.data.stages : [];
};

export default loadDecisionWorkableStages;
