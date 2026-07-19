const decisionValuesEqual = (left, right) => {
  if (Object.is(left, right)) return true;
  if (left == null || right == null || typeof left !== typeof right) return false;
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left)
      && Array.isArray(right)
      && left.length === right.length
      && left.every((value, index) => decisionValuesEqual(value, right[index]));
  }
  if (typeof left !== 'object') return false;
  const leftKeys = Object.keys(left);
  const rightKeys = Object.keys(right);
  return leftKeys.length === rightKeys.length
    && leftKeys.every((key) => Object.hasOwn(right, key)
      && decisionValuesEqual(left[key], right[key]));
};

/** Compare complete pending decisions, not only their durable row ids. */
export const pendingDecisionMapsEqual = (previous, next) => {
  const previousKeys = Object.keys(previous || {});
  const nextKeys = Object.keys(next || {});
  return previousKeys.length === nextKeys.length
    && previousKeys.every((key) => Object.hasOwn(next || {}, key)
      && decisionValuesEqual(previous[key], next[key]));
};

export default pendingDecisionMapsEqual;
