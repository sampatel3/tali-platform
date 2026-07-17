export const mapAssessmentForDetail = (assessment) => ({
  id: assessment.id,
  name: (assessment.candidate_name
    || assessment.candidate?.full_name
    || assessment.candidate_email
    || '').trim() || 'Unknown',
  email: assessment.candidate_email || assessment.candidate?.email || '',
  task: assessment.task_name || assessment.task?.name || 'Assessment',
  status: assessment.status || 'pending',
  score: assessment.score ?? assessment.overall_score ?? null,
  time: assessment.duration_taken ? `${Math.round(assessment.duration_taken / 60)}m` : '—',
  position: assessment.role_name || assessment.candidate?.position || '',
  completedDate: assessment.completed_at
    ? new Date(assessment.completed_at).toLocaleDateString()
    : null,
  breakdown: assessment.breakdown || null,
  prompts: assessment.prompt_count ?? 0,
  promptsList: assessment.prompts_list || [],
  timeline: assessment.timeline || [],
  results: assessment.results || [],
  token: assessment.token,
  _raw: assessment,
});
