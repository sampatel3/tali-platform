export const remainingSecondsUntil = (deadlineMs, nowMs = Date.now()) => {
  const deadline = Number(deadlineMs);
  const now = Number(nowMs);
  if (!Number.isFinite(deadline) || !Number.isFinite(now)) return 0;
  return Math.max(0, Math.ceil((deadline - now) / 1000));
};

export default remainingSecondsUntil;
