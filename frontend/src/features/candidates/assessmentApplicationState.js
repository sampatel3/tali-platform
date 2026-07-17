export const resolveAssessmentId = (application) => (
  application?.score_summary?.assessment_id
  || application?.valid_assessment_id
  || null
);

export const resolveAssessmentStatus = (application) => (
  String(
    application?.score_summary?.assessment_status
    || application?.valid_assessment_status
    || '',
  ).toLowerCase()
);
