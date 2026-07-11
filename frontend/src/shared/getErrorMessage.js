// One shared error-message extractor for every recruiter- and candidate-facing
// surface. Previously five near-identical copies drifted across the app
// (candidates, settings x2, tasks, assessment chat); this is the single source.
//
// Rules:
//   - Prefer the API's `detail` string when it reads like a human message.
//   - Handle FastAPI validation arrays ([{ loc, msg }]) into "field: message".
//   - Map connection timeouts to a plain "try again" line.
//   - Never surface raw status codes, stack text, or JSON blobs — fall back to
//     the caller's plain-English fallback instead.
export const getErrorMessage = (err, fallback = 'Something went wrong. Please try again.') => {
  // A stalled connection trips the per-request axios timeout (ECONNABORTED)
  // with no response body to read — give a clear "try again" rather than a
  // generic network line or the fallback.
  if (err?.code === 'ECONNABORTED' || err?.code === 'ETIMEDOUT') {
    return 'That took too long — please try again.';
  }

  const detail = err?.response?.data?.detail;
  if (detail != null) {
    if (typeof detail === 'string') {
      const trimmed = detail.trim();
      // Guard against raw JSON blobs / stack dumps leaking to users.
      if (trimmed && trimmed.length < 300 && !trimmed.startsWith('{') && !trimmed.startsWith('[')) {
        return trimmed;
      }
    } else if (Array.isArray(detail) && detail.length) {
      const first = detail[0] || {};
      const msg = first?.msg ?? String(first);
      const locParts = Array.isArray(first?.loc)
        ? first.loc.filter((segment) => String(segment).toLowerCase() !== 'body')
        : [];
      if (locParts.length) {
        const loc = locParts.join('.').replace(/_/g, ' ');
        return `${loc}: ${msg}`;
      }
      if (typeof msg === 'string' && msg.trim()) return msg.trim();
    } else if (typeof detail === 'object') {
      // FastAPI-Users password-validation failures come back as
      // { code, reason }; other endpoints use { message }.
      const objMsg = (typeof detail.reason === 'string' && detail.reason.trim())
        || (typeof detail.message === 'string' && detail.message.trim());
      if (objMsg) return objMsg;
    }
  }

  return fallback;
};

export default getErrorMessage;
